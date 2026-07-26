from __future__ import annotations

import asyncio
import os
import csv
import io
import shutil
import hmac
import json
from urllib.parse import urlsplit
from datetime import datetime, timezone
import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .auth import TelegramAuthError, demo_user, validate_telegram_init_data, validate_webapp_auth_token
from .cities import canonical_city, city_catalog, city_local_hour, city_today_key, is_supported_city, public_city_config
from .bot import (
    build_dispatcher,
    notify_creator_about_response,
    notify_game_created,
    notify_response_accepted,
    notify_new_chat_message,
    notify_new_request,
    notify_game_reminder,
    notify_puzzle_streak_reminder,
    set_webapp_menu_button,
)
from .database import Database, now_iso

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = ROOT_DIR / "webapp"

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://localhost:8000").rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
BOT_MODE = os.getenv("BOT_MODE", "polling").lower()
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"
def resolve_database_path(environment: Optional[Dict[str, str]] = None) -> str:
    env = environment if environment is not None else os.environ
    railway_runtime = bool(
        env.get("RAILWAY_ENVIRONMENT_ID")
        or env.get("RAILWAY_PROJECT_ID")
        or env.get("RAILWAY_SERVICE_ID")
    )
    volume_mount = (env.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    configured = (env.get("DATABASE_PATH") or "").strip()
    allow_ephemeral = (env.get("ALLOW_EPHEMERAL_DATABASE") or "").lower() == "true"

    if volume_mount and not configured:
        configured = str(Path(volume_mount) / "chess_irl.sqlite3")
    if not configured:
        configured = str(ROOT_DIR / "chess_irl.sqlite3")

    database_path = Path(configured).expanduser().resolve()
    if railway_runtime and not volume_mount and not allow_ephemeral:
        raise RuntimeError(
            "Persistent Railway Volume is not attached. Attach a Volume and mount it at /data; "
            "ChessMeet refuses to create another disposable SQLite database."
        )
    if railway_runtime and volume_mount:
        mount_path = Path(volume_mount).expanduser().resolve()
        try:
            database_path.relative_to(mount_path)
        except ValueError as exc:
            raise RuntimeError(
                f"DATABASE_PATH must be inside the Railway Volume mounted at {mount_path}."
            ) from exc
    return str(database_path)


DATABASE_PATH = resolve_database_path()
DEFAULT_CITY = canonical_city(os.getenv("DEFAULT_CITY", "Минск"))

BOT_IS_CONFIGURED = bool(BOT_TOKEN and BOT_TOKEN != "123456789:PASTE_YOUR_BOT_TOKEN_HERE")

db = Database(DATABASE_PATH)
bot: Optional[Bot] = None
polling_task: Optional[asyncio.Task] = None
notification_task: Optional[asyncio.Task] = None
admin_bot_task: Optional[asyncio.Task] = None


class GameCreate(BaseModel):
    city: str = Field(default="Минск", max_length=80)
    place: str = Field(min_length=2, max_length=120)
    area: str = Field(default="", max_length=120)
    address: str = Field(default="", max_length=200)
    place_id: str = Field(default="", max_length=250)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    map_url: str = Field(default="", max_length=500)
    date_label: str = Field(min_length=2, max_length=80)
    time_label: str = Field(min_length=2, max_length=40)
    scheduled_at: Optional[str] = None
    is_flexible: bool = False
    time_window_start: str = Field(default="", max_length=40)
    time_window_end: str = Field(default="", max_length=40)
    game_format: str = Field(min_length=2, max_length=80)
    level: str = Field(min_length=2, max_length=80)
    has_board: bool = True
    comment: str = Field(default="", max_length=500)

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        if not is_supported_city(value):
            raise ValueError("unsupported city")
        return canonical_city(value)

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not -90 <= value <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return value

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not -180 <= value <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return value

    @field_validator("map_url")
    @classmethod
    def validate_map_url(cls, value: str) -> str:
        clean = (value or "").strip()
        if not clean:
            return ""
        parsed = urlsplit(clean)
        if parsed.scheme != "https" or parsed.hostname not in {"openstreetmap.org", "www.openstreetmap.org"}:
            raise ValueError("map_url must be an HTTPS OpenStreetMap URL")
        return clean


class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    profile_city: str = Field(default="Минск", max_length=80)
    level: str = Field(default="Средний", max_length=80)
    bio: str = Field(default="", max_length=300)
    show_telegram_username: bool = False
    photo_data_url: str = Field(default="", max_length=2_000_000)
    notify_game_reminders: bool = True
    notify_new_requests: bool = False
    notify_puzzle_streak: bool = True
    theme_mode: str = Field(default="light", max_length=20)
    ui_language: str = Field(default="", max_length=10)
    subscription_format: str = Field(default="all", max_length=40)
    subscription_level: str = Field(default="all", max_length=80)

    @field_validator("profile_city")
    @classmethod
    def validate_profile_city(cls, value: str) -> str:
        if not is_supported_city(value):
            raise ValueError("unsupported city")
        return canonical_city(value)

class PreferencesUpdate(BaseModel):
    ui_language: Optional[str] = Field(default=None, pattern="^(ru|en)$")
    profile_city: Optional[str] = None
    notify_new_requests: Optional[bool] = None

    @field_validator("profile_city")
    @classmethod
    def validate_preference_city(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not is_supported_city(value):
            raise ValueError("unsupported city")
        return canonical_city(value)


class RatingCreate(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=300)


class ChatMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ResponseCreate(BaseModel):
    proposed_date_label: str = Field(default="", max_length=80)
    proposed_time_label: str = Field(default="", max_length=40)
    proposed_comment: str = Field(default="", max_length=300)

class CheckInCreate(BaseModel):
    late_minutes: int = Field(default=0, ge=0, le=120)


class PlaceRatingCreate(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=300)


class DiaryUpdate(BaseModel):
    result: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=1000)

class AnalyticsEventCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=80)
    event_data: Dict[str, Any] = Field(default_factory=dict)


class CancelGameRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


class UserReportCreate(BaseModel):
    reason: str = Field(default="Другое", max_length=120)
    comment: str = Field(default="", max_length=500)
    game_id: Optional[int] = None


class GamePhotoCreate(BaseModel):
    photo_data_url: str = Field(min_length=20, max_length=2_500_000)
    caption: str = Field(default="", max_length=200)


class DailyPuzzleAnswer(BaseModel):
    selected_move: str = Field(min_length=4, max_length=5)


class BadgeVisibilityUpdate(BaseModel):
    visible_badge_ids: list[int] = Field(default_factory=list)


class AdminBroadcastCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    telegram_id: Optional[int] = None


class AdminBadgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    icon: str = Field(default="🏅", max_length=8)
    description: str = Field(default="", max_length=300)
    color: str = Field(default="#2f8a4b", max_length=32)


class AdminBadgeIssue(BaseModel):
    telegram_id: int
    badge_id: int


class AdminReportResolve(BaseModel):
    status: str = Field(pattern="^(resolved|dismissed)$")
    note: str = Field(default="", max_length=500)


async def notification_loop() -> None:
    """Background MVP scheduler: game reminders + daily puzzle streak reminders."""
    while True:
        try:
            if bot:
                for event in await db.due_referral_notifications():
                    language = str(event.get("inviter_language") or "ru").lower()
                    is_en = language.startswith("en")
                    if not event.get("registration_notified_at"):
                        text = (
                            f"🎉 <b>{event['friend_name']}</b> joined ChessMeet through your link. "
                            "You will earn 10 points after their first game action."
                            if is_en else
                            f"🎉 <b>{event['friend_name']}</b> зарегистрировался по твоей ссылке. "
                            "10 очков начислятся после первого игрового действия."
                        )
                        try:
                            await bot.send_message(int(event["inviter_telegram_id"]), text)
                            await db.mark_referral_notification(int(event["referred_telegram_id"]), "registration")
                        except Exception:
                            pass
                    if event.get("status") == "activated" and not event.get("activation_notified_at"):
                        text = (
                            f"🏆 <b>{event['friend_name']}</b> became active. +10 referral points!"
                            if is_en else
                            f"🏆 <b>{event['friend_name']}</b> стал активным игроком. +10 реферальных очков!"
                        )
                        try:
                            await bot.send_message(int(event["inviter_telegram_id"]), text)
                            await db.mark_referral_notification(int(event["referred_telegram_id"]), "activation")
                        except Exception:
                            pass
                # Game reminders: 3 hours and 30 minutes before scheduled confirmed games.
                for item in await db.get_due_game_reminders():
                    recipients = []
                    if item.get("creator_notify_game_reminders", 1):
                        recipients.append(int(item["creator_telegram_id"]))
                    if item.get("responder_telegram_id") and item.get("responder_notify_game_reminders", 1):
                        recipients.append(int(item["responder_telegram_id"]))
                    for chat_id in set(recipients):
                        try:
                            await notify_game_reminder(bot, chat_id, item, item.get("reminder_type", ""), WEBAPP_URL)
                        except Exception:
                            pass

                # Daily streak reminder at 21:00 in each user's selected city.
                reminder_now = datetime.now(timezone.utc)
                for city in city_catalog():
                    if city_local_hour(city["name"], reminder_now) != 21:
                        continue
                    today_key = city_today_key(city["name"], reminder_now)
                    for recipient in await db.get_users_for_streak_reminder(city=city["name"], today_key=today_key):
                        try:
                            await notify_puzzle_streak_reminder(
                                bot,
                                int(recipient["telegram_id"]),
                                int(recipient.get("puzzle_streak") or 0),
                                WEBAPP_URL,
                                city=city["name"],
                            )
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, polling_task, notification_task, admin_bot_task

    await db.init()

    if BOT_IS_CONFIGURED:
        bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        try:
            # Default bottom-left menu button for all private chats.
            await set_webapp_menu_button(bot, WEBAPP_URL)
        except Exception:
            pass
        notification_task = asyncio.create_task(notification_loop())
        if BOT_MODE == "polling":
            dp = build_dispatcher(db=db, webapp_url=WEBAPP_URL, bot_token=BOT_TOKEN)
            polling_task = asyncio.create_task(dp.start_polling(bot))
    if os.getenv("ADMIN_BOT_TOKEN"):
        from .admin_bot import run_admin_bot
        admin_bot_task = asyncio.create_task(run_admin_bot())

    yield

    if notification_task:
        notification_task.cancel()
        try:
            await notification_task
        except asyncio.CancelledError:
            pass
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if admin_bot_task:
        admin_bot_task.cancel()
        try:
            await admin_bot_task
        except asyncio.CancelledError:
            pass
    if bot:
        await bot.session.close()


app = FastAPI(title="ChessMeet", version="1.4.1", lifespan=lifespan)

webapp_origin = urlsplit(WEBAPP_URL)
allowed_origins = []
if webapp_origin.scheme in {"http", "https"} and webapp_origin.netloc:
    allowed_origins.append(f"{webapp_origin.scheme}://{webapp_origin.netloc}")
if DEV_MODE:
    allowed_origins.extend(["http://localhost:8000", "http://127.0.0.1:8000"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR)), name="static")

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://telegram.org https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com; "
        "img-src 'self' data: blob: https://unpkg.com https://*.tile.openstreetmap.org; "
        "connect-src 'self' https://nominatim.openstreetmap.org; "
        "font-src 'self' data:; frame-ancestors https://web.telegram.org https://*.telegram.org"
    )
    return response


async def current_user(
    x_telegram_init_data: str = Header(default=""),
    x_chessmeet_auth: str = Header(default=""),
) -> Dict[str, Any]:
    if x_telegram_init_data and BOT_IS_CONFIGURED:
        try:
            parsed = validate_telegram_init_data(x_telegram_init_data, BOT_TOKEN)
            user_payload = parsed["user"]
        except TelegramAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    elif x_chessmeet_auth and BOT_IS_CONFIGURED:
        try:
            user_payload = validate_webapp_auth_token(x_chessmeet_auth, BOT_TOKEN)
        except TelegramAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    elif DEV_MODE:
        user_payload = demo_user()
    else:
        raise HTTPException(status_code=401, detail="Telegram auth is required")

    user = await db.upsert_user(user_payload, default_city=DEFAULT_CITY)
    await db.normalize_puzzle_streak(
        int(user["telegram_id"]),
        today_key=city_today_key(user.get("profile_city") or DEFAULT_CITY),
    )
    return await db.get_user(int(user["telegram_id"])) or user


def require_admin(x_admin_token: str = Header(default="")) -> None:
    if not ADMIN_TOKEN or not hmac.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Admin token required")


async def record_admin_action(
    actor: str,
    action: str,
    target_type: str = "",
    target_id: Any = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        actor_id = int(actor) if actor and str(actor).isdigit() else None
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            await conn.execute(
                """
                INSERT INTO admin_audit_log
                    (actor_telegram_id, action, target_type, target_id, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (actor_id, action[:80], target_type[:40], str(target_id)[:80],
                 json.dumps(details or {}, ensure_ascii=False)[:2000], now_iso()),
            )
            await conn.commit()
    except Exception:
        pass


@app.get("/")
async def index():
    return FileResponse(WEBAPP_DIR / "index.html")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        WEBAPP_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/health")
async def health():
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
    return {
        "ok": True,
        "bot_configured": BOT_IS_CONFIGURED,
        "bot_mode": BOT_MODE,
        "webapp_url": WEBAPP_URL,
        "default_city": DEFAULT_CITY,
        "version": "1.4.1",
        "railway_ready": True,
        "database_persistent": bool(volume_mount) if os.getenv("RAILWAY_SERVICE_ID") else True,
        "database_volume_attached": bool(volume_mount),
    }


@app.get("/api/config")
async def api_config():
    return {
        "default_city": DEFAULT_CITY,
        "cities": public_city_config(),
        "bot_username": BOT_USERNAME,
        "map_provider": "OpenStreetMap",
        "maps_require_api_key": False,
        "features": {
            "profile_photo": True,
            "ratings": True,
            "game_confirmation": False,
            "auto_expire": True,
            "private_chat": True,
            "public_profiles": True,
            "daily_puzzle": True,
            "puzzle_streak": True,
            "notification_settings": True,
            "game_reminders": True,
            "safety_reports": True,
            "smart_requests": True,
            "dark_theme": True,
            "favorite_players": True,
            "game_photos": True,
            "rematch": True,
            "badges": True,
            "puzzle_pack_size": len(db.daily_puzzles),
            "puzzle_source": db.puzzle_source,
            "quality_safety_pack": True,
            "edit_open_requests": True,
            "community_rules": True,
            "antispam": True,
        },
    }


@app.get("/api/bootstrap")
async def api_bootstrap(user: Dict[str, Any] = Depends(current_user)):
    """Single fast payload for Mini App startup.

    This reduces initial Telegram Mini App loading time by replacing several
    sequential browser requests with one backend request.
    """
    user["badges"] = await db.list_user_badges(int(user["telegram_id"]), public_only=False)
    user["referral"] = await db.referral_stats(int(user["telegram_id"]))
    city = user.get("profile_city") or DEFAULT_CITY
    games, my, daily_puzzle = await asyncio.gather(
        db.list_games(city=city or DEFAULT_CITY, viewer_telegram_id=int(user["telegram_id"])),
        db.list_my_games(telegram_id=int(user["telegram_id"])),
        db.get_daily_puzzle(int(user["telegram_id"]), puzzle_date=city_today_key(city)),
    )
    return {
        "config": {
            "default_city": DEFAULT_CITY,
            "cities": public_city_config(),
            "bot_username": BOT_USERNAME,
            "map_provider": "OpenStreetMap",
            "maps_require_api_key": False,
            "features": {
                "profile_photo": True,
                "ratings": True,
                "game_confirmation": False,
                "auto_expire": True,
                "private_chat": True,
                "public_profiles": True,
                "daily_puzzle": True,
                "puzzle_streak": True,
                "notification_settings": True,
                "game_reminders": True,
                "safety_reports": True,
                "smart_requests": True,
                "dark_theme": True,
                "favorite_players": True,
                "game_photos": True,
                "rematch": True,
                "badges": True,
                "puzzle_pack_size": len(db.daily_puzzles),
                "puzzle_source": db.puzzle_source,
                "quality_safety_pack": True,
                "edit_open_requests": True,
                "community_rules": True,
                "antispam": True,
                "fast_bootstrap": True,
            },
        },
        "user": user,
        "games": games,
        "my": my,
        "daily_puzzle": daily_puzzle,
    }


@app.get("/api/me")
async def api_me(user: Dict[str, Any] = Depends(current_user)):
    user["badges"] = await db.list_user_badges(int(user["telegram_id"]), public_only=False)
    user["referral"] = await db.referral_stats(int(user["telegram_id"]))
    return {"user": user, "default_city": DEFAULT_CITY}


@app.get("/api/referrals")
async def api_referrals(user: Dict[str, Any] = Depends(current_user)):
    return {"referral": await db.referral_stats(int(user["telegram_id"]))}


@app.patch("/api/me")
async def api_update_me(payload: ProfileUpdate, user: Dict[str, Any] = Depends(current_user)):
    try:
        updated = await db.update_user_profile(int(user["telegram_id"]), payload.model_dump())
    except ValueError as exc:
        if str(exc) == "PHOTO_TOO_LARGE":
            raise HTTPException(status_code=400, detail="Фото слишком большое") from exc
        if str(exc) == "INVALID_PHOTO":
            raise HTTPException(status_code=400, detail="Поддерживаются только PNG, JPEG, WebP и GIF") from exc
        raise
    return {"user": updated}

@app.patch("/api/me/preferences")
async def api_update_preferences(payload: PreferencesUpdate, user: Dict[str, Any] = Depends(current_user)):
    if payload.ui_language is None and payload.profile_city is None and payload.notify_new_requests is None:
        raise HTTPException(status_code=400, detail="No preferences supplied")
    updated = await db.update_user_preferences(
        int(user["telegram_id"]),
        ui_language=payload.ui_language,
        profile_city=payload.profile_city,
        notify_new_requests=payload.notify_new_requests,
    )
    return {"user": updated}

@app.get("/api/badges")
async def api_badges(user: Dict[str, Any] = Depends(current_user)):
    return {
        "all_badges": await db.list_badges(),
        "my_badges": await db.list_user_badges(int(user["telegram_id"]), public_only=False),
    }


@app.patch("/api/me/badges")
async def api_update_my_badges(payload: BadgeVisibilityUpdate, user: Dict[str, Any] = Depends(current_user)):
    badges = await db.update_visible_badges(int(user["telegram_id"]), payload.visible_badge_ids)
    user["badges"] = badges
    return {"badges": badges, "user": user}


@app.get("/api/users/{telegram_id}")
async def api_public_user(telegram_id: int, user: Dict[str, Any] = Depends(current_user)):
    public_user = await db.get_public_user(telegram_id, int(user["telegram_id"]))
    if not public_user:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return {"user": public_user}


@app.get("/api/daily-puzzle")
async def api_daily_puzzle(user: Dict[str, Any] = Depends(current_user)):
    return await db.get_daily_puzzle(
        int(user["telegram_id"]),
        puzzle_date=city_today_key(user.get("profile_city") or DEFAULT_CITY),
    )


@app.post("/api/daily-puzzle/answer")
async def api_answer_daily_puzzle(payload: DailyPuzzleAnswer, user: Dict[str, Any] = Depends(current_user)):
    try:
        result = await db.answer_daily_puzzle(
            int(user["telegram_id"]),
            payload.selected_move,
            puzzle_date=city_today_key(user.get("profile_city") or DEFAULT_CITY),
        )
    except ValueError as exc:
        if str(exc) == "INVALID_MOVE":
            raise HTTPException(status_code=400, detail="Некорректный ход") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@app.get("/api/games")
async def api_games(city: str = DEFAULT_CITY, user: Dict[str, Any] = Depends(current_user)):
    if not is_supported_city(city):
        raise HTTPException(status_code=400, detail="Unsupported city")
    games = await db.list_games(city=canonical_city(city), viewer_telegram_id=int(user["telegram_id"]))
    return {"games": games}

@app.get("/api/cities/{city}/stats")
async def api_city_stats(city: str, user: Dict[str, Any] = Depends(current_user)):
    if not is_supported_city(city):
        raise HTTPException(status_code=404, detail="Unsupported city")
    return await db.city_stats(canonical_city(city))

@app.get("/api/cities/{city}/places")
async def api_city_places(city: str, user: Dict[str, Any] = Depends(current_user)):
    if not is_supported_city(city):
        raise HTTPException(status_code=404, detail="Unsupported city")
    return {"places": await db.popular_places(canonical_city(city))}


@app.get("/api/my")
async def api_my(user: Dict[str, Any] = Depends(current_user)):
    return await db.list_my_games(telegram_id=int(user["telegram_id"]))


@app.post("/api/games")
async def api_create_game(payload: GameCreate, user: Dict[str, Any] = Depends(current_user)):
    data = payload.model_dump()
    data["city"] = data.get("city") or DEFAULT_CITY
    try:
        game = await db.create_game(int(user["telegram_id"]), data, default_city=DEFAULT_CITY)
    except ValueError as exc:
        mapping = {
            "TOO_MANY_OPEN_GAMES": (400, "Достигнут лимит открытых заявок. Реферальные уровни повышают этот лимит."),
            "CREATE_RATE_LIMIT": (429, "Слишком много заявок подряд. Подожди несколько минут."),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc

    if bot:
        try:
            await notify_game_created(bot, int(user["telegram_id"]), game)
        except Exception:
            pass
        try:
            recipients = await db.list_users_for_new_request_notifications(
                exclude_telegram_id=int(user["telegram_id"]),
                city=game.get("city") or DEFAULT_CITY,
                game_format=game.get("game_format") or "",
                level=game.get("level") or "",
            )
            for recipient in recipients:
                try:
                    await notify_new_request(bot, int(recipient["telegram_id"]), game, WEBAPP_URL)
                except Exception:
                    pass
        except Exception:
            pass

    return {"game": game}


@app.patch("/api/games/{game_id}")
async def api_update_game(game_id: int, payload: GameCreate, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.update_game(game_id, int(user["telegram_id"]), payload.model_dump())
        return {"game": game}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_FOUND": (404, "Заявка не найдена"),
            "NOT_ALLOWED": (403, "Редактировать может только автор заявки"),
            "GAME_ALREADY_ACCEPTED": (400, "Нельзя редактировать заявку после принятия отклика"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/respond")
async def api_respond_game(game_id: int, payload: Optional[ResponseCreate] = None, user: Dict[str, Any] = Depends(current_user)):
    try:
        response = await db.create_response(game_id, int(user["telegram_id"]), payload.model_dump() if payload else {})
    except ValueError as exc:
        error = str(exc)
        if error == "GAME_NOT_FOUND":
            raise HTTPException(status_code=404, detail="Заявка не найдена") from exc
        if error == "CANNOT_RESPOND_TO_OWN_GAME":
            raise HTTPException(status_code=400, detail="Нельзя откликнуться на свою заявку") from exc
        if error == "GAME_IS_NOT_OPEN":
            raise HTTPException(status_code=400, detail="Заявка уже закрыта") from exc
        if error == "USER_BLOCKED":
            raise HTTPException(status_code=403, detail="Отклик невозможен: один из пользователей заблокировал другого") from exc
        if error == "RESPONSE_RATE_LIMIT":
            raise HTTPException(status_code=429, detail="Слишком много откликов подряд. Подожди несколько минут.") from exc
        raise HTTPException(status_code=400, detail=error) from exc

    if bot:
        try:
            await notify_creator_about_response(bot, db, int(response["id"]))
        except Exception:
            pass

    return {"response": response}


@app.post("/api/games/{game_id}/waitlist")
async def api_join_waitlist(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        return {"waitlist": await db.join_waitlist(game_id, int(user["telegram_id"]))}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_FOUND": (404, "Заявка не найдена"),
            "CANNOT_JOIN_OWN_WAITLIST": (400, "Нельзя вступить в очередь своей заявки"),
            "WAITLIST_NOT_AVAILABLE": (409, "Лист ожидания доступен только для занятой партии"),
            "USER_BLOCKED": (403, "Вступить в очередь невозможно"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.delete("/api/games/{game_id}/waitlist", status_code=204)
async def api_leave_waitlist(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    await db.leave_waitlist(game_id, int(user["telegram_id"]))
    return Response(status_code=204)

@app.get("/api/games/{game_id}/responses")
async def api_game_responses(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        return {"responses": await db.list_game_responses(game_id, int(user["telegram_id"]))}
    except ValueError as exc:
        status = 404 if str(exc) == "GAME_NOT_FOUND" else 403
        raise HTTPException(status_code=status, detail=str(exc)) from exc

@app.post("/api/responses/{response_id}/accept")
async def api_accept_response(response_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        accepted = await db.accept_response_for_creator(response_id, int(user["telegram_id"]))
    except ValueError as exc:
        mapping = {"RESPONSE_NOT_FOUND": 404, "NOT_ALLOWED": 403, "RESPONSE_ALREADY_PROCESSED": 409}
        raise HTTPException(status_code=mapping.get(str(exc), 400), detail=str(exc)) from exc
    if bot:
        try:
            await notify_response_accepted(bot, db, response_id, WEBAPP_URL)
        except Exception:
            pass
    return {"response": accepted}

@app.post("/api/responses/{response_id}/decline")
async def api_decline_response(response_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        declined = await db.decline_response_for_creator(response_id, int(user["telegram_id"]))
        return {"response": declined}
    except ValueError as exc:
        mapping = {"RESPONSE_NOT_FOUND": 404, "NOT_ALLOWED": 403, "RESPONSE_ALREADY_PROCESSED": 409}
        raise HTTPException(status_code=mapping.get(str(exc), 400), detail=str(exc)) from exc


@app.post("/api/games/{game_id}/cancel")
async def api_cancel_game(game_id: int, payload: Optional[CancelGameRequest] = None, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.cancel_game(game_id, int(user["telegram_id"]), payload.reason if payload else "")
    except ValueError as exc:
        error = str(exc)
        if error == "GAME_NOT_FOUND":
            raise HTTPException(status_code=404, detail="Заявка не найдена") from exc
        if error == "NOT_ALLOWED":
            raise HTTPException(status_code=403, detail="Можно отменить только свою подтвержденную или созданную заявку") from exc
        raise HTTPException(status_code=400, detail=error) from exc
    return {"game": game}


@app.post("/api/games/{game_id}/confirm")
async def api_confirm_game(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.confirm_game(game_id, int(user["telegram_id"]))
    except ValueError as exc:
        error = str(exc)
        if error == "GAME_NOT_FOUND":
            raise HTTPException(status_code=404, detail="Заявка не найдена") from exc
        if error == "GAME_NOT_CONFIRMED":
            raise HTTPException(status_code=400, detail="Встреча пока не подтверждена") from exc
        if error == "NOT_ALLOWED":
            raise HTTPException(status_code=403, detail="Подтвердить может только участник партии") from exc
        raise HTTPException(status_code=400, detail=error) from exc
    return {"game": game}

@app.post("/api/games/{game_id}/check-in")
async def api_check_in_game(game_id: int, payload: Optional[CheckInCreate] = None, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.check_in_game(game_id, int(user["telegram_id"]), payload.late_minutes if payload else 0)
        return {"game": game}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_CONFIRMED": (400, "Check-in доступен только для подтверждённой партии"),
            "NOT_ALLOWED": (403, "Check-in доступен только участникам партии"),
            "CHECK_IN_NOT_AVAILABLE": (400, "Check-in доступен за 45 минут до встречи и ещё 2 часа после начала"),
            "INVALID_GAME_TIME": (400, "Не удалось определить время встречи"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc

@app.post("/api/analytics/event", status_code=204)
async def api_track_event(payload: AnalyticsEventCreate, user: Dict[str, Any] = Depends(current_user)):
    await db.track_event(
        int(user["telegram_id"]),
        payload.event_name,
        json.dumps(payload.event_data, ensure_ascii=False, separators=(",", ":")),
    )
    return Response(status_code=204)


@app.post("/api/games/{game_id}/rate")
async def api_rate_game(game_id: int, payload: RatingCreate, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.submit_rating(
            game_id,
            int(user["telegram_id"]),
            score=payload.score,
            comment=payload.comment,
        )
    except ValueError as exc:
        error = str(exc)
        mapping = {
            "GAME_NOT_FOUND": (404, "Партия не найдена"),
            "GAME_NOT_CONFIRMED": (400, "Партия еще не подтверждена"),
            "RATING_NOT_AVAILABLE_YET": (400, "Оценку можно поставить через час после запланированной встречи"),
            "NOT_ALLOWED": (403, "Оценку может поставить только участник партии"),
            "ALREADY_RATED": (400, "Ты уже поставил оценку"),
            "INVALID_SCORE": (400, "Оценка должна быть от 1 до 5"),
        }
        status_code, detail = mapping.get(error, (400, error))
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return {"game": game}


@app.get("/api/games/{game_id}/chat")
async def api_get_chat(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        return await db.list_chat_messages(game_id, int(user["telegram_id"]))
    except ValueError as exc:
        error = str(exc)
        mapping = {
            "GAME_NOT_FOUND": (404, "Партия не найдена"),
            "CHAT_NOT_AVAILABLE": (400, "Чат доступен только после принятия отклика"),
            "NOT_ALLOWED": (403, "Чат доступен только участникам партии"),
        }
        status_code, detail = mapping.get(error, (400, error))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/chat")
async def api_send_chat_message(game_id: int, payload: ChatMessageCreate, user: Dict[str, Any] = Depends(current_user)):
    try:
        message = await db.create_chat_message(game_id, int(user["telegram_id"]), payload.text)
    except ValueError as exc:
        error = str(exc)
        mapping = {
            "GAME_NOT_FOUND": (404, "Партия не найдена"),
            "CHAT_NOT_AVAILABLE": (400, "Чат доступен только после принятия отклика"),
            "NOT_ALLOWED": (403, "Чат доступен только участникам партии"),
            "EMPTY_MESSAGE": (400, "Сообщение пустое"),
            "CHAT_RATE_LIMIT": (429, "Слишком много сообщений подряд. Подожди несколько минут."),
        }
        status_code, detail = mapping.get(error, (400, error))
        raise HTTPException(status_code=status_code, detail=detail) from exc

    if bot:
        try:
            await notify_new_chat_message(bot, message, WEBAPP_URL)
        except Exception:
            pass
    return {"message": message}


@app.post("/api/users/{telegram_id}/favorite")
async def api_toggle_favorite_user(telegram_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        return await db.toggle_favorite_player(int(user["telegram_id"]), telegram_id)
    except ValueError as exc:
        if str(exc) == "CANNOT_FAVORITE_SELF":
            raise HTTPException(status_code=400, detail="Нельзя добавить себя в избранное") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/users/{telegram_id}/block")
async def api_block_user(telegram_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        return await db.block_user(int(user["telegram_id"]), telegram_id)
    except ValueError as exc:
        if str(exc) == "CANNOT_BLOCK_SELF":
            raise HTTPException(status_code=400, detail="Нельзя заблокировать себя") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/users/{telegram_id}/report")
async def api_report_user(telegram_id: int, payload: UserReportCreate, user: Dict[str, Any] = Depends(current_user)):
    try:
        report = await db.report_user(int(user["telegram_id"]), telegram_id, payload.reason, payload.comment, payload.game_id)
        return {"report": report}
    except ValueError as exc:
        mapping = {
            "CANNOT_REPORT_SELF": (400, "Нельзя пожаловаться на себя"),
            "GAME_NOT_CONFIRMED": (400, "Жалобу по партии можно отправить только после принятой партии"),
            "NOT_ALLOWED": (403, "Жалобу по партии может отправить только участник партии"),
            "AFTER_GAME_ACTION_NOT_AVAILABLE_YET": (400, "Жалоба станет доступна одновременно с отзывом — через час после встречи"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/no-show")
async def api_report_no_show(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.report_no_show(game_id, int(user["telegram_id"]))
        return {"game": game}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_CONFIRMED": (400, "Партия ещё не подтверждена"),
            "NOT_ALLOWED": (403, "Отметить no-show может только участник партии"),
            "AFTER_GAME_ACTION_NOT_AVAILABLE_YET": (400, "No-show станет доступен одновременно с отзывом — через час после встречи"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/photos")
async def api_add_game_photo(game_id: int, payload: GamePhotoCreate, user: Dict[str, Any] = Depends(current_user)):
    try:
        photo = await db.add_game_photo(game_id, int(user["telegram_id"]), payload.photo_data_url, payload.caption)
        return {"photo": photo}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_CONFIRMED": (400, "Фото можно добавить только к подтверждённой партии"),
            "NOT_ALLOWED": (403, "Фото может добавить только участник партии"),
            "INVALID_PHOTO": (400, "Некорректное фото"),
            "PHOTO_TOO_LARGE": (400, "Фото слишком большое"),
            "AFTER_GAME_ACTION_NOT_AVAILABLE_YET": (400, "Фото можно добавить одновременно с отзывом — через час после встречи"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/rematch")
async def api_create_rematch(game_id: int, user: Dict[str, Any] = Depends(current_user)):
    try:
        game = await db.create_rematch(game_id, int(user["telegram_id"]))
        return {"game": game}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_CONFIRMED": (400, "Реванш можно предложить только после принятой партии"),
            "NOT_ALLOWED": (403, "Реванш может предложить только участник партии"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/place-rating")
async def api_rate_place(game_id: int, payload: PlaceRatingCreate, user: Dict[str, Any] = Depends(current_user)):
    try:
        place_rating = await db.submit_place_rating(game_id, int(user["telegram_id"]), payload.score, payload.comment)
        return {"place_rating": place_rating}
    except ValueError as exc:
        mapping = {
            "INVALID_SCORE": (400, "Оценка места должна быть от 1 до 5"),
            "ALREADY_RATED_PLACE": (400, "Ты уже оценил это место по этой партии"),
            "GAME_NOT_CONFIRMED": (400, "Место можно оценить только после подтверждённой партии"),
            "NOT_ALLOWED": (403, "Оценить место может только участник партии"),
            "AFTER_GAME_ACTION_NOT_AVAILABLE_YET": (400, "Оценка места доступна одновременно с отзывом — через час после встречи"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.post("/api/games/{game_id}/diary")
async def api_update_diary(game_id: int, payload: DiaryUpdate, user: Dict[str, Any] = Depends(current_user)):
    try:
        diary = await db.upsert_game_diary(game_id, int(user["telegram_id"]), payload.result, payload.notes)
        return {"diary": diary}
    except ValueError as exc:
        mapping = {
            "GAME_NOT_CONFIRMED": (400, "Дневник доступен после подтверждённой партии"),
            "NOT_ALLOWED": (403, "Дневник доступен только участнику партии"),
            "AFTER_GAME_ACTION_NOT_AVAILABLE_YET": (400, "Запись в дневник доступна через час после встречи"),
        }
        status_code, detail = mapping.get(str(exc), (400, str(exc)))
        raise HTTPException(status_code=status_code, detail=detail) from exc


@app.get("/api/diary")
async def api_diary(user: Dict[str, Any] = Depends(current_user)):
    return {"games": await db.list_diary(int(user["telegram_id"]))}




def _database_file_path() -> Path:
    return Path(DATABASE_PATH).expanduser().resolve()


def _backup_database_path(prefix: str = "before_restore") -> Path:
    db_path = _database_file_path()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return db_path.with_name(f"{db_path.stem}_{prefix}_{stamp}{db_path.suffix}")


async def _validate_uploaded_sqlite(path: Path) -> dict[str, Any]:
    """Validate that uploaded file is a readable SQLite database with expected core tables."""
    expected_tables = {"users", "game_requests", "responses"}
    try:
        async with aiosqlite.connect(str(path)) as conn:
            conn.row_factory = aiosqlite.Row
            integrity = await conn.execute_fetchall("PRAGMA integrity_check")
            integrity_result = str(integrity[0][0]) if integrity else "unknown"
            if integrity_result.lower() != "ok":
                raise HTTPException(status_code=400, detail=f"SQLite integrity_check failed: {integrity_result}")
            rows = await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {str(row[0]) for row in rows}
            missing = sorted(expected_tables - tables)
            if missing:
                raise HTTPException(status_code=400, detail=f"Uploaded DB is missing required tables: {', '.join(missing)}")
            counts: dict[str, int] = {}
            for table in sorted(expected_tables):
                count_rows = await conn.execute_fetchall(f"SELECT COUNT(*) FROM {table}")
                counts[table] = int(count_rows[0][0]) if count_rows else 0
            return {"integrity": integrity_result, "tables": len(tables), "counts": counts}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Uploaded file is not a valid SQLite database: {exc}") from exc



async def _admin_table_rows(table: str, columns: str = "*", order_by: str = "id DESC", limit: int = 100) -> list[dict[str, Any]]:
    allowed_tables = {
        "users", "game_requests", "responses", "ratings", "user_reports", "game_photos",
        "chat_messages", "badges", "user_badges", "user_blocks", "daily_puzzle_attempts",
        "admin_audit_log",
    }
    if table not in allowed_tables:
        raise HTTPException(status_code=400, detail="Unsupported table")
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(f"SELECT {columns} FROM {table} ORDER BY {order_by} LIMIT ?", (limit,))
        return [dict(row) for row in rows]


def _csv_response(filename: str, rows: list[dict[str, Any]]) -> Response:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        buffer.write("")
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/admin/health")
async def api_admin_health(_: None = Depends(require_admin)):
    reset_count = await db.normalize_all_puzzle_streaks()
    return {
        "ok": True,
        "version": "1.4.1",
        "webapp_url": WEBAPP_URL,
        "database_path": DATABASE_PATH,
        "database_exists": Path(DATABASE_PATH).exists(),
        "database_size_bytes": Path(DATABASE_PATH).stat().st_size if Path(DATABASE_PATH).exists() else 0,
        "volume_mount_path": os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ""),
        "database_backups": db.list_local_backups(),
        "puzzle_streaks_reset_now": reset_count,
        "puzzle_pack_size": len(db.daily_puzzles),
        "puzzle_source": db.puzzle_source,
    }


@app.get("/api/admin/snapshot")
async def api_admin_snapshot(_: None = Depends(require_admin)):
    await db.normalize_all_puzzle_streaks()
    return await db.admin_export_snapshot()


@app.get("/api/admin/users")
async def api_admin_users(
    limit: int = 100,
    q: str = Query(default="", max_length=100),
    _: None = Depends(require_admin),
):
    limit = max(1, min(limit, 500))
    await db.normalize_all_puzzle_streaks()
    if q.strip():
        pattern = f"%{q.strip()}%"
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            conn.row_factory = aiosqlite.Row
            rows = await conn.execute_fetchall(
                """
                SELECT * FROM users
                WHERE CAST(telegram_id AS TEXT) LIKE ? OR username LIKE ?
                   OR display_name LIKE ? OR first_name LIKE ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (pattern, pattern, pattern, pattern, limit),
            )
        return {"users": [dict(row) for row in rows]}
    return {"users": await _admin_table_rows("users", order_by="created_at DESC", limit=limit)}


@app.get("/api/admin/users/{telegram_id}")
async def api_admin_user_detail(telegram_id: int, _: None = Depends(require_admin)):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        if not rows:
            raise HTTPException(status_code=404, detail="User not found")
        blocked = await conn.execute_fetchall(
            "SELECT 1 FROM user_blocks WHERE blocker_telegram_id = 0 AND blocked_telegram_id = ?",
            (telegram_id,),
        )
        reports = await conn.execute_fetchall(
            "SELECT * FROM user_reports WHERE reported_telegram_id = ? ORDER BY created_at DESC LIMIT 20",
            (telegram_id,),
        )
    return {"user": dict(rows[0]), "admin_blocked": bool(blocked), "reports": [dict(row) for row in reports]}


@app.get("/api/admin/games")
async def api_admin_games(limit: int = 100, _: None = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    return {"games": await _admin_table_rows("game_requests", order_by="created_at DESC", limit=limit)}


@app.get("/api/admin/reports")
async def api_admin_reports(limit: int = 100, _: None = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    return {"reports": await _admin_table_rows("user_reports", order_by="created_at DESC", limit=limit)}


@app.post("/api/admin/reports/{report_id}/resolve")
async def api_admin_resolve_report(
    report_id: int,
    payload: AdminReportResolve,
    x_admin_actor: str = Header(default=""),
    _: None = Depends(require_admin),
):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        cursor = await conn.execute(
            "UPDATE user_reports SET status = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
            (payload.status, int(x_admin_actor) if x_admin_actor.isdigit() else None, now_iso(), report_id),
        )
        await conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Report not found")
    await record_admin_action(x_admin_actor, f"report_{payload.status}", "report", report_id, {"note": payload.note})
    return {"ok": True, "report_id": report_id, "status": payload.status}


@app.get("/api/admin/audit")
async def api_admin_audit(limit: int = 100, _: None = Depends(require_admin)):
    limit = max(1, min(limit, 500))
    return {"actions": await _admin_table_rows("admin_audit_log", order_by="created_at DESC", limit=limit)}


@app.get("/api/admin/puzzles")
async def api_admin_puzzles(_: None = Depends(require_admin)):
    return {"count": len(db.daily_puzzles), "source": db.puzzle_source, "puzzles": db.daily_puzzles}


@app.post("/api/admin/broadcast")
async def api_admin_broadcast(
    payload: AdminBroadcastCreate,
    x_admin_actor: str = Header(default=""),
    _: None = Depends(require_admin),
):
    if not bot:
        raise HTTPException(status_code=400, detail="Bot is not configured/running")
    if payload.telegram_id:
        recipients = [int(payload.telegram_id)]
    else:
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            rows = await conn.execute_fetchall("SELECT telegram_id FROM users ORDER BY created_at DESC")
            recipients = [int(row[0]) for row in rows]
    sent = 0
    failed = 0
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id=chat_id, text=payload.text)
            sent += 1
        except Exception:
            failed += 1
    await record_admin_action(
        x_admin_actor,
        "broadcast",
        "user" if payload.telegram_id else "all_users",
        payload.telegram_id or "*",
        {"sent": sent, "failed": failed},
    )
    return {"sent": sent, "failed": failed, "total": len(recipients)}


@app.post("/api/admin/badges")
async def api_admin_create_badge(payload: AdminBadgeCreate, _: None = Depends(require_admin)):
    badge = await db.create_badge(payload.title, payload.icon, payload.description, payload.color)
    return {"badge": badge}


@app.post("/api/admin/badges/issue")
async def api_admin_issue_badge(payload: AdminBadgeIssue, _: None = Depends(require_admin)):
    result = await db.grant_badge(payload.telegram_id, payload.badge_id)
    return result


@app.post("/api/admin/users/{telegram_id}/block")
async def api_admin_block_user(
    telegram_id: int,
    x_admin_actor: str = Header(default=""),
    _: None = Depends(require_admin),
):
    ts = now_iso()
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO user_blocks (blocker_telegram_id, blocked_telegram_id, created_at) VALUES (0, ?, ?)",
            (telegram_id, ts),
        )
        await conn.commit()
    await record_admin_action(x_admin_actor, "user_block", "user", telegram_id)
    return {"blocked": True, "telegram_id": telegram_id}


@app.post("/api/admin/users/{telegram_id}/unblock")
async def api_admin_unblock_user(
    telegram_id: int,
    x_admin_actor: str = Header(default=""),
    _: None = Depends(require_admin),
):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute("DELETE FROM user_blocks WHERE blocker_telegram_id = 0 AND blocked_telegram_id = ?", (telegram_id,))
        await conn.commit()
    await record_admin_action(x_admin_actor, "user_unblock", "user", telegram_id)
    return {"blocked": False, "telegram_id": telegram_id}


@app.post("/api/admin/games/{game_id}/cancel")
async def api_admin_cancel_game(
    game_id: int,
    x_admin_actor: str = Header(default=""),
    _: None = Depends(require_admin),
):
    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute(
            "UPDATE game_requests SET status = 'cancelled', cancel_reason = 'Admin moderation', updated_at = ? WHERE id = ?",
            (now_iso(), game_id),
        )
        await conn.commit()
    await record_admin_action(x_admin_actor, "game_cancel", "game", game_id)
    return {"cancelled": True, "game_id": game_id}


@app.get("/api/admin/export/users.csv")
async def api_admin_export_users(_: None = Depends(require_admin)):
    rows = await _admin_table_rows("users", order_by="created_at DESC", limit=10000)
    return _csv_response("users.csv", rows)


@app.get("/api/admin/export/games.csv")
async def api_admin_export_games(_: None = Depends(require_admin)):
    rows = await _admin_table_rows("game_requests", order_by="created_at DESC", limit=10000)
    return _csv_response("games.csv", rows)


@app.get("/api/admin/db/info")
async def api_admin_db_info(_: None = Depends(require_admin)):
    db_path = _database_file_path()
    info = {
        "database_path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
    }
    if db_path.exists():
        try:
            async with aiosqlite.connect(str(db_path)) as conn:
                rows = await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                info["tables"] = [row[0] for row in rows]
                for table in ["users", "game_requests", "responses", "ratings", "badges", "user_badges"]:
                    if table in info["tables"]:
                        count_rows = await conn.execute_fetchall(f"SELECT COUNT(*) FROM {table}")
                        info[f"{table}_count"] = int(count_rows[0][0]) if count_rows else 0
        except Exception as exc:
            info["error"] = str(exc)
    return info


@app.get("/api/admin/db/backup")
async def api_admin_db_backup(_: None = Depends(require_admin)):
    db_path = _database_file_path()
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Database file not found")
    return FileResponse(
        path=str(db_path),
        media_type="application/octet-stream",
        filename="chess_irl_railway_backup.sqlite3",
    )


@app.post("/api/admin/db/restore")
async def api_admin_db_restore(file: UploadFile = File(...), _: None = Depends(require_admin)):
    db_path = _database_file_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not file.filename.lower().endswith((".sqlite3", ".sqlite", ".db")):
        raise HTTPException(status_code=400, detail="Upload a .sqlite3/.sqlite/.db file")

    temp_path = db_path.with_name(f".{db_path.name}.upload.tmp")
    try:
        uploaded_size = 0
        max_upload_size = 250 * 1024 * 1024
        with temp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                uploaded_size += len(chunk)
                if uploaded_size > max_upload_size:
                    raise HTTPException(status_code=413, detail="Database backup is too large")
                out.write(chunk)
        validation = await _validate_uploaded_sqlite(temp_path)

        backup_path = None
        if db_path.exists():
            backup_path = _backup_database_path("before_restore")
            shutil.copy2(db_path, backup_path)

        os.replace(temp_path, db_path)
        # Re-run migrations/index creation on the restored DB so older local DBs are upgraded.
        await db.init()
        return {
            "ok": True,
            "restored_to": str(db_path),
            "size_bytes": db_path.stat().st_size,
            "backup_created": str(backup_path) if backup_path else None,
            "validation": validation,
            "message": "Database restored. Restart/redeploy the Railway service if the bot still shows stale data.",
        }
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass


@app.post("/api/internal/accept/{response_id}")
async def api_internal_accept_response(response_id: int, x_admin_token: str = Header(default="")):
    # Protected helper endpoint for potential future webhook/admin integration.
    # Normal Telegram accept flow uses bot callback buttons and does not need this endpoint.
    require_admin(x_admin_token)
    try:
        accepted = await db.accept_response(response_id)
    except ValueError as exc:
        if str(exc) == "RESPONSE_NOT_FOUND":
            raise HTTPException(status_code=404, detail="Отклик не найден") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if bot:
        try:
            await notify_response_accepted(bot, db, response_id, WEBAPP_URL)
        except Exception:
            pass
    return {"response": accepted}
