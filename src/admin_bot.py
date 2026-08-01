from __future__ import annotations

import asyncio
import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)


ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_API_URL = os.getenv("ADMIN_API_URL", os.getenv("WEBAPP_URL", "")).rstrip("/")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


def parse_ids(value: str) -> set[int]:
    result: set[int] = set()
    for item in (value or "").replace(";", ",").split(","):
        if item.strip().isdigit():
            result.add(int(item.strip()))
    return result


ADMIN_IDS = parse_ids(os.getenv("ADMIN_TELEGRAM_IDS", ""))
OWNER_IDS = parse_ids(os.getenv("ADMIN_OWNER_IDS", "")) or set(ADMIN_IDS)


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        user = data.get("event_from_user")
        if not user or user.id not in ADMIN_IDS:
            if isinstance(event, Message):
                await event.answer("⛔ Доступ запрещён.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещён", show_alert=True)
            return None
        data["admin_role"] = "owner" if user.id in OWNER_IDS else "moderator"
        return await handler(event, data)


class ApiError(RuntimeError):
    pass


class AdminApi:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def _request_sync(
        self,
        method: str,
        path: str,
        actor_id: int,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "X-Admin-Token": self.token,
                "X-Admin-Actor": str(actor_id),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"API {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"API недоступен: {exc.reason}") from exc

    async def request(
        self,
        method: str,
        path: str,
        actor_id: int,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        return await asyncio.to_thread(self._request_sync, method, path, actor_id, payload)

    async def get(self, path: str, actor_id: int) -> Any:
        return await self.request("GET", path, actor_id)

    async def post(self, path: str, actor_id: int, payload: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, actor_id, payload or {})

    def _download_sync(self, path: str, actor_id: int) -> bytes:
        request = urllib.request.Request(
            self.base_url + path,
            headers={"X-Admin-Token": self.token, "X-Admin-Actor": str(actor_id)},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()

    async def download(self, path: str, actor_id: int) -> bytes:
        return await asyncio.to_thread(self._download_sync, path, actor_id)


api = AdminApi(ADMIN_API_URL, ADMIN_TOKEN)
router = Router()
router.message.middleware(AdminOnlyMiddleware())
router.callback_query.middleware(AdminOnlyMiddleware())


class AdminForm(StatesGroup):
    user_search = State()
    broadcast_text = State()
    broadcast_confirm = State()


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def main_menu() -> InlineKeyboardMarkup:
    return keyboard([
        [("📊 Статистика", "menu:stats"), ("👥 Пользователи", "menu:users")],
        [("🎮 Заявки", "menu:games"), ("🚨 Жалобы", "menu:reports")],
        [("🏙 Новые города", "menu:city_requests"), ("📍 Партнёры", "menu:partners")],
        [("🗺 Карты", "menu:map_reports"), ("🧩 Задачи", "menu:puzzles")],
        [("📣 Рассылка", "menu:broadcast"), ("🗄 Backup", "menu:backup")],
        [("🩺 Система", "menu:system"), ("📜 Аудит", "menu:audit")],
    ])


def back_menu() -> InlineKeyboardMarkup:
    return keyboard([[("← Главное меню", "menu:home")]])


def user_name(user: dict[str, Any]) -> str:
    return user.get("display_name") or user.get("first_name") or user.get("username") or "Игрок"


@router.message(CommandStart())
@router.message(Command("admin"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    role = "владелец" if message.from_user.id in OWNER_IDS else "модератор"
    await message.answer(
        f"♜ <b>ChessMeet Admin</b>\nРоль: {role}\n\nВыбери раздел:",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("♜ <b>ChessMeet Admin</b>\n\nВыбери раздел:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:stats")
async def stats(callback: CallbackQuery):
    snapshot = await api.get("/api/admin/snapshot", callback.from_user.id)
    reports = await api.get("/api/admin/reports?limit=500", callback.from_user.id)
    users = snapshot.get("users", [])
    games = snapshot.get("games", [])
    open_reports = sum(item.get("status", "open") == "open" for item in reports.get("reports", []))
    open_games = sum(item.get("status") in {"open", "pending"} for item in games)
    referral = snapshot.get("referral", {})
    analytics = snapshot.get("analytics", {})
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Пользователи: <b>{len(users)}</b>\n"
        f"Всего заявок: <b>{len(games)}</b>\n"
        f"Активные заявки: <b>{open_games}</b>\n"
        f"Открытые жалобы: <b>{open_reports}</b>\n"
        f"Рефералы: <b>{referral.get('activated', 0)}</b> активных / {referral.get('registered', 0)} переходов\n\n"
        f"<b>Последние 7 дней</b>\n"
        f"Новые пользователи: <b>{analytics.get('new_users_7d', 0)}</b>\n"
        f"Активные пользователи: <b>{analytics.get('active_users_7d', 0)}</b>\n"
        f"Новые заявки: <b>{analytics.get('games_7d', 0)}</b>\n"
        f"Завершённые встречи всего: <b>{analytics.get('completed_games', 0)}</b>"
    )
    await callback.message.edit_text(text, reply_markup=back_menu())
    await callback.answer()


def compact_cell(value: Any, width: int) -> str:
    text = str(value or "—").replace("\n", " ").strip()
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


async def render_users_table(
    callback: CallbackQuery,
    page: int = 0,
    sort: str = "newest",
    status: str = "all",
    city: str = "",
) -> None:
    page_size = 8
    page = max(0, page)
    data = await api.get(
        f"/api/admin/users?limit={page_size}&offset={page * page_size}"
        f"&sort={urllib.parse.quote(sort)}&status={urllib.parse.quote(status)}"
        f"&city={urllib.parse.quote(city)}",
        callback.from_user.id,
    )
    users = data.get("users", [])
    total = int(data.get("total", 0))
    last_page = max(0, (total - 1) // page_size)
    page = min(page, last_page)
    lines = ["  ID         ИМЯ          ГОРОД      ИГР РЕФ"]
    for user in users:
        games = int(user.get("games_created") or 0) + int(user.get("responses_count") or 0)
        marker = "⛔" if user.get("admin_blocked") else "●" if str(user.get("updated_at") or "") >= (datetime.now(timezone.utc) - timedelta(days=7)).isoformat() else " "
        lines.append(
            f"{marker} {compact_cell(user.get('telegram_id'), 10):<10} "
            f"{compact_cell(user_name(user), 12):<12} "
            f"{compact_cell(user.get('profile_city'), 10):<10} "
            f"{games:>3} {int(user.get('invite_count') or 0):>3}"
        )
    text = (
        f"👥 <b>Пользователи</b> · {total}\n"
        f"Страница {page + 1}/{last_page + 1} · {sort}/{status}"
        f"{f' · {city}' if city else ''}\n\n"
        f"<pre>{html.escape(chr(10).join(lines))}</pre>\n"
        "Нажми ID ниже, чтобы открыть карточку."
    )
    rows = [
        [(compact_cell(user_name(user), 18), f"user:{user['telegram_id']}")]
        for user in users
    ]
    navigation = []
    if page > 0:
        navigation.append(("←", f"users:view:{page - 1}:{sort}:{status}:{city or '-'}"))
    if page < last_page:
        navigation.append(("→", f"users:view:{page + 1}:{sort}:{status}:{city or '-'}"))
    if navigation:
        rows.append(navigation)
    rows.extend([
        [("🆕 Новые", f"users:view:0:newest:{status}:{city or '-'}"),
         ("⚡ Активные", f"users:view:0:activity:{status}:{city or '-'}")],
        [("🎮 По играм", f"users:view:0:games:{status}:{city or '-'}"),
         ("⭐ По рейтингу", f"users:view:0:rating:{status}:{city or '-'}")],
        [("Все", f"users:view:0:{sort}:all:{city or '-'}"),
         ("За 7 дней", f"users:view:0:{sort}:active7d:{city or '-'}"),
         ("⛔ Блок", f"users:view:0:{sort}:blocked:{city or '-'}")],
        [("🏙 Город", "users:cities"), ("🔄 Обновить", f"users:view:{page}:{sort}:{status}:{city or '-' }")],
        [("🔎 Поиск", "users:search")],
        [("← Главное меню", "menu:home")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(F.data == "menu:users")
async def users_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await render_users_table(callback, 0)


@router.callback_query(F.data.startswith("users:view:"))
async def users_page(callback: CallbackQuery):
    _, _, page, sort, status, city = callback.data.split(":", 5)
    await render_users_table(callback, int(page), sort, status, "" if city == "-" else city)


@router.callback_query(F.data == "users:cities")
async def users_cities(callback: CallbackQuery):
    data = await api.get("/api/admin/users?limit=1", callback.from_user.id)
    rows = [[(f"{item['city_name']} · {item['users_count']}", f"users:view:0:newest:all:{item['city_name']}")]
            for item in data.get("cities", [])[:12]]
    rows.append([("Все города", "users:view:0:newest:all:-")])
    await callback.message.edit_text("🏙 <b>Выбери город</b>", reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(F.data == "users:search")
async def users_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminForm.user_search)
    await callback.message.edit_text(
        "🔎 <b>Поиск пользователя</b>\n\nОтправь Telegram ID, username или имя.",
        reply_markup=keyboard([[("← К таблице", "menu:users")]]),
    )
    await callback.answer()


@router.message(AdminForm.user_search)
async def search_user(message: Message, state: FSMContext):
    query = urllib.parse.quote((message.text or "").strip())
    data = await api.get(f"/api/admin/users?q={query}&limit=10", message.from_user.id)
    users = data.get("users", [])
    if not users:
        await message.answer("Ничего не найдено. Попробуй другой запрос.", reply_markup=back_menu())
        return
    rows = [[(f"{user_name(user)} · {user['telegram_id']}", f"user:{user['telegram_id']}")] for user in users]
    rows.append([("← Главное меню", "menu:home")])
    await state.clear()
    await message.answer("Результаты:", reply_markup=keyboard(rows))


@router.callback_query(F.data.startswith("user:"))
async def user_detail(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    data = await api.get(f"/api/admin/users/{user_id}", callback.from_user.id)
    user = data["user"]
    blocked = data.get("admin_blocked", False)
    activity = data.get("activity", {})
    confirmed = int(activity.get("confirmed_games") or 0)
    no_shows = int(activity.get("no_shows") or 0)
    reliability = round(((max(0, confirmed - no_shows) + 4) / (confirmed + 5)) * 100) if confirmed else 80
    registered_at = str(user.get("created_at") or "—")[:16].replace("T", " ")
    last_seen = str(user.get("updated_at") or "—")[:16].replace("T", " ")
    recent_lines = []
    for game in data.get("recent_games", []):
        role = "создал" if game.get("role") == "creator" else "отклик"
        recent_lines.append(
            f"• #{game.get('id')} · {game.get('status')} · {role}\n"
            f"  {html.escape(str(game.get('city') or '—'))}, {html.escape(str(game.get('place') or '—'))} · "
            f"{html.escape(str(game.get('date_label') or '—'))} {html.escape(str(game.get('time_label') or ''))}"
        )
    text = (
        f"👤 <b>{html.escape(user_name(user))}</b>\n"
        f"ID: <code>{user_id}</code>\n"
        f"Username: @{html.escape(str(user.get('username') or '—'))}\n"
        f"Город: {html.escape(str(user.get('profile_city') or user.get('city') or '—'))}\n"
        f"Уровень: {html.escape(str(user.get('level') or '—'))}\n"
        f"Рейтинг: {float(user.get('rating_avg') or 0):.1f} ({user.get('rating_count') or 0})\n"
        f"Надёжность: <b>{reliability}%</b> · встреч {confirmed} · no-show {no_shows}\n"
        f"Заявки/отклики: {activity.get('games_created', 0)} / {activity.get('responses_sent', 0)}\n"
        f"В избранном у игроков: {activity.get('favorited_by', 0)}\n"
        f"Рефералы: {activity.get('referrals_activated', 0)} активных / {activity.get('referrals_registered', 0)} всего\n"
        f"Реферальные очки: {user.get('referral_points') or 0}\n"
        f"Жалоб: {len(data.get('reports', []))}\n"
        f"Регистрация: <code>{registered_at}</code>\n"
        f"Последняя активность: <code>{last_seen}</code>\n"
        f"Статус: {'🔴 заблокирован' if blocked else '🟢 активен'}"
        + (f"\n\n<b>Последние партии</b>\n{chr(10).join(recent_lines)}" if recent_lines else "")
    )
    action = ("Разблокировать", f"confirm:unblock:{user_id}") if blocked else ("Заблокировать", f"confirm:block:{user_id}")
    await callback.message.edit_text(
        text,
        reply_markup=keyboard([[action, ("⚠️ Предупредить", f"confirm:warn:{user_id}")], [("← К таблице", "menu:users")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm:block:"))
@router.callback_query(F.data.startswith("confirm:unblock:"))
@router.callback_query(F.data.startswith("confirm:warn:"))
async def confirm_user_action(callback: CallbackQuery):
    _, action, user_id = callback.data.split(":")
    await callback.message.edit_text(
        f"Подтверди действие: <b>{action}</b> пользователя <code>{user_id}</code>.",
        reply_markup=keyboard([
            [("✅ Подтвердить", f"do:{action}:{user_id}"), ("Отмена", f"user:{user_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do:block:"))
@router.callback_query(F.data.startswith("do:unblock:"))
@router.callback_query(F.data.startswith("do:warn:"))
async def execute_user_action(callback: CallbackQuery):
    _, action, user_id = callback.data.split(":")
    if action == "warn":
        await api.post(
            "/api/admin/broadcast",
            callback.from_user.id,
            {
                "telegram_id": int(user_id),
                "text": "⚠️ Предупреждение модерации ChessMeet.\n\nПожалуйста, соблюдай правила сообщества. Повторные нарушения могут привести к блокировке.",
            },
        )
    else:
        await api.post(f"/api/admin/users/{user_id}/{action}", callback.from_user.id)
    await callback.answer("Готово", show_alert=True)
    callback.data = f"user:{user_id}"
    await user_detail(callback)


@router.callback_query(F.data == "menu:games")
async def games(callback: CallbackQuery):
    data = await api.get("/api/admin/games?limit=20", callback.from_user.id)
    items = data.get("games", [])
    rows = []
    for game in items[:15]:
        label = f"#{game['id']} {game.get('city', '')} · {game.get('status', '')}"
        rows.append([(label[:55], f"game:{game['id']}")])
    rows.append([("← Главное меню", "menu:home")])
    await callback.message.edit_text("🎮 <b>Последние заявки</b>", reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(F.data == "menu:partners")
async def partner_places(callback: CallbackQuery):
    data = await api.get("/api/admin/partner-places", callback.from_user.id)
    places = data.get("places", [])
    lines = ["📍 <b>Партнёрские места</b>", ""]
    for place in places[:20]:
        state = "✓" if place.get("is_active") else "⏸"
        lines.append(f"{state} #{place['id']} {html.escape(place.get('name') or '—')} · {html.escape(place.get('city') or '—')} · выборов {place.get('clicks_count', 0)} · партий {place.get('games_count', 0)}")
    if not places:
        lines.append("Партнёрских мест пока нет. Добавить их можно через защищённый Admin API.")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:map_reports")
async def map_reports(callback: CallbackQuery):
    data = await api.get("/api/admin/map-diagnostics?limit=20", callback.from_user.id)
    reports = data.get("reports", [])
    lines = ["🗺 <b>Диагностика карт</b>", ""]
    for item in reports[:15]:
        lines.append(f"#{item['id']} · <code>{item.get('telegram_id')}</code> · {html.escape(item.get('platform') or 'unknown')} · {html.escape(item.get('tile_status') or 'unknown')} · v{html.escape(item.get('app_version') or '—')}")
    if not reports:
        lines.append("Отчётов пока нет.")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:puzzles")
async def puzzles(callback: CallbackQuery):
    data = await api.get("/api/admin/puzzles", callback.from_user.id)
    stats = data.get("statistics", [])
    disabled = data.get("disabled_ids", [])
    lines = ["🧩 <b>Задачи дня</b>", "", f"В наборе: <b>{data.get('count', 0)}</b>", f"Отключено: <b>{len(disabled)}</b>"]
    if stats:
        lines.extend(["", "Последняя статистика:"])
        for item in stats[-10:]:
            attempts = int(item.get('attempts') or 0); solved = int(item.get('solved_users') or 0)
            rate = round(solved / attempts * 100) if attempts else 0
            lines.append(f"#{item.get('puzzle_id')} · {rate}% решили · ср. {item.get('average_attempts') or 0} попыток")
    await callback.message.edit_text("\n".join(lines), reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:city_requests")
async def city_requests(callback: CallbackQuery):
    data = await api.get("/api/admin/city-requests?limit=50", callback.from_user.id)
    items = data.get("requests", [])
    lines = ["🏙 <b>Запросы новых городов</b>", ""]
    for item in items:
        lines.append(
            f"• <b>{html.escape(str(item.get('city_name') or '—'))}</b> — "
            f"{html.escape(str(item.get('sender_name') or '—'))} "
            f"(<code>{item.get('telegram_id')}</code>)"
        )
    if not items:
        lines.append("Новых запросов пока нет.")
    await callback.message.edit_text("\n".join(lines)[:3900], reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("game:"))
async def game_detail(callback: CallbackQuery):
    game_id = int(callback.data.split(":")[1])
    data = await api.get("/api/admin/games?limit=500", callback.from_user.id)
    game = next((item for item in data.get("games", []) if int(item["id"]) == game_id), None)
    if not game:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    text = (
        f"🎮 <b>Заявка #{game_id}</b>\n"
        f"{game.get('city')} · {game.get('place')}\n"
        f"{game.get('date_label')} {game.get('time_label')}\n"
        f"{game.get('game_format')} · {game.get('level')}\n"
        f"Статус: <b>{game.get('status')}</b>\n"
        f"Автор: <code>{game.get('creator_telegram_id')}</code>"
    )
    rows = []
    if game.get("status") not in {"cancelled", "completed", "expired"}:
        rows.append([("Отменить заявку", f"confirm:cancel_game:{game_id}")])
    rows.append([("← К заявкам", "menu:games")])
    await callback.message.edit_text(text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(F.data.startswith("confirm:cancel_game:"))
async def confirm_cancel_game(callback: CallbackQuery):
    game_id = callback.data.rsplit(":", 1)[1]
    await callback.message.edit_text(
        f"Отменить заявку <b>#{game_id}</b>?",
        reply_markup=keyboard([[("✅ Отменить", f"do:cancel_game:{game_id}"), ("Назад", f"game:{game_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("do:cancel_game:"))
async def cancel_game(callback: CallbackQuery):
    game_id = callback.data.rsplit(":", 1)[1]
    await api.post(f"/api/admin/games/{game_id}/cancel", callback.from_user.id)
    await callback.answer("Заявка отменена", show_alert=True)
    callback.data = "menu:games"
    await games(callback)


@router.callback_query(F.data == "menu:reports")
async def reports(callback: CallbackQuery):
    data = await api.get("/api/admin/reports?limit=30", callback.from_user.id)
    items = data.get("reports", [])
    rows = []
    for report in items[:20]:
        icon = "🚨" if report.get("status", "open") == "open" else "✓"
        rows.append([(f"{icon} #{report['id']} → {report.get('reported_telegram_id')}", f"report:{report['id']}")])
    rows.append([("← Главное меню", "menu:home")])
    await callback.message.edit_text("🚨 <b>Жалобы</b>", reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(F.data.startswith("report:"))
async def report_detail(callback: CallbackQuery):
    report_id = int(callback.data.split(":")[1])
    data = await api.get("/api/admin/reports?limit=500", callback.from_user.id)
    report = next((item for item in data.get("reports", []) if int(item["id"]) == report_id), None)
    if not report:
        await callback.answer("Жалоба не найдена", show_alert=True)
        return
    text = (
        f"🚨 <b>Жалоба #{report_id}</b>\n"
        f"На: <code>{report.get('reported_telegram_id')}</code>\n"
        f"От: <code>{report.get('reporter_telegram_id')}</code>\n"
        f"Причина: {report.get('reason')}\n"
        f"Комментарий: {report.get('comment') or '—'}\n"
        f"Статус: {report.get('status', 'open')}"
    )
    rows = []
    if report.get("status", "open") == "open":
        rows.append([("✅ Решена", f"resolve:{report_id}:resolved"), ("Отклонить", f"resolve:{report_id}:dismissed")])
        rows.append([("Заблокировать нарушителя", f"confirm:block:{report.get('reported_telegram_id')}")])
    rows.append([("← К жалобам", "menu:reports")])
    await callback.message.edit_text(text, reply_markup=keyboard(rows))
    await callback.answer()


@router.callback_query(F.data.startswith("resolve:"))
async def resolve_report(callback: CallbackQuery):
    _, report_id, status = callback.data.split(":")
    await api.post(
        f"/api/admin/reports/{report_id}/resolve",
        callback.from_user.id,
        {"status": status, "note": "Processed in admin bot"},
    )
    await callback.answer("Статус сохранён", show_alert=True)
    callback.data = "menu:reports"
    await reports(callback)


@router.callback_query(F.data == "menu:system")
async def system(callback: CallbackQuery):
    health = await api.get("/api/admin/health", callback.from_user.id)
    size_mb = float(health.get("database_size_bytes") or 0) / 1024 / 1024
    text = (
        "🩺 <b>Система</b>\n\n"
        f"API: {'🟢 работает' if health.get('ok') else '🔴 ошибка'}\n"
        f"Версия: {health.get('version')}\n"
        f"База: {size_mb:.2f} MB\n"
        f"Volume: <code>{health.get('volume_mount_path') or 'не подключён'}</code>\n"
        f"Backups: {len(health.get('database_backups', []))}\n"
        f"Задачи: {health.get('puzzle_pack_size')}"
    )
    await callback.message.edit_text(text, reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:audit")
async def audit(callback: CallbackQuery):
    data = await api.get("/api/admin/audit?limit=20", callback.from_user.id)
    lines = ["📜 <b>Последние действия</b>", ""]
    for item in data.get("actions", []):
        lines.append(
            f"• {item.get('action')} · {item.get('target_type')} {item.get('target_id')}"
            f"\n  admin <code>{item.get('actor_telegram_id') or 'system'}</code>"
        )
    await callback.message.edit_text("\n".join(lines)[:3900], reply_markup=back_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:backup")
async def backup(callback: CallbackQuery):
    await callback.message.edit_text(
        "🗄 <b>Экспорт и резервные копии</b>",
        reply_markup=keyboard([
            [("👥 Пользователи CSV", "export:users"), ("🎮 Заявки CSV", "export:games")],
            [("🗄 Полная база SQLite", "export:database")],
            [("← Главное меню", "menu:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("export:"))
async def export_data(callback: CallbackQuery):
    export_type = callback.data.split(":")[1]
    if export_type == "database" and callback.from_user.id not in OWNER_IDS:
        await callback.answer("Полная база доступна только владельцу", show_alert=True)
        return
    paths = {
        "users": ("/api/admin/export/users.csv", "chessmeet-users.csv"),
        "games": ("/api/admin/export/games.csv", "chessmeet-games.csv"),
        "database": ("/api/admin/db/backup", "chessmeet-backup.sqlite3"),
    }
    if export_type not in paths:
        await callback.answer("Неизвестный экспорт", show_alert=True)
        return
    await callback.answer("Готовлю файл…")
    path, filename = paths[export_type]
    content = await api.download(path, callback.from_user.id)
    await callback.message.answer_document(
        BufferedInputFile(content, filename=filename),
        caption=f"Экспорт ChessMeet · {export_type}",
    )


@router.callback_query(F.data == "menu:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Только для владельца", show_alert=True)
        return
    await state.set_state(AdminForm.broadcast_text)
    await callback.message.edit_text(
        "📣 <b>Новая рассылка</b>\n\nОтправь текст сообщения. Перед отправкой будет предпросмотр.",
        reply_markup=back_menu(),
    )
    await callback.answer()


@router.message(AdminForm.broadcast_text)
async def broadcast_preview(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Нужен текст сообщения.")
        return
    await state.update_data(broadcast_text=text)
    await state.set_state(AdminForm.broadcast_confirm)
    await message.answer(
        f"📣 <b>Предпросмотр</b>\n\n{text}\n\nОтправить всем пользователям?",
        reply_markup=keyboard([[("✅ Отправить всем", "broadcast:confirm"), ("Отмена", "menu:home")]]),
    )


@router.callback_query(F.data == "broadcast:confirm", AdminForm.broadcast_confirm)
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("Только для владельца", show_alert=True)
        return
    data = await state.get_data()
    result = await api.post(
        "/api/admin/broadcast",
        callback.from_user.id,
        {"text": data["broadcast_text"], "telegram_id": None},
    )
    await state.clear()
    await callback.message.edit_text(
        f"✅ Рассылка завершена\n\nОтправлено: {result.get('sent')}\nОшибок: {result.get('failed')}",
        reply_markup=back_menu(),
    )
    await callback.answer()


@router.errors()
async def errors(event):
    exception = event.exception
    update = event.update
    message = getattr(update, "message", None) or getattr(getattr(update, "callback_query", None), "message", None)
    if message:
        await message.answer(f"⚠️ Ошибка: {str(exception)[:500]}", reply_markup=back_menu())
    return True


async def report_monitor(bot: Bot) -> None:
    seen: set[int] = set()
    while True:
        try:
            actor = next(iter(OWNER_IDS or ADMIN_IDS))
            data = await api.get("/api/admin/reports?limit=50", actor)
            open_reports = [item for item in data.get("reports", []) if item.get("status", "open") == "open"]
            current = {int(item["id"]) for item in open_reports}
            for report in open_reports:
                report_id = int(report["id"])
                if report_id not in seen:
                    for admin_id in ADMIN_IDS:
                        await bot.send_message(
                            admin_id,
                            f"🚨 Новая жалоба #{report_id}\nНа пользователя: <code>{report.get('reported_telegram_id')}</code>\n"
                            f"Причина: {report.get('reason')}",
                            reply_markup=keyboard([[("Открыть", f"report:{report_id}")]]),
                        )
            seen = current
        except Exception:
            pass
        await asyncio.sleep(60)


async def daily_external_backup(bot: Bot) -> None:
    """Keep one recoverable copy outside Railway's filesystem every day."""
    while True:
        now = datetime.now(timezone.utc)
        next_run = (now + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
        await asyncio.sleep(max(60, (next_run - now).total_seconds()))
        try:
            actor = next(iter(OWNER_IDS))
            content = await api.download("/api/admin/db/backup", actor)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            document = BufferedInputFile(content, filename=f"chessmeet-{stamp}.sqlite3")
            for owner_id in OWNER_IDS:
                await bot.send_document(
                    owner_id,
                    document,
                    caption=f"🗄 Ежедневная резервная копия ChessMeet · {stamp} UTC",
                )
        except Exception as exc:
            for owner_id in OWNER_IDS:
                try:
                    await bot.send_message(owner_id, f"⚠️ Не удалось создать ежедневный backup: {str(exc)[:300]}")
                except Exception:
                    pass


async def service_health_monitor(bot: Bot) -> None:
    failures = 0
    alerted = False
    while True:
        try:
            actor = next(iter(OWNER_IDS or ADMIN_IDS))
            health = await api.get("/api/admin/health", actor)
            if not health.get("database_exists"):
                raise RuntimeError("database file is missing")
            if alerted:
                for admin_id in ADMIN_IDS:
                    await bot.send_message(admin_id, "✅ ChessMeet снова работает, база данных доступна.")
            failures = 0
            alerted = False
        except Exception as exc:
            failures += 1
            if failures >= 3 and not alerted:
                alerted = True
                for admin_id in ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, f"🚨 ChessMeet недоступен три проверки подряд: {str(exc)[:300]}")
                    except Exception:
                        pass
        await asyncio.sleep(120)


async def run_admin_bot() -> None:
    if not ADMIN_BOT_TOKEN:
        raise RuntimeError("ADMIN_BOT_TOKEN is required")
    if not ADMIN_API_URL or not ADMIN_TOKEN:
        raise RuntimeError("ADMIN_API_URL and ADMIN_TOKEN are required")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_TELEGRAM_IDS is required")
    bot = Bot(ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    monitor = asyncio.create_task(report_monitor(bot))
    backup_task = asyncio.create_task(daily_external_backup(bot))
    health_task = asyncio.create_task(service_health_monitor(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        monitor.cancel()
        backup_task.cancel()
        health_task.cancel()
        await bot.session.close()


async def main() -> None:
    await run_admin_bot()


if __name__ == "__main__":
    asyncio.run(main())
