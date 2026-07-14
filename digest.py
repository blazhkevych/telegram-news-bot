import os
import requests
import sqlite3
from datetime import datetime, date
from bot import call_llm   # той самий ланцюг провайдерів (Gemini→Cerebras→Groq)

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
    lines = []
    for city in CITIES:
        try:
            r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": city["lat"], "longitude": city["lon"],
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
                    "timezone": "Europe/Kyiv", "forecast_days": 1,
                },
                timeout=10,
            )
            d = r.json()["daily"]
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
        except:
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
    conn = sqlite3.connect(DB_PATH)
    today_str = date.today().isoformat()
    rows = conn.execute("""
        SELECT title FROM published
        WHERE published_at >= ?
        ORDER BY published_at DESC LIMIT 20
    """, (today_str,)).fetchall()
    conn.close()
    return [r[0] for r in rows]

def morning_digest():
    weather = get_weather()
    titles  = get_recent_news()
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
    titles = get_recent_news()
    news_text = "\n".join(f"- {t}" for t in titles[:15]) or "Новини дня відсутні."

    text = call_llm(f"""Ти редактор UA News. Склади вечірній підсумок — топ-5 подій дня.
Починай з "🌙 Підсумки дня від UA News"
Формат: нумерований список, кожен пункт 1 речення.
Пиши ЛИШЕ про події, які є в списку новин нижче. Нічого не додумуй.
Стиль: чіткий, журналістський. Числа тільки цифрами. БЕЗ хештегів.

Новини:
{news_text}

Напиши лише текст підсумку.""", max_tokens=600)
    if not text or text == "RATE_LIMIT":
        return "🌙 Підсумки дня від UA News незабаром."
    return text

def post(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHANNEL_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True}
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
        ok = post(evening_digest())
    return ok

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
