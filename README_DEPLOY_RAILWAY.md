# ChessMeet — Railway deploy guide

## 1. Что меняется после Railway

На локальном Cloudflare Tunnel ссылка часто меняется. Из-за этого после перезапуска приходилось писать `/start`, чтобы Telegram получил новую кнопку Mini App.

На Railway будет постоянный URL. После настройки `WEBAPP_URL` кнопка Mini App будет вести на один и тот же адрес, и `/start` после каждого перезапуска больше не нужен.

## 2. Рекомендуемая структура

Залей в GitHub только проектные файлы:

```text
src/
webapp/
requirements.txt
railway.toml
Procfile
run.sh
README.md
README_DEPLOY_RAILWAY.md
daily_puzzles_lichess_mate1_verified_300.json
```

Не коммить:

```text
.env
.venv/
*.sqlite3
build/
dist/
*.exe
```

## 3. Railway Variables

В Railway → Service → Variables добавь:

```env
BOT_TOKEN=...
WEBAPP_URL=https://your-service.up.railway.app
BOT_USERNAME=chessmeetbot
BOT_MODE=polling
DEV_MODE=false
DATABASE_PATH=/data/chess_irl.sqlite3
DEFAULT_CITY=Минск
ADMIN_TOKEN=long_random_secret
```

## 4. SQLite Volume

Для MVP можно оставить SQLite, но нужен persistent Volume.

В Railway создай Volume и смонтируй его в:

```text
/data
```

Тогда база будет жить здесь:

```text
/data/chess_irl.sqlite3
```

Без volume база может потеряться при redeploy/restart.

## 5. Deploy

Railway запускает приложение командой из `railway.toml`:

```bash
uvicorn src.main:app --host 0.0.0.0 --port $PORT
```

После deploy проверь:

```text
https://your-service.up.railway.app/health
```

Ожидаемый ответ:

```json
{
  "ok": true,
  "version": "0.13.0",
  "railway_ready": true
}
```

## 6. Telegram Mini App button

После Railway:

1. Убедись, что `WEBAPP_URL` в Railway Variables равен Railway URL.
2. Перезапусти service.
3. Один раз отправь боту `/start`.

Дальше кнопка будет постоянной.

## 7. Admin API

Remote admin endpoints защищены заголовком:

```text
X-Admin-Token: твой_ADMIN_TOKEN
```

Примеры:

```text
GET /api/admin/health
GET /api/admin/users
GET /api/admin/games
GET /api/admin/reports
GET /api/admin/puzzles
POST /api/admin/broadcast
POST /api/admin/users/{telegram_id}/block
POST /api/admin/users/{telegram_id}/unblock
POST /api/admin/games/{game_id}/cancel
```

## 8. Сброс серии задачки

В v0.13.0 серия проверяется по 00:00 МСК:

- решил сегодня → серия активна;
- решил вчера → серия ещё активна, можно продлить сегодня;
- последний решённый день старше вчера → текущая серия сбрасывается в 0;
- лучший рекорд и общее число решённых задач не сбрасываются.

## Restore old local SQLite database to Railway

If the Railway bot starts with empty data, your old SQLite database is still local on your computer. Use the restore tool:

1. Make sure your Railway service has `ADMIN_TOKEN` and `DATABASE_PATH=/data/chess_irl.sqlite3`.
2. Make sure a Railway Volume is mounted at `/data`.
3. Keep your old local `chess_irl.sqlite3` file safe.
4. Run:

```text
RESTORE_LOCAL_DB_TO_RAILWAY.bat
```

Enter:

```text
Railway/Admin API URL: https://your-service.up.railway.app
ADMIN_TOKEN: your ADMIN_TOKEN
Local SQLite DB path: C:\path\to\old\chess_irl.sqlite3
```

Type `RESTORE` to confirm. The server will create a backup of the existing remote DB before replacing it.

To download a Railway DB backup to your computer, run:

```text
BACKUP_RAILWAY_DB_TO_LOCAL.bat
```

The remote admin client also has buttons: `DB Info`, `Backup DB`, `Restore DB`.
