from __future__ import annotations

from typing import Dict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MenuButtonWebApp,
    WebAppInfo,
)

from .database import Database


def display_name(user: Dict) -> str:
    if user.get("show_telegram_username") and user.get("username"):
        return f"@{user['username']}"
    return user.get("display_name") or user.get("first_name") or "Игрок"


def main_keyboard(webapp_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♟ Открыть Chess IRL Minsk", web_app=WebAppInfo(url=webapp_url))],
            [InlineKeyboardButton(text="🧩 Задачка дня", web_app=WebAppInfo(url=f"{webapp_url}?screen=puzzle"))],
            [
                InlineKeyboardButton(text="➕ Создать заявку", web_app=WebAppInfo(url=f"{webapp_url}?screen=create")),
                InlineKeyboardButton(text="📋 Найти партии", web_app=WebAppInfo(url=f"{webapp_url}?screen=games")),
            ],
            [
                InlineKeyboardButton(text="🧾 Мои партии", web_app=WebAppInfo(url=f"{webapp_url}?screen=my")),
                InlineKeyboardButton(text="👤 Профиль", web_app=WebAppInfo(url=f"{webapp_url}?screen=profile")),
            ],
        ]
    )


def menu_button(webapp_url: str) -> MenuButtonWebApp:
    return MenuButtonWebApp(
        text="♟ ChessMeet",
        web_app=WebAppInfo(url=webapp_url),
    )


async def set_webapp_menu_button(bot: Bot, webapp_url: str, chat_id: int | None = None) -> None:
    """Sets the bottom-left Telegram menu button to open the Mini App."""
    await bot.set_chat_menu_button(
        chat_id=chat_id,
        menu_button=menu_button(webapp_url),
    )


def response_keyboard(response_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data=f"accept:{response_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline:{response_id}"),
            ]
        ]
    )


async def notify_creator_about_response(bot: Bot, db: Database, response_id: int) -> None:
    details = await db.get_response_details(response_id)
    if not details:
        return

    responder = await db.get_user(details["responder_telegram_id"])
    responder_name = display_name(responder or {"first_name": "Игрок"})
    address = f"\n📌 {details['address']}" if details.get("address") else ""
    map_link = f"\n🗺 <a href=\"{details['map_url']}\">Открыть место на карте</a>" if details.get("map_url") else ""
    proposed = ""
    if details.get("proposed_time_label") or details.get("proposed_comment"):
        proposed = "\n\n🕒 <b>Предложение игрока</b>"
        if details.get("proposed_date_label") or details.get("proposed_time_label"):
            proposed += f"\nДата/время: {details.get('proposed_date_label') or details['date_label']} {details.get('proposed_time_label') or ''}"
        if details.get("proposed_comment"):
            proposed += f"\nКомментарий: {details['proposed_comment']}"
    text = (
        f"♟ <b>Новый отклик на твою заявку</b>\n\n"
        f"{responder_name} хочет сыграть с тобой.\n\n"
        f"📍 {details['city']}, {details['place']}"
        f"{address}"
        f"{map_link}\n"
        f"🗓 {details['date_label']} в {details['time_label']}\n"
        f"⏱ {details['game_format']}"
        f"{proposed}\n\n"
        f"Принять отклик?"
    )
    await bot.send_message(
        chat_id=details["creator_telegram_id"],
        text=text,
        reply_markup=response_keyboard(response_id),
        disable_web_page_preview=True,
    )


async def notify_game_created(bot: Bot, telegram_id: int, game: Dict) -> None:
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "✅ <b>Заявка опубликована</b>\n\n"
            f"📍 {game['city']}, {game['place']}\n"
            f"🗓 {game['date_label']} в {game['time_label']}\n"
            f"⏱ {game['game_format']}\n"
            f"🎯 {game['level']}\n\n"
            "Когда кто-то откликнется, я пришлю уведомление."
        ),
        disable_web_page_preview=True,
    )


async def notify_new_request(bot: Bot, telegram_id: int, game: Dict, webapp_url: str) -> None:
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "♟ <b>Новая заявка на партию</b>\n\n"
            f"📍 {game.get('city', 'Минск')}, {game.get('place', '')}\n"
            f"🗓 {game.get('date_label', '')} в {game.get('time_label', '')}\n"
            f"⏱ {game.get('game_format', '')}\n"
            f"🎯 {game.get('level', '')}"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть заявки", web_app=WebAppInfo(url=f"{webapp_url}?screen=games"))]]
        ),
        disable_web_page_preview=True,
    )


async def notify_game_reminder(bot: Bot, telegram_id: int, game: Dict, reminder_type: str, webapp_url: str) -> None:
    label = "за 30 минут" if reminder_type == "30m" else "за 3 часа"
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            f"⏰ <b>Напоминание о партии {label}</b>\n\n"
            f"📍 {game.get('place', '')}\n"
            f"🗓 {game.get('date_label', '')} в {game.get('time_label', '')}\n"
            f"⏱ {game.get('game_format', '')}\n\n"
            "Открой чат, если нужно уточнить детали."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Открыть чат", web_app=WebAppInfo(url=f"{webapp_url}?screen=chat&game={game.get('id')}"))]]
        ),
        disable_web_page_preview=True,
    )


async def notify_puzzle_streak_reminder(
    bot: Bot,
    telegram_id: int,
    streak: int,
    webapp_url: str,
    city: str = "Минск",
) -> None:
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            "🧩 <b>Не потеряй серию!</b>\n\n"
            f"У тебя серия {streak}. Сегодняшняя задача ещё не решена.\n"
            f"Открой задачу дня до полуночи по времени города {city}."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Решить задачку", web_app=WebAppInfo(url=f"{webapp_url}?screen=puzzle"))]]
        ),
    )


async def notify_response_accepted(bot: Bot, db: Database, response_id: int, webapp_url: str) -> None:
    details = await db.get_response_details(response_id)
    if not details:
        return
    address = f"\n📌 {details['address']}" if details.get("address") else ""
    map_link = f"\n🗺 <a href=\"{details['map_url']}\">Открыть место на карте</a>" if details.get("map_url") else ""
    creator = await db.get_user(details["creator_telegram_id"])
    creator_name = display_name(creator or {"first_name": "Игрок"})
    await bot.send_message(
        chat_id=details["responder_telegram_id"],
        text=(
            "✅ <b>Твой отклик приняли</b>\n\n"
            f"👤 {creator_name}\n"
            f"📍 {details['city']}, {details['place']}"
            f"{address}"
            f"{map_link}\n"
            f"🗓 {details['date_label']} в {details['time_label']}\n\n"
            "Чат уже открыт — уточните детали игры и после встречи поставьте оценку сопернику."
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Открыть чат", web_app=WebAppInfo(url=f"{webapp_url}?screen=chat&game={details['game_id']}"))],
                [InlineKeyboardButton(text="🧾 Мои партии", web_app=WebAppInfo(url=f"{webapp_url}?screen=my"))],
            ]
        ),
        disable_web_page_preview=True,
    )


async def notify_new_chat_message(bot: Bot, message: Dict, webapp_url: str) -> None:
    opponent_id = message.get("opponent_telegram_id")
    game = message.get("game") or {}
    sender = message.get("sender") or {}
    if not opponent_id:
        return
    await bot.send_message(
        chat_id=opponent_id,
        text=(
            f"💬 <b>Новое сообщение по партии</b>\n\n"
            f"{display_name(sender)}: {message.get('text', '')}\n\n"
            f"📍 {game.get('place', 'место не указано')}\n"
            f"🗓 {game.get('date_label', '')} {game.get('time_label', '')}"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Открыть чат", web_app=WebAppInfo(url=f"{webapp_url}?screen=chat&game={game.get('id')}"))]
            ]
        ),
        disable_web_page_preview=True,
    )


def build_dispatcher(db: Database, webapp_url: str) -> Dispatcher:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if message.from_user:
            await db.upsert_user(message.from_user.model_dump(), default_city="Минск")
            try:
                await set_webapp_menu_button(message.bot, webapp_url, chat_id=message.chat.id)
            except Exception:
                # Some users can restrict Web App buttons in privacy settings.
                pass

        await message.answer(
            "Привет! Это <b>ChessMeet</b> — мини-приложение для поиска шахматной партии в реальной жизни.\n\n",
            reply_markup=main_keyboard(webapp_url),
        )

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "Как это работает:\n\n"
            "1. Заполни профиль и при желании добавь фото.\n"
            "2. Создай заявку и выбери место на карте.\n"
            "3. Игроки откликаются, а автор принимает или отклоняет отклик в Telegram.\n"
            "4. После принятия отклика партия сразу подтверждается, а чат открывается автоматически.\n"
            "5. Бот напомнит о встрече за 3 часа и за 30 минут, если уведомления включены.\n"
            "6. Через час после запланированной встречи можно поставить оценку сопернику.\n\n"
            "Для безопасности встречайтесь только в публичных местах.",
        )


    @router.message(Command("daily"))
    async def daily_cmd(message: Message) -> None:
        if message.from_user:
            await db.upsert_user(message.from_user.model_dump(), default_city="Минск")
        await message.answer(
            "🧩 <b>Задачка дня</b>\n\n"
            "Реши мат в 1 ход и сохрани ежедневную серию в профиле.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Открыть задачку", web_app=WebAppInfo(url=f"{webapp_url}?screen=puzzle"))],
                ]
            ),
        )

    @router.message(Command("app"))
    async def app_cmd(message: Message) -> None:
        await message.answer("Открыть приложение:", reply_markup=main_keyboard(webapp_url))

    @router.callback_query(F.data.startswith("accept:"))
    async def accept_response(callback: CallbackQuery) -> None:
        response_id = int(callback.data.split(":", 1)[1])
        details = await db.get_response_details(response_id)
        if not details:
            await callback.answer("Отклик не найден", show_alert=True)
            return
        if not callback.from_user or callback.from_user.id != details["creator_telegram_id"]:
            await callback.answer("Принять отклик может только автор заявки", show_alert=True)
            return
        if details["status"] != "pending":
            await callback.answer("Этот отклик уже обработан", show_alert=True)
            return

        accepted = await db.accept_response(response_id)
        responder = await db.get_user(accepted["responder_telegram_id"])
        responder_name = display_name(responder or {"first_name": "Игрок"})
        map_link = f"\n🗺 <a href=\"{accepted['map_url']}\">Открыть место на карте</a>" if accepted.get("map_url") else ""
        address = f"\n📌 {accepted['address']}" if accepted.get("address") else ""

        await callback.message.edit_text(
            f"✅ Отклик принят. Ты играешь с {responder_name}.\n\n"
            f"📍 {accepted['city']}, {accepted['place']}"
            f"{address}"
            f"{map_link}\n"
            f"🗓 {accepted['date_label']} в {accepted['time_label']}\n\n"
            f"Чат уже открыт — можно уточнить детали игры.",
            disable_web_page_preview=True,
        )
        await callback.answer("Отклик принят")
        await notify_response_accepted(callback.bot, db, response_id, webapp_url)

    @router.callback_query(F.data.startswith("decline:"))
    async def decline_response(callback: CallbackQuery) -> None:
        response_id = int(callback.data.split(":", 1)[1])
        details = await db.get_response_details(response_id)
        if not details:
            await callback.answer("Отклик не найден", show_alert=True)
            return
        if not callback.from_user or callback.from_user.id != details["creator_telegram_id"]:
            await callback.answer("Отклонить отклик может только автор заявки", show_alert=True)
            return
        if details["status"] != "pending":
            await callback.answer("Этот отклик уже обработан", show_alert=True)
            return

        declined = await db.decline_response(response_id)
        await callback.message.edit_text("❌ Отклик отклонён.")
        await callback.answer("Отклик отклонён")
        await callback.bot.send_message(
            chat_id=declined["responder_telegram_id"],
            text="❌ Автор заявки отклонил твой отклик. Можно выбрать другую партию в приложении.",
        )

    dp = Dispatcher()
    dp.include_router(router)
    return dp
