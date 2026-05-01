# ChessMeet v0.13.1 — Railway Ready audit

## Проверено

- Python-синтаксис `src/*.py`, launcher и remote admin client.
- JavaScript-синтаксис `webapp/app.js`.
- Версии в backend/frontend обновлены до `0.13.1`.
- Добавлены Railway deploy files.
- Добавлены защищённые admin endpoints.
- Исправлена логика сброса серии задачки по 00:00 МСК.

## Новое

- `railway.toml`
- `Procfile`
- `README_DEPLOY_RAILWAY.md`
- `chessmeet_remote_admin.py`
- `RUN_REMOTE_ADMIN_CLIENT.bat`
- remote admin API: `/api/admin/*`

## Важно

Для Railway обязательно установи:

```env
DEV_MODE=false
DATABASE_PATH=/data/chess_irl.sqlite3
ADMIN_TOKEN=long_random_secret
```

И подключи Railway Volume на `/data`, если остаёшься на SQLite.
