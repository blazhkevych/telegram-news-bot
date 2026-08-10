import os
import sqlite3
import subprocess
from datetime import datetime, date, timedelta
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
    """Повертає True, якщо скрипт завершився успішно (код 0)."""
    env = {**os.environ, **(extra_env or {})}
    return subprocess.run(["python", script], env=env).returncode == 0


# ─── Сторож графіка ───────────────────────────────────────────────────────
# За добу 09–10.08 канал зробив 123 прогони: 96 із них — workflow_dispatch від
# ЗОВНІШНЬОГО пінгера (EXTERNAL_TRIGGER.md) і лише 27 — власний cron GitHub,
# який б'є врозбіг (09:22, 10:04, 10:51…). Тобто рівний графік «кожні 15 хв»
# тримає один зовнішній сервіс, і його зупинка не має ЖОДНОГО сигналу: канал
# просто почне виходити раз на годину, а виглядатиме це як «мало новин».
# Ловимо це так: щоразу пишемо час прогону, і якщо від попереднього минуло
# більше MAX_GAP_MIN — шлемо адміну рядок. Прогони GitHub-cron (~1 на годину)
# лишаються навіть при мертвому пінгері, тож саме вони й піднімуть тривогу.
MAX_GAP_MIN = 40   # 15 хв графіка × 2 + запас на чергу concurrency


def watch_schedule():
    conn = _conn()
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    row  = conn.execute("SELECT v FROM meta WHERE k='last_run'").fetchone()
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_run', ?)",
                 (now.isoformat(),))
    conn.commit()
    conn.close()
    if not row:
        return                                   # перший прогін — нема з чим порівнювати
    try:
        prev = datetime.fromisoformat(row[0])
    except ValueError:
        return
    gap = (now - prev).total_seconds() / 60
    if gap <= MAX_GAP_MIN:
        return
    print(f"⚠️ Розрив у графіку: {gap:.0f} хв (норма ≤ {MAX_GAP_MIN})")
    token, chat = os.environ.get("FEEDBACK_BOT_TOKEN"), os.environ.get("ADMIN_CHAT_ID")
    if not (token and chat):
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat,
                  "text": f"⚠️ Прогонів не було {gap:.0f} хв "
                          f"(попередній {prev.strftime('%d.%m %H:%M')}, норма ≤ {MAX_GAP_MIN}). "
                          f"Найімовірніша причина — зупинився зовнішній тригер."},
            timeout=10)
    except Exception as e:
        print(f"⚠️ Не вдалося попередити про розрив: {e}")


print(f"🕐 Київський час: {now.strftime('%H:%M')} ({now.strftime('%Z')})")
watch_schedule()

# 1) Новини — щоразу
print("📰 Збір новин...")
run("bot.py")

# 2) Статистика втрат — одна спроба на добу (від 6:00)
if hour >= 6 and not done_today("war_stats"):
    print("📊 Статистика втрат...")
    if run("war_stats.py"):
        mark_today("war_stats")

# 2b) Перевірка RSS на «живість» — раз на добу (від 6:00)
if hour >= 6 and not done_today("feed_check"):
    print("🩺 Перевірка RSS...")
    if run("feed_check.py"):
        mark_today("feed_check")

# 3) Ранковий дайджест — раз на добу, перший запуск від 6:00
if 6 <= hour < 21 and not done_today("morning"):
    print("🌅 Ранковий дайджест...")
    if run("digest.py", {"DIGEST_TYPE": "morning"}):
        mark_today("morning")

# 4) Вечірній підсумок — раз на добу, від 21:00
if hour >= 21 and not done_today("evening"):
    print("🌙 Вечірній підсумок...")
    if run("digest.py", {"DIGEST_TYPE": "evening"}):
        mark_today("evening")
