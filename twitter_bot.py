import os
import sqlite3
import hashlib
import feedparser
import requests
import calendar
from datetime import datetime, date

TWITTER_API_KEY       = os.environ["TWITTER_API_KEY"]
TWITTER_API_SECRET    = os.environ["TWITTER_API_SECRET"]
TWITTER_ACCESS_TOKEN  = os.environ["TWITTER_ACCESS_TOKEN"]
TWITTER_ACCESS_SECRET = os.environ["TWITTER_ACCESS_SECRET"]
GROQ_API_KEY          = os.environ["GROQ_API_KEY"]

MONTHLY_LIMIT = 500
DB_PATH       = "twitter.db"

RSS_FEEDS = [
    {"url": "https://www.ukrinform.ua/rss/block-lastnews", "lang": "uk"},
    {"url": "https://www.pravda.com.ua/rss/view_news/",   "lang": "uk"},
    {"url": "https://suspilne.media/rss/news.xml",        "lang": "uk"},
    {"url": "https://dou.ua/lenta/feed/",                 "lang": "uk"},
    {"url": "https://techcrunch.com/feed/",               "lang": "en"},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml","lang": "en"},
]

# Браузерний User-Agent: багато видань ріжуть дефолтний UA feedparser і
# віддають порожньо (див. bot.py FEED_UA / БАГ-009). Дубльовано тут навмисно —
# twitter_bot.py не імпортує bot.py, бо той на імпорті вимагає TELEGRAM_*
# змінні оточення, яких немає в кроці Twitter-воркфлоу.
FEED_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def parse_feed(url):
    try:
        r = requests.get(url, headers={"User-Agent": FEED_UA}, timeout=15)
        if r.status_code == 200 and r.content:
            d = feedparser.parse(r.content)
            if d.entries:
                return d
    except Exception as e:
        print(f"⚠️ parse_feed requests {url}: {str(e)[:80]}")
    return feedparser.parse(url)

# ── База даних ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS twitter_posts (
            hash         TEXT PRIMARY KEY,
            title        TEXT,
            published_at TEXT,
            year_month   TEXT
        )
    """)
    conn.commit()
    return conn

def is_published(conn, url: str) -> bool:
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute(
        "SELECT 1 FROM twitter_posts WHERE hash=?", (h,)
    ).fetchone() is not None

def mark_published(conn, url: str, title: str):
    h          = hashlib.md5(url.encode()).hexdigest()
    year_month = datetime.utcnow().strftime("%Y-%m")
    conn.execute(
        "INSERT OR IGNORE INTO twitter_posts VALUES (?,?,?,?)",
        (h, title, datetime.utcnow().isoformat(), year_month)
    )
    conn.commit()

# ── Скільки постів опубліковано цього місяця ───────────────
def posts_this_month(conn) -> int:
    year_month = datetime.utcnow().strftime("%Y-%m")
    row = conn.execute(
        "SELECT COUNT(*) FROM twitter_posts WHERE year_month=?",
        (year_month,)
    ).fetchone()
    return row[0] if row else 0

# ── Скільки постів публікувати сьогодні ───────────────────
def posts_allowed_today(conn) -> int:
    today          = date.today()
    days_in_month  = calendar.monthrange(today.year, today.month)[1]
    days_remaining = days_in_month - today.day + 1
    used           = posts_this_month(conn)
    remaining      = MONTHLY_LIMIT - used

    if remaining <= 0:
        return 0

    # Рівномірно розподіляємо залишок на дні що лишились
    allowed = remaining // days_remaining
    return max(1, allowed)  # мінімум 1 пост якщо ліміт не вичерпано

# ── Збір новин ─────────────────────────────────────────────
def fetch_news(conn) -> list[dict]:
    items = []
    for feed_cfg in RSS_FEEDS:
        try:
            feed = parse_feed(feed_cfg["url"])
            for entry in feed.entries[:5]:
                url = entry.get("link", "")
                if not url or is_published(conn, url):
                    continue
                items.append({
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "url":     url,
                    "source":  feed.feed.get("title", ""),
                    "lang":    feed_cfg["lang"],
                })
        except Exception as e:
            print(f"⚠️ Помилка читання {feed_cfg['url']}: {e}")
    return items

# ── AI генерація твіту ─────────────────────────────────────
def generate_tweet(item: dict) -> str | None:
    lang_note = (
        "Новина англійською — переклади та напиши українською."
        if item["lang"] == "en"
        else "Новина українською."
    )

    prompt = f"""Ти SMM-редактор українського новинного каналу в Twitter/X.
{lang_note}

Напиши твіт за цією новиною. Правила:
- Мова: українська
- Максимум 260 символів включно з посиланням (лишай 25 символів для URL)
- Тобто текст не більше 235 символів
- Починай з найголовнішого факту
- Додай 1-2 emoji на початку
- БЕЗ хештегів
- Живий і зрозумілий стиль

Заголовок: {item['title']}
Текст: {item['summary'][:400]}

Напиши лише текст твіту, без пояснень."""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "model":       "llama-3.3-70b-versatile",
                "messages":    [{"role": "user", "content": prompt}],
                "max_tokens":  150,
                "temperature": 0.7,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Помилка Groq: {e}")
        return None

# ── Публікація в Twitter через OAuth 1.0a ─────────────────
def post_tweet(text: str, url: str) -> bool:
    import tweepy

    tweet_text = f"{text}\n\n{url}"
    if len(tweet_text) > 280:
        tweet_text = tweet_text[:252] + f"...\n\n{url}"

    try:
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
        )
        client.create_tweet(text=tweet_text)
        print(f"✅ Твіт опубліковано: {url}")
        return True
    except Exception as e:
        print(f"❌ Помилка Twitter: {e}")
        return False

# ── Головна функція ────────────────────────────────────────
def main():
    conn    = init_db()
    allowed = posts_allowed_today(conn)
    used    = posts_this_month(conn)
    today   = date.today()
    days_in_month  = calendar.monthrange(today.year, today.month)[1]
    days_remaining = days_in_month - today.day + 1

    print(f"📊 Місяць: використано {used}/{MONTHLY_LIMIT} постів")
    print(f"📅 Залишилось днів: {days_remaining}")
    print(f"📝 Сьогодні можна опублікувати: {allowed}")

    if allowed == 0:
        print("⛔ Місячний ліміт вичерпано.")
        conn.close()
        return

    news  = fetch_news(conn)
    count = 0

    for item in news:
        if count >= allowed:
            break
        if not item["url"]:
            continue

        tweet_text = generate_tweet(item)
        if not tweet_text:
            continue

        if post_tweet(tweet_text, item["url"]):
            mark_published(conn, item["url"], item["title"])
            count += 1

    print(f"\n🏁 Готово. Опубліковано {count} твітів сьогодні.")
    conn.close()

if __name__ == "__main__":
    main()
