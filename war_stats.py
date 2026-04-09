import os
import requests
import re
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]

def fetch_losses_image():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get("https://www.zsu.gov.ua/oriientovni-vtraty-protyvnyka",
                        headers=headers, timeout=15)
        
        # Шукаємо S3 URL в HTML напряму через regex
        matches = re.findall(
            r's3-bucket\.mil\.gov\.ua[^"\'&]+kill-statistic[^"\'&]+\.webp',
            r.text
        )
        
        if matches:
            # Декодуємо URL якщо є %2F тощо
            from urllib.parse import unquote
            url = unquote(matches[0])
            full_url = f"https://{url}"
            print(f"✅ Знайдено: {full_url[:80]}...")
            return full_url
            
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

    print(f"✅ Знайдено: {image_url[:80]}...")
    post_image_to_telegram(image_url)

if __name__ == "__main__":
    main()
