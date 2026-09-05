"""Раз на добу перевіряє КОЖНОГО LLM-провайдера і шле звіт адміну.

Навіщо окремий скрипт, якщо є підсумок прогону (STATS у bot.py). Підсумок
показує лише тих, кого прогін СПРОБУВАВ, і не розрізняє два зовсім різні
діагнози, які в логах виглядають однаково — рядком з хрестиком:

  • «сьогодні вичерпано» (429) — провайдер живий, завтра працюватиме;
  • «мертвий назавжди» (401/402/403/404/410) — ключ, підписка або назва
    моделі більше не існують, і жоден наступний виклик не має шансу.

Ціна нерозрізнення відома. GitHub Models віддавав 410 на КОЖНОМУ виклику з
30.07 і був прибраний аж 10.08 (БАГ-016) — 11 діб марних запитів. Mistral
віддає 402 «Check your subscription» на КОЖНОМУ виклику: аудит 17.08.2026
намірив ~5,6 мертвих викликів на прогін ≈ 660 на добу, і в логах це виглядало
як звичайний шум. Обидва рази проблему помітила людина, що читала логи, а не
система. Цей скрипт робить діагноз автоматичним.

Друга частина звіту — чи ЖИВА ще налаштована модель у каталозі провайдера.
Саме тут помер версійний ярлик NVIDIA (`deepseek-v4-flash` → 410 «end of
life», БАГ-016): провайдер був справний, а модель зникла. Каталог відповідає
на це питання ДО того, як воно стане падінням у бойовому прогоні.

Запуск локально:
    # PowerShell
    $env:GROQ_API_KEY="..."; python llm_check.py
    # bash
    GROQ_API_KEY=... python llm_check.py

У GitHub Actions запускається сам, раз на добу — див. scheduler.py.
"""
import time

import requests

from bot import LLM_PROVIDERS, LLM_TIMEOUT, models_of, notify_admin, reasoning_params

# Пробник — СПРАВЖНЯ генерація, а не «ping» на 5 токенів. Аудит 05.09.2026
# (БАГ-017): NVIDIA deepseek-v4-flash відповідав на п'ятитокенний пробник за
# 30 с і отримував «✅ працює», а в бою за LLM_TIMEOUT не встигав у 99%
# викликів (1 054 таймаути на 8 успіхів за добу). Перевірка, яка міряє не те,
# що робить бойовий код, гірша за відсутню: їй вірять. Тому — приблизно той
# самий обсяг, що в rewrite (кілька сотень токенів), той самий таймаут, і в
# звіті — секунди.
PROBE = [{"role": "user", "content":
          "Напиши українською три речення про те, чому Київ називають містом "
          "каштанів. Без вступу й списків."}]
PROBE_TOKENS = 300

# Класифікація за HTTP-статусом. Ключове рішення скрипта — до якої з трьох
# груп віднести провайдера, бо від цього залежить, що робити власнику.
FATAL = {                     # лікується лише правкою конфігу або ключа
    401: "ключ невалідний",
    402: "підписка не активна",
    403: "доступ заборонено",
    404: "модель не існує",
    410: "модель/сервіс закрито назавжди",
}


def models_url(chat_url):
    """Каталог моделей поруч із чатом: .../chat/completions → .../models.

    Працює для всіх наших провайдерів, бо всі вони OpenAI-сумісні — саме та
    властивість, заради якої їх і обрано.
    """
    return chat_url.replace("/chat/completions", "/models")


def catalog(p):
    """Список моделей провайдера, або None якщо каталог недоступний.

    Список НЕ є доказом придатності (gemini-2.5-flash був у списку, але
    «no longer available to new users» — БАГ-008), тому це другий голос поруч
    із реальним викликом, а не заміна йому.
    """
    try:
        r = requests.get(models_url(p["url"]),
                         headers={"Authorization": f"Bearer {p['key']}"},
                         timeout=20)
        if not r.ok:
            return None
        return [m.get("id", "") for m in r.json().get("data", [])] or None
    except Exception:
        return None


def in_catalog(model, ids):
    """Чи є модель у каталозі — з поправкою на префікси провайдерів.

    Точне порівняння давало ХИБНУ тривогу: Google віддає імена як
    `models/gemini-3.5-flash`, тож усі три моделі Gemini у першому ж звіті
    (17.08) отримали «немає в каталозі», хоча реальний виклик до двох із них
    повертав 200. Хибний сигнал у щоденному звіті гірший за відсутній: його
    швидко вчаться ігнорувати — разом зі справжніми.
    """
    return any(i == model or i.endswith("/" + model) or model.endswith("/" + i)
               for i in ids)


def probe(p, model):
    """Реальний виклик КОНКРЕТНОЇ моделі. Повертає (значок, пояснення)."""
    body = {"model": model, "messages": PROBE, "max_tokens": PROBE_TOKENS}
    body.update(reasoning_params(model))     # той самий запит, що в call_llm
    t0 = time.monotonic()
    try:
        r = requests.post(
            p["url"],
            headers={"Authorization": f"Bearer {p['key']}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=LLM_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        # Не смерть, але й не «працює»: у бою call_llm цю модель теж не
        # дочекається. Окремий значок, щоб у звіті це не зливалось із 5xx.
        return "🐢", f"не встигла за {LLM_TIMEOUT} с — у бою марна"
    except Exception as e:
        # NVIDIA на безкоштовному тарифі ділить потужність між усіма і
        # регулярно віддає 529/обрив з'єднання, лишаючись робочою.
        return "⚠️", f"мережа: {str(e)[:60]}"
    took = time.monotonic() - t0

    if r.status_code in FATAL:
        return "⚰️", f"{r.status_code} — {FATAL[r.status_code]}"
    if r.status_code == 429:
        return "🟡", "429 — квота на сьогодні вичерпана (провайдер живий)"
    if r.status_code >= 500:
        return "⚠️", f"{r.status_code} — перевантажений, тимчасово"
    if r.status_code >= 400:
        return "⚰️", f"{r.status_code} — {' '.join((r.text or '').split())[:80]}"
    try:
        choice  = r.json()["choices"][0]
        content = (choice.get("message", {}).get("content") or "").strip()
    except Exception:
        return "⚰️", "200, але відповідь не в форматі OpenAI"
    if not content:
        return "⚰️", "200, але порожня відповідь"
    cut = " (обірвано: finish_reason=length)" if choice.get("finish_reason") == "length" else ""
    return "✅", f"працює, {took:.1f} с{cut}"


def main():
    lines, dead, slow = [], [], []
    alive = 0
    for p in LLM_PROVIDERS:
        cat = catalog(p)
        # Перевіряємо ВСІ моделі провайдера, а не лише першу: сенс запасних у
        # тому, щоб на момент смерті основної вже знати, що заміна робоча.
        # Дізнатись про це в бойовому прогоні — запізно.
        ok_here, slow_here = False, False
        for i, model in enumerate(models_of(p)):
            mark, why = probe(p, model)
            # Про каталог згадуємо, лише коли він СУПЕРЕЧИТЬ конфігу: «моделі
            # немає у списку» — сигнал. «Є» або «не спитали» — шум.
            note = "  ⚠️ немає в каталозі" if (cat is not None and not in_catalog(model, cat)) else ""
            role = "основна" if i == 0 else f"запасна {i}"
            lines.append(f"{mark} {p['name']} / {model} ({role}): {why}{note}")
            if mark in ("✅", "🟡"):
                ok_here = True
            elif mark == "🐢":
                slow_here = True
        if ok_here:
            alive += 1
        elif slow_here:
            slow.append(p["name"])   # живий, але жодна модель не встигає
        else:
            dead.append(p["name"])

    head = (f"🤖 Перевірка LLM: {alive} із {len(LLM_PROVIDERS)} придатні"
            + (f"\n☠️ ПРИБРАТИ З ЛАНЦЮГА: {', '.join(dead)} — "
               f"кожен виклик до них гарантовано марний" if dead else "")
            + (f"\n🐢 ЗАМІНИТИ МОДЕЛІ: {', '.join(slow)} — жодна не встигає за "
               f"{LLM_TIMEOUT} с, у бою це лише таймаути" if slow else ""))

    report = head + "\n\n" + "\n".join(lines)
    print(report)
    notify_admin(report)

    # Свідомо ЗАВЖДИ завершуємось успішно, навіть якщо придатних нуль.
    # scheduler.run() позначає задачу зробленою лише при коді 0, а «зробленою»
    # вона має стати в будь-якому разі: інакше перевірка повторюватиметься
    # щопрогону і засипле адміна тим самим звітом ~96 разів на добу. Терміновість
    # несе ТЕКСТ звіту (рядок «ПРИБРАТИ З ЛАНЦЮГА»), а не код виходу.


if __name__ == "__main__":
    main()
