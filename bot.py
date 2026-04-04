import os
import sqlite3
import hashlib
import feedparser
import requests
import time
from datetime import datetime

# ── Джерела новин (20 джерел) ─────────────────────────────
RSS_FEEDS = [
    # Загальні українські
    {"url": "https://www.ukrinform.ua/rss/block-lastnews",     "lang": "uk"},
    {"url": "https://www.pravda.com.ua/rss/view_news/",        "lang": "uk"},
    {"url": "https://suspilne.media/rss/news.xml",             "lang": "uk"},
    {"url": "https://www.radiosvoboda.org/api/zrqiteoiqp",     "lang": "uk"},
    {"url": "https://tsn.ua/rss/full.rss",                     "lang": "uk"},
    {"url": "https://www.unian.ua/rss/news.rss",               "lang": "uk"},
    {"url": "https://ua.korrespondent.net/rss/all.rss",        "lang": "uk"},
    {"url": "https://nv.ua/rss/all.xml",                       "lang": "uk"},
    # IT та технології
    {"url": "https://dou.ua/lenta/feed/",                      "lang": "uk"},
    {"url": "https://techcrunch.com/feed/",                    "lang": "en"},
    {"url": "https://www.theverge.com/rss/index.xml",          "lang": "en"},
    {"url": "https://feeds.arstechnica.com/arstechnica/index", "lang": "en"},
    # Міжнародні
    {"url": "https://feeds.bbci.co.uk/ukrainian/rss.xml",      "lang": "uk"},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",     "lang": "en"},
    {"url": "https://rss.dw.com/xml/rss-uk-ukr",               "lang": "uk"},
    # Економіка
    {"url": "https://mind.ua/rss.xml",                         "lang": "uk"},
    {"url": "https://biz.liga.net/all/rss.xml",                "lang": "uk"},
    # Наука
    {"url": "https://www.sciencedaily.com/rss/all.xml",        "lang": "en"},
]

# ── Тематичні рубрики ──────────────────────────────────────
CATEGORIES = {
    "🇺🇦 Україна": [
        "україн", "зеленськ", "рада", "кабмін", "зсу", "фронт",
        "окупац", "деокупац", "мобілізац", "обстріл", "ракет",
    ],
    "🌍 Світ": [
        "трамп", "байден", "нато", "євросоюз", "оон", "кремль",
        "путін", "сша", "великобритан", "франц", "німеч",
    ],
    "💻 Технології": [
        "ai", "штучний інтелект", "openai", "google", "apple",
        "microsoft", "startups", "стартап", "it", "software",
        "технолог", "кіберб", "хакер",
    ],
    "💰 Економіка": [
        "ввп", "інфляц", "бюджет", "нбу", "гривн", "долар",
        "євро", "бізнес", "ринок", "акц", "інвест",
    ],
    "⚡ Енергетика": [
        "енергетик", "електрик", "блекаут", "укренерго",
        "газ", "нафт", "атомн", "ядерн",
    ],
}

# ── Спам-фільтр (ці слова = пропускаємо) ──────────────────
SPAM_KEYWORDS = [
    "реклама", "знижка", "акція", "розпродаж", "купи зараз",
    "промокод", "affiliate", "sponsored", "advertisement",
]

TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID        = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
DB_PATH           = "published.db"
MAX_POSTS_PER_RUN = 5   # збільшили до 5 постів за запуск

# ── База даних ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS published (
            hash         TEXT PRIMARY KEY,
            title        TEXT,
            published_at TEXT
        )
    """)
    # Таблиця для фактчекінгу — зберігаємо ключові слова новин
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_topics (
            keyword      TEXT PRIMARY KEY,
            count        INTEGER DEFAULT 1,
            first_seen   TEXT
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

# ── Фактчекінг — підрахунок згадувань теми ────────────────
def get_topic_count(conn, keywords: list[str]) -> int:
    """Повертає скільки різних джерел згадали цю тему."""
    max_count = 0
    for kw in keywords:
        row = conn.execute(
            "SELECT count FROM seen_topics WHERE keyword=?", (kw,)
        ).fetchone()
        if row:
            max_count = max(max_count, row[0])
    return max_count

def update_topic_count(conn, keywords: list[str]):
    for kw in keywords:
        existing = conn.execute(
            "SELECT count FROM seen_topics WHERE keyword=?", (kw,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE seen_topics SET count=count+1 WHERE keyword=?", (kw,)
            )
        else:
            conn.execute(
                "INSERT INTO seen_topics VALUES (?,1,?)",
                (kw, datetime.utcnow().isoformat())
            )
    conn.commit()

def extract_keywords(title: str) -> list[str]:
    """Витягує ключові слова для фактчекінгу."""
    words = title.lower().split()
    # Беремо слова довші за 5 символів як значущі
    return [w.strip(".,!?«»\"'") for w in words if len(w) > 5]

# ── Визначення категорії ───────────────────────────────────
def get_category(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    for category, keywords in CATEGORIES.items():
        if any(kw in text for kw in keywords):
            return category
    return "📰 Новини"  # за замовчуванням

# ── Спам-фільтр ────────────────────────────────────────────
def is_spam(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in SPAM_KEYWORDS)

# ── Збір новин із RSS ──────────────────────────────────────
def fetch_news(conn) -> list[dict]:
    items = []
    for feed_cfg in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries[:5]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                url     = entry.get("link", "")

                if not url or is_published(conn, url):
                    continue
                if is_spam(title, summary):
                    print(f"🚫 Спам: {title[:50]}")
                    continue

                # Оновлюємо лічильник згадувань теми
                keywords = extract_keywords(title)
                update_topic_count(conn, keywords)

                items.append({
                    "title":    title,
                    "summary":  summary,
                    "url":      url,
                    "source":   feed.feed.get("title", ""),
                    "lang":     feed_cfg["lang"],
                    "keywords": keywords,
                    "category": get_category(title, summary),
                })
        except Exception as e:
            print(f"⚠️ Помилка читання {feed_cfg['url']}: {e}")

    # Сортуємо — спочатку новини що згадуються в кількох джерелах
    items.sort(
        key=lambda x: get_topic_count(conn, x["keywords"]),
        reverse=True
    )
    return items

# ── AI обробка через Groq ──────────────────────────────────
def rewrite_with_ai(item: dict) -> str | None:
    lang_note = (
        "Новина англійською — переклади та перепиши українською."
        if item["lang"] == "en"
        else "Новина вже українською — перепиши живою мовою."
    )

    prompt = f"""Ти редактор популярного українського Telegram-каналу з новинами.
{lang_note}

Правила:
- Мова виключно українська
- Довжина: 3–5 речень, коротко і по суті
- Починай з найголовнішого факту
- Стиль: живий, зрозумілий, без канцеляризмів і сенсаційності
- НЕ використовуй хештеги
- НЕ пиши "Джерело:" в тексті
- НЕ вигадуй факти яких немає в оригіналі

Заголовок: {item['title']}
Текст: {item['summary'][:800]}
Джерело: {item['source']}

Напиши лише готовий текст посту, без пояснень."""

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
                "max_tokens":  400,
                "temperature": 0.7,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Помилка Groq: {e}")
        return None

# ── Публікація в Telegram ──────────────────────────────────
def post_to_telegram(text: str, url: str, category: str) -> bool:
    full_text = f"{category}\n\n{text}\n\n🔗 [Читати повністю]({url})"
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id":                  CHANNEL_ID,
            "text":                     full_text,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": False,
        }
    )
    if response.status_code == 200:
        print(f"✅ Опубліковано [{category}]: {url}")
        return True
    else:
        print(f"❌ Помилка Telegram: {response.text}")
        return False

# ── Головна функція ────────────────────────────────────────
def main():
    conn  = init_db()
    news  = fetch_news(conn)
    count = 0

    print(f"📥 Знайдено {len(news)} нових новин")

    for item in news:
        if count >= MAX_POSTS_PER_RUN:
            break
        if not item["url"]:
            continue

        # Фактчекінг: публікуємо якщо тема згадана 2+ рази
        topic_count = get_topic_count(conn, item["keywords"])
        if topic_count < 2:
            print(f"⏳ Чекаємо підтвердження: {item['title'][:50]}")
            continue

        print(f"📝 Обробляю [{item['category']}]: {item['title'][:50]}...")
        post_text = rewrite_with_ai(item)
        if not post_text:
            continue

        if post_to_telegram(post_text, item["url"], item["category"]):
            mark_published(conn, item["url"], item["title"])
            count += 1
            time.sleep(3)  # пауза між постами

    print(f"\n🏁 Готово. Опубліковано {count} новин.")
    conn.close()

if __name__ == "__main__":
    main()
