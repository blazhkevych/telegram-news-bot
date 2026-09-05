import os
import sqlite3
import hashlib
import html
import feedparser
import requests
import time
import re
import calendar
from datetime import datetime, timedelta

# "name" — назва бренду для підпису «📰 За даними: ...». Задана ЯВНО, бо
# заголовок самого RSS-фіда довільний і часто беззмістовний: Укрінформ віддає
# «Останні новини», Цензор — «Цензор.НЕТ - Новини», DOU — «Найцікавіше на DOU»,
# а Європравда не віддає нічого (і в пості з'являлось «Читати повністю»).
# ⛔ ЖОРСТКЕ ПРАВИЛО (рішення власника 17.08.2026): ЖОДНИХ російських і
# проросійських джерел у цьому списку. Причина — недовіра до них як до джерела
# фактів, а канал обіцяє «перевірені факти». Правило діє БЕЗ ВИНЯТКІВ і не
# скасовується зручністю.
#
# Записано тут, бо спокуса виглядає розумно і повертатиметься. Аудит 17.08
# показав, що за добу вийшло ≥6 постів про події в РФ (звільнений економіст
# ВЕБ.РФ, вивід капіталу, школи при військових частинах) — усе переказом
# українських медіа. Звідси природний висновок «візьмімо першоджерело» і
# кандидати The Moscow Times / Meduza / The Insider, які технічно живі й
# віддають по 30-50 записів. ЇХ ВІДХИЛЕНО. Незалежність видання від Кремля не
# робить його прийнятним: правило про походження, а не про редакційну позицію.
#
# Що робити замість цього: події в РФ беремо переказом українських і західних
# медіа зі списку нижче — так канал і працює зараз.
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
    # Додано 17.08.2026 (усі перевірені живими того ж дня — див. запис нижче).
    # Економічна правда закриває реальну дірку: канал регулярно постить курси
    # валют і економіку переказом УНІАН, тобто попит є, а профільного джерела
    # не було.
    {"url": "https://www.epravda.com.ua/rss/",                   "lang": "uk", "name": "Економічна правда"},
    {"url": "https://ua.interfax.com.ua/news/last.rss",          "lang": "uk", "name": "Інтерфакс-Україна"},
    # ZN.ua — лише через Google News: власний https://zn.ua/rss віддає 0 записів.
    # Ціна та сама, що в Reuters/AP/CNN (БАГ-015): до моделі доходить лише
    # заголовок, тому частина новин чесно піде в SKIP.
    {"url": "https://news.google.com/rss/search?q=when:1d+site:zn.ua&hl=uk&gl=UA&ceid=UA:uk", "lang": "uk", "name": "ZN.ua"},
    # Радіо Свобода і DW переведені з Google News на ВЛАСНІ фіди (БАГ-015):
    # через Google News модель отримувала лише заголовок — і анонс там дорівнює
    # заголовку, і fetch_article_text не читає заглушку Google. Прямі фіди
    # дають анонс і повний текст статті (1460–2500 символів проти 0).
    {"url": "https://www.radiosvoboda.org/api/zrqiteuuir",        "lang": "uk", "name": "Радіо Свобода"},
    {"url": "https://rss.dw.com/rdf/rss-ukr-all",                 "lang": "uk", "name": "DW"},
    {"url": "https://feeds.bbci.co.uk/ukrainian/rss.xml",        "lang": "uk", "name": "BBC Україна"},
    {"url": "https://news.google.com/rss?hl=uk&gl=UA&ceid=UA:uk", "lang": "uk", "name": "Google News"},
    # --- Українські (розслідування / армія) ---
    {"url": "https://bihus.info/feed",                           "lang": "uk", "name": "Бігус.Інфо"},
    {"url": "https://armyinform.com.ua/feed/",                   "lang": "uk", "name": "АрміяInform"},
    # Мілітарний: власний фід віддає 403 — увесь militarnyi.com (і стара
    # адреса mil.in.ua) закритий JS-перевіркою Cloudflare, включно з
    # robots.txt. Це не блокування нашого User-Agent, а заслін від будь-якого
    # клієнта без браузера, тож підбирати заголовки марно й нечесно.
    # Законний обхід — той самий, що лишився для Reuters і AP: беремо матеріали
    # через публічний фід Google News, який має доступ до сайту легально.
    # Джерело в пості лишається «Мілітарний» — воно підставляється з тегу
    # <source> запису (див. fetch_news).
    # ⚠️ Свідоме обмеження (БАГ-015): через Google News до моделі доходить лише
    # ЗАГОЛОВОК — анонс там дорівнює заголовку, а тіла статті не буде подвійно
    # (resolve_gnews_url більше не розгортає посилання, та й сам militarnyi.com
    # віддав би 403). Тому такі новини здебільшого підуть у SKIP як тизери — і
    # це правильна поведінка, вигадувати факти з заголовка канал не буде.
    # Реальна користь інша: заголовок доживає до merge_by_event і додає
    # «Мілітарний» у рядок «За даними», коли ту саму подію описало ще якесь
    # джерело з повним текстом.
    {"url": "https://news.google.com/rss/search?q=when:1d+site:militarnyi.com&hl=uk&gl=UA&ceid=UA:uk", "lang": "uk", "name": "Мілітарний"},
    # --- Світові (загальні) ---
    {"url": "https://feeds.bbci.co.uk/news/world/rss.xml",       "lang": "en", "name": "BBC"},
    {"url": "https://www.theguardian.com/world/rss",             "lang": "en", "name": "The Guardian"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",         "lang": "en", "name": "Al Jazeera"},
    {"url": "https://www.euronews.com/rss",                      "lang": "en", "name": "Euronews"},
    # Додано 17.08.2026. Kyiv Independent був згаданий у README як джерело
    # каналу, але в коді його не було ЖОДНОГО разу. Власний фід
    # (kyivindependent.com/feed/ і /rss/) віддає 0 записів — тільки Google News.
    # lang="en" — він пише англійською, тож потрапляє в англійський кошик; там
    # він має перевагу за ukraine_score і, найімовірніше, витіснятиме частину
    # суто світових новин. Це свідомо: канал український.
    {"url": "https://news.google.com/rss/search?q=when:1d+site:kyivindependent.com&hl=en-US&gl=US&ceid=US:en", "lang": "en", "name": "Kyiv Independent"},
    {"url": "https://www.politico.eu/feed/",                     "lang": "en", "name": "Politico Europe"},
    {"url": "https://www.france24.com/en/rss",                   "lang": "en", "name": "France24"},
    {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "lang": "en", "name": "NYT World"},
    # CNN — той самий вимушений обхід, що й Reuters/AP нижче (БАГ-015), але
    # причина ІНША і небезпечніша. Старий rss.cnn.com не помер: він віддає
    # HTTP 200 і 29 записів, тому feed_check рахував його живим. Просто всі
    # записи ЗАМОРОЖЕНІ на 14.04.2023 — фід не оновлювався 1064 доби.
    # Наслідок: за 7 діб CNN дав у канал РІВНО НУЛЬ постів, і жодна перевірка
    # про це не сказала (аудит 17.08.2026). Перевірено того ж дня: edition.cnn.com
    # (куди веде 302 з www) віддає 0 записів, cnn_world.rss і edition.rss —
    # та сама заморозка 2023 року. Google News site:cnn.com віддає 100 свіжих.
    # Ціна відома й та сама, що в Reuters/AP: до моделі доходить лише заголовок.
    {"url": "https://news.google.com/rss/search?q=when:1d+site:cnn.com&hl=en-US&gl=US&ceid=US:en", "lang": "en", "name": "CNN"},
    # Reuters і AP лишаються на Google News ВИМУШЕНО (БАГ-015): власних
    # відкритих RSS у них більше немає — перевірено 08.08, усі відомі адреси
    # дають 404/401 або взагалі не резолвляться. Ціна відома: до моделі йде
    # лише заголовок, тому частина їхніх новин чесно піде у SKIP.
    {"url": "https://news.google.com/rss/search?q=when:1d+site:reuters.com&hl=en-US&gl=US&ceid=US:en", "lang": "en", "name": "Reuters"},
    {"url": "https://news.google.com/rss/search?q=when:1d+site:apnews.com&hl=en-US&gl=US&ceid=US:en", "lang": "en", "name": "AP"},
    # --- Технології / наука ---
    # topic="tech" — НЕ косметика, а окремий кошик у fetch_news (issue #7).
    # Без нього ці фіди програють війні у власній мовній групі й не доходять
    # до курації взагалі: за добу 09–10.08 вони дали 7 постів із 352 (2%),
    # причому The Guardian, CNN, Ars Technica, ScienceDaily і DOU не дали
    # ЖОДНОГО, хоч feed_check показав їх живими. Опис каналу обіцяє
    # «технології, наука» — обіцянку тримає саме цей тег.
    {"url": "https://dou.ua/lenta/feed/",                        "lang": "uk", "name": "DOU",          "topic": "tech"},
    {"url": "https://techcrunch.com/feed/",                      "lang": "en", "name": "TechCrunch",   "topic": "tech"},
    {"url": "https://www.theverge.com/rss/index.xml",            "lang": "en", "name": "The Verge",    "topic": "tech"},
    {"url": "https://feeds.arstechnica.com/arstechnica/index",   "lang": "en", "name": "Ars Technica", "topic": "tech"},
    {"url": "https://www.sciencedaily.com/rss/all.xml",          "lang": "en", "name": "ScienceDaily", "topic": "tech"},
]

SPAM_KEYWORDS = [
    "реклама", "знижка", "розпродаж", "купи зараз",
    "промокод", "affiliate", "sponsored", "advertisement",
    # Езотерика/треш (18.07 у канал зайшла «шаманка прогнозує перелом у
    # війні» з розділу УНІАН lite/astrology) — несумісно з «перевіреними
    # фактами». Слова однозначні, на звичайні новини не спрацюють.
    "шаманк", "астролог", "таролог", "гороскоп", "екстрасенс",
    "ворожк", "нумеролог", "провидиц", "ясновидець", "ясновидиц",
]

# Збори коштів (рішення власника 18.07: категорично без них). Слово «збір»
# саме по собі — НЕ маркер (податковий збір, збір урожаю, збірна) — ловимо
# лише однозначні словосполучення і платіжні реквізити.
FUNDRAISER_MARKERS = [
    "send.monobank", "monobank.ua/jar", "банка monobank", "банку monobank",
    "збір на ", "збір коштів", "збору коштів", "оголосив збір", "оголосили збір",
    "оголошує збір", "відкрив збір", "відкрито збір", "відкриває збір",
    "запускає збір", "запустив збір", "закрити збір", "закриття збору",
    "долучитися до збору", "долучитись до збору", "задонать", "задонатити",
    "реквізити для допомоги", "власкор збирає", "збирає кошти", "збирають кошти",
]

def is_fundraiser(title, summary):
    text = (title + " " + (summary or "")).lower()
    return any(m in text for m in FUNDRAISER_MARKERS)

TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID        = os.environ["TELEGRAM_CHANNEL_ID"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
DB_PATH           = "published.db"
# Скільки постів максимум за один прогін. 3 → 2 (10.08, після добового зрізу).
# ПРИЧИНА, замірена, а не відчута: за добу 09–10.08 канал видав 352 пости —
# один кожні 4 хвилини — і 88% прогонів упирались саме в цю константу. Тобто
# обсяг задавала НЕ наявність новин, а число тут. Прочитали ці 352 пости
# ~2 людини, і за 6 діб (≈2000 постів) канал не набрав жодного підписника —
# отже обсяг не працює ні на кого, і платити за нього більше нічим.
#
# ЩО ЦЕ ДАЄ, крім меншого спаму: відбір відсортовано за ПІДТВЕРДЖЕНІСТЮ
# (source_count — скільки різних видань написали про подію). Коли з тих самих
# ~12 кандидатів у канал виходить 2, а не 3, зрізається саме «хвіст» —
# одноджерельні новини. Значок «✅ Підтверджено N джерелами» був у 6% постів;
# це той важіль, який має його підняти. Заодно вдвічі менше викликів LLM на
# прогін — тобто менший ризик 429 і запас квоти під якісніший відбір.
#
# ЯК ВІДКОТИТИ: поставити 3. Нічого іншого міняти не треба — max_pick у
# curate_with_ai рахується від цього числа (MAX_POSTS_PER_RUN * 2), і резерв
# «модель-письменник ще може відхилити частину» лишається дворазовим.
# Невідібрані кандидати не потрапляють у skipped, тож наступний прогін бачить
# їх знову; застою це не робить — сортування третім ключем бере свіжіше.
MAX_POSTS_PER_RUN = 2

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
    # ПОРЯДОК ПЕРЕБУДОВАНО 05.09.2026 за підсумками добового аудиту (див.
    # БАГ-017 у BUGS.md). Замір за 04–05.09: 100 прогонів, ~4 300 викликів LLM,
    # з них успішних 114 (2,6%). Cerebras — 1 124 × 402, NVIDIA — 1 054
    # таймаути, Groq — 1 022 × 429, Gemini — 971 × 429. Курація працювала у 18
    # прогонах зі 100, решта — запасний лексичний шлях без дедупу й відбору.
    #
    # ДВА ВИСНОВКИ, на яких тримається новий порядок:
    # 1. Квоти Groq і Gemini — ПОМОДЕЛЬНІ, не на акаунт. У Groq кожна з
    #    gpt-oss-120b / gpt-oss-20b / qwen3.8-27b / qwen3.6-27b має власні
    #    200 тис. токенів/добу (console.groq.com/docs/rate-limits). Старий код
    #    на 429 кидав провайдера цілком (`break`), тобто три чверті квоти
    #    лежали невикористані. Тепер 429 у провайдера з `per_model_limit`
    #    означає «наступна модель», а не «наступний провайдер».
    # 2. «Працює» в llm_check ≠ «встигає в бою». NVIDIA deepseek-v4-flash
    #    відповідав на 5-токенний пробник за 30 с і не встигав 900 токенів за
    #    LLM_TIMEOUT у 99% бойових викликів. Тому llm_check тепер генерує
    #    справжній текст із бойовим таймаутом і міряє секунди.
    #
    # save_strong і "top" лишаються: Groq — найсильніша модель і найдефіцитніша
    # квота, тож курація й перший пост прогону йдуть до нього, решта — спершу
    # на резервні ноги.
    {"name": "Groq",
     "url":  "https://api.groq.com/openai/v1/chat/completions",
     "key":  os.environ.get("GROQ_API_KEY"),
     # Чотири моделі = чотири окремі добові квоти по 200 тис. токенів. Порядок
     # — за якістю: gpt-oss-120b (та сама, що була в Cerebras), потім Qwen 3.8
     # 27B (новіша), Qwen 3.6, і 20B як добивка. llama-3.3-70b з безкоштовного
     # плану Groq прибрано 26.08.2026 («Enterprise / Contact Sales») — не
     # повертати. Показово: llm_check раніше бачив 200 на п'ятитокенному
     # пробнику, а бойові виклики ловили 429 — ліміт саме ТОКЕННИЙ.
     "models": ["openai/gpt-oss-120b",
                "qwen/qwen3.8-27b",
                "qwen/qwen3.6-27b",
                "openai/gpt-oss-20b"],
     "per_model_limit": True,
     "top":  True},
    # SambaNova Cloud — доданий 05.09.2026. Безкоштовний тариф без картки:
    # 200 тис. токенів/добу НА КОЖНУ модель, 20 запитів/хв, OpenAI-сумісний
    # ендпоінт (docs.sambanova.ai/docs/en/models/rate-limits). У каталозі —
    # той самий gpt-oss-120b, тобто якість без втрат, плюс ще чотири окремі
    # квоти. Активується сам, щойно в секретах з'явиться SAMBANOVA_API_KEY.
    {"name": "SambaNova",
     "url":  "https://api.sambanova.ai/v1/chat/completions",
     "key":  os.environ.get("SAMBANOVA_API_KEY"),
     "models": ["gpt-oss-120b",
                "DeepSeek-V3.2",
                "Meta-Llama-3.3-70B-Instruct",
                "gemma-4-31B-it"],
     "per_model_limit": True},
    # Cloudflare Workers AI — доданий 17.08.2026, але секрети CF_ACCOUNT_ID /
    # CF_API_TOKEN так і не задано (аудит 05.09: у логах прогону обидва
    # порожні), тож нога досі не працювала жодного разу.
    # ПОПРАВКА до старої оцінки «10 тис. нейронів ≈ 1 300 відповідей»: за
    # прайсом gpt-oss-120b коштує 31,8 тис. нейронів за 1 млн вхідних і
    # 68,2 тис. за 1 млн вихідних токенів, тобто типовий виклик (~1,5 тис.
    # вхідних + ~0,4 тис. вихідних) ≈ 75 нейронів → ~130 викликів на добу, а не
    # 1 300. Це резерв на пів години пікового потоку, не «самотужки тримати
    # канал». Ліміт — на акаунт, тому без per_model_limit.
    #
    # URL залежить від акаунта, тому збирається з CF_ACCOUNT_ID. Провайдер
    # активується сам, щойно з'являться ОБИДВА секрети (без account_id URL
    # безглуздий, тому key нижче навмисно None, якщо бракує хоч одного).
    {"name": "Cloudflare",
     "url":  "https://api.cloudflare.com/client/v4/accounts/"
             f"{os.environ.get('CF_ACCOUNT_ID', '')}/ai/v1/chat/completions",
     "key":  (os.environ.get("CF_API_TOKEN")
              if os.environ.get("CF_ACCOUNT_ID") else None),
     "models": ["@cf/openai/gpt-oss-120b",
                "@cf/google/gemma-4-26b-a4b-it",
                "@cf/meta/llama-4-scout-17b-16e-instruct"]},
    {"name": "NVIDIA",
     # build.nvidia.com (NIM): ліміт лише 40 запитів/хв, БЕЗ обмеження обсягу.
     # Плата — надійність: потужність спільна для всіх безкоштовних акаунтів,
     # звідси 529 Overloaded і таймаути.
     "url":  "https://integrate.api.nvidia.com/v1/chat/completions",
     "key":  os.environ.get("NVIDIA_API_KEY"),
     # ⚠️ gpt-oss-120b ТУТ НЕ ПРАЦЮЄ, хоч і є в каталозі (17.08: 10 з 10 у
     # таймаут) — reasoning-модель на спільному залізі не встигає. Наявність
     # моделі в каталозі нічого не каже про її ЛАТЕНТНІСТЬ у провайдера.
     # Аудит 05.09: deepseek-v4-flash, який 17.08 давав 63%, тепер 1 054
     # таймаути на 8 успіхів за добу — теж не встигає. Тому першими — нові
     # легкі MoE-моделі (30B із 3B активних), які з'явились у каталозі; порядок
     # серед них — припущення, справжню латентність покаже llm_check із
     # заміром секунд. Модель, що впала в таймаут, до кінця прогону не
     # викликається (див. _SLOW_MODELS у call_llm). Каталог відкритий без ключа:
     #   curl -s https://integrate.api.nvidia.com/v1/models
     "models": ["nvidia/nemotron-3.5-lightning-30b-a3b",
                "nvidia/nemotron-nano-3-30b-a3b",
                "deepseek-ai/deepseek-v4-flash-0731",
                "nvidia/nemotron-3-super-120b-a12b"]},
    {"name": "Gemini",
     "url":  "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
     "key":  os.environ.get("GEMINI_API_KEY"),
     # Останній резерв. Квоти в Gemini теж помодельні: 05.09 о 03:00 UTC
     # gemini-3.5-flash уже віддавав 429, а gemini-flash-latest — 200, але
     # старий `break` до нього не доходив. gemini-3.8-flash — найновіша
     # стабільна (ai.google.dev/gemini-api/docs/models), 3.5-flash-lite має
     # більший добовий ліміт запитів. Google урізає безкоштовні квоти без
     # попередження — точні цифри лише в AI Studio.
     "models": ["gemini-3.8-flash",
                "gemini-3.5-flash-lite",
                "gemini-3.5-flash",
                "gemini-flash-latest"],
     "per_model_limit": True},
    # ⚰️ Cerebras ВИДАЛЕНО (БАГ-017, 05.09.2026). 21.07.2026 Cerebras замінив
    # безкоштовний тариф на разові $5 кредитів із прив'язкою картки; відтоді
    # кожен виклик — 402 «Payment required». llm_check писав «ПРИБРАТИ З
    # ЛАНЦЮГА: Cerebras» щодоби щонайменше з 20.08, але провайдер стояв ПЕРШИМ
    # ще пів місяця: ~1 100 мертвих викликів на добу. Третій випадок класу
    # «GitHub Models / Mistral» — і перший, коли автоматичний діагноз був, а
    # реакції не було. Секрет CEREBRAS_API_KEY у news_bot.yml прибрано.
    # ⏭ ЯК ПОВЕРНУТИ: лише за платним тарифом — суперечить умові проекту.
    # ⚰️ GitHub Models ВИДАЛЕНО (БАГ-016, 10.08.2026): GitHub закрив СЕРВІС
    # цілком 30.07.2026, заміни в межах GitHub немає (шлях міграції — платний
    # Azure AI Foundry). Провайдер повертав 410 на КОЖНОМУ виклику.
    # ⚰️ Mistral ВИДАЛЕНО (ревізія 17.08.2026): 402 «Check your subscription»
    # на КОЖНОМУ виклику, ~660 мертвих запитів на добу. Безкоштовний план
    # Experiment більше не покриває цю модель. ЯК ПОВЕРНУТИ: якщо Mistral
    # відновить безкоштовний доступ — додати запис; llm_check покаже 200.
] if p["key"]]

# Скільки чекати на відповідь провайдера. Було 30 с — дорого: аудит 17.08.2026
# намірив ~1,3 таймаути NVIDIA на прогін ≈ 80 хвилин простою на добу. NVIDIA на
# безкоштовному тарифі ділить потужність між усіма, тож «повільно» тут означає
# не «зараз відповість», а «сьогодні перевантажена».
#
# Спершу поставили 12 с — і це виявилось ЗАМАЛО, але помітили лише в бою:
# перші два прогони на новому коді дали 10 таймаутів з 10 (щоправда, разом з
# іншою помилкою — reasoning-моделлю першою в NVIDIA, див. нижче). 20 с —
# компроміс: вистачає deepseek-v4-flash, який і давав основний потік, але не
# змушує чекати півхвилини на перевантаженого провайдера.
# Ціна помилки тут невелика й асиметрична: замалий таймаут ріже робочого
# провайдера, завеликий лише марнує час. Тому при сумніві — більше.
LLM_TIMEOUT = 20

# Моделі, які в ЦЬОМУ прогоні вже відповіли «мене більше немає» (404/410).
# Живе лише в пам'яті процесу і навмисно не зберігається: провайдери іноді
# віддають 404 помилково, а перезапуск раз на 15 хв дає безкоштовну повторну
# перевірку. Сенс — не повторювати мертвий виклик 5-6 разів за один прогін.
_DEAD_MODELS = set()

# Пам'ять ОДНОГО прогону (аудит 05.09.2026, БАГ-017) — див. call_llm.
# Не зберігається між прогонами навмисно: 429 і таймаут — стан «зараз», а
# перезапуск раз на 15 хв дає безкоштовну повторну перевірку.
_SLOW_MODELS    = set()   # модель не встигла за LLM_TIMEOUT у цьому прогоні
_LIMITED_MODELS = set()   # 429 у провайдера з помодельною квотою
_DEAD_PROVIDERS = set()   # 4xx (крім 429/404), 5xx або мережева помилка


def models_of(p):
    """Моделі провайдера за пріоритетом: перша — бажана, решта — запасні.

    НАВІЩО СПИСОК, А НЕ РЯДОК. Одна захардкоджена назва — це відкладена
    зупинка, і проект уже двічі на це наступив (обидва рази БАГ-016):
    NVIDIA `deepseek-v4-flash` помер 07.08.2026 о 09:00 UTC і віддавав 410
    «has reached its end of life» на КОЖНОМУ виклику, а Gemini `2.5-flash`
    Google закрив для нових користувачів. Провайдер при цьому лишався живим —
    падала саме назва. Каталоги ж волатильні: у Cerebras список моделей, за
    свідченнями користувачів, скорочувався без попередження.

    Зі списком така смерть перестає бути аварією: call_llm ловить 404/410,
    запам'ятовує назву в _DEAD_MODELS і бере наступну — канал не помічає.
    Запис у STATS усе одно піде адміну, тобто мовчазною підміна не буде.

    Сумісність: якщо у провайдера заданий одиничний "model" — працює як раніше.
    """
    return p.get("models") or [p["model"]]


def is_model_gone(r):
    """Чи означає відповідь «цієї моделі більше немає» (на відміну від «зайнята»).

    Розрізнення принципове: 429/5xx — привід зачекати або піти до іншого
    провайдера, 404/410 — привід НАЗАВЖДИ змінити назву моделі. Плутанина між
    ними коштувала проекту 11 діб марних викликів до GitHub Models.

    Частина провайдерів віддає це не статусом, а текстом у 400 — тому й
    перевірка тіла. Слова взяті з реальних відповідей: NVIDIA писала
    «has reached its end of life», OpenAI-сумісні — «model not found»,
    «decommissioned».
    """
    if r.status_code in (404, 410):
        return True
    if r.status_code != 400:
        return False
    body = (r.text or "").lower()
    return any(s in body for s in ("model not found", "does not exist",
                                   "end of life", "decommission",
                                   "no longer available", "unknown model"))


def call_llm(prompt, max_tokens=900, temperature=0.4, save_strong=False):
    """Пробує провайдерів по черзі. Повертає текст, 'RATE_LIMIT' (усі в ліміті)
    або None (усі впали з іншої причини).

    save_strong=True — бережемо найсильнішу модель (першу в списку): черга
    починається з резервних. Навіщо: добовий ліміт Groq (120B) з'їдався за
    перші години ~400 викликами, і решту дня ВСЕ писала слабка gemma (звідси
    одруки «дешею», «всіій»). Тепер сильна модель дістається найважливішому
    (курація + топ-подія кожного прогону = ~200 викликів, розтягнутих на
    добу), а добивка йде на резервні ноги (SambaNova/Cloudflare/NVIDIA/Gemini).
    Якщо резервні впали — Groq усе одно підстрахує (він у кінці черги).

    ПАМ'ЯТЬ ПРОГОНУ (аудит 05.09.2026, БАГ-017). Раніше кожен виклик заново
    пробував усіх: 12 кандидатів × (402 Cerebras + 20-секундний таймаут NVIDIA
    + 429 Groq + 429 Gemini) = 5–6 хвилин марного прогону, і «всі в ліміті»
    не спрацьовувало ніколи, бо 402/таймаут лімітом не вважались. Тепер:
      • провайдер, що впав із 4xx (крім 429/404), 5xx або мережевою помилкою,
        до кінця прогону не викликається (_DEAD_PROVIDERS);
      • модель, що впала в таймаут, до кінця прогону не викликається
        (_SLOW_MODELS) — але ІНШІ моделі того ж провайдера пробуються, бо
        латентність у NVIDIA залежить від моделі, не від акаунта;
      • 429 у провайдера з `per_model_limit` (Groq, Gemini, SambaNova) вимикає
        лише цю модель (_LIMITED_MODELS) і бере наступну — квоти там помодельні,
        і старий `break` кидав три чверті квоти невикористаними.
    Провайдери й моделі, вимкнені пам'яттю прогону, у «всі в ліміті» не
    голосують: коли живих не лишилось, повертаємо RATE_LIMIT і main() зупиняє
    прогін, а не перебирає кандидатів далі."""
    providers = LLM_PROVIDERS
    if save_strong and len(LLM_PROVIDERS) > 1:
        # «Дорогі» моделі (top: сильні, але з куцим добовим лімітом) — у кінець
        providers = ([p for p in LLM_PROVIDERS if not p.get("top")]
                     + [p for p in LLM_PROVIDERS if p.get("top")])
    all_rate_limited = True
    for p in providers:
        if p["name"] in _DEAD_PROVIDERS:
            continue          # у цьому прогоні вже впав — не марнуємо час
        for model in models_of(p):
            if (model in _DEAD_MODELS or model in _SLOW_MODELS
                    or model in _LIMITED_MODELS):
                continue      # у цьому прогоні вже впевнились, що вона не відповість
            body = {"model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens, "temperature": temperature}
            # Прикручуємо «міркування» до мінімуму там, де це підтримано.
            #
            # НАВІЩО: gpt-oss — reasoning-модель, і за замовчуванням
            # (reasoning_effort="medium") вона витрачає частину max_tokens на
            # внутрішні міркування. Замір 17.08: приблизно у 2 прогонах з 5
            # Cerebras віддавав finish_reason="length" — відповідь обірвано,
            # бо на сам пост токенів не лишилось. Запобіжник це ловив і віддавав
            # наступному провайдеру, тобто ~20% перших спроб пропадало марно.
            # Нам міркування тут не потрібні: завдання — переписати готову
            # новину в пост, а не розв'язати задачу.
            #
            # ЧОМУ "low", А НЕ "none": провайдери gpt-oss приймають лише
            # low/medium/high і відповідають 400 на "none" — повністю вимкнути
            # міркування через API не можна (перевірено 17.08.2026). "low" —
            # це підлога.
            #
            # ЧОМУ ПЕРЕВІРКА ПО ІМЕНІ МОДЕЛІ, А НЕ ПО ПРОВАЙДЕРУ: поруч із
            # gpt-oss у тих самих провайдерів стоять Qwen/Gemma/Llama, які цього
            # параметра не знають — на них він поїхав би зайвим полем і міг би
            # завалити запасний шлях саме тоді, коли він потрібен.
            if "gpt-oss" in model:
                body["reasoning_effort"] = "low"
            try:
                r = requests.post(
                    p["url"],
                    headers={"Authorization": f"Bearer {p['key']}",
                             "Content-Type": "application/json"},
                    json=body,
                    timeout=LLM_TIMEOUT,
                )
                if r.status_code == 429:
                    STATS["err"][p["name"]] = "ліміт (429)"
                    if p.get("per_model_limit"):
                        # Квота помодельна: вимикаємо лише цю модель і беремо
                        # наступну в того ж провайдера.
                        _LIMITED_MODELS.add(model)
                        nxt = [m for m in models_of(p)
                               if m not in _LIMITED_MODELS and m not in _DEAD_MODELS
                               and m not in _SLOW_MODELS]
                        print(f"⚠️ {p['name']}: {model} у ліміті — "
                              + (f"беру {nxt[0]}" if nxt else "моделей більше немає"))
                        continue
                    print(f"⚠️ {p['name']} ліміт — пробуємо наступного провайдера.")
                    break     # ліміт на акаунт — решта його моделей уперлася б
                              # у ту саму квоту
                if is_model_gone(r):
                    # Модель зникла — але ПРОВАЙДЕР живий. Лишаємось тут і
                    # беремо запасну назву (див. _DEAD_MODELS). Решта 4xx —
                    # проблема ключа/доступу, там запасна модель не допоможе.
                    all_rate_limited = False
                    _DEAD_MODELS.add(model)
                    nxt = [m for m in models_of(p) if m not in _DEAD_MODELS]
                    print(f"⚰️ {p['name']}: модель {model} закрито ({r.status_code}) — "
                          + (f"перемикаюсь на {nxt[0]}" if nxt else "запасних немає"))
                    STATS["err"][p["name"]] = f"модель {model} закрито ({r.status_code})"
                    continue
                if r.status_code >= 400:
                    # raise_for_status показує лише статус+URL (URL ще й обрізається
                    # логом до 70 симв. → «...generativelanguage.google»). Тіло
                    # відповіді містить справжню причину (модель/ключ/API вимкнено).
                    all_rate_limited = False
                    reason = " ".join((r.text or "").split())
                    print(f"❌ {p['name']}: {r.status_code} — {reason[:300]} "
                          f"(до кінця прогону не викликаю)")
                    STATS["err"][p["name"]] = f"{r.status_code}: {reason[:120]}"
                    _DEAD_PROVIDERS.add(p["name"])
                    break
                all_rate_limited = False
                choice  = r.json()["choices"][0]
                content = (choice.get("message", {}).get("content") or "").strip()
                # finish_reason="length" = відповідь ОБІРВАНО на ліміті токенів.
                # Так у канал потрапляли пости на півслові («...дворічну підтрим»):
                # reasoning-моделі (gpt-oss-120b) палять max_tokens на «міркування»,
                # і на сам текст їх не лишається. Обірване НЕ публікуємо — краще
                # віддати наступному провайдеру.
                if choice.get("finish_reason") == "length":
                    print(f"⚠️ {p['name']}: відповідь обірвано на ліміті токенів — наступний провайдер.")
                    STATS["err"][p["name"]] = "обірвано (finish_reason=length)"
                    break
                if content:
                    STATS["ok"][p["name"]] = STATS["ok"].get(p["name"], 0) + 1
                    return content
            except requests.exceptions.Timeout:
                # Не встигла САМЕ ЦЯ модель — сусідня в того ж провайдера може
                # встигати (NVIDIA: reasoning-моделі vs легкі MoE). Тому
                # вимикаємо модель, а не провайдера.
                all_rate_limited = False
                _SLOW_MODELS.add(model)
                print(f"🐢 {p['name']}: {model} не встигла за {LLM_TIMEOUT} с — "
                      f"до кінця прогону не викликаю")
                STATS["err"][p["name"]] = f"таймаут {LLM_TIMEOUT} с ({model})"
                continue
            except Exception as e:
                all_rate_limited = False
                print(f"❌ {p['name']}: {e} (до кінця прогону не викликаю)")
                STATS["err"][p["name"]] = str(e)[:120]
                _DEAD_PROVIDERS.add(p["name"])
                break
    return "RATE_LIMIT" if all_rate_limited else None

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS published (
            hash TEXT PRIMARY KEY, title TEXT, published_at TEXT
        )
    """)
    # msg_id — номер повідомлення в каналі: вечірній дайджест робить із нього
    # прямі посилання t.me/<канал>/<msg_id> на кожен пункт підсумку.
    try:
        conn.execute("ALTER TABLE published ADD COLUMN msg_id INTEGER")
    except sqlite3.OperationalError:
        pass  # колонка вже є
    # posted_title — ФАКТИЧНИЙ заголовок поста в каналі (написаний моделлю).
    # Потрібен для дедупу: RSS-заголовки різних видань про ту саму подію геть
    # різні («ППО знешкодила ракету і 69 дронів» vs «Ворог запустив 7 ракет та
    # 90 БпЛА») — порівняння з ними пропускало дублі. Заголовки ж, які пише
    # наша модель, для однієї події лексично близькі — по них дубль ловиться.
    try:
        conn.execute("ALTER TABLE published ADD COLUMN posted_title TEXT")
    except sqlite3.OperationalError:
        pass  # колонка вже є
    # source — назви видань, на які пост посилається в рядку «📰 За даними».
    # Навіщо: без цієї колонки питання «чи всі 28 джерел доходять до каналу»
    # неможливо поставити до бази — у сесії 16 відповідь довелося знімати
    # парсингом публічної сторінки Telegram по 160 постах. Тепер це один SQL.
    # Для злитої групи пишемо ОСНОВНЕ джерело першим, решту через кому —
    # рівно те, що читач бачить у підписі поста.
    try:
        conn.execute("ALTER TABLE published ADD COLUMN source TEXT")
    except sqlite3.OperationalError:
        pass  # колонка вже є
    # seen_topics БІЛЬШЕ НЕ ІСНУЄ (БАГ-014). Таблиця мала міряти «трендовість»
    # теми, а насправді міряла, скільки прогонів новина провисіла у стрічці:
    # лічильник ріс для КОЖНОГО кандидата на КОЖНОМУ прогоні, тож найвищий бал
    # отримувало найлежаліше. Тепер «підтвердженість» береться з кількості
    # РІЗНИХ ДЖЕРЕЛ у злитій групі — це вже рахує merge_by_event, і це саме те,
    # що ми й хотіли міряти. Прибирання одноразове й ідемпотентне: після DROP
    # таблиці немає, наступний прогін сюди не заходить. VACUUM — щоб 71 тисяча
    # рядків реально пішла з файла, який комітиться в публічний репозиторій
    # (урок сесії 17: DROP звільняє сторінки, але не витирає байти).
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='seen_topics'").fetchone():
        conn.execute("DROP TABLE seen_topics")
        conn.commit()
        conn.execute("VACUUM")
        print("🧹 seen_topics прибрано (БАГ-014)")
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

def mark_published(conn, url, title, msg_id=None, posted_title=None, source=None):
    """Позначає URL опублікованим. posted_title — заголовок, який реально
    вийшов у канал (для дедупу наступних прогонів). source — видання з підпису
    «За даними». Колонки перелічено явно, щоб INSERT не залежав від порядку
    міграцій ALTER TABLE."""
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO published "
                 "(hash, title, published_at, msg_id, posted_title, source) "
                 "VALUES (?,?,?,?,?,?)",
                 (h, title, datetime.utcnow().isoformat(), msg_id, posted_title,
                  source))
    conn.commit()


def sources_label(item):
    """Рядок для published.source: основне джерело першим, решта через кому.

    Береться з item["sources"] — того самого списку, який format_post_html
    показує читачеві в «📰 За даними». Тобто в базі опиняється рівно те, що
    вийшло в канал, а не здогад. Порядок збережено, повтори прибрано."""
    names = [s.get("name") for s in (item.get("sources") or []) if s.get("name")]
    if not names and item.get("source"):
        names = [item["source"]]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return ", ".join(out) or None


def source_report(conn, days=7):
    """Скільки постів у каналі дало кожне джерело за N діб.

    Навіщо: у сесії 16 на це питання довелося відповідати парсингом публічної
    сторінки Telegram по 160 постах — база джерела не зберігала. Тепер це один
    запит. Рахуємо ЛИШЕ рядки з msg_id — те, що реально вийшло в канал;
    рядки злитих груп без msg_id це «з'їдені» дублі, а не пости.
    Пост із кількох джерел («за даними X, Y») зараховується КОЖНОМУ з них —
    саме так читач і бачить підпис."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT source FROM published "
        "WHERE published_at >= ? AND msg_id IS NOT NULL", (since,)
    ).fetchall()
    counts, no_source = {}, 0
    for (src,) in rows:
        if not src:
            no_source += 1        # пости до появи колонки — чесно показуємо окремо
            continue
        for name in (n.strip() for n in src.split(",")):
            if name:
                counts[name] = counts.get(name, 0) + 1
    lines = [f"📊 Джерела за {days} діб: {len(rows)} постів у каналі"]
    if no_source:
        lines.append(f"(з них {no_source} без запису джерела — до міграції)")
    for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {n:>4}  {name}")
    # Джерела зі списку, які за цей час не дали НІЧОГО. «Google News» тут
    # з'являтиметься завжди й це нормально: його бренд підмінюється справжнім
    # виданням із тегу <source>, тобто під своїм ім'ям він публікуватись і не
    # може (див. аудит сесії 16).
    silent = [f["name"] for f in RSS_FEEDS if f["name"] not in counts]
    if silent:
        lines.append(f"🔇 Мовчали ({len(silent)}): " + ", ".join(silent))
    return "\n".join(lines)

def is_skipped(conn, url):
    h = hashlib.md5(url.encode()).hexdigest()
    return conn.execute("SELECT 1 FROM skipped WHERE hash=?", (h,)).fetchone()

def mark_skipped(conn, url, title, reason):
    """Запам'ятати відхилену новину, щоб не витрачати на неї виклик LLM знову."""
    h = hashlib.md5(url.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO skipped VALUES (?,?,?,?)",
                 (h, title, reason, datetime.utcnow().isoformat()))
    conn.commit()

def source_count(item):
    """Скільки РІЗНИХ видань написали про цю подію.

    Замінив собою get_topic_count у сортуванні кандидатів (БАГ-014). Старий
    лічильник міряв, скільки прогонів новина провисіла у стрічці, тобто
    піднімав нагору найлежаліше. Кількість джерел — це те, що ми й хотіли
    міряти під словом «трендовість»: подія, про яку написали чотири видання,
    важливіша за ту, про яку написало одне. Її ж читач бачить у пості як
    «✅ Підтверджено N джерелами», тобто відбір і обіцянка каналу тепер
    говорять одне й те саме."""
    return len(item.get("sources") or []) or 1


def entry_ts(entry):
    """Час публікації запису (секунди UTC). Немає дати — 0, тобто за рівних
    інших ключів такий запис іде в кінець, а не наперед."""
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                return calendar.timegm(parsed)
            except Exception:
                pass
    return 0


def is_spam(title, summary):
    text = (title + " " + summary).lower()
    return any(kw in text for kw in SPAM_KEYWORDS)

# Літери, яких в українській абетці немає взагалі, і службові слова, які в
# українській не трапляються. Раніше в списку були «для », «при », «так »,
# «все » — звичайні українські слова, і будь-який анонс із трьома з них
# відсікався як російський. Аудит 05.09.2026 (БАГ-018): за добу так загинуло
# 17 українських новин, три з них — у кожному зі 100 прогонів («Понад 4600
# ракет і дронів перехопила українська ППО», «Сили оборони відбили 207 атак»,
# «МАГАТЕ заявило про локальне припинення вогню»). Ще одна дірка старого
# коду: маркери шукались як підрядки, тож «или » збігалось із «ходили »,
# «били ». Тепер — лише цілі слова (\b) і лише те, чого в українській немає.
_RU_LETTERS = re.compile(r"[ыэъё]")
_RU_WORDS   = re.compile(
    r"\b(это|этот|эта|что|чтобы|или|если|как|его|её|они|который|которая|"
    r"которые|также|здесь|сейчас|уже|ещё|очень|только|после|через|между|"
    r"из|из-за|от|около|против|со|во|обо)\b")

def is_russian(title, summary):
    text = (title + " " + summary).lower()
    # Одна «ы» — може бути цитата чи прізвище; три ознаки — вже мова тексту.
    score = len(_RU_LETTERS.findall(text)) + len(_RU_WORDS.findall(text))
    return score >= 3

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
    # Порівнюємо з ОБОМА заголовками: RSS-овим (title) і тим, що реально
    # вийшов у канал (posted_title). Канальні заголовки однієї події лексично
    # близькі між собою — саме по них ловляться дублі, які RSS-заголовки
    # різних видань маскують різними формулюваннями.
    rows = conn.execute(
        "SELECT title, posted_title FROM published WHERE published_at >= ?",
        (since,)
    ).fetchall()
    for row in rows:
        for old_title in row:
            if not old_title:
                continue
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

def is_semantic_duplicate(conn, headline, hours=24, limit=60):
    """Семантичний дедуп ФАКТИЧНОГО заголовка поста проти опублікованого за добу.

    Навіщо (бойова перевірка 19.07): URL-дедуп блокує лише джерела, ВЖЕ злиті
    в групу на момент публікації. Коли інше видання пише про ту саму подію в
    наступному прогоні, його URL у БД відсутній, а заголовок лексично інший
    (Жаккар < 0.5) — і подія виходила в канал повторно (Wildberries ×6, поїзд
    на Запоріжжі ×4 за 45 хв). Правило в загальному промпті курації системно
    не тримає, тому — окремий МАЛЕНЬКИЙ виклик з єдиним завданням «дубль чи ні».

    Fail-open: якщо LLM недоступний (429) чи відповів сміттям — вважаємо НЕ
    дублем: краще зрідка пропустити повтор, ніж мовчки губити новини. Виклик
    дешевий (~1,2 тис. токенів) і йде з save_strong=True — спершу на резервні
    провайдери, добовий ліміт сильної моделі не чіпає."""
    if not headline:
        return False
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT COALESCE(posted_title, title) FROM published "
        "WHERE published_at >= ? ORDER BY published_at DESC LIMIT ?",
        (since, limit)).fetchall()
    if not rows:
        return False
    published = "\n".join(f"{i+1}. {r[0][:110]}" for i, r in enumerate(rows))
    prompt = f"""Опубліковані за останню добу заголовки новинного каналу:
{published}

Новий заголовок-кандидат:
{headline[:200]}

Чи повідомляє кандидат про ТУ САМУ подію, що якийсь із опублікованих заголовків?
ТА САМА подія — це й ОНОВЛЕННЯ: уточнені цифри жертв чи збитих цілей, «атака
триває», зведення або реакції про ту саму атаку/заяву/подію.
РІЗНІ міста — завжди РІЗНІ події. Дві незалежні події в одному місті — теж різні.

Відповідай ЛИШЕ одним рядком без пояснень:
ДУБЛЬ <номер опублікованого заголовка> — якщо та сама подія
ОК — якщо подія нова"""
    raw = call_llm(prompt, max_tokens=1200, temperature=0.0, save_strong=True)
    if not raw or raw == "RATE_LIMIT":
        return False  # fail-open: перевірити не вдалося — публікуємо як раніше
    verdict = raw.strip().upper()
    if verdict.startswith("ДУБЛЬ") or verdict.startswith("DUP"):
        m = re.search(r"\d+", raw)
        idx = int(m.group()) - 1 if m else -1
        matched = rows[idx][0] if 0 <= idx < len(rows) else "?"
        print(f"⏭ Семантичний дубль: «{headline[:60]}» ≈ «{matched[:60]}»")
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


def fetch_article_text(url, max_chars=2500):
    """Повний текст статті за посиланням з RSS.

    Навіщо: RSS-анонс часто ~2 речення-тизер без суті («НБУ відповів на
    чутки» — а ЩО відповів, лише у статті). Замість відкидати такі новини,
    бот іде за посиланням і віддає моделі справжній текст. Пости стають
    повнішими для ВСІХ новин, не лише тизерів (~6 запитів на прогін — дешево).
    Повертає '' якщо не вийшло (пейвол/JS/редірект Google News) — тоді
    модель працює з анонсом, а SKIP-тизер лишається останнім запобіжником."""
    try:
        r = requests.get(url, headers={"User-Agent": FEED_UA}, timeout=12,
                         allow_redirects=True)
        if r.status_code != 200 or not r.text:
            return ""
        page = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", r.text)
        # <article> — там менше сміття (меню, «читайте також»), якщо сайт її має
        m = re.search(r"(?is)<article[^>]*>(.*?)</article>", page)
        if m:
            page = m.group(1)
        paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)
        text  = " ".join(re.sub(r"(?s)<[^>]+>", " ", p) for p in paras)
        text  = html.unescape(re.sub(r"\s+", " ", text)).strip()
        # <200 символів = витягли не статтю, а обгортку — краще чесне «нема»
        return text[:max_chars] if len(text) >= 200 else ""
    except Exception as e:
        print(f"⚠️ fetch_article {url[:60]}: {str(e)[:60]}")
        return ""


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
                # Свіжість групи — за найновішим її учасником: подія настільки
                # свіжа, наскільки свіже найпізніше повідомлення про неї.
                m["ts"] = max(m.get("ts") or 0, it.get("ts") or 0)
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


def resolve_gnews_url(url):
    """Розгортає redirect-посилання Google News (news.google.com/rss/articles/…)
    до прямого URL статті. Це дає: чесне посилання для читача, робочий
    fetch_article_text (заглушку Google він читати не вміє) і дедуп за
    реальним URL. Новий формат Google не декодується офлайн, тому пробуємо
    HTTP: 1) редірект; 2) адреса в HTML заглушки. Не вийшло — None,
    і далі все працює зі старим GN-посиланням (як раніше)."""
    try:
        r = requests.get(url, headers={"User-Agent": FEED_UA},
                         timeout=10, allow_redirects=True)
        if "news.google.com" not in r.url:
            return r.url
        # Заглушка: реальна адреса буває в data-n-au або першому <a> не на Google
        m = re.search(r'data-n-au="([^"]+)"', r.text or "")
        if m:
            return html.unescape(m.group(1))
        for href in re.findall(r'<a[^>]+href="(https?://[^"]+)"', r.text or ""):
            if "google.com" not in href and "gstatic.com" not in href:
                return html.unescape(href)
    except Exception as e:
        print(f"⚠️ resolve_gnews: {str(e)[:60]}")
    return None


# Один бренд — один підпис. Google News у тегу <source> віддає то бренд, то
# домен, то бренд із хвостом рубрики, і в каналі це вилазило як різні джерела:
# за 276 постів 33 різні підписи, з них «nv.ua» проти «NV», «radiosvoboda.org»
# проти «Радіо Свобода», «AP News» проти «AP», «Суспільне | Новини» проти
# «Суспільне». Читачеві це виглядає неохайно, а звіт source_report через це
# ділить одне видання надвоє і показує його ж у списку «мовчали».
SOURCE_ALIASES = {
    "nv.ua": "NV", "tsn.ua": "ТСН", "pravda.com.ua": "Українська правда",
    "suspilne.media": "Суспільне", "ukrinform.ua": "Укрінформ",
    "censor.net": "Цензор.НЕТ", "unian.ua": "УНІАН", "unian.net": "УНІАН",
    "radiosvoboda.org": "Радіо Свобода", "dw.com": "DW",
    "fakty.com.ua": "Факти ICTV", "militarnyi.com": "Мілітарний",
    "apnews.com": "AP", "ap news": "AP", "reuters.com": "Reuters",
    "eurointegration.com.ua": "Європейська правда",
    "armyinform.com.ua": "АрміяInform", "bihus.info": "Бігус.Інфо",
}


def normalize_source(name):
    """Зводить підпис джерела до канонічного бренду.

    Спершу відрізаємо хвіст рубрики («Суспільне | Новини» → «Суспільне»), потім
    шукаємо в SOURCE_ALIASES без урахування регістру. Невідоме ім'я лишаємо як
    є: краще показати незнайомий бренд, ніж загубити атрибуцію."""
    name = (name or "").strip()
    if not name:
        return name
    name = name.split(" | ")[0].strip() or name
    return SOURCE_ALIASES.get(name.lower(), name)


# Колонки думок — не новини. 18.07 колонка NV /opinion/ вийшла в канал як
# факт («Зеленський звільнив міністра оборони Федорова») — суміш оцінок
# автора з подіями. Канал обіцяє «перевірені факти», тому opinion відсікаємо.
OPINION_MARKERS = ("/opinion/", "/opinion-", "/blogs/", "/blog/",
                   "/columns/", "/dumka/", "/dumky/", "/publications/authors/",
                   # розважальні/езотеричні розділи видань — не новини
                   "/astrology/", "/horoscope/", "/goroskop", "/lite/astrology")

def is_opinion_url(url):
    return any(m in (url or "").lower() for m in OPINION_MARKERS)


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
                if is_fundraiser(title, summary):
                    print(f"🚫 Збір коштів: {title[:50]}")
                    mark_skipped(conn, url, title, "fundraiser")
                    continue
                if is_russian(title, summary):
                    print(f"🚫 Російська: {title[:50]}")
                    mark_skipped(conn, url, title, "russian")
                    continue
                # Явна назва бренду; feed.title — лише запасний варіант
                source = feed_cfg.get("name") or feed.feed.get("title", "")
                if "news.google.com" in url:
                    # Справжнє видання лежить у тегу <source> запису — без
                    # нього пост показував джерело «Google News» (4336/4340).
                    gsrc = getattr(entry, "source", None) or {}
                    source = gsrc.get("title") or source
                    # Пробуємо розгорнути redirect до прямої адреси статті.
                    real = resolve_gnews_url(url)
                    if real:
                        url = real
                        # Повторна перевірка вже за ПРЯМИМ URL: ту саму статтю
                        # ми могли опублікувати з власного фіду видання.
                        if is_published(conn, url) or is_skipped(conn, url):
                            continue
                # Один бренд — один підпис (див. normalize_source): Google News
                # віддає то «NV», то «nv.ua», і в каналі це були різні джерела.
                source = normalize_source(source)
                if is_opinion_url(url):
                    print(f"🚫 Колонка думок: {title[:50]}")
                    mark_skipped(conn, url, title, "opinion")
                    continue
                items.append({
                    "title":     title,
                    "summary":   summary,
                    "url":       url,
                    "source":    source,
                    "lang":      feed_cfg["lang"],
                    # Рубрика фіду ("tech" або None) — по ній fetch_news тримає
                    # окремий кошик для техно/науки. dict(it) у merge_by_event і
                    # merge_group копіює ключ разом з рештою, тож тег переживає
                    # злиття подій.
                    "topic":     feed_cfg.get("topic"),
                    "image_url": extract_image(entry),
                    # Час публікації — третій ключ сортування: за рівної
                    # підтвердженості вище має бути свіжіше, бо канал обіцяє
                    # новини «в реальному часі».
                    "ts":        entry_ts(entry),
                })
        except Exception as e:
            print(f"⚠️ {feed_cfg['url']}: {e}")
    # Одна подія з різних джерел → один кандидат («за даними X, Y»)
    before = len(items)
    items  = merge_by_event(items)
    if before != len(items):
        print(f"🔗 Злито за подіями: {before} → {len(items)} кандидатів")
    # Спершу — новини про Україну, потім — за ПІДТВЕРДЖЕНІСТЮ (скільки різних
    # видань написали про подію), потім — за свіжістю. Другий ключ раніше був
    # get_topic_count, який насправді міряв, скільки прогонів новина провисіла
    # у стрічці, тобто піднімав нагору найлежаліше (БАГ-014). Заміряно 08.08 на
    # живому прогоні: подія, підтверджена ЧОТИРМА джерелами, стояла 13-ю з 52 в
    # українському кошику, який бере 9 — тобто найкраще підтверджена новина
    # прогону вилітала, а одноджерельні виходили в канал.
    items.sort(key=lambda x: (ukraine_score(x), source_count(x), x.get("ts") or 0),
               reverse=True)
    # Резервуємо місця світовим новинам. Без цього ukraine_score (бал за кожне
    # українське/воєнне слово) виштовхував англомовні джерела — BBC, Guardian,
    # Reuters, AP — за межі топ-12, і курація їх узагалі не бачила: канал виходив
    # лише з українських джерел, хоч і обіцяє «новини України ТА СВІТУ».
    # Третій кошик — техно/наука (issue #7). Мовного резерву їм НЕ вистачало:
    # DOU сидів в українському кошику проти півсотні воєнних новин, а
    # TechCrunch/Verge/Ars/ScienceDaily ділили en[:3] з Reuters/AP/BBC про
    # війну — і за добу 09–10.08 програли всі три слоти (2% каналу, п'ять
    # фідів з нулем постів). Резерв дає їм місце в кандидатах; чи вийде новина
    # в канал, і далі вирішує курація — це не квота на публікацію.
    # Ціна резерву — по одному слоту з кожного кошика (9→8 і 3→2). Світові
    # новини цим не ламаються: у них 11% каналу проти 2% у техно/науки.
    ua   = [x for x in items if x.get("lang") == "uk" and x.get("topic") != "tech"]
    en   = [x for x in items if x.get("lang") == "en" and x.get("topic") != "tech"]
    tech = [x for x in items if x.get("topic") == "tech"]
    picked = ua[:8] + en[:2] + tech[:2]
    if len(picked) < 12:                       # чогось бракує — добираємо рештою
        rest = [x for x in ua[8:] + en[2:] + tech[2:] if x not in picked]
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
    # Повний текст статті: анонс у RSS часто тизер без суті. Сирий HTML-текст
    # може містити сміття сайту — модель попереджено брати лише саму новину.
    # Тягнемо ЛИШЕ коли анонс куций: у довгому (700+ симв.) суть уже є, а
    # зайві ~1000 токенів на кожен виклик з'їдали добовий ліміт Cerebras —
    # у підсумках адміна 16.07 по обіді з'явились «Cerebras: ліміт (429)».
    article = (fetch_article_text(item["url"])
               if len(item.get("summary") or "") < 700 else "")
    article_block = (
        f"\n\nПовний текст статті (взято з сайту автоматично; ігноруй уривки"
        f" меню/реклами/«читайте також», бери лише те, що про цю новину):\n{article}"
        if article else ""
    )
    # Обсяг поста — від обсягу РЕАЛЬНОГО матеріалу. Коли фактів жменя, а формат
    # вимагає «2-3 змістовні абзаци», модель добудовує решту з фантазії — 18.07
    # так вийшов повністю вигаданий пост «Кабмін спростував відставку Федорова»
    # (у статті за посиланням нічого подібного не було). Мало матеріалу —
    # вимагаємо КОРОТКИЙ пост, а не насичений.
    material_len = (len(item.get("summary") or "") + len(article)
                    + sum(len(e) for e in extra[:3]))
    if material_len >= 400:
        length_rule = ("2–3 ЗМІСТОВНІ абзаци, розділені порожнім рядком. Кожен "
                       "абзац додає НОВУ конкретику з джерела: обставини, деталі, "
                       "наслідки, тло події. Пост має бути насиченим — НЕ в одне речення.")
    else:
        length_rule = ("ОДИН короткий абзац (2–4 речення) СТРОГО з наявних фактів. "
                       "Матеріалу в джерелі мало — НЕ розтягуй пост і НІЧОГО не "
                       "додумуй; якщо фактів бракує навіть на 2 речення — SKIP.")
    prompt = f"""Ти досвідчений журналіст українського Telegram-каналу UA News.
{lang_note}

ГОЛОВНЕ ПРАВИЛО: пиши ЛИШЕ те, що прямо є в джерелі нижче. Краще коротший
пост, ніж хоч один вигаданий факт — від точності залежить довіра до каналу.

Новину вже відібрав редактор — вона ВАЖЛИВА, тож твоє завдання її написати.
SKIP відповідай лише у крайньому разі:
- відверта реклама/спам;
- ЗБІР КОШТІВ: новина закликає донатити чи містить реквізити (банка monobank,
  номер картки, PayPal) — канал такого не публікує, завжди SKIP;
- у тексті взагалі немає про що писати;
- ТИЗЕР БЕЗ СУТІ: заголовок обіцяє відповідь («відповіли на чутки»,
  «пояснили, чи…», «назвали причину», «стало відомо…»), а САМОЇ відповіді
  (що саме вирішили / пояснили / назвали) немає НІ в анонсі, НІ в повному
  тексті статті нижче. Пост «посадовці почали пояснювати» без того, ЩО САМЕ
  вони пояснили, підриває довіру — такого краще не публікувати взагалі.
В усіх інших сумнівах — ПИШИ, а не пропускай.

Якщо важлива — напиши у стилі якісної журналістики:
- Мова: виключно українська
- ФОРМАТ (важливо, стиль каналу):
  • Перший рядок — короткий заголовок-суть (хто/що/де), почни його ОДНИМ
    доречним за ЗМІСТОМ І ТОНОМ емодзі. Для трагедій (загибель, обстріл,
    руйнування) — стримані: 💥 🚨 ⚠️ 🕯 🔴. Для нейтрального/позитивного — за
    темою: 🚀 космос/техніка, 📚 культура, 💰 економіка, 🕊 мир, ⚡️ терміново.
    НЕ став святкових чи грайливих емодзі на трагічні події. Без крапки в кінці.
  • Далі — порожній рядок, потім {length_rule}
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
- НЕ ВИГАДУЙ ПОДІЙ-РЕАКЦІЙ: заяв, спростувань, підтверджень, коментарів
  пресслужб, урядів чи посадовців. Якщо в джерелі ніхто нічого не «спростував»,
  не «заявив» і не «підтвердив» — цих слів НЕ МОЖЕ бути в пості. Вигадане
  спростування чи заява — найгірша можлива помилка новинного каналу.

Заголовок: {item['title']}
Анонс із RSS: {item['summary'][:800]}
Джерело: {item['source']}{extra_block}{article_block}

Напиши лише готовий текст або SKIP."""

    # Температура 0.2 — щоб модель менше «додумувала» деталі/стать.
    # max_tokens 2400 (було 900→1600): reasoning-моделі (gpt-oss-120b, gemini-3.5)
    # спершу палять токени на внутрішні «міркування» — при нижчій стелі виклик
    # обривався (finish_reason=length): квота витрачена, результату нуль
    # (16.07 це стабільно ловив Gemini). Стеля — це ЗАПАС, а не витрата:
    # моделі, що не «думають», більше токенів не згенерують.
    return call_llm(prompt, max_tokens=2400, temperature=0.2,
                    save_strong=save_strong)


# ---------------------------------------------------------------------------
# ОСТАННІЙ ЗАПОБІЖНИК ПЕРЕД КАНАЛОМ
# ---------------------------------------------------------------------------
# 29.07 у канал пішла ВІДПОВІДЬ МОДЕЛІ замість новини (msg 7442): «Будь ласка,
# надайте повний текст джерела… я не можу вигадувати факти». Причина: у main()
# перевірялася рівно одна умова — чи починається відповідь зі слова SKIP. Будь-що
# інше вважалося готовим постом. Тут — перевірка, що модель повернула саме пост,
# а не свої міркування, відмову чи службову розмітку.

# Фрази, яких у справжній новині бути не може: модель говорить про СЕБЕ або про
# завдання. Порівнюємо в нижньому регістрі по всьому тексту поста.
_META_MARKERS = (
    "надайте повний текст", "надайте текст", "надати повний текст",
    "у вашому повідомленні", "у наданому тексті немає", "текст джерела відсутн",
    "згідно з правилами", "згідно із правилами", "не можу вигадувати",
    "переходити за зовнішніми посиланнями", "не маю доступу до",
    "як мовна модель", "як асистент", "як штучний інтелект",
    "надано лише заголовок", "лише заголовок та посилання",
    "ось готовий пост", "ось пост", "ось текст поста", "готовий текст:",
    "provide the full text", "as an ai", "i cannot", "i'm unable", "i am unable",
    "<think", "</think", "```",
)

# Службові позначки на початку рядка («Заголовок:», «Абзац 1», «Para 2»).
_SERVICE_LINE = re.compile(
    r"^\s*(абзац|параграф|заголовок|пост|текст|para|paragraph)\s*\d*\s*[:：]",
    re.IGNORECASE)

# Перший рядок — це ЗАГОЛОВОК. 160 символів — з великим запасом: на 4346 реально
# опублікованих постів довших за 160 було лише 7, і всі сім — саме дефект
# (модель не поставила порожній рядок і зліпила заголовок з текстом).
_HEADLINE_MAX = 160
# Коротше 80 символів — це не новина, а обрубок.
_POST_MIN = 80


def is_bad_output(text):
    """Чи є відповідь моделі НЕпридатною до публікації.

    Повертає причину (рядок) або None, якщо все гаразд. Причину пишемо в лог
    і в skipped — щоб було видно, що саме і як часто ламається.
    """
    if not text:
        return "порожньо"
    body = text.strip()
    low = body.lower()

    for m in _META_MARKERS:
        if m in low:
            return f"мета-текст моделі: «{m}»"

    for line in body.splitlines():
        if _SERVICE_LINE.match(line):
            return f"службова позначка: «{line.strip()[:40]}»"

    if len(body) < _POST_MIN:
        return f"занадто коротко ({len(body)} символів)"

    head = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    if len(head) > _HEADLINE_MAX:
        return f"перший рядок не заголовок ({len(head)} символів)"

    # Пост має бути українською. Якщо серед літер кирилиці менше половини —
    # модель відповіла не тією мовою (резервні провайдери іноді зриваються
    # в англійську). Поріг низький навмисно: у новинах бувають латинські
    # назви (F-16, Nova Poshta, Reuters), і вони не мають вмикати відсів.
    letters = [ch for ch in body if ch.isalpha()]
    if letters:
        cyr = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
        if cyr / len(letters) < 0.5:
            return "пост не українською"

    return None


def format_post_html(text, url, sources=None, badge=True):
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

    links, checkable = [], 0
    for s in (sources or [])[:4]:
        name, href = (s.get("name"), s.get("url")) if isinstance(s, dict) else (s, None)
        if not name:
            continue
        name_esc = html.escape(name)
        if href:
            checkable += 1
        links.append(f'<a href="{html.escape(href, quote=True)}">{name_esc}</a>'
                     if href else name_esc)
    if not links:
        # Запобіжник: без джерел пост лишиться зовсім без посилання на статтю.
        links = [f'<a href="{html.escape(url, quote=True)}">Читати повністю</a>']
        return f"{body}\n\n📰 <i>За даними: {', '.join(links)}</i>"

    # ✅ Знак довіри (GROWTH.md 5.1). Це структурна перевага, якої немає в
    # каналу з редакцією: бот читає 28 стрічок одночасно і знає, скільки видань
    # написали про ту саму подію. Цифра НЕ вигадується — це рівно ті джерела,
    # що перелічені нижче й клікабельні, тож читач може перевірити кожне.
    # Рахуємо саме ті джерела, які показані Й КЛІКАБЕЛЬНІ (а не весь
    # merge-список): краще недорахувати, ніж заявити «5 джерел» і дати
    # перевірити чотири. Одне джерело значка не отримує — «підтверджено
    # 1 джерелом» це не підтвердження, а просто джерело, і воно вже рядком нижче.
    # badge=False — коли рядок не влазить у ліміт підпису до фото (див.
    # post_to_telegram). Втратити знак довіри неприємно, втратити пост гірше.
    mark = (f"✅ <b>Підтверджено {checkable} джерелами</b>\n"
            if badge and checkable >= 2 else "")
    return f"{body}\n\n{mark}📰 <i>За даними: {', '.join(links)}</i>"


# Ліміт підпису до фото в Telegram. Для звичайного повідомлення ліміт 4096 —
# туди значок влазить завжди, тому перевірка потрібна лише для sendPhoto.
TG_CAPTION_LIMIT = 1024


def visible_len(html_text):
    """Довжина так, як її рахує Telegram: розмітка в ліміт не входить."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)))


# ---------------------------------------------------------------------------
# НІЧНИЙ РЕЖИМ: пости без звукового сповіщення
# ---------------------------------------------------------------------------
# Канал видає ~270 постів на добу — це пінг раз на ~5 хвилин, і вночі теж.
# Для підписника це головна причина натиснути «вимкнути сповіщення» назавжди,
# а мовчазний канал = мертві покази реклами. Зрілі новинні канали роблять інакше:
# вночі пост ВИХОДИТЬ, але без звуку — Telegram уміє це параметром
# disable_notification. Новина нікуди не зникає і нічим не відрізняється: вона
# одразу видима в списку чатів з лічильником непрочитаних, просто не будить
# телефон о 3-й ночі.
#
# Свідомий компроміс: вночі бувають і критичні новини (масована атака). Цей
# канал — новинний агрегатор, а не система оповіщення; для тривог у людей є
# спеціальні боти. Якщо власник вирішить інакше — досить змінити два числа нижче
# (наприклад, QUIET_FROM_HOUR = QUIET_TO_HOUR = 0 повністю вимкне тихий режим).
QUIET_FROM_HOUR = 23   # з 23:00 включно — тихо
QUIET_TO_HOUR   = 7    # до 07:00 — о 07:00 звук уже вмикається


def is_quiet_hour():
    """Чи зараз «тиха» година за київським часом (True = постити без звуку).

    Будь-який збій із часовою зоною трактуємо як «день» (зі звуком) — тобто
    як поведінку, що була до цієї зміни. Публікація важливіша за тихий режим.
    """
    try:
        import pytz
        hour = datetime.now(pytz.timezone("Europe/Kiev")).hour
    except Exception as e:
        print(f"⚠️ Не вдалося визначити київську годину ({e}) — постимо зі звуком")
        return False

    if QUIET_FROM_HOUR == QUIET_TO_HOUR:
        return False                      # тихий режим вимкнено
    if QUIET_FROM_HOUR < QUIET_TO_HOUR:
        return QUIET_FROM_HOUR <= hour < QUIET_TO_HOUR
    # Вікно переходить через північ (23 -> 7): це два відрізки доби.
    return hour >= QUIET_FROM_HOUR or hour < QUIET_TO_HOUR


def max_posts_now():
    """Скільки постів дозволено цьому прогону: вночі — половина денної норми.

    Тихий режим вище прибрав ЗВУК, але не ОБСЯГ, а це різні проблеми. Замір за
    добу 16–17.08.2026: із 180 постів 61 (34%) вийшов у вікні 23:00–07:00, і
    80% результативних прогонів уперлися в стелю. Тобто вночі канал працює з
    денною інтенсивністю, і підписник зранку відкриває ~60 непрочитаних, де
    удар по Ізюму лежить між «пророцтвами Баби Ванги» та «лелеками на
    Прикарпатті». Звук вимкнено — але прокрутити це все одно треба, і саме
    тут важливі новини й губляться.

    Чому 1, а не 0: вночі трапляється найцінніше для цього каналу (масовані
    атаки), і глушити ніч повністю означало б втрачати саме їх. Одиниця
    зрізає хвіст, а не голову: курація сортує кандидатів за важливістю, тож
    єдиний нічний пост — це ТОП-подія прогону. Бонусом вона дістає найсильнішу
    доступну модель (rewrite_with_ai викликається з save_strong=count > 0,
    а для першого поста прогону count == 0).

    max_pick у curate_with_ai свідомо НЕ чіпаємо: пул кандидатів лишається
    денним (4), тобто вночі модель обирає найкраще з чотирьох, а не з двох.
    Невідібране в skipped не потрапляє й повертається кандидатом наступного
    прогону — застою немає.

    Вікно «ночі» береться з is_quiet_hour() навмисно: одне поняття ночі на
    весь проект. Зміна QUIET_*_HOUR автоматично рухає і звук, і обсяг.

    ЯК ВІДКОТИТИ: повернути `return MAX_POSTS_PER_RUN` першим рядком.
    """
    return 1 if is_quiet_hour() else MAX_POSTS_PER_RUN


def post_to_telegram(text, url, image_url=None, sources=None):
    full_text   = format_post_html(text, url, sources)
    valid_image = image_url and is_valid_image(image_url)
    quiet       = is_quiet_hour()   # вночі — без звуку, див. is_quiet_hour()

    # Значок «✅ Підтверджено N джерелами» додає ~28 символів, а підпис до фото
    # Telegram ріже на 1024 — перевищення це не обрізаний підпис, а ПОМИЛКА
    # запиту, тобто втрачений пост. Виміряно 08.08 по 139 постах із фото:
    # найдовший 1023 символи, 11 постів у зоні 996+. Тому для фото значок
    # ставимо лише якщо він влазить; текстовим постам (ліміт 4096) нічого не
    # загрожує.
    if valid_image and visible_len(full_text) > TG_CAPTION_LIMIT:
        full_text = format_post_html(text, url, sources, badge=False)
        if visible_len(full_text) > TG_CAPTION_LIMIT:
            # Не влазить навіть без значка. Раніше такий пост просто ГИНУВ:
            # sendPhoto повертав помилку, post_to_telegram — False, новина не
            # виходила взагалі. Тепер публікуємо її текстом (ліміт 4096) —
            # втрачаємо картинку, але не новину. Значок повертаємо: у
            # текстовому пості місця вистачає.
            full_text, valid_image = format_post_html(text, url, sources), False
            print("🖼→📝 Підпис довший за 1024 — пост іде без картинки")

    if valid_image:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            json={"chat_id": CHANNEL_ID, "photo": image_url,
                  "caption": full_text, "parse_mode": "HTML",
                  "disable_notification": quiet}
        )
    else:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHANNEL_ID, "text": full_text,
                  "parse_mode": "HTML", "disable_web_page_preview": False,
                  "disable_notification": quiet}
        )

    if response.status_code == 200:
        print(f"✅ {'🖼' if valid_image else '📝'}{' 🔕' if quiet else ''} {url}")
        # message_id потрібен дайджесту для прямого посилання на пост.
        try:
            return response.json()["result"]["message_id"]
        except Exception:
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
    # COALESCE(posted_title, title): моделі показуємо те, що РЕАЛЬНО вийшло
    # в канал (заголовок нашого поста), а не RSS-заголовок одного з джерел —
    # так їй легше впізнати «цю подію ми вже висвітлили».
    rows = conn.execute(
        "SELECT COALESCE(posted_title, title) FROM published "
        "WHERE published_at >= ? "
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
   ОНОВЛЕННЯ події — теж вона: уточнена кількість збитих цілей чи постраждалих,
   «атака триває», ранкове зведення про ту саму нічну атаку, реакції на неї.
   Одна масована атака за ніч = ОДИН пост у каналі, хоч би скільки видань про
   неї писало і під якими кутами.
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

    # max_tokens 2400, не 400: reasoning-моделі (gpt-oss-120b, gemini-3.5)
    # спалюють токени на «міркування» ще ДО відповіді. При 400 кожен виклик
    # обривався (finish_reason=length), Groq відпадав — і курацію ЗАВЖДИ
    # робила найслабша модель ланцюга (gemma-31b), яка пропускала дублі.
    # Обірваний виклик = витрачена квота БЕЗ результату, тому стеля з запасом.
    raw = call_llm(prompt, max_tokens=2400, temperature=0.1)
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
        m["ts"] = max(m.get("ts") or 0, it.get("ts") or 0)
    # УСІ url/заголовки групи — щоб після публікації позначити опублікованими
    # КОЖНЕ джерело події, а не лише базове. Без цього URL-и решти джерел
    # лишалися «новими», і наступний прогін публікував ту саму подію знову з
    # іншим базовим джерелом (18.07 «7 ракет / 90 дронів» вийшла так 6 разів).
    # "source" тут — джерело САМЕ цього рядка групи, а не всієї події: у
    # published кожен URL має записатись зі своїм виданням, інакше звіт по
    # джерелах порахував би основне джерело стільки разів, скільки в групі
    # учасників.
    m["group"] = [{"url": items[i]["url"], "title": items[i]["title"],
                   "source": items[i].get("source")}
                  for i in idxs if items[i].get("url")]
    return m


# Мінімальна лексична схожість заголовків, щоб вважати їх ОДНІЄЮ подією.
# 0.15 (а не 0.5, як у merge_by_event) — навмисно низький: курація бачить
# перефрази, яких лексика не бачить, і ламати її правильні злиття не можна.
# Поріг підібрано на реальних даних (564 злиття за 27.07–03.08): усе, що
# нижче 0.15, — це різні події («блокада портів» + «удар по терміналу Нової
# пошти»), а вже з 0.15 починаються справжні пари («131 БПЛА» + «107 із 131»).
GROUP_MIN_SIM = 0.15


def split_unrelated_groups(items, groups):
    """Розбиває групи курації, всередині яких НЕ одна подія.

    Навіщо. Курація має відповідати «рядок = подія, номери через кому».
    Слабші моделі резервного ланцюга регулярно віддають ОДИН плаский список
    («3,7,1,9,2,5»), а парсер приймає будь-який рядок із цифр — і 6 різних
    новин злипаються в одну «подію». Наслідки бачив на бойових даних:
      • 21.07 (msg 5200) до новини про Федорова приклеїлась новина про
        можливу відставку Сирського — модель змішала їх і ВИГАДАЛА абзац
        про Залужного та спростування Генштабу, якого не існувало;
      • 02–03.08 удар по терміналу «Нової пошти» під Харковом і стрілянина
        по військових ТЦК в Одесі не вийшли в канал зовсім: їх поглинули
        чужі пости й позначили опублікованими.
    Тобто хибне злиття б'є двічі — і вигадкою в пості, і втраченою новиною.

    Що робимо. Базою беремо той самий елемент, який візьме merge_group
    (найдовший summary), і лишаємо в групі лише ті заголовки, що лексично
    схожі на базовий і не суперечать йому за містом. Решта стають окремими
    подіями ОДРАЗУ ПІСЛЯ своєї групи — тобто не зникають, а йдуть у чергу.

    Чому не боїмося «зайвого» розбиття. Якщо ми помилково розділили дві
    новини про одну подію, її зловлять наявні шари дедупу (лексичний по
    RSS-заголовку і лексичний + семантичний по ЗГЕНЕРОВАНОМУ заголовку
    перед публікацією) — ціна помилки: один зайвий виклик LLM. Ціна
    протилежної помилки — вигаданий пост у каналі й убита новина.
    """
    out = []
    for g in groups:
        if len(g) < 2:
            out.append(g)
            continue
        base = max(g, key=lambda i: len(items[i].get("summary") or ""))
        base_title = items[base]["title"]
        bw = _title_words(base_title)
        keep, split = [base], []
        for i in g:
            if i == base:
                continue
            w = _title_words(items[i]["title"])
            sim = len(bw & w) / len(bw | w) if (bw and w) else 0.0
            if sim >= GROUP_MIN_SIM and not _place_conflict(base_title,
                                                            items[i]["title"]):
                keep.append(i)
            else:
                print(f"✂️ Розділено групу (схожість {sim:.2f}): "
                      f"{base_title[:45]} ↮ {items[i]['title'][:45]}")
                split.append(i)
        out.append(keep)
        # Кожен відщеплений кандидат — окрема подія, одразу після своєї групи.
        out.extend([i] for i in split)
    return out


def main():
    conn  = init_db()
    news  = fetch_news(conn)
    count = 0
    skipped_cnt = 0
    bad_cnt = 0          # відповіді моделі, відсіяні як не-пост (is_bad_output)
    print(f"📥 Знайдено {len(news)} нових новин")

    # Курація: один виклик LLM відбирає події (дедуп + злиття + важливість).
    # Лексика цього не витягує — «на Сумщину»/«по Сумах» для неї різні події.
    groups = curate_with_ai(conn, news)
    if groups:
        # Курація помиляється зі злиттям (див. split_unrelated_groups):
        # перевіряємо її групи лексикою ПЕРЕД злиттям, інакше в один пост
        # потрапляють різні події, а «зайві» новини гинуть непоміченими.
        before_groups = len(groups)
        groups = split_unrelated_groups(news, groups)
        if len(groups) != before_groups:
            print(f"✂️ Групи курації перевірено: {before_groups} → {len(groups)} подій")
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
        if count >= max_posts_now():   # вночі — 1, удень — MAX_POSTS_PER_RUN
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

        # ОСТАННІЙ ЗАПОБІЖНИК: модель повернула не пост, а свої міркування,
        # відмову чи службову розмітку. До 03.08 така відповідь ішла в канал
        # як новина (msg 7442) — перевірялося лише слово SKIP на початку.
        bad = is_bad_output(post_text)
        if bad:
            print(f"🚫 Брак від моделі ({bad}): {item['title'][:50]}")
            mark_skipped(conn, item["url"], item["title"], "bad_output")
            bad_cnt += 1
            continue

        # Заголовок, який піде в канал — перший ЗМІСТОВНИЙ рядок поста.
        #
        # БАГ-013: раніше брався просто перший НЕПОРОЖНІЙ рядок. Якщо модель
        # ставила емодзі окремим рядком, у posted_title потрапляло саме «🚨»
        # (msg 5516, 7598, 9065, 9613 — приблизно раз на 4 дні). Шкода не в
        # самому пості: такий «заголовок» отруює базу дедупу, бо наступна
        # новина з тим самим емодзі-рядком була б тихо вбита як
        # duplicate_event. Тому шукаємо перший рядок, де є хоча б 3 літери.
        #
        # Якщо змістовного рядка немає взагалі — лишаємо None, а НЕ відкочуємось
        # на перший непорожній: краще порожній posted_title (дедуп і курація
        # мають фолбек COALESCE(posted_title, title) на RSS-заголовок), ніж
        # отруйний «🚨» у базі. Сам пост при цьому виходить у канал як є —
        # його якість — зона відповідальності is_bad_output вище.
        headline = next((ln.strip()[:200] for ln in post_text.splitlines()
                         if len(re.findall(r"[^\W\d_]", ln)) >= 3), None)

        # Дедуп по ЗГЕНЕРОВАНОМУ заголовку — ДО публікації. Бойова перевірка
        # 19.07 показала: перевірки RSS-заголовка недостатньо — різні видання
        # формулюють одну подію по-різному (Жаккар < 0.5), і подія виходила
        # повторно (Wildberries ×6, поїзд на Запоріжжі ×4). Заголовки ж НАШОЇ
        # моделі для однієї події близькі. Два шари: безкоштовний лексичний,
        # а якщо він мовчить — семантичний LLM (бачить перефрази).
        if headline and (is_duplicate_title(conn, headline)
                         or is_semantic_duplicate(conn, headline)):
            print(f"⏭ Дубль події (по заголовку поста): {headline[:50]}")
            # Скіпаємо ВСЮ злиту групу: інакше її URL-и повернуться
            # кандидатами наступного прогону і знову спалять виклики LLM.
            mark_skipped(conn, item["url"], item["title"], "duplicate_event")
            for g in item.get("group", []):
                if g["url"] != item["url"]:
                    mark_skipped(conn, g["url"], g["title"], "duplicate_event")
            continue

        msg_id = post_to_telegram(post_text, item["url"], item.get("image_url"),
                                  item.get("sources"))
        if msg_id:
            # headline зберігаємо в published.posted_title — по ньому
            # працюватиме дедуп наступних прогонів.
            mark_published(conn, item["url"], item["title"],
                           msg_id if isinstance(msg_id, int) else None,
                           posted_title=headline,
                           source=sources_label(item))
            # Позначаємо опублікованими й РЕШТУ джерел злитої групи: їхні URL
            # інакше повернулися б кандидатами вже наступного прогону, і та
            # сама подія вийшла б у канал повторно з іншим базовим джерелом.
            for g in item.get("group", []):
                if g["url"] != item["url"]:
                    mark_published(conn, g["url"], g["title"],
                                   posted_title=headline,
                                   source=g.get("source"))
            count += 1
            time.sleep(3)

    print(f"\n🏁 Опубліковано {count} постів.")
    if STATS["ok"]:
        # Дублюємо баланс моделей у stdout: у логах Actions видно, які
        # провайдери реально працюють (у Telegram-звіті це є, у логах не було).
        print("📈 Моделі: " + ", ".join(f"{k}×{v}" for k, v in STATS["ok"].items()))

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
        # Брак від моделі показуємо ОКРЕМО від SKIP: SKIP — це нормальне
        # редакційне рішення, а брак — збій, який треба помічати одразу.
        if bad_cnt:
            summary += f"\n🚫 Відсіяно як не-пост: {bad_cnt}"
        if STATS["ok"]:
            summary += "\n✅ Моделі: " + ", ".join(f"{k}×{v}" for k, v in STATS["ok"].items())
        if STATS["err"]:
            summary += "\n⚠️ Помилки: " + "; ".join(f"{k}: {v}" for k, v in STATS["err"].items())
        notify_admin(summary)

    conn.close()

if __name__ == "__main__":
    main()
