import os
import sqlite3
import subprocess
from datetime import datetime, date
import pytz

# «Живий режим»: воркфлоу запускається часто (кожні ~15 хв), тож:
#   • новини — щоразу (bot.py сам постить по кілька свіжих і не дублює);
#   • дайджести й статистика втрат — РАЗ НА ДОБУ (захист від повторів нижче).

KYIV    = pytz.timezone("Europe/Kiev")
now     = datetime.now(KYIV)
hour    = now.hour
DB_PATH = "published.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS daily_log (den TEXT, kind TEXT, PRIMARY KEY(den, kind))"
    )
    conn.commit()
    return conn


def done_today(kind):
    conn = _conn()
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT 1 FROM daily_log WHERE den=? AND kind=?", (today, kind)
    ).fetchone()
    conn.close()
    return bool(row)


def mark_today(kind):
    conn = _conn()
    today = date.today().isoformat()
    conn.execute("INSERT OR IGNORE INTO daily_log VALUES (?, ?)", (today, kind))
    conn.commit()
    conn.close()


def run(script, extra_env=None):
    env = {**os.environ, **(extra_env or {})}
    subprocess.run(["python", script], env=env)


print(f"🕐 Київський час: {now.strftime('%H:%M')} ({now.strftime('%Z')})")

# 1) Новини — щоразу
print("📰 Збір новин...")
run("bot.py")

# 2) Статистика втрат — одна спроба на добу (від 6:00)
if hour >= 6 and not done_today("war_stats"):
    print("📊 Статистика втрат...")
    run("war_stats.py")
    mark_today("war_stats")

# 3) Ранковий дайджест — раз на добу, перший запуск від 6:00
if 6 <= hour < 21 and not done_today("morning"):
    print("🌅 Ранковий дайджест...")
    run("digest.py", {"DIGEST_TYPE": "morning"})
    mark_today("morning")

# 4) Вечірній підсумок — раз на добу, від 21:00
if hour >= 21 and not done_today("evening"):
    print("🌙 Вечірній підсумок...")
    run("digest.py", {"DIGEST_TYPE": "evening"})
    mark_today("evening")
