"""Діагностика Gemini — НЕ чіпає бойовий bot.py.

Навіщо: bot.py логує 404 від Gemini лише як обрізаний «...generativelanguage.google».
Цей скрипт показує ПОВНУ відповідь Google, щоб зрозуміти справжню причину:
модель не існує / ключ невалідний / Generative Language API не увімкнено.

Запуск локально (ключ той самий, що в GitHub Secret GEMINI_API_KEY):

    # PowerShell
    $env:GEMINI_API_KEY="ваш_ключ"; python gemini_check.py
    # bash
    GEMINI_API_KEY=ваш_ключ python gemini_check.py
"""
import os
import requests

KEY = os.environ.get("GEMINI_API_KEY")
if not KEY:
    raise SystemExit("❌ Немає GEMINI_API_KEY в оточенні. Задай і запусти ще раз.")

BASE  = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-2.5-flash"  # той самий, що в bot.py

# 1) Список моделей — одразу видно, чи ключ і API взагалі робочі,
#    і чи є серед доступних потрібна модель.
print("=== 1) Доступні моделі для цього ключа ===")
r = requests.get(f"{BASE}/models", params={"key": KEY}, timeout=30)
print("HTTP", r.status_code)
if r.ok:
    names = [m.get("name", "") for m in r.json().get("models", [])]
    for n in names:
        print("   ", n)
    hit = [n for n in names if MODEL in n]
    print(f"\n   Модель '{MODEL}' у списку: {'ТАК' if hit else 'НІ'}")
else:
    print(r.text[:1000])

# 2) Той самий виклик, що робить bot.py (OpenAI-сумісний ендпоінт).
print("\n=== 2) Chat completion як у bot.py ===")
r = requests.post(
    f"{BASE}/openai/chat/completions",
    headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    json={"model": MODEL,
          "messages": [{"role": "user", "content": "Скажи 'ок' одним словом."}],
          "max_tokens": 20},
    timeout=30,
)
print("HTTP", r.status_code)
print(r.text[:1000])

print("\n--- Підказка ---")
print("404 у п.2, але 200 у п.1 → неправильна назва моделі (звір зі списком вище).")
print("404/403 у п.1 → API не ввімкнено або ключ не той проєкт "
      "(увімкни 'Generative Language API' у Google Cloud / AI Studio).")
