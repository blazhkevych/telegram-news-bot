import os
import sqlite3
import hashlib
import feedparser
import requests
import time
import re
from datetime import datetime

# ── Джерела новин ─────────────────────────────────────────
RSS_FEEDS = [
    {"url": "https://www.ukrinform.ua/rss/block-lastnews",     "lang": "uk"},
    {"url": "https://www.pravda.com.ua/rss/view_news/",        "lang": "uk"},
    {"url": "https://suspilne.media/rss/news.xml",             "lang": "uk"},
    {"url": "https://www.radiosvoboda.org/api/zrqiteoiqp",     "lang": "uk"},
    {"url": "https://tsn.ua/rss/full.rss",                     "lang": "uk"},
    {"url": "https://www.unian.ua/rss/news.rss",               "lang": "uk"},
    {"url": "https://ua.korrespondent.net/rss/all.rss",        "lang": "uk"},
    {"url": "https://nv.ua/ukr/rss/all.xml",                   "lang": "uk"},
    {"url": "https://dou.ua/lenta/feed/",                      "lang": "uk"},
    {"url": "https://techcrunch.com/feed/",                    "lang": "en"},
    {"url": "https://www.theverge.com/rss/index.xml",          "lang": "en"},
    {"url": "https://feeds.arstechnica.com/arstechnica/index", "lang": "en"},
    {"url": "https://feeds.bbci.co.uk/ukrainian/rss.xml",      "lang": "uk"},
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",     "lang": "en"},
    {"url": "https://rss.dw.com/xml/rss-uk-ukr",               "lang": "uk"},
    {"url": "https://mind.ua/rss.xml",                         "lang": "uk"},
    {"url": "https://biz.liga.net/all/rss.xml",                "lang": "uk"},
    {"url": "https://www.sciencedaily.com/rss/all.xml",        "lang": "en"},
]

CATEGORIES = {
    "🇺🇦 Україна": [
        "україн", "зеленськ", "верховна рада", "кабмін", "зсу", "фронт",
        "окупац", "деокупац", "мобілізац", "обстріл", "ракет", "київ",
    ],
    "🌍 Світ": [
        "трамп", "байден", "нато", "євросоюз", "оон", "кремль",
        "путін", "сша", "великобритан", "франц", "німеч", "китай",
        "german", "military", "approval", "армія", "військ", "мобіліз",
        "орбан", "угорщин", "orbán", "orban", "польщ", "румун",
    ],
    "💻 Технології": [
        "штучний інтелект", "openai", "google", "apple", "microsoft",
        "стартап", "кіберб", "хакер", "software", "hardware",
        "програм", "додаток", "смартфон",
    ],
    "💰 Економіка": [
        "ввп", "інфляц", "бюджет", "нбу", "гривн", "долар",
        "євро", "бізнес", "ринок", "акц", "інвест", "банк",
    ],
    "⚡ Енергетика": [
        "енергетик", "електрик", "блекаут", "укренерго",
        "газ", "нафт", "атомн", "ядерн", "світло",
    ],
    "🔬 Наука": [
        "вчені", "дослідник", "науков", "відкрит", "experiment",
        "research", "study", "science", "brain", "cell", "gene",
        "climate", "space", "nasa", "зорі", "планет",
    ],
    "🏥 Здоров'я": [
        "здоров", "медицин", "лікар", "хвороб", "вакцин",
        "cancer", "virus", "health", "medical", "disease",
        "лікуван", "препарат", "hospital",
    ],
}

SPAM_KEYWORDS = [
    "реклама", "знижка", "акція", "розпродаж", "купи зараз",
    "промокод", "affiliate", "sponsored", "advertisement",
]

TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID        = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
DB_PATH           = "published.db"
MAX_POSTS_PER_RUN = 5

# ── База даних ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS published (
            hash TEXT PRIMARY KEY, title TEXT, published_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_topics (
            keyword TEXT PRIMARY KEY, count INTEGER DEFAULT 1, first_seen TEXT
        )
    """)
    conn.commit()
    return conn

def is_published(conn, url):
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM published WHERE hash=?", (h,)).fetchone()

def mark_published(conn, url, title):
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO published VALUES (?,?,?)",
                 (h, title, datetime.utcnow().isoformat()))
    conn.commit()

def get_topic_count(conn, keywords):
    max_count = 0
    for kw in keywords:
        row = conn.execute(
            "SELECT count FROM seen_topics WHERE keyword=?", (kw,)
        ).fetchone()
        if row:
            max_count = max(max_count, row[0])
    return max_count

def update_topic_count(conn, keywords):
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

def extract_keywords(title):
    words = title.lower().split()
    return [w.strip(".,!?«»\"'") for w in words if len(w) > 5]

def get_category(title, summary):
    text = (title + " " + summary).lower()
    priority_order = [
        "🇺🇦 Україна",
        "🌍 Світ",
        "⚡ Енергетика",
        "💰 Економіка",
        "💻 Технології",
        "🏥 Здоров'я",
        "🔬 Наука",
    ]
    for category in priority_order:
        keywords = CATEGORIES[category]
        if any(kw in text for kw in keywords):
            return category
    return "📰 Новини"

def is_spam(title, summary):
    text = (title + " " + summary).lower()
    return any(kw in text for kw in SPAM_KEYWORDS)

def is_russian(title, summary):
    text = (title + " " + summary).lower()
    russian_markers = [
        "из ", "это ", "для ", "все ", "как ", "так ", "его ",
        "что ", "или ", "при ", "они ", "если ", "чтобы ",
    ]
    count = sum(1 for marker in russian_markers if marker in text)
    return count >= 3

# ── Витяг картинки з RSS ───────────────────────────────────
def extract_image(entry):
    if hasattr(entry, "media_content") and entry.media_content:
        for m in entry.media_content:
            if m.get("type", "").startswith("image"):
                return m.get("url")
    if hasattr(entry, "enclosures") and entry.enclosures:
        for e in entry.enclosures:
            if e.get("type", "").startswith("image"):
                return e.get("href") or e.get("url")
    if hasattr(entry, "summary"):
        match = re.search(r']+src=["\']([^"\']+)["\']', entry.summary or "")
        if match:
            return match.group(1)
    if hasattr(entry, "content") and entry.content:
        for c in entry.content:
            match = re.search(r']+src=["\']([^"\']+)["\']', c.get("value", ""))
            if match:
                return match.group(1)
    return None

def is_valid_image(url):
    try:
        r = requests.head(url, timeout=5, allow_redirects=True)
        content_type = r.headers.get("content-type", "")
        return r.status_code == 200 and "image" in content_type
    except:
        return False

# ── Збір новин ─────────────────────────────────────────────
def fetch_news(conn):
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
                    continue
                if is_russian(title, summary):
                    print(f"🚫 Російська мова: {title[:50]}")
                    continue

                keywords  = extract_keywords(title)
                update_topic_count(conn, keywords)
                image_url = extract_image(entry)

                items.append({
                    "title":     title,
                    "summary":   summary,
                    "url":       url,
                    "source":    feed.feed.get("title", ""),
                    "lang":      feed_cfg["lang"],
                    "keywords":  keywords,
                    "category":  get_category(title, summary),
                    "image_url": image_url,
                })
        except Exception as e:
            print(f"⚠️ Помилка {feed_cfg['url']}: {e}")

    items.sort(key=lambda x: get_topic_count(conn, x["keywords"]), reverse=True)
    return items

# ── AI обробка ─────────────────────────────────────────────
def rewrite_with_ai(item):
    lang_note = (
        "Новина англійською — переклади та перепиши українською."
        if item["lang"] == "en"
        else "Новина вже українською — перепиши живою мовою."
    )
    prompt = f"""Ти редактор популярного українського Telegram-каналу з новинами.
{lang_note}

ВАЖЛИВО: Спочатку оціни чи ця новина є важливою для українського читача.
Якщо новина НЕ стосується України, світової політики, технологій, економіки,
науки або здоров'я — відповідай лише словом: SKIP

Якщо новина важлива — перепиши її за правилами:
- Мова виключно українська
- Довжина: 3–5 речень, коротко і по суті
- Починай з найголовнішого факту
- Стиль: живий, зрозумілий, без канцеляризмів
- НЕ використовуй хештеги
- НЕ пиши "Джерело:" в тексті
- НЕ вигадуй факти
- Дати та числа пиши ЦИФРАМИ (3 квітня, а не "три квітня")
- Якщо не знаєш як перекласти слово — опиши його значення українською

Заголовок: {item['title']}
Текст: {item['summary'][:800]}
Джерело: {item['source']}

Напиши лише готовий текст посту або слово SKIP."""

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.7},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Помилка Groq: {e}")
        return None

# ── Публікація в Telegram ──────────────────────────────────
def post_to_telegram(text, url, category, image_url=None):
    full_text = f"{text}\n\n🔗 [Читати повністю]({url})"
    valid_image = image_url and is_valid_image(image_url)

    if valid_image:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            json={
                "chat_id":    CHANNEL_ID,
                "photo":      image_url,
                "caption":    full_text,
                "parse_mode": "Markdown",
            }
        )
    else:
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
        img_status = "🖼" if valid_image else "📝"
        print(f"✅ {img_status} Опубліковано [{category}]: {url}")
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

        topic_count = get_topic_count(conn, item["keywords"])
        if topic_count < 2:
            print(f"⏳ Чекаємо підтвердження: {item['title'][:50]}")
            continue

        print(f"📝 Обробляю [{item['category']}]: {item['title'][:50]}...")
        post_text = rewrite_with_ai(item)
        if not post_text:
            continue

        # Пропускаємо нерелевантні новини
        if post_text.strip().upper() == "SKIP":
            print(f"⏭ Нерелевантна новина: {item['title'][:50]}")
            continue

        if post_to_telegram(post_text, item["url"], item["category"], item.get("image_url")):
            mark_published(conn, item["url"], item["title"])
            count += 1
            time.sleep(3)

    print(f"\n🏁 Готово. Опубліковано {count} постів.")
    conn.close()

if __name__ == "__main__":
    main()
