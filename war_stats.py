import os
import requests
import re
from bs4 import BeautifulSoup
from datetime import date

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]

def fetch_stats():
    try:
        import feedparser
        feed = feedparser.parse("https://www.ukrinform.ua/rss/block-ato")
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            url = entry.get("link", "")
            if any(kw in title.lower() for kw in [
                "бойові втрати", "генштаб", "зведення", "втрати ворога"
            ]):
                return {"title": title, "text": summary, "url": url}
    except Exception as e:
        print(f"⚠️ Помилка парсингу: {e}")
    return None

def format_stats(item: dict) -> str | None:
    """Форматує статистику через AI."""
    prompt = f"""Ти редактор українського Telegram-каналу UA News.
Перед тобою офіційне зведення Генерального штабу ЗСУ про втрати ворога.

Відформатуй це як короткий пост для Telegram:
- Починай з "📊 Втрати ворога станом на [дата]"
- Виділи ключові цифри: особовий склад, танки, артилерія, авіація, дрони
- Кожен рядок окремо з відповідним emoji
- Стиль: офіційний але читабельний
- Числа тільки цифрами
- В кінці: "Дані: Генеральний штаб ЗСУ"

Заголовок: {item['title']}
Текст: {item['text'][:1000]}

Напиши лише готовий пост."""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.3},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Groq: {e}")
        return None

def post_to_telegram(text: str):
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
    print("📊 Збираємо статистику втрат...")
    item = fetch_stats()
    if not item:
        print("⚠️ Зведення Генштабу не знайдено")
        return

    print(f"✅ Знайдено: {item['title'][:60]}")
    text = format_stats(item)
    if not text:
        return

    post_to_telegram(text)

if __name__ == "__main__":
    main()
