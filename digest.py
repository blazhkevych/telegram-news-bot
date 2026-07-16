import os
import html
import requests
import sqlite3
from datetime import datetime, date
from bot import call_llm   # той самий ланцюг провайдерів (Groq→Cerebras→Gemini)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
DB_PATH        = "published.db"
DIGEST_TYPE    = os.environ.get("DIGEST_TYPE", "morning")

CITIES = [
    # Підконтрольні території
    {"name": "Київ",           "lat": 50.45, "lon": 30.52},
    {"name": "Харків",         "lat": 49.99, "lon": 36.23},
    {"name": "Одеса",          "lat": 46.48, "lon": 30.73},
    {"name": "Дніпро",         "lat": 48.46, "lon": 35.05},
    {"name": "Запоріжжя",      "lat": 47.84, "lon": 35.14},
    {"name": "Львів",          "lat": 49.84, "lon": 24.03},
    {"name": "Кривий Ріг",     "lat": 47.91, "lon": 33.39},
    {"name": "Миколаїв",       "lat": 46.97, "lon": 32.00},
    {"name": "Вінниця",        "lat": 49.23, "lon": 28.47},
    {"name": "Полтава",        "lat": 49.59, "lon": 34.55},
    {"name": "Черкаси",        "lat": 49.44, "lon": 32.06},
    {"name": "Чернігів",       "lat": 51.49, "lon": 31.28},
    {"name": "Суми",           "lat": 50.91, "lon": 34.80},
    {"name": "Житомир",        "lat": 50.25, "lon": 28.66},
    {"name": "Хмельницький",   "lat": 49.42, "lon": 26.99},
    {"name": "Рівне",          "lat": 50.62, "lon": 26.25},
    {"name": "Луцьк",          "lat": 50.74, "lon": 25.32},
    {"name": "Тернопіль",      "lat": 49.55, "lon": 25.59},
    {"name": "Івано-Франківськ","lat": 48.92, "lon": 24.71},
    {"name": "Ужгород",        "lat": 48.62, "lon": 22.29},
    {"name": "Чернівці",       "lat": 48.29, "lon": 25.94},
    {"name": "Кропивницький",  "lat": 48.51, "lon": 32.27},
    {"name": "Херсон",         "lat": 46.64, "lon": 32.62},
    {"name": "Краматорськ",    "lat": 48.72, "lon": 37.56},
    # Тимчасово окуповані
    {"name": "Донецьк 🔴",     "lat": 48.00, "lon": 37.80},
    {"name": "Луганськ 🔴",    "lat": 48.57, "lon": 39.31},
    {"name": "Сімферополь 🔴", "lat": 44.95, "lon": 34.10},
    {"name": "Маріуполь 🔴",   "lat": 47.10, "lon": 37.54},
    {"name": "Мелітополь 🔴",  "lat": 46.85, "lon": 35.36},
]

def get_weather():
    """Погода ОДНИМ запитом на всі міста (issue #3).

    29 послідовних запитів до Open-Meteo стабільно падали з ReadTimeout для
    ~9 останніх міст (сервіс пригальмовує серію з одного IP) — у дайджесті
    третина міст була «дані недоступні». API приймає списки координат через
    кому і повертає масив результатів у тому ж порядку."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  ",".join(str(c["lat"]) for c in CITIES),
                "longitude": ",".join(str(c["lon"]) for c in CITIES),
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                "timezone": "Europe/Kyiv", "forecast_days": 1,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):   # одна точка → об'єкт, кілька → масив
            data = [data]
    except Exception as e:
        print(f"⚠️ погода (спільний запит): {type(e).__name__}: {str(e)[:80]}")
        return "дані тимчасово недоступні"

    lines = []
    for city, block in zip(CITIES, data):
        try:
            d = block["daily"]
            t_max = round(d["temperature_2m_max"][0])
            t_min = round(d["temperature_2m_min"][0])
            rain  = d["precipitation_sum"][0]
            wcode = d["weathercode"][0]

            if wcode == 0:               icon = "☀️"
            elif wcode in (1, 2, 3):     icon = "⛅"
            elif wcode in (45, 48):      icon = "🌫"
            elif wcode in (51,53,55,61,63,65,80,81,82): icon = "🌧"
            elif wcode in (71,73,75,77,85,86):           icon = "🌨"
            elif wcode in (95,96,99):    icon = "⛈"
            else:                        icon = "🌤"

            rain_str = f", дощ {rain}мм" if rain > 0.5 else ""
            lines.append(f"{icon} {city['name']}: {t_min}°…{t_max}°{rain_str}")
        except Exception as e:
            print(f"⚠️ погода {city['name']}: {type(e).__name__}: {str(e)[:80]}")
            lines.append(f"🌤 {city['name']}: дані недоступні")
    return "\n".join(lines)

def get_rates():
    """Курс НБУ (безкоштовний API, без ключа): долар і євро."""
    try:
        r = requests.get(
            "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json",
            timeout=10,
        )
        data  = r.json()
        rates = {x["cc"]: x["rate"] for x in data if x["cc"] in ("USD", "EUR")}
        usd, eur = rates.get("USD"), rates.get("EUR")
        if usd and eur:
            return f"💵 Долар: {usd:.2f} грн    💶 Євро: {eur:.2f} грн"
    except Exception as e:
        print(f"⚠️ НБУ: {e}")
    return None


def get_recent_news():
    """[(title, msg_id), ...] за сьогодні. msg_id — номер поста в каналі
    (може бути None для старих записів або якщо колонки ще немає)."""
    conn = sqlite3.connect(DB_PATH)
    today_str = date.today().isoformat()
    try:
        rows = conn.execute("""
            SELECT title, msg_id FROM published
            WHERE published_at >= ?
            ORDER BY published_at DESC LIMIT 20
        """, (today_str,)).fetchall()
    except sqlite3.OperationalError:      # БД ще без колонки msg_id
        rows = [(t, None) for (t,) in conn.execute("""
            SELECT title FROM published
            WHERE published_at >= ?
            ORDER BY published_at DESC LIMIT 20
        """, (today_str,)).fetchall()]
    conn.close()
    return rows

def morning_digest():
    weather = get_weather()
    titles  = [t for t, _ in get_recent_news()]
    news_text = "\n".join(f"- {t}" for t in titles[:10]) or "Новини ще збираються."
    today_fmt = date.today().strftime("%d.%m.%Y")

    intro = call_llm(f"""Ти редактор українського Telegram-каналу UA News.
Склади ранковий дайджест. Починай з "🌅 Доброго ранку!"
Якщо новин ще немає — напиши 1-2 теплі речення що UA News вже на зв'язку і незабаром поділиться новинами.
Якщо новини є — коротко (2-3 речення) згадай найважливіші події.
Стиль: теплий і живий. Числа тільки цифрами. БЕЗ хештегів. БЕЗ англійських слів.

Новини:
{news_text}

Напиши лише текст дайджесту без пояснень.""", max_tokens=500)
    if not intro or intro == "RATE_LIMIT":
        intro = "🌅 Доброго ранку! UA News вже на зв'язку."

    rates      = get_rates()
    rates_line = f"\n\n{rates}" if rates else ""
    return f"{intro}{rates_line}\n\n🌤 Погода на {today_fmt}:\n{weather}"

def evening_digest():
    """Повертає (text, parse_mode). Основний шлях — HTML зі стрілкою-посиланням
    на ПОВНИЙ пост каналу під кожним пунктом (прохання власника: «сухий» список
    заголовків без посилань не давав читачеві шляху до деталей). Модель обирає
    топ-5 і віддає «номер|речення» — посилання підставляємо самі з msg_id,
    щоб LLM не могла їх переплутати чи вигадати."""
    rows = get_recent_news()[:15]
    if not rows:
        return "🌙 Підсумки дня від UA News незабаром.", None
    news_text = "\n".join(f"{i+1}. {t}" for i, (t, _) in enumerate(rows))

    raw = call_llm(f"""Ти редактор українського каналу UA News. Із пронумерованого списку
подій дня обери топ-5 НАЙВАЖЛИВІШИХ (різні теми, без двох пунктів про одне).
Для кожної напиши ОДНЕ змістовне речення-підсумок: живою мовою, без
канцелярщини, з головною цифрою/фактом. Почни речення доречним за тоном
емодзі (для трагедій — стримані 💥🚨🕯, не святкові).

Відповідай СУВОРО по рядку на подію, без будь-якого іншого тексту:
<номер зі списку>|<емодзі й речення>

Приклад:
7|💶 ЄС виділяє Україні 920 млн євро на відновлення енергетики перед зимою.

Список подій:
{news_text}""", max_tokens=700, temperature=0.3)
    if not raw or raw == "RATE_LIMIT":
        return "🌙 Підсумки дня від UA News незабаром.", None

    # Публічний канал: CHANNEL_ID = "@username" → лінк t.me/username/msg_id
    username = CHANNEL_ID.lstrip("@") if str(CHANNEL_ID).startswith("@") else None
    items, used = [], set()
    for line in raw.strip().splitlines():
        if "|" not in line:
            continue
        num, text = line.split("|", 1)
        num, text = num.strip().strip(".").strip(), text.strip()
        if not num.isdigit() or not text:
            continue
        idx = int(num) - 1
        if not (0 <= idx < len(rows)) or idx in used:
            continue
        used.add(idx)
        msg_id = rows[idx][1]
        link = (f' <a href="https://t.me/{username}/{msg_id}">→ пост</a>'
                if username and msg_id else "")
        items.append(f"{len(items)+1}. {html.escape(text)}{link}")
        if len(items) == 5:
            break

    if len(items) >= 3:
        return ("<b>🌙 Підсумки дня від UA News</b>\n\n"
                + "\n\n".join(items)), "HTML"

    # Модель не втримала формат «номер|текст» — запасний простий шлях, як раніше
    text = call_llm(f"""Ти редактор UA News. Склади вечірній підсумок — топ-5 подій дня.
Починай з "🌙 Підсумки дня від UA News"
Формат: нумерований список, кожен пункт 1 речення.
Пиши ЛИШЕ про події, які є в списку новин нижче. Нічого не додумуй.
Стиль: чіткий, журналістський. Числа тільки цифрами. БЕЗ хештегів.

Новини:
{news_text}

Напиши лише текст підсумку.""", max_tokens=600, temperature=0.3)
    if not text or text == "RATE_LIMIT":
        return "🌙 Підсумки дня від UA News незабаром.", None
    return text, None

def post(text, parse_mode=None):
    # parse_mode лише той, що явно передали (HTML з екрануванням у вечірньому
    # дайджесті). Сирий LLM-текст шлемо БЕЗ parse_mode: випадкові _ * [ у
    # Markdown валили б увесь пост 400-кою (клас багу, вже лікований у bot.py).
    payload = {"chat_id": CHANNEL_ID, "text": text,
               "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json=payload
    )
    if r.status_code == 200:
        print(f"✅ Дайджест опубліковано ({DIGEST_TYPE})")
        return True
    print(f"❌ {r.text}")
    return False

def main():
    if DIGEST_TYPE == "morning":
        print("🌅 Ранковий дайджест...")
        ok = post(morning_digest())
    else:
        print("🌙 Вечірній підсумок...")
        text, mode = evening_digest()
        ok = post(text, mode)
        if not ok and mode == "HTML":
            # Захисна сітка: якщо Telegram відхилив HTML-варіант — шлемо
            # той самий текст без розмітки, аби підсумок точно вийшов.
            import re as _re
            plain = _re.sub(r"<[^>]+>", "", text)
            ok = post(html.unescape(plain))
    return ok

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
