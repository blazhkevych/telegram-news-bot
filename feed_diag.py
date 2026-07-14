"""Детальна діагностика RSS-джерел: чому кожен фід порожній + перевірка КАНДИДАТІВ.

ВАЖЛИВО: запускати НА раннері GitHub (воркфлоу feed_diag.yml), бо картина
залежить від сервера. Для кожного джерела показує HTTP-статус, кількість
записів, або тип помилки (таймаут / з'єднання / 200-без-RSS = Cloudflare).

CANDIDATES — список НОВИХ/виправлених адрес на перевірку. Живих (✅) я потім
впишу в bot.py RSS_FEEDS. Тут вони НЕ впливають на бойовий бот.
"""
import requests
import feedparser
from bot import RSS_FEEDS, notify_admin, FEED_UA

# Кандидати: виправлені адреси мертвих + нові поважні видання + Google News.
CANDIDATES = [
    # --- виправлення мертвих ---
    "https://rss.unian.net/site/news_ukr.rss",          # УНІАН (правильна адреса)
    "https://suspilne.media/rss/all.rss",               # Suspilne (спроба)
    "https://www.liga.net/news/all/rss.xml",            # LIGA (без biz-піддомену)
    "https://ua.korrespondent.net/rss/",                # Корреспондент (спроба)
    # --- нові поважні українські ---
    "https://hromadske.ua/feed",                        # Громадське
    "https://censor.net/ua/includes/news_uk.xml",       # Цензор.НЕТ
    "https://lb.ua/rss/ukr/news.xml",                   # LB.ua
    "https://zn.ua/ukr/rss/",                           # Дзеркало тижня
    "https://www.eurointegration.com.ua/rss/",          # Європейська правда
    "https://www.epravda.com.ua/rss/",                  # Економічна правда
    "https://glavcom.ua/rss/rss.xml",                   # Главком
    # --- надійний агрегатор ---
    "https://news.google.com/rss?hl=uk&gl=UA&ceid=UA:uk",  # Google News Україна (топ)
    # --- світові ---
    "https://www.aljazeera.com/xml/rss/all.xml",        # Al Jazeera
    "https://www.theguardian.com/world/rss",            # The Guardian
    "https://www.euronews.com/rss",                     # Euronews
]


def diag_one(url):
    try:
        r = requests.get(url, headers={"User-Agent": FEED_UA}, timeout=20)
        code = r.status_code
        n = len(feedparser.parse(r.content).entries) if r.content else 0
        if code == 200 and n > 0:
            return f"✅ {n} записів"
        if code == 200 and n == 0:
            head = " ".join((r.text or "")[:80].split())
            return f"⚠️ 200 але 0 записів (не RSS?): {head}"
        return f"❌ HTTP {code}"
    except requests.exceptions.Timeout:
        return "❌ таймаут (>20с)"
    except requests.exceptions.ConnectionError as e:
        return f"❌ з'єднання: {str(e)[:50]}"
    except Exception as e:
        return f"❌ {type(e).__name__}: {str(e)[:50]}"


def _send(report):
    print(report)
    for i in range(0, len(report), 3500):
        notify_admin(report[i:i + 3500])


def main():
    cur = ["🔬 ПОТОЧНІ джерела (з IP сервера GitHub):"]
    cur += [f"{diag_one(f['url'])} — {f['url']}" for f in RSS_FEEDS]
    _send("\n".join(cur))

    cand = ["🧪 КАНДИДАТИ (нові/виправлені на перевірку):"]
    cand += [f"{diag_one(u)} — {u}" for u in CANDIDATES]
    _send("\n".join(cand))


if __name__ == "__main__":
    main()
