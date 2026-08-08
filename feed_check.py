"""Раз на добу перевіряє всі RSS-джерела на «живість» і шле звіт адміну.
Так ревізія джерел стає автоматичною: бот сам каже, який фід відвалився.

Друга частина звіту — скільки постів кожне джерело реально дало в канал за
7 діб. Дві половини відповідають на різні питання і разом розрізняють дві
причини мовчання джерела: «фід не віддає записів» (перша) і «записи є, але
відбір їх не пропускає» (друга). У сесії 16 ці причини змішались, і на
розділення пішли три сесії."""
from bot import RSS_FEEDS, notify_admin, parse_feed, init_db, source_report


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
        head = (f"🩺 Перевірка RSS: {len(dead)} із {total} мертвих/порожніх:\n"
                + "\n".join(dead))
        print(f"🩺 Мертвих фідів: {len(dead)}/{total}")
    else:
        head = f"🩺 Перевірка RSS: усі {total} джерел живі ✅"
        print(f"🩺 Усі {total} фідів живі")

    # Звіт по джерелах не має права завалити перевірку фідів: якщо база чомусь
    # недоступна, адмін усе одно мусить дізнатись про мертвий фід.
    try:
        conn = init_db()
        report = source_report(conn, days=7)
        conn.close()
        print(report)
    except Exception as e:
        report = f"⚠️ Звіт по джерелах не побудувався: {str(e)[:80]}"
        print(report)

    notify_admin(head + "\n\n" + report)


if __name__ == "__main__":
    main()
