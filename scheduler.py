import subprocess
from datetime import datetime
import pytz

KYIV = pytz.timezone("Europe/Kiev")
now  = datetime.now(KYIV)
hour = now.hour

print(f"🕐 Київський час: {now.strftime('%H:%M')} ({now.strftime('%Z')})")

if hour == 6:
    print("🌅 Запускаємо ранковий дайджест...")
    subprocess.run(["python", "digest.py"], env={**__import__('os').environ, "DIGEST_TYPE": "morning"})
    print("📊 Запускаємо статистику втрат...")
    subprocess.run(["python", "war_stats.py"])

elif hour == 21:
    print("🌙 Запускаємо вечірній підсумок...")
    subprocess.run(["python", "digest.py"], env={**__import__('os').environ, "DIGEST_TYPE": "evening"})

elif hour in (7, 9, 11, 13, 16, 19):
    print("📰 Запускаємо збір новин...")
    subprocess.run(["python", "bot.py"])

else:
    print(f"😴 Година {hour}:00 — нічого не запускаємо.")
