import os
import hashlib
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


def user_pseudonym(user_id):
    # Незворотний псевдонім замість Telegram-id.
    #
    # ЧОМУ СІЛЛЮ Є ТОКЕН: published.db комітиться в ПУБЛІЧНИЙ репозиторій.
    # Простий sha256(user_id) там не захищає нічого: простір Telegram-id —
    # це десяткові числа, їх перебирають повністю за хвилини, тобто хеш без
    # солі відновлюється до вихідного id тривіально. Сіль має бути секретом,
    # якого немає в репозиторії, — беремо вже наявний FEEDBACK_BOT_TOKEN
    # (лежить у GitHub Secrets), щоб власнику не треба було заводити новий.
    #
    # НАСЛІДОК РОТАЦІЇ: якщо токен колись перевипустять, псевдоніми зміняться
    # і той самий підписник стане «новим». Для нашої мети (бачити, чи це те
    # саме джерело фідбеку в межах періоду) це прийнятно.
    if user_id is None:
        return None
    return hashlib.sha256(f"{FEEDBACK_TOKEN}:{user_id}".encode()).hexdigest()[:16]


def init_feedback_db(conn):
    # Таблиця для збереження повідомлень підписників.
    # update_id як PRIMARY KEY + INSERT OR IGNORE = захист від повторів:
    # якщо скрипт впаде після запису, але до save_offset, повторний прогін
    # не створить дубль рядка (ідемпотентно, як і решта бота).
    #
    # ⚠️ ПРИВАТНІСТЬ: цей файл БД комітиться у публічний репозиторій, тому
    # особистих даних підписника тут не зберігаємо — ні user_id, ні @username,
    # ні імені. Ідентичність потрібна лише адміну, і вона йому йде окремим
    # каналом (пересилання в Telegram у forward_to_admin), який у git не
    # потрапляє. Тут лишається незворотний user_hash — рівно стільки, щоб
    # відрізнити «двоє різних людей» від «одна людина написала двічі».
    cols = {r[1] for r in conn.execute("PRAGMA table_info(feedback)")}

    if not cols:
        conn.execute("""
            CREATE TABLE feedback (
                update_id   INTEGER PRIMARY KEY,  -- унікальний номер оновлення Telegram
                user_hash   TEXT,                 -- незворотний псевдонім (див. user_pseudonym)
                text        TEXT,                 -- текст повідомлення
                received_at TEXT                  -- час отримання (UTC, ISO)
            )
        """)
    elif "user_hash" not in cols:
        # Міграція зі старої схеми (user_id/username/name). Робиться один раз,
        # автоматично, як і решта міграцій проекту. Наявні рядки НЕ втрачаються:
        # текст і час переносяться, а id перетворюється на псевдонім — після
        # чого стовпці з особистими даними зникають із файлу разом зі старою
        # таблицею. ALTER TABLE тут не досить: він додав би user_hash, але
        # лишив би user_id/username/name на місці, тобто діру відкритою.
        conn.execute("""
            CREATE TABLE feedback_new (
                update_id   INTEGER PRIMARY KEY,
                user_hash   TEXT,
                text        TEXT,
                received_at TEXT
            )
        """)
        for update_id, user_id, text, received_at in conn.execute(
            "SELECT update_id, user_id, text, received_at FROM feedback"
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO feedback_new "
                "(update_id, user_hash, text, received_at) VALUES (?, ?, ?, ?)",
                (update_id, user_pseudonym(user_id), text, received_at),
            )
        conn.execute("DROP TABLE feedback")
        conn.execute("ALTER TABLE feedback_new RENAME TO feedback")
        conn.commit()
        # DROP TABLE звільняє сторінки, але НЕ витирає їх: старі значення
        # лишаються у файлі як вільне місце й читаються шістнадцятковим
        # редактором. Для БД, яку комітять у публічний репозиторій, це те саме,
        # що не видаляти. VACUUM переписує файл начисто. Робиться лише тут,
        # у гілці міграції (один раз за життя бази), а не на кожному прогоні.
        conn.execute("VACUUM")

    conn.commit()


def log_feedback(conn, update_id, user, text):
    # Записуємо одне повідомлення підписника в БД. Зберігаємо все за весь час:
    # це цінний сигнал для продукту, а обсяг мізерний.
    #
    # ⚠️ Сам ТЕКСТ лишається як є — він і є сигнал, заради якого таблиця
    # існує. Якщо підписник напише в ньому свій телефон чи адресу, вони
    # потраплять у публічний репозиторій. Захистити це кодом не можна;
    # прибирати такий рядок треба руками (і пам'ятати, що історія git
    # зберігає видалене).
    conn.execute(
        "INSERT OR IGNORE INTO feedback "
        "(update_id, user_hash, text, received_at) "
        "VALUES (?, ?, ?, ?)",
        (
            update_id,
            user_pseudonym(user.get("id")),
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
