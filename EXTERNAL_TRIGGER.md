# Зовнішній тригер запусків (cron-job.org → GitHub API)

**Навіщо.** Вбудований `cron: '*/15 * * * *'` у `news_bot.yml` GitHub на
безкоштовному тарифі майже не тримає — заплановані запуски затримуються й
дропаються під навантаженням. Реально виходить ~7–10 запусків на добу з дірами
по 3–4 год замість ~90. Через це в каналі мало новин, а дайджести «сповзають».

**Рішення.** Зовнішній безкоштовний планувальник (cron-job.org) щопівгодини б'є
по GitHub REST API і запускає воркфлоу через `workflow_dispatch`. Це обходить
внутрішнє дропання GitHub. Вбудований `*/15` лишаємо як безкоштовний резерв —
`concurrency` у воркфлоу не дасть двом прогонам накластися.

---

## Крок 1. Створити токен (fine-grained PAT)

GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**:
- **Repository access:** Only select repositories → `blazhkevych/telegram-news-bot`.
- **Permissions → Repository permissions → Actions: Read and write.**
  (`Metadata: Read-only` додасться автоматично. Більше нічого не треба.)
- **Expiration:** на свій розсуд (напр. 1 рік; після спливу — перевипустити).
- Згенерувати, **скопіювати токен** (показується один раз).

⚠️ Токен = секрет. Він живе ТІЛЬКИ в налаштуваннях cron-job.org (крок 2).
Ніколи не комітити його в репо. Якщо витік — відкликати токен у GitHub і зробити новий.

---

## Крок 2. Завдання на cron-job.org

Зареєструватись на https://cron-job.org (безкоштовно) → **Create cronjob**:

- **Title:** `UA News trigger`
- **URL:**
  ```
  https://api.github.com/repos/blazhkevych/telegram-news-bot/actions/workflows/news_bot.yml/dispatches
  ```
- **Schedule:** кожні 30 хв (напр. хвилини `0` і `30`). Можна щогодини — тоді
  до ~24 прогонів/добу × до 3 постів = вистачить; 30 хв — «живіше».
- Розділ **Advanced / Request:**
  - **Request method:** `POST`
  - **Headers** (додати три):
    ```
    Accept: application/vnd.github+json
    Authorization: Bearer <ВСТАВ_СЮДИ_ТОКЕН>
    X-GitHub-Api-Version: 2022-11-28
    ```
  - **Request body:**
    ```
    {"ref":"main"}
    ```
- Зберегти. Увімкнути (Enabled).

**Перевірка:** у cron-job.org натиснути **Run now / Test run** → має бути
відповідь **HTTP 204 No Content** (це успіх для dispatch). За хвилину в
GitHub → Actions з'явиться новий прогон з тригером `workflow_dispatch`,
а у фідбек-боті — звичний підсумок «🤖 Збір новин…».

Якщо приходить **404** — перевір назву репо/файлу у URL і що токен має доступ
саме до цього репо. **401/403** — токен без права `Actions: write` або протух.

---

## Що це НЕ лагодить

- Якість переказу (БАГ-006) і Twitter 401 (БАГ-007) — окремо.
- «дані недоступні» для частини міст у погоді — окремий розбір `digest.py`.
- Латентне: у `scheduler.py` `mark_today("morning")` викликається навіть якщо
  `digest.py` впав (ранкові LLM-429) → дайджест може «згубитись» на добу.
  Розібрати в наступній сесії.
