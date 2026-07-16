import os
import sqlite3
import hashlib
import html
import feedparser
import requests
import time
import re
from datetime import datetime, timedelta

# "name" — назва бренду для підпису «📰 За даними: ...». Задана ЯВНО, бо
# заголовок самого RSS-фіда довільний і часто беззмістовний: Укрінформ віддає
# «Останні новини», Цензор — «Цензор.НЕТ - Новини», DOU — «Найцікавіше на DOU»,
# а Європравда не віддає нічого (і в пості з'являлось «Читати повністю»).
RSS_FEEDS = [
    # --- Українські (загальні) ---
    {"url": "https://www.ukrinform.ua/rss/block-lastnews",       "lang": "uk", "name": "Укрінформ"},
    {"url": "https://www.pravda.com.ua/rss/view_news/",          "lang": "uk", "name": "Українська правда"},
    {"url": "https://suspilne.media/rss/all.rss",                "lang": "uk", "name": "Суспільне"},
    {"url": "https://tsn.ua/rss/full.rss",                       "lang": "uk", "name": "ТСН"},
    {"url": "https://rss.unian.net/site/news_ukr.rss",           "lang": "uk", "name": "УНІАН"},
    {"url": "https://nv.ua/ukr/rss/all.xml",                     "lang": "uk", "name": "NV"},
    {"url": "https://censor.net/ua/includes/news_uk.xml",        "lang": "uk", "name": "Цензор.НЕТ"},
    {"url": "https://lb.ua/rss/ukr/news.xml",                    "lang": "uk", "name": "LB.ua"},
    {"url": "https://www.eurointegration.com.ua/rss/",           "lang": "uk", "name": "Європейська правда"},
    {"url": "https://news.google.com/rss/search?q=when:1d+site:radiosvoboda.org&hl=uk&gl=UA&ceid=UA:uk", "lang": "uk", "name": "Радіо Свобода"},
    {"url": "https://news.google.com/rss/search?q=when:1d+site:dw.com&hl=uk&gl=UA&ceid=UA:uk", "lang": "uk", "name": "DW"},
    {"url": "https://feeds.bbci.co.uk/ukrainian/rss.xml",        "lang": "uk", "name": "BBC Україна"},
    {"url": "https://news.google.com/rss?hl=uk&gl=UA&ceid=UA:uk", "lang": "uk", "name": "Google News"},
    # --- Українські (розслідування / армія) ---
    {"url": "https://bihus.info/feed",                           "lang": "uk", "name": "Бігус.Інфо"},
    {"url": "https://armyinform.com.ua/feed/",                   "lang": "uk", "name": "АрміяInform"},
    {"url": "https://militarnyi.com/uk/feed/",                   "lang": "uk", "name": "Мілітарний"},
    # --- Світові (загальні) ---
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",       "lang": "en", "name": "BBC"},
    {"url": "https://www.theguardian.com/world/rss",             "lang": "en", "name": "The Guardian"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",         "lang": "en", "name": "Al Jazeera"},
    {"url": "https://www.euronews.com/rss",                      "lang": "en", "name": "Euronews"},
    {"url": "http://rss.cnn.com/rss/edition_world.rss",          "lang": "en", "name": "CNN"},
    {"url": "https://news.google.com/rss/search?q=when:1d+site:reuters.com&hl=en-US&gl=US&ceid=US:en", "lang": "en", "name": "Reuters"},
    {"url": "https://news.google.com/rss/search?q=when:1d+site:apnews.com&hl=en-US&gl=US&ceid=US:en", "lang": "en", "name": "AP"},
    # --- Технології / наука ---
    {"url": "https://dou.ua/lenta/feed/",                        "lang": "uk", "name": "DOU"},
    {"url": "https://techcrunch.com/feed/",                      "lang": "en", "name": "TechCrunch"},
    {"url": "https://www.theverge.com/rss/index.xml",            "lang": "en", "name": "The Verge"},
    {"url": "https://feeds.arstechnica.com/arstechnica/index",   "lang": "en", "name": "Ars Technica"},
    {"url": "https://www.sciencedaily.com/rss/all.xml",          "lang": "en", "name": "ScienceDaily"},
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
    # Порядок = ЯКІСТЬ (найсильніша модель перша) — це прямо б'є в БАГ-006
    # (галюцинації/вигадана стать: сильніша модель менше «додумує»).
    # Ланцюг самобалансується: якщо Groq упреться в добовий ліміт (429) —
    # автоматично підхоплює Cerebras (щедрий ліміт + швидкість), тобто НЕ гірше
    # за попередню поведінку. Підсумок адміну (STATS) показує реальний баланс
    # «Groq×N, Cerebras×M» — за ним видно, чи тримає Groq навантаження.
    {"name": "Groq",
     "url":  "https://api.groq.com/openai/v1/chat/completions",
     "key":  os.environ.get("GROQ_API_KEY"),
     "model": "openai/gpt-oss-120b"},     # 120B, найсильніша в ланцюзі; llama знято 2026-06-17
    {"name": "Cerebras",
     "url":  "https://api.cerebras.ai/v1/chat/completions",
     "key":  os.environ.get("CEREBRAS_API_KEY"),
     "model": "gemma-4-31b"},             # 31B, швидкий резерв: 1 млн токенів/добу
    {"name": "Gemini",
     "url":  "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "key":  os.environ.get("GEMINI_API_KEY"),
     "model": "gemini-3.5-flash"},        # останній резерв; gemini-2.5-flash Google закрив
     # для нових користувачів (404 «no longer available»); 3.5-flash — актуальна GA. БАГ-008.
] if p["key"]]

def call_llm(prompt, max_tokens=900, temperature=0.4, save_strong=False):
    """Пробує провайдерів по черзі. Повертає текст, 'RATE_LIMIT' (усі в ліміті)
    або None (усі впали з іншої причини).

    save_strong=True — бережемо найсильнішу модель (першу в списку): черга
    починається з резервних. Навіщо: добовий ліміт Groq (120B) з'їдався за
    перші години ~400 викликами, і решту дня ВСЕ писала слабка gemma (звідси
    одруки «дешею», «всіій»). Тепер сильна модель дістається найважливішому
    (курація + топ-подія кожного прогону = ~200 викликів, розтягнутих на
    добу), а добивка йде на Cerebras/Gemini, чиї щедрі ліміти простоювали.
    Якщо резервні впали — Groq усе одно підстрахує (він у кінці черги)."""
    providers = LLM_PROVIDERS
    if save_strong and len(LLM_PROVIDERS) > 1:
        providers = LLM_PROVIDERS[1:] + LLM_PROVIDERS[:1]
    all_rate_limited = True
    for p in providers:
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
            choice  = r.json()["choices"][0]
            content = (choice.get("message", {}).get("content") or "").strip()
            # finish_reason="length" = відповідь ОБІРВАНО на ліміті токенів.
            # Так у канал потрапляли пости на півслові («...дворічну підтрим»):
            # reasoning-моделі (gpt-oss-120b) палять max_tokens на «міркування»,
            # і на сам текст їх не лишається. Обірване НЕ публікуємо — краще
            # віддати наступному провайдеру (Cerebras — без «міркувань»).
            if choice.get("finish_reason") == "length":
                print(f"⚠️ {p['name']}: відповідь обірвано на ліміті токенів — наступний провайдер.")
                STATS["err"][p["name"]] = "обірвано (finish_reason=length)"
                continue
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
    # Новини, які модель відхилила (SKIP) або які визнано дублем. БЕЗ цієї
    # таблиці кожен наступний прогін (кожні ~15 хв) ганяв ТІ САМІ новини через
    # LLM, знову діставав SKIP і марно палив добові ліміти всіх провайдерів
    # (у логах — 429 на Groq/Cerebras/Gemini і «опубліковано 0 з 12»).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS skipped (
            hash TEXT PRIMARY KEY, title TEXT, reason TEXT, skipped_at TEXT
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

def is_skipped(conn, url):
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM skipped WHERE hash=?", (h,)).fetchone()

def mark_skipped(conn, url, title, reason):
    """Запам'ятати відхилену новину, щоб не витрачати на неї виклик LLM знову."""
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO skipped VALUES (?,?,?,?)",
                 (h, title, reason, datetime.utcnow().isoformat()))
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
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return True
        if r.status_code in (403, 405):
            # деякі CDN (Cloudflare тощо) блокують HEAD, хоча GET віддає
            # картинку нормально — не завантажуємо тіло, лише заголовки.
            r = requests.get(url, timeout=5, allow_redirects=True, stream=True)
            ok = r.status_code == 200 and "image" in r.headers.get("content-type", "")
            r.close()
            return ok
        return False
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

# Синоніми, що позначають те саме (інакше «122 дрони» і «122 БпЛА» — різні події).
_SYNONYMS = {"бпла": "дрон", "безп": "дрон", "шахе": "дрон", "shah": "дрон", "дрон": "дрон"}

# Топоніми. Потрібні, бо схожість слів обманює: «Росія атакувала Одесу ракетами»
# і «Росія атакувала Харків ракетами» збігаються на 0.60 (спільні росія/атакувала/
# ракетами), хоча це РІЗНІ удари по РІЗНИХ містах. Зливати їх — гірше за дубль:
# зникає ціла новина й виходить хибна атрибуція. Тому: різні локації = різні події.
_PLACES = [
    "київ", "харків", "одес", "дніпр", "запор", "львів", "херсон", "миколаїв",
    "полтав", "сум", "чернігів", "черкас", "житомир", "вінниц", "рівн", "луцьк",
    "тернопіл", "ужгород", "чернівц", "кропивниц", "хмельниц", "івано-франків",
    "донеч", "донец", "луган", "маріуп", "краматорськ", "бахмут", "покровськ",
    "кривий ріг", "кримськ", "крим", "керч", "севастопол", "мелітопол", "бердянськ",
    "бєлгород", "курськ", "ростов", "новоросійськ", "москв", "брянськ",
]

def _places(text):
    t = (text or "").lower()
    return {p for p in _PLACES if p in t}

def _place_conflict(a, b):
    """True, якщо в заголовках названі РІЗНІ локації (жодної спільної)."""
    pa, pb = _places(a), _places(b)
    return bool(pa) and bool(pb) and not (pa & pb)

def _title_words(title):
    """Токени заголовка для порівняння схожості (Жаккар).
    Префікс 4 (грубий стемінг): «росія»/«російських» → «росі», інакше форми
    того самого слова не збігались і дублі проходили. Числа лишаємо цілими —
    для новин це найсильніший сигнал тієї самої події (122 дрони, 1470 втрат)."""
    words = re.findall(r"[а-яіїєґёa-z0-9']+", (title or "").lower())
    out = set()
    for w in words:
        if w.isdigit():
            if len(w) >= 2:
                out.add("#" + w)
        elif len(w) >= 4 and w not in _STOPWORDS:
            p = w[:4]
            out.add(_SYNONYMS.get(p, p))
    return out

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
        # Різні міста — різні події, навіть якщо решта слів збігається
        # («атакували Одесу» / «атакували Харків» = 0.60): інакше друга
        # новина мовчки зникала б як «дубль».
        if _place_conflict(title, old_title):
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


def merge_by_event(items, threshold=0.5):
    """Зливає новини про ОДНУ подію з різних джерел в один кандидат.

    Навіщо (три ефекти одразу):
      • канал не завалює 5 постів про ту саму нічну атаку — виходить один;
      • у моделі більше матеріалу (summary з кількох джерел) → пост повніший;
      • 1 виклик LLM замість 5 → бережемо добові ліміти провайдерів.
    Основою беремо item із найдовшим summary (найінформативніший),
    решта дають додатковий матеріал і атрибуцію «за даними X, Y»."""
    merged = []
    for it in items:
        w = _title_words(it["title"])
        placed = False
        if w:
            for m in merged:
                # Порівнюємо з УСІМА заголовками кластера, а не лише з поточним:
                # основа кластера змінюється (беремо найінформативніший варіант),
                # і при порівнянні лише з нею база «пливла» — наступні дублі
                # переставали збігатися (так у канал пройшли два пости про Мі-28).
                if not any(
                    (lambda w0: bool(w0) and (len(w | w0) > 0)
                                and len(w & w0) / len(w | w0) >= threshold
                                and not _place_conflict(it["title"], t))(_title_words(t))
                    for t in m["_titles"]
                ):
                    continue
                m["_titles"].append(it["title"])
                if it.get("source") and not any(s["name"] == it["source"] for s in m["sources"]):
                    m["sources"].append({"name": it["source"], "url": it["url"]})
                if it.get("summary"):
                    m["extra"].append(it["summary"])
                # основою лишаємо найінформативніший варіант
                if len(it.get("summary") or "") > len(m.get("summary") or ""):
                    m["title"], m["summary"], m["url"] = it["title"], it["summary"], it["url"]
                if not m.get("image_url") and it.get("image_url"):
                    m["image_url"] = it["image_url"]
                placed = True
                break
        if not placed:
            it = dict(it)
            # Джерело = назва + ПРЯМЕ посилання на його статтю: читач має мати
            # змогу перевірити кожне джерело, а не читати назву текстом.
            it["sources"] = ([{"name": it["source"], "url": it["url"]}]
                             if it.get("source") else [])
            it["extra"]   = []
            it["_titles"] = [it["title"]]
            merged.append(it)
    return merged


def fetch_news(conn):
    items = []
    for feed_cfg in RSS_FEEDS:
        try:
            feed = parse_feed(feed_cfg["url"])
            for entry in feed.entries[:5]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                url     = entry.get("link", "")
                if not url or is_published(conn, url) or is_skipped(conn, url):
                    continue
                if is_spam(title, summary):
                    continue
                if is_russian(title, summary):
                    print(f"🚫 Російська: {title[:50]}")
                    continue
                keywords = extract_keywords(title)
                update_topic_count(conn, keywords)
                items.append({
                    "title":     title,
                    "summary":   summary,
                    "url":       url,
                    # Явна назва бренду; feed.title — лише запасний варіант
                    "source":    feed_cfg.get("name") or feed.feed.get("title", ""),
                    "lang":      feed_cfg["lang"],
                    "keywords":  keywords,
                    "image_url": extract_image(entry),
                })
        except Exception as e:
            print(f"⚠️ {feed_cfg['url']}: {e}")
    # Одна подія з різних джерел → один кандидат («за даними X, Y»)
    before = len(items)
    items  = merge_by_event(items)
    if before != len(items):
        print(f"🔗 Злито за подіями: {before} → {len(items)} кандидатів")
    # Спершу — новини про Україну, потім — за «трендовістю» (частота теми)
    items.sort(key=lambda x: (ukraine_score(x), get_topic_count(conn, x["keywords"])),
               reverse=True)
    # Резервуємо місця світовим новинам. Без цього ukraine_score (бал за кожне
    # українське/воєнне слово) виштовхував англомовні джерела — BBC, Guardian,
    # Reuters, AP — за межі топ-12, і курація їх узагалі не бачила: канал виходив
    # лише з українських джерел, хоч і обіцяє «новини України ТА СВІТУ».
    ua = [x for x in items if x.get("lang") == "uk"]
    en = [x for x in items if x.get("lang") == "en"]
    picked = ua[:9] + en[:3]
    if len(picked) < 12:                       # чогось бракує — добираємо рештою
        rest = [x for x in ua[9:] + en[3:] if x not in picked]
        picked += rest[:12 - len(picked)]
    return picked

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

def rewrite_with_ai(item, save_strong=False):
    lang_note = (
        "Новина англійською — переклади та перепиши українською."
        if item["lang"] == "en"
        else "Новина вже українською — перепиши."
    )
    # Якщо подію підтвердили кілька джерел — даємо моделі ВЕСЬ їхній матеріал:
    # пост виходить повнішим, а факти, що збігаються, надійніші.
    extra = [e for e in (item.get("extra") or []) if e]
    extra_block = ""
    if extra:
        more = "\n".join(f"- {e[:400]}" for e in extra[:3])
        # sources — це {name, url} (не рядки): беремо саме назви, інакше
        # join падає з TypeError і бот не публікує нічого.
        names = [(s.get("name") if isinstance(s, dict) else s)
                 for s in (item.get("sources") or [])[1:]]
        names = [n for n in names if n]
        who = f" ({', '.join(names)})" if names else ""
        extra_block = (
            f"\n\nЦю саму подію описали й інші джерела{who}.\n"
            f"Матеріал звідти (використай для повноти, факти мають збігатися):\n{more}"
        )
    prompt = f"""Ти досвідчений журналіст українського Telegram-каналу UA News.
{lang_note}

ГОЛОВНЕ ПРАВИЛО: пиши ЛИШЕ те, що прямо є в джерелі нижче. Краще коротший
пост, ніж хоч один вигаданий факт — від точності залежить довіра до каналу.

Новину вже відібрав редактор — вона ВАЖЛИВА, тож твоє завдання її написати.
SKIP відповідай лише у крайньому разі:
- відверта реклама/спам;
- у тексті взагалі немає про що писати;
- ТИЗЕР БЕЗ СУТІ: заголовок обіцяє відповідь («відповіли на чутки»,
  «пояснили, чи…», «назвали причину», «стало відомо…»), а САМОЇ відповіді
  (що саме вирішили / пояснили / назвали) у тексті джерела НЕМАЄ. Пост
  «посадовці почали пояснювати» без того, ЩО САМЕ вони пояснили, підриває
  довіру — такого поста краще не публікувати взагалі.
В усіх інших сумнівах — ПИШИ, а не пропускай.

Якщо важлива — напиши у стилі якісної журналістики:
- Мова: виключно українська
- ФОРМАТ (важливо, стиль каналу):
  • Перший рядок — короткий заголовок-суть (хто/що/де), почни його ОДНИМ
    доречним за ЗМІСТОМ І ТОНОМ емодзі. Для трагедій (загибель, обстріл,
    руйнування) — стримані: 💥 🚨 ⚠️ 🕯 🔴. Для нейтрального/позитивного — за
    темою: 🚀 космос/техніка, 📚 культура, 💰 економіка, 🕊 мир, ⚡️ терміново.
    НЕ став святкових чи грайливих емодзі на трагічні події. Без крапки в кінці.
  • Далі — порожній рядок, потім 2–3 ЗМІСТОВНІ абзаци, розділені порожнім
    рядком. Кожен абзац додає НОВУ конкретику з джерела: обставини, деталі,
    наслідки, тло події. Пост має бути насиченим — НЕ в одне речення.
  • НЕ став сам жирний/курсив чи розмітку (*, _, #) — чистий текст;
    форматування додасть система.
  • НЕ пиши службових позначок і не показуй хід думок: жодних «Para 1»,
    «Абзац 1», «Заголовок:», «Ось пост:» — одразу готовий текст.
- Наповнюй пост КОНКРЕТИКОЮ з джерела (що саме, коли, де, наслідки, тло).
  НЕ додавай порожніх фраз-заповнювачів («це підкреслює важливість», «це
  свідчить про...», загальні висновки без нової інформації) — це «вода».
- Якщо заголовок ставить питання або анонсує відповідь — пост МУСИТЬ цю
  відповідь дати (з джерела). Немає відповіді в джерелі — це тизер, SKIP.
- ОБОВ'ЯЗКОВО зберігай точні назви, якщо вони є в джерелі: яка саме нагорода
  чи орден, назва/номер закону, посада, назва документа, угоди, підрозділу.
  «Нагородив орденом князя Ярослава Мудрого V ступеня» — правильно;
  «нагородив» без назви ордена — втрачена суть новини.
- Стиль: точний, нейтральний, без сенсаційності та канцеляризмів
- Числа і дати: лише цифрами (5 квітня, 3 млрд, 47%)
- Якщо незнайоме слово — опиши зміст, не залишай англійського
- Якщо в джерелі мало деталей — напиши коротше (2 речення), але ЗМІСТОВНО.
  Не відмовляйся від новини лише через те, що опис короткий.
- БЕЗ хештегів, БЕЗ "Джерело:", БЕЗ вигаданих фактів

ТОЧНІСТЬ (найважливіше — від цього залежить довіра до каналу):
- Пиши ЛИШЕ те, що є в тексті джерела. Нічого не додумуй.
- Імена, прізвища, посади й назви залишай точно як у джерелі.
- НЕ вигадуй стать людини. Орієнтуйся на те, як узгоджені слова в самому
  джерелі: якщо там «прем'єр Свириденко заявила» — пиши «заявила», не «заявив».
  Якщо стать із джерела не зрозуміла — формулюй нейтрально (за посадою чи
  прізвищем), не став рід дієслів і займенники навмання.
- НЕ приписуй людям посад, звань чи ролей, яких немає в тексті джерела
  (напр. не називай когось «головою СБУ» чи «міністром», якщо цього там нема).
- Не повторюй той самий факт двічі й не «долий води».
- Жодних припущень, домислів чи фактів, яких немає в джерелі.

Заголовок: {item['title']}
Текст: {item['summary'][:800]}
Джерело: {item['source']}{extra_block}

Напиши лише готовий текст або SKIP."""

    # Температура 0.2 — щоб модель менше «додумувала» деталі/стать.
    # max_tokens 1600 (не 900): reasoning-моделі (gpt-oss-120b) спершу палять
    # токени на внутрішні «міркування», і при 900 на сам пост їх не лишалось —
    # текст обривався на півслові. Запас + перевірка finish_reason у call_llm.
    return call_llm(prompt, max_tokens=1600, temperature=0.2,
                    save_strong=save_strong)

def format_post_html(text, url, sources=None):
    """Стиль NV для parse_mode=HTML: перший (непорожній) рядок — жирний
    заголовок, решта абзаців — як є. Екрануємо <, >, & у ВСЬОМУ тексті
    новини, щоб сирі символи не ламали HTML-розмітку Telegram (у Markdown
    таке валило публікацію на символах _ * [ ).

    Внизу — ЗАВЖДИ «📰 За даними: ...», де кожне джерело є прямим посиланням
    на свою статтю. Окремий «🔗 Читати повністю» прибрано: він вів рівно на
    ту саму статтю, що й перше джерело, тобто дублював його. Тепер усі пости
    однакові, а читач завжди бачить (і може перевірити) джерела — це і є
    обіцяне каналом «довіряй тому, що перевірено»."""
    lines = text.strip().split("\n")
    head_idx = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    parts = []
    for i, ln in enumerate(lines):
        esc = html.escape(ln)
        parts.append(f"<b>{esc}</b>" if i == head_idx else esc)
    body = "\n".join(parts).strip()

    links = []
    for s in (sources or [])[:4]:
        name, href = (s.get("name"), s.get("url")) if isinstance(s, dict) else (s, None)
        if not name:
            continue
        name_esc = html.escape(name)
        links.append(f'<a href="{html.escape(href, quote=True)}">{name_esc}</a>'
                     if href else name_esc)
    if not links:
        # Запобіжник: без джерел пост лишиться зовсім без посилання на статтю.
        links = [f'<a href="{html.escape(url, quote=True)}">Читати повністю</a>']
    return f"{body}\n\n📰 <i>За даними: {', '.join(links)}</i>"


def post_to_telegram(text, url, image_url=None, sources=None):
    full_text   = format_post_html(text, url, sources)
    valid_image = image_url and is_valid_image(image_url)

    if valid_image:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            json={"chat_id": CHANNEL_ID, "photo": image_url,
                  "caption": full_text, "parse_mode": "HTML"}
        )
    else:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_ID, "text": full_text,
                  "parse_mode": "HTML", "disable_web_page_preview": False}
        )

    if response.status_code == 200:
        print(f"✅ {'🖼' if valid_image else '📝'} {url}")
        return True
    print(f"❌ Telegram: {response.text}")
    return False

def curate_with_ai(conn, items, max_pick=MAX_POSTS_PER_RUN * 2):
    """ОДИН виклик LLM замість ~12 окремих: модель бачить і вже опубліковане
    за добу, і всіх кандидатів — сама відкидає дублі (зокрема перефрази, чого
    лексика не вміє: «на Сумщину» vs «по Сумах»), зливає одну подію з різних
    джерел і вибирає найважливіше.

    Повертає список груп індексів (найважливіша перша) або None — тоді
    працює запасний лексичний шлях (напр. коли всі провайдери в 429).

    max_pick вдвічі більший за MAX_POSTS_PER_RUN НАВМИСНО — потрібен запас:
    частину відібраного модель-письменник ще може відхилити (SKIP), і без
    резерву прогін дає 0 постів (саме так сталося 15.07 о 12:31–12:46).
    """
    if not items:
        return None
    since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    # LIMIT 60, не 25: канал видає ~300 постів/добу, тож 25 заголовків — це
    # лише ~2 години історії. Ранкові події ставали «невидимими» для курації
    # вже ввечері — саме так 15.07 ті самі новини заходили в канал по 2-3 рази.
    rows = conn.execute(
        "SELECT title FROM published WHERE published_at >= ? "
        "ORDER BY published_at DESC LIMIT 60", (since,)
    ).fetchall()
    published = "\n".join(f"- {r[0][:110]}" for r in rows) or "— (за добу ще нічого)"
    cands = "\n".join(f"{i+1}. {it['title'][:110]}" for i, it in enumerate(items))

    prompt = f"""Ти головний редактор українського новинного каналу UA News.

ВЖЕ ОПУБЛІКОВАНО за останню добу:
{published}

НОВІ КАНДИДАТИ:
{cands}

Завдання:
1. Відкинь кандидата, якщо він про ТУ САМУ подію, що вже опублікована вище —
   навіть якщо формулювання інше або цифри уточнені (напр. «скинули КАБи на
   Сумщину, 17 поранених» і «вдарили КАБами по Сумах, 7 поранених» — це ОДНА подія).
2. Об'єднай в одну групу кандидатів, які пишуть про ОДНУ подію з різних джерел.
3. Відкинь нецікаве українському читачеві (реклама, дрібні події, гороскопи).
4. Обери максимум {max_pick} НАЙВАЖЛИВІШИХ подій; найважливіша — першою.
   За можливості бери РІЗНІ теми, а не {max_pick} однакових.

ВАЖЛИВО: різні міста — це завжди РІЗНІ події (не об'єднуй).
Дві різні події в одному місті — теж різні (не об'єднуй).

Відповідай ЛИШЕ номерами: один рядок = одна подія, номери через кому.
Жодних пояснень, заголовків чи іншого тексту.

Приклад правильної відповіді:
3,7
1
9"""

    # max_tokens 1600, не 400: gpt-oss-120b (Groq) — reasoning-модель, вона
    # спалює токени на «міркування» ще ДО відповіді. При 400 кожен виклик
    # обривався (finish_reason=length), Groq відпадав — і курацію ЗАВЖДИ
    # робила найслабша модель ланцюга (gemma-31b), яка пропускала дублі.
    raw = call_llm(prompt, max_tokens=1600, temperature=0.1)
    if not raw or raw == "RATE_LIMIT":
        return None

    groups, seen = [], set()
    for line in raw.strip().splitlines():
        line = line.strip().strip(".-•").strip()
        if not line or not re.fullmatch(r"[\d,\s]+", line):
            continue  # сміття/пояснення — ігноруємо рядок
        nums = []
        for part in line.split(","):
            part = part.strip()
            if part.isdigit():
                n = int(part) - 1
                if 0 <= n < len(items) and n not in seen:
                    nums.append(n)
                    seen.add(n)
        if nums:
            groups.append(nums)
        if len(groups) >= max_pick:
            break
    return groups or None


def merge_group(items, idxs):
    """Зливає обрані моделлю кандидати однієї події в один пост."""
    base = max((items[i] for i in idxs), key=lambda x: len(x.get("summary") or ""))
    m = dict(base)
    m["sources"] = list(base.get("sources") or
                        ([{"name": base["source"], "url": base["url"]}]
                         if base.get("source") else []))
    m["extra"]   = list(base.get("extra") or [])
    for i in idxs:
        it = items[i]
        if it is base:
            continue
        for s in (it.get("sources") or
                  ([{"name": it["source"], "url": it["url"]}] if it.get("source") else [])):
            if s and not any(x["name"] == s["name"] for x in m["sources"]):
                m["sources"].append(s)
        if it.get("summary"):
            m["extra"].append(it["summary"])
        if not m.get("image_url") and it.get("image_url"):
            m["image_url"] = it["image_url"]
    return m


def main():
    conn  = init_db()
    news  = fetch_news(conn)
    count = 0
    skipped_cnt = 0
    print(f"📥 Знайдено {len(news)} нових новин")

    # Курація: один виклик LLM відбирає події (дедуп + злиття + важливість).
    # Лексика цього не витягує — «на Сумщину»/«по Сумах» для неї різні події.
    groups = curate_with_ai(conn, news)
    if groups:
        picked = [merge_group(news, g) for g in groups]
        ai_curated = True
        print(f"🧠 Курація AI: {len(news)} кандидатів → {len(picked)} подій "
              f"(злито джерел: {sum(len(g) for g in groups)})")
    else:
        # Запасний шлях (усі провайдери в 429 / модель віддала сміття):
        # працюємо як раніше — лексичний дедуп по одному кандидату.
        picked = news
        ai_curated = False
        print("↩️ Курація недоступна — запасний лексичний шлях")

    for item in picked:
        if count >= MAX_POSTS_PER_RUN:
            break
        if not item["url"]:
            continue
        # Лексична перевірка дублів — на ОБОХ шляхах (не лише запасному).
        # Курація бачить тільки хвіст опублікованого і на слабкій моделі
        # пропускала повтори: 15–16.07 у канал по 2-3 рази зайшли «ЦРУ:
        # 20-30 хвилин», «21-й пакет санкцій», Мі-28 — зокрема ТОЙ САМИЙ
        # заголовок з Google News (URL інший → URL-дедуп не ловить).
        # Хибного злиття різних міст не буде: _place_conflict усередині
        # is_duplicate_title лишає «Одеса» vs «Харків» окремими подіями.
        if is_duplicate_title(conn, item["title"]):
            print(f"⏭ Дубль події (вже постили): {item['title'][:50]}")
            mark_skipped(conn, item["url"], item["title"], "duplicate")
            continue

        print(f"📝 {item['title'][:60]}...")
        # Перший пост прогону — найважливіша подія (курація сортує за
        # важливістю): їй — найсильніша модель. Решті — резервні провайдери,
        # щоб добовий ліміт Groq розтягнувся на весь день (див. call_llm).
        post_text = rewrite_with_ai(item, save_strong=count > 0)
        if not post_text:
            continue
        if post_text == "RATE_LIMIT":
            print("🛑 Усі провайдери в ліміті — зупиняємо прогін.")
            break
        if post_text.strip().upper().startswith("SKIP"):
            print(f"⏭ AI пропустив: {item['title'][:50]}")
            # Запам'ятовуємо, інакше наступний прогін знову витратить на неї виклик.
            mark_skipped(conn, item["url"], item["title"], "ai_skip")
            skipped_cnt += 1
            continue

        if post_to_telegram(post_text, item["url"], item.get("image_url"),
                            item.get("sources")):
            mark_published(conn, item["url"], item["title"])
            count += 1
            time.sleep(3)

    print(f"\n🏁 Опубліковано {count} постів.")

    # Підсумок адміну — лише коли є що сказати (щоб не спамити при частих запусках)
    if count > 0 or STATS["err"]:
        summary = f"🤖 Збір новин: опубліковано {count} з {len(news)} кандидатів."
        # Видимість шляху й відмов: без цього «0 з 12» не пояснює ПРИЧИНУ —
        # 15.07 довелось лізти в логи Actions, щоб побачити, що курація
        # відпрацювала, а всі відібрані новини відхилив письменник (SKIP).
        summary += (f"\n🧠 Курація: {len(picked)} подій"
                    if ai_curated else "\n↩️ Курація недоступна (запасний шлях)")
        if skipped_cnt:
            summary += f"; ✂️ відхилено моделлю: {skipped_cnt}"
        if STATS["ok"]:
            summary += "\n✅ Моделі: " + ", ".join(f"{k}×{v}" for k, v in STATS["ok"].items())
        if STATS["err"]:
            summary += "\n⚠️ Помилки: " + "; ".join(f"{k}: {v}" for k, v in STATS["err"].items())
        notify_admin(summary)

    conn.close()

if __name__ == "__main__":
    main()
