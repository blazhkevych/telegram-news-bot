import os
import requests
import sqlite3
from datetime import date

# Втрати противника — стабільний відкритий API (дані Генштабу ЗСУ, з приростом
# за добу). Раніше вишкрібали картинку з сайту ЗСУ — це ламалось; тепер JSON.

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
DB_PATH        = "published.db"
API_URL        = "https://russianwarship.rip/api/v2/statistics/latest"

# (підпис, ключ у API) — порядок = порядок у пості
ROWS = [
    ("👤 Особовий склад",          "personnel_units"),
    ("🛡 Танки",                   "tanks"),
    ("🚙 ББМ",                     "armoured_fighting_vehicles"),
    ("🎯 Артсистеми",              "artillery_systems"),
    ("🚀 РСЗВ",                    "mlrs"),
    ("🛰 Засоби ППО",              "aa_warfare_systems"),
    ("✈️ Літаки",                  "planes"),
    ("🚁 Гелікоптери",             "helicopters"),
    ("🛩 БпЛА",                    "uav_systems"),
    ("🚢 Кораблі/катери",          "warships_cutters"),
    ("🚀 Крилаті ракети",          "cruise_missiles"),
    ("🚛 Автотехніка й цистерни",  "vehicles_fuel_tanks"),
]


def _ensure_log():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS war_stats_log (den TEXT PRIMARY KEY, image_url TEXT)"
    )
    conn.commit()
    return conn


def already_posted_today():
    conn  = _ensure_log()
    today = date.today().isoformat()
    row = conn.execute("SELECT 1 FROM war_stats_log WHERE den=?", (today,)).fetchone()
    conn.close()
    return bool(row)


def mark_posted(tag):
    conn  = _ensure_log()
    today = date.today().isoformat()
    conn.execute("INSERT OR REPLACE INTO war_stats_log VALUES (?, ?)", (today, tag))
    conn.commit()
    conn.close()


def fmt(n):
    return f"{n:,}".replace(",", " ")   # 1420690 -> "1 420 690"


def build_message(data):
    s   = data["stats"]
    inc = data.get("increase", {})
    day = data.get("day", "")
    try:
        y, m, d = data["date"].split("-")
        date_fmt = f"{d}.{m}.{y}"
    except Exception:
        date_fmt = data.get("date", "")

    lines = [f"📊 *Орієнтовні втрати противника* — {date_fmt} ({day}-й день)\n"]
    for label, key in ROWS:
        val = s.get(key)
        if val is None:
            continue
        plus     = inc.get(key, 0)
        plus_str = f" (+{fmt(plus)})" if plus else ""
        lines.append(f"{label}: {fmt(val)}{plus_str}")
    lines.append("\nДжерело: Генеральний штаб ЗСУ")
    return "\n".join(lines)


def post(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHANNEL_ID, "text": text,
              "parse_mode": "Markdown", "disable_web_page_preview": True},
    )
    if r.status_code == 200:
        print("✅ Статистику втрат опубліковано")
        return True
    print(f"❌ Telegram: {r.text}")
    return False


def main():
    if already_posted_today():
        print("⏭ Статистику вже опубліковано сьогодні — пропускаємо.")
        return
    try:
        r = requests.get(API_URL, timeout=15)
        r.raise_for_status()
        data = r.json()["data"]
    except Exception as e:
        print(f"⚠️ API втрат недоступний: {e}")
        return

    if post(build_message(data)):
        mark_posted(data.get("date", date.today().isoformat()))


if __name__ == "__main__":
    main()
