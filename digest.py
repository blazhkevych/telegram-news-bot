import os
import requests
import sqlite3
from datetime import datetime, date

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
DB_PATH        = "published.db"
DIGEST_TYPE    = os.environ.get("DIGEST_TYPE", "morning")

CITIES = [
    {"name": "Київ",       "lat": 50.45, "lon": 30.52},
    {"name": "Харків",     "lat": 49.99, "lon": 36.23},
    {"name": "Одеса",      "lat": 46.48, "lon": 30.73},
    {"name": "Дніпро",     "lat": 48.46, "lon": 35.05},
    {"name": "Запоріжжя",  "lat": 47.84, "lon": 35.14},
    {"name": "Львів",      "lat": 49.84, "lon": 24.03},
    {"name": "Миколаїв",   "lat": 46.97, "lon": 32.00},
    {"name": "Вінниця",    "lat": 49.23, "lon": 28.47},
    {"name": "Полтава",    "lat": 49.59, "lon": 34.55},
    {"name": "Кривий Ріг", "lat": 47.91, "lon": 33.39},
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

def groq(prompt, max_tokens=400):
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens, "temperature": 0.7},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def morning_digest():
    weather = get_weather()
    titles  = get_recent_news()
    news_text = "\n".join(f"- {t}" for t in titles[:10]) or "Новини ще збираються."
    today_fmt = date.today().strftime("%d.%m.%Y")

    try:
        intro = groq(f"""Ти редактор українського Telegram-каналу UA News.
Склади короткий ранковий дайджест. Починай з привітання.
Коротко (2-3 речення) згадай найважливіші події зі списку.
Стиль: теплий і живий. Числа тільки цифрами. БЕЗ хештегів.

Новини:
{news_text}

Напиши лише текст дайджесту.""", max_tokens=250)
    except:
        intro = "🌅 Доброго ранку! UA News вже на зв'язку."

    return f"{intro}\n\n🌤 Погода на {today_fmt}:\n{weather}"

def evening_digest():
    titles = get_recent_news()
    news_text = "\n".join(f"- {t}" for t in titles[:15]) or "Новини дня відсутні."

    try:
        return groq(f"""Ти редактор UA News. Склади вечірній підсумок — топ-5 подій дня.
Починай з "🌙 Підсумки дня від UA News"
Формат: нумерований список, кожен пункт 1 речення.
Стиль: чіткий, журналістський. Числа тільки цифрами. БЕЗ хештегів.

Новини:
{news_text}

Напиши лише текст підсумку.""")
    except:
        return "🌙 Підсумки дня від UA News незабаром."

def post(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHANNEL_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True}
    )
    if r.status_code == 200:
        print(f"✅ Дайджест опубліковано ({DIGEST_TYPE})")
    else:
        print(f"❌ {r.text}")

def main():
    if DIGEST_TYPE == "morning":
        print("🌅 Ранковий дайджест...")
        post(morning_digest())
    else:
        print("🌙 Вечірній підсумок...")
        post(evening_digest())

if __name__ == "__main__":
    main()
