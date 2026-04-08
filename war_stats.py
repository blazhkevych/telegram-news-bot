import os
import requests
import feedparser
from datetime import date

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]

SOURCES = [
    "https://www.ukrinform.ua/rss/block-lastnews",
    "https://suspilne.media/rss/news.xml",
    "https://www.unian.ua/rss/news.rss",
]

KEYWORDS = [
    "бойові втрати", "втрати ворога", "генштаб",
    "знищено", "збито", "ппо", "повітряна",
    "загарбник", "окупант", "особового складу",
    "танк", "артилер", "безпілотн", "крилат",
    "зведення", "станом на",
]

def fetch_stats():
    for source_url in SOURCES:
        try:
            feed = feedparser.parse(source_url)
            for entry in feed.entries[:20]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                url     = entry.get("link", "")
                text    = (title + " " + summary).lower()
                matches = sum(1 for kw in KEYWORDS if kw in text)
                if matches >= 2:
                    print(f"✅ Знайдено зведення: {title[:60]}")
                    return {"title": title, "text": summary, "url": url}
        except Exception as e:
            print(f"⚠️ {source_url}: {e}")
    return None

def format_stats(item):
    prompt = f"""Ти редактор українського Telegram-каналу UA News.
Перед тобою новина про втрати ворога або зведення ППО.

Відформатуй як пост для Telegram:
- Починай з "📊 Зведення станом на [дата]"
- Виділи ключові цифри окремими рядками з emoji
- Особовий склад: 👥
- Танки: 🪖
- Артилерія: 💥
- Літаки/вертольоти: ✈️
- Дрони: 🚁
- Кораблі: 🚢
- В кінці: "Джерело: Генеральний штаб ЗСУ"
- Числа тільки цифрами
- БЕЗ хештегів

Заголовок: {item['title']}
Текст: {item['text'][:1000]}

Напиши лише готовий пост."""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.3},
            timeout=30,
        )
        if r.status_code == 429:
            print("⚠️ Groq ліміт")
            return None
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Groq: {e}")
        return None

def post_to_telegram(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHANNEL_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True}
    )
    if r.status_code == 200:
        print("✅ Статистику опубліковано")
    else:
        print(f"❌ Telegram: {r.text}")

def main():
    print("📊 Збираємо зведення...")
    item = fetch_stats()
    if not item:
        print("⚠️ Зведення не знайдено")
        return
    text = format_stats(item)
    if text:
        post_to_telegram(text)

if __name__ == "__main__":
    main()
