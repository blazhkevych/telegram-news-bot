import os
import requests
import re
import sqlite3
from datetime import date
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
DB_PATH        = "published.db"   # той самий файл, що комітиться воркфлоу


def _ensure_log():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS war_stats_log (
            den TEXT PRIMARY KEY, image_url TEXT
        )
    """)
    conn.commit()
    return conn


def already_posted(image_url):
    """True, якщо статистику вже постили сьогодні або цю саму картинку раніше."""
    conn  = _ensure_log()
    today = date.today().isoformat()
    if conn.execute("SELECT 1 FROM war_stats_log WHERE den=?", (today,)).fetchone():
        conn.close()
        return True
    if conn.execute("SELECT 1 FROM war_stats_log WHERE image_url=?", (image_url,)).fetchone():
        conn.close()
        return True
    conn.close()
    return False


def mark_posted(image_url):
    conn  = _ensure_log()
    today = date.today().isoformat()
    conn.execute("INSERT OR REPLACE INTO war_stats_log VALUES (?,?)", (today, image_url))
    conn.commit()
    conn.close()

def fetch_losses_image():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(
            "https://www.zsu.gov.ua/oriientovni-vtraty-protyvnyka",
            headers=headers, timeout=15
        )

        # Next.js зберігає дані в __NEXT_DATA__ JSON в HTML
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
        if match:
            import json
            data = json.loads(match.group(1))
            # Конвертуємо в рядок і шукаємо kill-statistic
            data_str = json.dumps(data)
            urls = re.findall(r'(https://s3-bucket\.mil\.gov\.ua[^"]+kill-statistic[^"]+\.webp)', data_str)
            if urls:
                print(f"✅ Знайдено в NEXT_DATA: {urls[0][:80]}")
                return urls[0]

        # Запасний варіант — шукаємо напряму в HTML
        urls = re.findall(r'(https://s3-bucket\.mil\.gov\.ua[^"\'\\]+kill-statistic[^"\'\\]+\.webp)', r.text)
        if urls:
            print(f"✅ Знайдено в HTML: {urls[0][:80]}")
            return urls[0]

        # Шукаємо encoded URL
        from urllib.parse import unquote
        encoded = re.findall(r's3-bucket\.mil\.gov\.ua%2F[^"\'&]+kill-statistic[^"\'&]+\.webp', r.text)
        if encoded:
            url = "https://" + unquote(encoded[0])
            print(f"✅ Знайдено encoded: {url[:80]}")
            return url

        print("🔍 Дебаг: шукаємо будь-що з mil.gov.ua...")
        mil_urls = re.findall(r's3-bucket\.mil\.gov\.ua[^"\'&\s]+', r.text)
        for u in mil_urls[:3]:
            print(f"   → {u[:100]}")

    except Exception as e:
        print(f"⚠️ Помилка: {e}")
    return None

def post_image_to_telegram(image_url):
    """Публікує картинку в Telegram канал."""
    caption = "📊 *Орієнтовні втрати противника*\n\nДжерело: Генеральний штаб ЗСУ"

    # Спробуємо спочатку як URL
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        json={
            "chat_id":    CHANNEL_ID,
            "photo":      image_url,
            "caption":    caption,
            "parse_mode": "Markdown",
        }
    )

    if r.status_code == 200:
        print("✅ Статистику опубліковано")
        return True

    # Якщо не вийшло — завантажуємо і надсилаємо як файл
    print(f"⚠️ Пробуємо завантажити файл...")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_data = requests.get(image_url, headers=headers, timeout=15).content
        r2 = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": ("stats.webp", img_data, "image/webp")}
        )
        if r2.status_code == 200:
            print("✅ Статистику опубліковано (файл)")
            return True
        print(f"❌ Telegram: {r2.text}")
    except Exception as e:
        print(f"❌ Помилка завантаження: {e}")
    return False

def main():
    print("📊 Збираємо картинку втрат...")
    image_url = fetch_losses_image()

    if not image_url:
        print("⚠️ Картинку не знайдено")
        return

    if already_posted(image_url):
        print("⏭ Статистику вже опубліковано сьогодні — пропускаємо.")
        return

    print(f"✅ Знайдено: {image_url[:80]}...")
    if post_image_to_telegram(image_url):
        mark_posted(image_url)

if __name__ == "__main__":
    main()
