import os
import sqlite3
import hashlib
import feedparser
import requests
import time
import re
from datetime import datetime, timedelta

RSS_FEEDS = [
    # --- Українські (загальні) ---
    {"url": "https://www.ukrinform.ua/rss/block-lastnews",       "lang": "uk"},
    {"url": "https://www.pravda.com.ua/rss/view_news/",          "lang": "uk"},
    {"url": "https://suspilne.media/rss/all.rss",                "lang": "uk"},
    {"url": "https://tsn.ua/rss/full.rss",                       "lang": "uk"},
    {"url": "https://rss.unian.net/site/news_ukr.rss",           "lang": "uk"},
    {"url": "https://nv.ua/ukr/rss/all.xml",                     "lang": "uk"},
    {"url": "https://censor.net/ua/includes/news_uk.xml",        "lang": "uk"},
    {"url": "https://lb.ua/rss/ukr/news.xml",                    "lang": "uk"},
    {"url": "https://www.eurointegration.com.ua/rss/",           "lang": "uk"},
    {"url": "https://news.google.com/rss/search?q=when:1d+site:radiosvoboda.org&hl=uk&gl=UA&ceid=UA:uk", "lang": "uk"},  # Радіо Свобода (через Google News)
    {"url": "https://news.google.com/rss/search?q=when:1d+site:dw.com&hl=uk&gl=UA&ceid=UA:uk", "lang": "uk"},  # DW українською (через Google News)
    {"url": "https://feeds.bbci.co.uk/ukrainian/rss.xml",        "lang": "uk"},
    {"url": "https://news.google.com/rss?hl=uk&gl=UA&ceid=UA:uk", "lang": "uk"},  # агрегатор
    # --- Українські (розслідування / армія) ---
    {"url": "https://bihus.info/feed",                           "lang": "uk"},
    {"url": "https://armyinform.com.ua/feed/",                   "lang": "uk"},
    {"url": "https://militarnyi.com/uk/feed/",                   "lang": "uk"},
    # --- Світові (загальні) ---
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",       "lang": "en"},
    {"url": "https://www.theguardian.com/world/rss",             "lang": "en"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",         "lang": "en"},
    {"url": "https://www.euronews.com/rss",                      "lang": "en"},
    {"url": "http://rss.cnn.com/rss/edition_world.rss",            "lang": "en"},  # CNN World
    {"url": "https://news.google.com/rss/search?q=when:1d+site:reuters.com&hl=en-US&gl=US&ceid=US:en", "lang": "en"},  # Reuters (через Google News)
    {"url": "https://news.google.com/rss/search?q=when:1d+site:apnews.com&hl=en-US&gl=US&ceid=US:en", "lang": "en"},  # AP (через Google News)
    # --- Технології / наука ---
    {"url": "https://dou.ua/lenta/feed/",                        "lang": "uk"},
    {"url": "https://techcrunch.com/feed/",                      "lang": "en"},
    {"url": "https://www.theverge.com/rss/index.xml",            "lang": "en"},
    {"url": "https://feeds.arstechnica.com/arstechnica/index",   "lang": "en"},
    {"url": "https://www.sciencedaily.com/rss/all.xml",          "lang": "en"},
]

SPAM_KEYWORDS = [
    "реклама", "знижка", "розпродаж", "купи зараз",
    "промокод", "affiliate", "sponsored", "advertisement",
]

TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID        = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
DB_PATH           = "published.db"
MAX_POSTS_PER_RUN = 3   # «живий режим»: запуск часто, кілька постів за раз

# ── Самодіагностика: підсумок запуску адміну в Telegram ────
FEEDBACK_TOKEN = os.environ.get("FEEDBACK_BOT_TOKEN")
ADMIN_ID       = os.environ.get("ADMIN_CHAT_ID")
STATS = {"ok": {}, "err": {}}   # провайдер -> лічильник успіхів / остання помилка

def notify_admin(text):
    """Короткий підсумок роботи адміну (якщо задано креди фідбек-бота)."""
    if not (FEEDBACK_TOKEN and ADMIN_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{FEEDBACK_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text}, timeout=10
        )
    except Exception as e:
        print(f"⚠️ notify_admin: {e}")

# ── Безкоштовні LLM-провайдери (усі OpenAI-сумісні) ────────
# Пробуємо по черзі: якщо один уперся в ліміт/помилку — бере наступний.
# Провайдер без ключа в оточенні автоматично пропускається.
LLM_PROVIDERS = [p for p in [
    # Порядок = пріоритет. Перевірені робочі — першими; Gemini поки останній
    # (дає 404 — треба перевірити ключ AI Studio / вмикання Generative Language API).
    {"name": "Cerebras",
     "url":  "https://api.cerebras.ai/v1/chat/completions",
     "key":  os.environ.get("CEREBRAS_API_KEY"),
     "model": "gemma-4-31b"},             # 1 млн токенів/добу, швидко, без «міркувань»
    {"name": "Groq",
     "url":  "https://api.groq.com/openai/v1/chat/completions",
     "key":  os.environ.get("GROQ_API_KEY"),
     "model": "openai/gpt-oss-120b"},     # llama-моделі Groq знято з підтримки 2026-06-17
    {"name": "Gemini",
     "url":  "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "key":  os.environ.get("GEMINI_API_KEY"),
     "model": "gemini-3.5-flash"},        # gemini-2.5-flash Google закрив для нових
     # користувачів (404 «no longer available»); 3.5-flash — актуальна GA. БАГ-008.
] if p["key"]]

def call_llm(prompt, max_tokens=900, temperature=0.4):
    """Пробує провайдерів по черзі. Повертає текст, 'RATE_LIMIT' (усі в ліміті)
    або None (усі впали з іншої причини)."""
    all_rate_limited = True
    for p in LLM_PROVIDERS:
        try:
            r = requests.post(
                p["url"],
                headers={"Authorization": f"Bearer {p['key']}",
                         "Content-Type": "application/json"},
                json={"model": p["model"],
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": max_tokens, "temperature": temperature},
                timeout=30,
            )
            if r.status_code == 429:
                print(f"⚠️ {p['name']} ліміт — пробуємо наступного провайдера.")
                STATS["err"][p["name"]] = "ліміт (429)"
                continue
            if r.status_code >= 400:
                # raise_for_status показує лише статус+URL (URL ще й обрізається
                # логом до 70 симв. → «...generativelanguage.google»). Тіло
                # відповіді містить справжню причину (модель/ключ/API вимкнено).
                all_rate_limited = False
                reason = " ".join((r.text or "").split())
                print(f"❌ {p['name']}: {r.status_code} — {reason[:300]}")
                STATS["err"][p["name"]] = f"{r.status_code}: {reason[:120]}"
                continue
            all_rate_limited = False
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content:
                STATS["ok"][p["name"]] = STATS["ok"].get(p["name"], 0) + 1
                return content
        except Exception as e:
            all_rate_limited = False
            print(f"❌ {p['name']}: {e}")
            STATS["err"][p["name"]] = str(e)[:120]
            continue
    return "RATE_LIMIT" if all_rate_limited else None

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

UA_TERMS = [
    "україн", "зсу", "київ", "харків", "одес", "дніпро", "запор", "львів",
    "херсон", "миколаїв", "полтав", "суми", "чернігів", "донеч", "донец",
    "луган", "маріуп", "фронт", "окуп", "зеленськ", "генштаб", "мобіліз",
    "обстріл", "ракет", "дрон", "шахед", "тривог", "бпла", "удар", "війн",
    "росі", "путін", "санкц", "нато", "євросоюз", "переговор", "полон",
    "прем'єр", "кабмін", "верховна рада", "нбу", "гривн",
]

_STOPWORDS = {
    "який", "яка", "яке", "які", "цей", "про", "для", "від", "над", "під",
    "при", "або", "але", "так", "тим", "цьому", "після", "через", "між",
    "його", "вони", "було", "буде", "може", "цього", "щодо", "також", "цим",
    "тому", "уже", "вже", "ще", "як", "що",
}

def _title_words(title):
    # префікс слова (грубий стемінг) — щоб «заклад»/«закладу»/«закладом» збігались
    words = re.findall(r"[а-яіїєґёa-z0-9']+", (title or "").lower())
    return {w[:6] for w in words if len(w) >= 4 and w not in _STOPWORDS}

def is_duplicate_title(conn, title, hours=24, threshold=0.5):
    """True, якщо про цю ж подію вже постили за останні `hours` (схожість
    заголовків за Жаккаром). Ловить дублі з різних джерел, але не зливає
    різні події (напр. дві окремі атаки того ж міста)."""
    new = _title_words(title)
    if not new:
        return False
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT title FROM published WHERE published_at >= ?", (since,)
    ).fetchall()
    for (old_title,) in rows:
        old = _title_words(old_title)
        if not old:
            continue
        inter = len(new & old)
        union = len(new | old)
        if union and inter / union >= threshold:
            return True
    return False

def ukraine_score(item):
    """Оцінка «наскільки це про Україну» — щоб такі новини йшли першими."""
    text  = (item["title"] + " " + item["summary"]).lower()
    score = sum(1 for t in UA_TERMS if t in text)
    if item.get("lang") == "uk":
        score += 1
    return score

# Браузерний User-Agent: багато видань (UNIAN, Suspilne, DW, LIGA, Mind,
# Korrespondent) ріжуть дефолтний UA feedparser і віддають порожньо. Тягнемо
# фід через requests зі «звичайним» UA, а байти вже парсимо feedparser.
FEED_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def parse_feed(url):
    """RSS через браузерний UA. Fallback на прямий feedparser, якщо requests впав."""
    try:
        r = requests.get(url, headers={"User-Agent": FEED_UA}, timeout=15)
        if r.status_code == 200 and r.content:
            d = feedparser.parse(r.content)
            if d.entries:
                return d
        # порожньо або HTTP-помилка — пробуємо напряму (раптом requests блокують, а fp ні)
    except Exception as e:
        print(f"⚠️ parse_feed requests {url}: {str(e)[:80]}")
    return feedparser.parse(url)


def fetch_news(conn):
    items = []
    for feed_cfg in RSS_FEEDS:
        try:
            feed = parse_feed(feed_cfg["url"])
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
    # Спершу — новини про Україну, потім — за «трендовістю» (частота теми)
    items.sort(key=lambda x: (ukraine_score(x), get_topic_count(conn, x["keywords"])),
               reverse=True)
    # Обмежуємо кількість кандидатів (щоб частіше набирати до MAX_POSTS_PER_RUN)
    items = items[:12]
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

ТОЧНІСТЬ (найважливіше — від цього залежить довіра до каналу):
- Пиши ЛИШЕ те, що є в тексті джерела. Нічого не додумуй.
- Імена, прізвища, посади й назви залишай точно як у джерелі.
- НЕ вигадуй стать людини. Якщо з джерела стать невідома — формулюй
  нейтрально (за посадою чи прізвищем), не став дієслова й займенники навмання.
- НЕ приписуй людям посад, звань чи ролей, яких немає в тексті джерела
  (напр. не називай когось «головою СБУ» чи «міністром», якщо цього там нема).
- Не повторюй той самий факт двічі й не «долий води». Якщо інформації мало —
  напиши коротше (1–2 речення), це нормально.
- Жодних припущень, домислів чи фактів, яких немає в джерелі.

Заголовок: {item['title']}
Текст: {item['summary'][:800]}
Джерело: {item['source']}

Напиши лише готовий текст або SKIP."""

    return call_llm(prompt, max_tokens=900, temperature=0.4)

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
        # Релевантність тепер вирішує сама модель (у промпті — SKIP для
        # нецікавого): жорсткий локальний фільтр більше не ріже новини
        # (напр. науку/здоров'я англійською, яку він не розпізнавав).

        if is_duplicate_title(conn, item["title"]):
            print(f"⏭ Дубль події (вже постили): {item['title'][:50]}")
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

    # Підсумок адміну — лише коли є що сказати (щоб не спамити при частих запусках)
    if count > 0 or STATS["err"]:
        summ