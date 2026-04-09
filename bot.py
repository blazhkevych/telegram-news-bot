import os
import sqlite3
import hashlib
import feedparser
import requests
import time
import re
from datetime import datetime

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
    {"url": "https://biz.liga.net/ukr/all/rss.xml",            "lang": "uk"},
    {"url": "https://www.sciencedaily.com/rss/all.xml",        "lang": "en"},
    {"url": "https://www.oporaua.org/feed",                    "lang": "uk"},
    {"url": "https://bihus.info/feed",                         "lang": "uk"},
]

SPAM_KEYWORDS = [
    "реклама", "знижка", "розпродаж", "купи зараз",
    "промокод", "affiliate", "sponsored", "advertisement",
]

TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID        = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
DB_PATH           = "published.db"
MAX_POSTS_PER_RUN = 5

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
            conn.execute("UPDATE seen_topics SET count=count+1 WHERE keyword=?", (kw,))
        else:
            conn.execute("INSERT INTO seen_topics VALUES (?,1,?)",
                         (kw, datetime.utcnow().isoformat()))
    conn.commit()

def extract_keywords(title):
    words = title.lower().split()
    return [w.strip(".,!?«»\"'") for w in words if len(w) > 5]

def is_spam(title, summary):
    text = (title + " " + summary).lower()
    return any(kw in text for kw in SPAM_KEYWORDS)

def is_russian(title, summary):
    text = (title + " " + summary).lower()
    markers = ["из ", "это ", "для ", "все ", "как ", "так ",
               "его ", "что ", "или ", "при ", "они ", "если "]
    return sum(1 for m in markers if m in text) >= 3

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
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', entry.summary or "")
        if match:
            return match.group(1)
    if hasattr(entry, "content") and entry.content:
        for c in entry.content:
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', c.get("value", ""))
            if match:
                return match.group(1)
    return None

def is_valid_image(url):
    try:
        r = requests.head(url, timeout=5, allow_redirects=True)
        return r.status_code == 200 and "image" in r.headers.get("content-type", "")
    except:
        return False

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
                if is_russian(title, title):
                    print(f"🚫 Російська: {title[:50]}")
                    continue
                keywords = extract_keywords(title)
                update_topic_count(conn, keywords)
                items.append({
                    "title":     title,
                    "summary":   summary,
                    "url":       url,
                    "source":    feed.feed.get("title", ""),
                    "lang":      feed_cfg["lang"],
                    "keywords":  keywords,
                    "image_url": extract_image(entry),
                })
        except Exception as e:
            print(f"⚠️ {feed_cfg['url']}: {e}")
    items.sort(key=lambda x: get_topic_count(conn, x["keywords"]), reverse=True)
    # Обмежуємо кількість кандидатів для Groq
    items = items[:8]
    return items

def is_relevant(title, summary):
    """Швидка локальна перевірка релевантності без Groq."""
    text = (title + " " + summary).lower()
    relevant_keywords = [
        # Україна і війна
        "україн", "зсу", "київ", "харків", "одес", "фронт", "окупац",
        "зеленськ", "генштаб", "мобіліз", "обстріл", "ракет", "дрон",
        # Світова політика
        "трамп", "байден", "путін", "нато", "євросоюз", "оон", "сша",
        "росі", "китай", "іран", "ізраїл", "близьк", "війн", "мир",
        "переговор", "санкці", "договір", "ceasefire", "war", "peace",
        # Технології
        "ai", "штучний інтелект", "openai", "google", "apple", "microsoft",
        "стартап", "технолог", "кіберб", "хакер",
        # Економіка
        "економік", "бюджет", "нбу", "долар", "євро", "нафт", "газ",
        "інфляц", "ввп", "банк", "ринок", "oil", "trade",
        # Наука і здоров'я
        "вчені", "науков", "дослідж", "медицин", "здоров", "хвороб",
        "вакцин", "cancer", "climate", "space", "nasa",
    ]
    return any(kw in text for kw in relevant_keywords)

def rewrite_with_ai(item):
    lang_note = (
        "Новина англійською — переклади та перепиши українською."
        if item["lang"] == "en"
        else "Новина вже українською — перепиши."
    )
    prompt = f"""Ти досвідчений журналіст українського Telegram-каналу UA News.
{lang_note}

ВАЖЛИВО: Оціни чи новина важлива для українського читача.
Якщо НЕ стосується України, світової політики (включно з США, Іраном, Близьким Сходом, Ізраїлем), технологій, економіки, науки або здоров'я — відповідай: SKIP
Зупинення вогню, переговори, санкції, нафта, геополітика — це ЗАВЖДИ важливо для українського читача.

Якщо важлива — напиши у стилі якісної журналістики:
- Мова: виключно українська
- Перший рядок: найголовніший факт (хто, що, де, коли)
- Далі: контекст і значення події для читача
- Довжина: 3–4 речення, не більше
- Стиль: точний, нейтральний, без сенсаційності та канцеляризмів
- Числа і дати: лише цифрами (5 квітня, 3 млрд, 47%)
- Якщо незнайоме слово — опиши зміст, не залишай англійського
- БЕЗ хештегів, БЕЗ "Джерело:", БЕЗ вигаданих фактів

Заголовок: {item['title']}
Текст: {item['summary'][:800]}
Джерело: {item['source']}

Напиши лише готовий текст або SKIP."""

    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 400, "temperature": 0.6},
            timeout=30,
        )
        if r.status_code == 429:
            print("⚠️ Groq ліміт вичерпано — завершуємо запуск.")
            return "RATE_LIMIT"
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Groq: {e}")
        return None

def post_to_telegram(text, url, image_url=None):
    full_text = f"{text}\n\n🔗 [Читати повністю]({url})"
    valid_image = image_url and is_valid_image(image_url)

    if valid_image:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            json={"chat_id": CHANNEL_ID, "photo": image_url,
                  "caption": full_text, "parse_mode": "Markdown"}
        )
    else:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_ID, "text": full_text,
                  "parse_mode": "Markdown", "disable_web_page_preview": False}
        )

    if response.status_code == 200:
        print(f"✅ {'🖼' if valid_image else '📝'} {url}")
        return True
    print(f"❌ Telegram: {response.text}")
    return False

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
        if get_topic_count(conn, item["keywords"]) < 1:
            print(f"⏳ Чекаємо: {item['title'][:50]}")
            continue
        if not is_relevant(item["title"], item["summary"]):
            print(f"⏭ Нерелевантна: {item['title'][:50]}")
            continue

        print(f"📝 {item['title'][:60]}...")
        post_text = rewrite_with_ai(item)
        if not post_text:
            continue
        if post_text == "RATE_LIMIT":
            print("🛑 Зупиняємо — ліміт Groq. Наступний запуск через годину.")
            break
        if post_text.strip().upper().startswith("SKIP"):
            print(f"⏭ AI пропустив: {item['title'][:50]}")
            continue

        if post_to_telegram(post_text, item["url"], item.get("image_url")):
            mark_published(conn, item["url"], item["title"])
            count += 1
            time.sleep(3)

    print(f"\n🏁 Опубліковано {count} постів.")
    conn.close()

if __name__ == "__main__":
    main()
