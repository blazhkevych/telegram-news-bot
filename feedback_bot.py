import os
import requests

FEEDBACK_TOKEN = os.environ["FEEDBACK_BOT_TOKEN"]
ADMIN_ID       = os.environ["ADMIN_CHAT_ID"]
OFFSET_FILE    = "feedback_offset.txt"

def get_offset():
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))

def get_updates(offset):
    r = requests.get(
        f"https://api.telegram.org/bot{FEEDBACK_TOKEN}/getUpdates",
        params={"offset": offset, "timeout": 10}
    )
    return r.json().get("result", [])

def forward_to_admin(update):
    msg = update.get("message", {})
    if not msg:
        return
    user     = msg.get("from", {})
    username = user.get("username", "")
    name     = user.get("first_name", "Анонім")
    text     = msg.get("text", "")
    if not text:
        return

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
    offset  = get_offset()
    updates = get_updates(offset)
    for update in updates:
        forward_to_admin(update)
        offset = update["update_id"] + 1
    if updates:
        save_offset(offset)

if __name__ == "__main__":
    main()
