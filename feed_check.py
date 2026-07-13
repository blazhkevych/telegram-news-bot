"""Раз на добу перевіряє всі RSS-джерела на «живість» і шле звіт адміну.
Так ревізія джерел стає автоматичною: бот сам каже, який фід відвалився."""
from bot import RSS_FEEDS, notify_admin, parse_feed


def main():
    dead = []
    for f in RSS_FEEDS:
        try:
            d = parse_feed(f["url"])                 # той самий браузерний UA, що й у боті
            if not d.entries:                       # порожньо = не працює
                dead.append(f["url"])
        except Exception as e:
            dead.append(f"{f['url']} ({str(e)[:40]})")

    total = len(RSS_FEEDS)
    if dead:
        notify_admin(
            f"🩺 Перевірка RSS: {len(dead)} із {total} мертвих/порожніх:\n"
            + "\n".join(dead)
        )
        print(f"🩺 Мертвих фідів: {len(dead)}/{total}")
    else:
        notify_admin(f"🩺 Перевірка RSS: усі {total} джерел живі ✅")
        print(f"🩺 Усі {total} фідів живі")


if __name__ == "__main__":
    main()
