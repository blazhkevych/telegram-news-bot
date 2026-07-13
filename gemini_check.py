"""Діагностика Gemini — НЕ чіпає бойовий bot.py.

Навіщо: bot.py логує 404 від Gemini лише як обрізаний «...generativelanguage.google».
Цей скрипт показує ПОВНУ відповідь Google, щоб зрозуміти справжню причину:
модель не існує / ключ невалідний / Generative Language API не увімкнено.

Запуск локально (ключ той самий, що в GitHub Secret GEMINI_API_KEY):

    # PowerShell
    $env:GEMINI_API_KEY="ваш_ключ"; python gemini_check.py
    # bash
    GEMINI_API_KEY=ваш_ключ python gemini_check.py
"""
import os
import requests

KEY = os.environ.get("GEMINI_API_KEY")
if not KEY:
    raise SystemExit("❌ Немає GEMINI_API_KEY в оточенні. Задай і запусти ще раз.")

BASE  = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-3.5-flash"  # той самий, що зараз у bot.py
# Кандидати на резервну модель (перевіряємо через РЕАЛЬНИЙ виклик генерації —
# присутність у списку моделей ще не означає, що модель доступна, як показав
# gemini-2.5-flash: у списку є, але «no longer available to new users»).
CANDIDATES = [
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-flash-latest",
]

# 1) Список моделей — одразу видно, чи ключ і API взагалі робочі,
#    і чи є серед доступних потрібна модель.
print("=== 1) Доступні моделі для цього ключа ===")
r = requests.get(f"{BASE}/models", params={"key": KEY}, timeout=30)
print("HTTP", r.status_code)
if r.ok:
    names = [m.get("name", "") for m in r.json().get("models", [])]
    for n in names:
        print("   ", n)
    hit = [n for n in names if MODEL in n]
    print(f"\n   Модель '{MODEL}' у списку: {'ТАК' if hit else 'НІ'}")
else:
    print(r.text[:1000])

# 2) Реальний виклик генерації (OpenAI-сумісний ендпоінт, як у bot.py) —
#    по кожному кандидату. Зелений (HTTP 200) = модель придатна для bot.py.
print("\n=== 2) Chat completion по кандидатах (як у bot.py) ===")
# Класифікація за статусом:
#   200 → працює зараз;  404 → модель закрита (треба міняти назву);
#   503/429 → назва ВАЛІДНА (резолвиться), просто зайнята / вичерпана квота —
#             для bot.py придатна, бо це резервний провайдер і fallback покриє.
valid_names = []   # назва прийнята (200/503/429) — годиться для bot.py
dead_names  = []   # 404 — модель закрита
for m in CANDIDATES:
    r = requests.post(
        f"{BASE}/openai/chat/completions",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={"model": m,
              "messages": [{"role": "user", "content": "Скажи 'ок' одним словом."}],
              "max_tokens": 20},
        timeout=30,
    )
    c = r.status_code
    mark = {200: "✅ працює", 404: "❌ закрита (404)",
            503: "🟡 валідна, зайнята (503)", 429: "🟡 валідна, квота (429)"}.get(
                c, f"⚠️ HTTP {c}")
    print(f"{mark:32} {m}")
    if c == 404:
        dead_names.append(m)
    elif c in (200, 503, 429):
        valid_names.append(m)
    else:
        valid_names.append(m)  # інші коди — назва, найпевніше, теж валідна
    if c != 200:
        print("     ", " ".join((r.text or "").split())[:180])

print("\n--- Підсумок ---")
print("Валідні назви (годяться для bot.py):", ", ".join(valid_names) or "—")
print("Закриті (404, не використовувати):  ", ", ".join(dead_names) or "—")
if MODEL in dead_names:
    repl = valid_names[0] if valid_names else "(немає валідних — перевір ключ/API)"
    print(f"\n❌ У bot.py стоїть '{MODEL}' — вона ЗАКРИТА. Заміни на: {repl}")
elif MODEL in valid_names:
    print(f"\n✅ У bot.py стоїть '{MODEL}' — назва валідна. "
          "503/429 транзієнтні; як резервний провайдер годиться.")
else:
    print(f"\n⚠️ '{MODEL}' не було серед перевірених — додай у CANDIDATES.")
