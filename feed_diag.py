"""Детальна діагностика RSS-джерел: чому кожен фід порожній.

ВАЖЛИВО: запускати НА раннері GitHub (окремий воркфлоу feed_diag.yml), бо
блокування, схоже, по IP сервера. З домашнього комп'ютера ті самі фіди можуть
відкриватись нормально — і проблему не буде видно.

Для кожного джерела показує: HTTP-статус, кількість записів, або тип помилки
(таймаут / з'єднання / 200-без-RSS = ймовірно Cloudflare-заглушка).
"""
import requests
import feedparser
from bot import RSS_FEEDS, notify_admin, FEED_UA


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


def main():
    lines = [f"{diag_one(f['url'])} — {f['url']}" for f in RSS_FEEDS]
    report = "🔬 Діагностика RSS (з IP сервера GitHub):\n" + "\n".join(lines)
    print(report)
    # звіт довгий — ріжемо на шматки в межах ліміту Telegram
    for i in range(0, len(report), 3500):
        notify_admin(report[i:i + 3500])


if __name__ == "__main__":
    main()
