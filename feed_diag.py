"""Детальна діагностика RSS-джерел + перевірка КАНДИДАТІВ.

ВАЖЛИВО: запускати НА раннері GitHub (воркфлоу feed_diag.yml), бо картина
залежить від сервера. Для кожного джерела показує HTTP-статус, кількість
записів, або тип помилки (таймаут / з'єднання / 200-без-RSS = Cloudflare).

CANDIDATES — нові/виправлені адреси на перевірку. Живих (✅) потім впишу в
bot.py RSS_FEEDS. Тут вони НЕ впливають на бойовий бот.
"""
import requests
import feedparser
from bot import RSS_FEEDS, notify_admin, FEED_UA

CANDIDATES = [
    # Радіо Свобода — пряма RSS протухла, беремо через Google News (site-фільтр)
    "https://news.google.com/rss/search?q=when:1d+site:radiosvoboda.org&hl=uk&gl=UA&ceid=UA:uk",
    # DW українською — теж через Google News
    "https://news.google.com/rss/search?q=when:1d+site:dw.com&hl=uk&gl=UA&ceid=UA:uk",
    # ще світові
    "http://rss.cnn.com/rss/edition_world.rss",
    "https://news.google.com/rss/search?q=when:1d+site:reuters.com&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=when:1d+site:apnews.com&hl=en-US&gl=US&ceid=US:en",
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
