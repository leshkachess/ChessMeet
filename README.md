# ChessMeet MVP v0.13.0 — Railway Ready

Telegram Mini App для поиска офлайн-шахматных партий в Минске.

## Что внутри

- Telegram Bot + Mini App.
- Заявки на офлайн-партии.
- Профили с фото, рейтингом, значками и настройками темы.
- Отклики, принятие заявки и чат между игроками.
- Напоминания о партии.
- Ежедневная задача «мат в 1» и серия.
- Система жалоб, no-show, блокировок и модерации.
- Админ API для удалённой админки после Railway.
- Подготовка к Railway deploy.

## Главное исправление v0.13.0

Серия задачки теперь нормализуется по 00:00 МСК:

- решил сегодня — серия активна;
- решил вчера — серия ещё активна, можно продлить сегодня;
- последний решённый день старше вчера — текущая серия сбрасывается в 0;
- лучший рекорд и общее число решённых задач сохраняются.

Проверка серии вызывается при входе пользователя, bootstrap, задачке дня, профиле, напоминаниях и admin health.

## Локальный запуск

```powershell
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

Проверка:

```text
http://localhost:8000/health
```

## Railway

См. подробную инструкцию:

```text
README_DEPLOY_RAILWAY.md
```

Ключевые файлы:

```text
railway.toml
Procfile
run.sh
.env.example
.gitignore
```

## Remote Admin

После Railway локальный launcher больше не должен читать SQLite-файл напрямую. Используй:

```text
chessmeet_remote_admin.py
RUN_REMOTE_ADMIN_CLIENT.bat
```

Он подключается к Railway URL через `ADMIN_TOKEN`.
