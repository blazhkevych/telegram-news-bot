import os
import sqlite3
import hashlib
import feedparser
import requests
import google.generativeai as genai
from datetime import datetime

# ── Налаштування ──────────────────────────────────────────
RSS_FEEDS = [
    "https://www.ukrinform.ua/rss/block-lastnews",   # Укрінформ
    "https://dou.ua/lenta/feed/",                    # DOU (IT)
    "https://feeds.bbci.co.uk/ukrainian/rss.xml",    # BBC Ukraine
    "https://www.pravda.com.ua/rss/view_news/",      # Укрправда
    "https://techcrunch.com/feed/",                  # TechCrunch
]

TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID       = os.environ["TELEGRAM_CHANNEL_ID"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
DB_PATH          = "published.db"
MAX_POSTS_PER_RUN = 3   # скільки новин публікуємо за один запуск

# ── База даних (пам'ять про опубліковані новини) ───────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS published (
            hash TEXT PRIMARY KEY,
            title TEXT,
            published_at TEXT
        )
    """)
    conn.commit()
    return conn

def is_published(conn, url: str) -> bool:
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute(
        "SELECT 1 FROM published WHERE hash=?", (h,)
    ).fetchone() is not None

def mark_published(conn, url: str, title: str):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute(
        "INSERT OR IGNORE INTO published VALUES (?,?,?)",
        (h, title, datetime.utcnow().isoformat())
    )
    conn.commit()

# ── Збір новин із RSS ──────────────────────────────────────
def fetch_news() -> list[dict]:
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:   # беремо 5 свіжих з кожного
                items.append({
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "url":     entry.get("link", ""),
                    "source":  feed.feed.get("title", ""),
                })
        except Exception as e:
            print(f"Помилка читання {url}: {e}")
    return items

# ── AI обробка через Gemini ────────────────────────────────
def rewrite_with_ai(title: str, summary: str, source: str) -> str | None:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""Ти редактор популярного українського Telegram-каналу з новинами.
Перепиши цю новину для каналу. Правила:
- Мова: українська
- Довжина: 3-5 речень, коротко і по суті
- Починай з найголовнішого факту
- Додай 1-2 доречних emoji на початку
- НЕ використовуй хештеги
- НЕ пиши "Джерело:" в тексті
- Стиль: живий, зрозумілий, без канцеляризмів

Заголовок: {title}
Текст: {summary}
Джерело: {source}

Напиши лише готовий пост, без пояснень."""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Помилка Gemini: {e}")
        return None

# ── Публікація в Telegram ──────────────────────────────────
def post_to_telegram(text: str, url: str) -> bool:
    full_text = f"{text}\n\n🔗 [Читати повністю]({url})"
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id":    CHANNEL_ID,
            "text":       full_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
    )
    if response.status_code == 200:
        print(f"✅ Опубліковано: {url}")
        return True
    else:
        print(f"❌ Помилка Telegram: {response.text}")
        return False

# ── Головна функція ────────────────────────────────────────
def main():
    conn = init_db()
    news  = fetch_news()
    count = 0

    for item in news:
        if count >= MAX_POSTS_PER_RUN:
            break
        if not item["url"] or is_published(conn, item["url"]):
            continue

        print(f"Обробляю: {item['title'][:60]}...")
        post_text = rewrite_with_ai(
            item["title"], item["summary"], item["source"]
        )
        if not post_text:
            continue

        if post_to_telegram(post_text, item["url"]):
            mark_published(conn, item["url"], item["title"])
            count += 1

    print(f"Готово. Опубліковано {count} новин.")
    conn.close()

if __name__ == "__main__":
    main()
