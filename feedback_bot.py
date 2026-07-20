import os
import sqlite3
from datetime import datetime, timezone
import requests

FEEDBACK_TOKEN = os.environ["FEEDBACK_BOT_TOKEN"]
ADMIN_ID       = os.environ["ADMIN_CHAT_ID"]
OFFSET_FILE    = "feedback_offset.txt"
DB_FILE        = "published.db"   # той самий стан-файл, що вже комітиться назад воркфлоу


def get_offset():
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    except:
        return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def init_feedback_db(conn):
    # Таблиця для збереження повідомлень підписників.
    # update_id як PRIMARY KEY + INSERT OR IGNORE = захист від повторів:
    # якщо скрипт впаде після запису, але до save_offset, повторний прогін
    # не створить дубль рядка (ідемпотентно, як і решта бота).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            update_id   INTEGER PRIMARY KEY,  -- унікальний номер оновлення Telegram
            user_id     INTEGER,              -- Telegram-id підписника
            username    TEXT,                 -- @username (може бути порожнім)
            name        TEXT,                 -- ім'я підписника
            text        TEXT,                 -- текст повідомлення
            received_at TEXT                  -- час отримання (UTC, ISO)
        )
    """)
    conn.commit()


def log_feedback(conn, update_id, user, text):
    # Записуємо одне повідомлення підписника в БД. Зберігаємо все за весь час:
    # це цінний сигнал для продукту, а обсяг мізерний.
    conn.execute(
        "INSERT OR IGNORE INTO feedback "
        "(update_id, user_id, username, name, text, received_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            update_id,
            user.get("id"),
            user.get("username", ""),
            user.get("first_name", "Анонім"),
            text,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get_updates(offset):
    r = requests.get(
        f"https://api.telegram.org/bot{FEEDBACK_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 10}
    )
    return r.json().get("result", [])


def forward_to_admin(update, conn=None):
    msg = update.get("message", {})
    if not msg:
        return
    user     = msg.get("from", {})
    username = user.get("username", "")
    name     = user.get("first_name", "Анонім")
    text     = msg.get("text", "")
    if not text:
        return

    # Спершу зберігаємо повідомлення в БД (щоб воно не загубилось, навіть якщо
    # пересилання в Telegram раптом не пройде), потім пересилаємо адміну.
    if conn is not None:
        log_feedback(conn, update.get("update_id"), user, text)

    user_info = f"@{username}" if username else f"{name} (id: {user.get('id')})"
    forward_text = (
        f"📩 Повідомлення від підписника\n"
        f"👤 {user_info}\n\n"
        f"💬 {text}"
    )
    requests.post(
        f"https://api.telegram.org/bot{FEEDBACK_TOKEN}/sendMessage",
        json={"chat_id": ADMIN_ID, "text": forward_text}
    )
    # Підтвердження підписнику
    requests.post(
        f"https://api.telegram.org/bot{FEEDBACK_TOKEN}/sendMessage",
        json={
            "chat_id": msg["chat"]["id"],
            "text": "✅ Дякуємо! Ваше повідомлення отримано редакцією UA News."
        }
    )


def main():
    conn = sqlite3.connect(DB_FILE)
    init_feedback_db(conn)

    offset  = get_offset()
    updates = get_updates(offset)
    for update in updates:
        forward_to_admin(update, conn)
        offset = update["update_id"] + 1
    if updates:
        save_offset(offset)

    conn.close()


if __name__ == "__main__":
    main()
