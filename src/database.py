from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import base64
import binascii
import csv
import io
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import urlopen
from .cities import canonical_city, city_info, city_today_key
from zoneinfo import ZoneInfo

import aiosqlite

try:
    import chess
except Exception:  # pragma: no cover - fallback when python-chess is not installed yet
    chess = None

try:
    import zstandard as zstd
except Exception:  # pragma: no cover - fallback when zstandard is not installed yet
    zstd = None


STATUS_OPEN = "open"
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"


def validate_image_data_url(value: str, max_encoded_length: int) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) > max_encoded_length:
        raise ValueError("PHOTO_TOO_LARGE")
    match = re.fullmatch(r"data:image/(png|jpe?g|webp|gif);base64,([A-Za-z0-9+/]+={0,2})", value, re.IGNORECASE)
    if not match:
        raise ValueError("INVALID_PHOTO")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("INVALID_PHOTO") from exc
    kind = match.group(1).lower()
    valid = {
        "png": payload.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": payload.startswith(b"\xff\xd8\xff"),
        "jpeg": payload.startswith(b"\xff\xd8\xff"),
        "gif": payload.startswith((b"GIF87a", b"GIF89a")),
        "webp": len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP",
    }.get(kind, False)
    if not valid:
        raise ValueError("INVALID_PHOTO")
    return value
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_COMPLETED = "completed"
MOSCOW_OFFSET = timedelta(hours=3)
MOSCOW_TZ = timezone(MOSCOW_OFFSET)
LICHESS_PUZZLE_DB_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
PUZZLE_CACHE_FILENAME = "daily_puzzles_lichess_mate1_verified_300.json"
PUZZLE_TARGET_COUNT = 300


# Small offline fallback. On normal startup the app streams the official public
# Lichess puzzle database, filters mateIn1 puzzles, and verifies every position
# locally with python-chess.
DAILY_PUZZLES = [
    {
        "id": 101,
        "title": "Запертый король",
        "side": "Белые начинают",
        "fen": "7k/4QKpp/8/8/8/8/8/8 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "e7f8",
        "solution_san": "Qf8#",
        "explanation": "Qf8# — ферзь атакует короля по 8-й горизонтали, а поля отхода закрыты собственными пешками и контролем белого короля.",
    },
    {
        "id": 102,
        "title": "Ладья на последней горизонтали",
        "side": "Белые начинают",
        "fen": "7k/R5pp/5K2/8/8/8/8/8 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "a7a8",
        "solution_san": "Ra8#",
        "explanation": "Ra8# — ладья даёт мат по 8-й горизонтали. Король h8 не имеет свободных полей из-за собственных пешек.",
    },
    {
        "id": 103,
        "title": "Вертикальный удар",
        "side": "Белые начинают",
        "fen": "7k/5K1p/8/8/8/8/8/6R1 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "g1g8",
        "solution_san": "Rg8#",
        "explanation": "Rg8# — ладья атакует короля рядом, а белый король защищает ладью и контролирует ключевые поля.",
    },
    {
        "id": 104,
        "title": "Длинный ход ферзя",
        "side": "Белые начинают",
        "fen": "7k/5Kpp/8/8/8/8/8/1Q6 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "b1b8",
        "solution_san": "Qb8#",
        "explanation": "Qb8# — ферзь перекрывает 8-ю горизонталь, а король h8 зажат собственными пешками.",
    },
    {
        "id": 105,
        "title": "Ладья издалека",
        "side": "Белые начинают",
        "fen": "6k1/5ppp/4K3/8/8/8/8/R7 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "a1a8",
        "solution_san": "Ra8#",
        "explanation": "Ra8# — ладья атакует короля g8 по 8-й горизонтали и контролирует все поля отхода.",
    },
    {
        "id": 106,
        "title": "Ферзь перекрывает линию",
        "side": "Белые начинают",
        "fen": "6k1/4Kppp/8/8/2Q5/8/8/8 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "c4c8",
        "solution_san": "Qc8#",
        "explanation": "Qc8# — ферзь бьёт по 8-й горизонтали, а собственные пешки чёрных закрывают королю выход.",
    },
    {
        "id": 107,
        "title": "Диагональный финал",
        "side": "Белые начинают",
        "fen": "7k/5Kpp/8/8/8/Q7/8/8 w - - 0 1",
        "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
        "solution_move": "a3f8",
        "solution_san": "Qf8#",
        "explanation": "Qf8# — ферзь приходит по диагонали и ставит мат по 8-й горизонтали.",
    },
]


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def parse_local_game_datetime(date_label: Optional[str], time_label: Optional[str], city: Optional[str] = None) -> Optional[datetime]:
    """
    Parses user-facing game date/time as Minsk/Moscow local time (UTC+3).
    This avoids the old bug where local 22:30 was stored/read as 22:30 UTC.
    """
    if not date_label or not time_label:
        return None
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", str(date_label))
    time_match = re.search(r"(\d{1,2})[:.](\d{2})", str(time_label))
    if not date_match or not time_match:
        return None
    try:
        y, m, d = [int(x) for x in date_match.group(1).split("-")]
        hh = int(time_match.group(1))
        mm = int(time_match.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        tz = ZoneInfo(city_info(city)["timezone"]) if city else MOSCOW_TZ
        return datetime(y, m, d, hh, mm, tzinfo=tz).astimezone(timezone.utc)
    except Exception:
        return None


def effective_game_datetime(game: Dict[str, Any]) -> Optional[datetime]:
    """
    Source of truth for reminders/ratings:
    first rebuild from user-facing date_label/time_label as UTC+3,
    then fallback to stored scheduled_at for older/flexible/invalid entries.
    """
    local_dt = parse_local_game_datetime(game.get("date_label"), game.get("time_label"), game.get("city"))
    if local_dt:
        return local_dt
    return parse_iso(game.get("scheduled_at"))



class Database:
    def __init__(self, path: str):
        self.path = path
        self.daily_puzzles = list(DAILY_PUZZLES)
        self.puzzle_source = "offline-fallback"

    async def init(self) -> None:
        self._load_or_fetch_daily_puzzles()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._backup_before_migration()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    city TEXT DEFAULT 'Минск',
                    display_name TEXT,
                    show_telegram_username INTEGER NOT NULL DEFAULT 0,
                    level TEXT DEFAULT 'Средний',
                    bio TEXT DEFAULT '',
                    profile_city TEXT DEFAULT 'Минск',
                    photo_data_url TEXT DEFAULT '',
                    rating_avg REAL DEFAULT 0,
                    rating_count INTEGER DEFAULT 0,
                    puzzle_streak INTEGER DEFAULT 0,
                    puzzle_best_streak INTEGER DEFAULT 0,
                    puzzle_solved_count INTEGER DEFAULT 0,
                    puzzle_last_solved_date TEXT DEFAULT '',
                    notify_game_reminders INTEGER NOT NULL DEFAULT 1,
                    notify_new_requests INTEGER NOT NULL DEFAULT 0,
                    notify_puzzle_streak INTEGER NOT NULL DEFAULT 1,
                    theme_mode TEXT DEFAULT 'light',
                    ui_language TEXT DEFAULT '',
                    onboarding_completed INTEGER NOT NULL DEFAULT 0,
                    puzzle_reminder_sent_date TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS game_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_telegram_id INTEGER NOT NULL,
                    city TEXT NOT NULL DEFAULT 'Минск',
                    place TEXT NOT NULL,
                    area TEXT,
                    address TEXT,
                    place_id TEXT DEFAULT '',
                    latitude REAL,
                    longitude REAL,
                    map_url TEXT DEFAULT '',
                    date_label TEXT NOT NULL,
                    time_label TEXT NOT NULL,
                    scheduled_at TEXT,
                    game_format TEXT NOT NULL,
                    level TEXT NOT NULL,
                    has_board INTEGER NOT NULL DEFAULT 1,
                    comment TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    accepted_response_id INTEGER,
                    creator_confirmed INTEGER NOT NULL DEFAULT 0,
                    responder_confirmed INTEGER NOT NULL DEFAULT 0,
                    reminder_3h_sent INTEGER NOT NULL DEFAULT 0,
                    reminder_30m_sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (creator_telegram_id) REFERENCES users(telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    responder_telegram_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    proposed_date_label TEXT DEFAULT '',
                    proposed_time_label TEXT DEFAULT '',
                    proposed_comment TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(game_id, responder_telegram_id),
                    FOREIGN KEY (game_id) REFERENCES game_requests(id),
                    FOREIGN KEY (responder_telegram_id) REFERENCES users(telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    rater_telegram_id INTEGER NOT NULL,
                    rated_telegram_id INTEGER NOT NULL,
                    score INTEGER NOT NULL,
                    comment TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(game_id, rater_telegram_id),
                    FOREIGN KEY (game_id) REFERENCES game_requests(id),
                    FOREIGN KEY (rater_telegram_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (rated_telegram_id) REFERENCES users(telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    sender_telegram_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (game_id) REFERENCES game_requests(id),
                    FOREIGN KEY (sender_telegram_id) REFERENCES users(telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_puzzle_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    puzzle_date TEXT NOT NULL,
                    puzzle_id INTEGER NOT NULL,
                    selected_option INTEGER,
                    selected_move TEXT DEFAULT '',
                    solved INTEGER NOT NULL DEFAULT 0,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(telegram_id, puzzle_date),
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                )
                """
            )


            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_blocks (
                    blocker_telegram_id INTEGER NOT NULL,
                    blocked_telegram_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (blocker_telegram_id, blocked_telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS favorite_players (
                    owner_telegram_id INTEGER NOT NULL,
                    favorite_telegram_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (owner_telegram_id, favorite_telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reporter_telegram_id INTEGER NOT NULL,
                    reported_telegram_id INTEGER NOT NULL,
                    game_id INTEGER,
                    reason TEXT NOT NULL,
                    comment TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS game_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    uploader_telegram_id INTEGER NOT NULL,
                    photo_data_url TEXT NOT NULL,
                    caption TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS place_ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    rater_telegram_id INTEGER NOT NULL,
                    place_key TEXT NOT NULL,
                    place_name TEXT NOT NULL,
                    address TEXT DEFAULT '',
                    map_url TEXT DEFAULT '',
                    score INTEGER NOT NULL,
                    comment TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(game_id, rater_telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS game_diary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    owner_telegram_id INTEGER NOT NULL,
                    result TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(game_id, owner_telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS badges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    icon TEXT NOT NULL DEFAULT '🏅',
                    description TEXT DEFAULT '',
                    color TEXT DEFAULT '#2f8a4b',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_badges (
                    telegram_id INTEGER NOT NULL,
                    badge_id INTEGER NOT NULL,
                    is_visible INTEGER NOT NULL DEFAULT 0,
                    note TEXT DEFAULT '',
                    awarded_at TEXT NOT NULL,
                    PRIMARY KEY (telegram_id, badge_id),
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (badge_id) REFERENCES badges(id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER,
                    event_name TEXT NOT NULL,
                    event_data TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_telegram_id INTEGER,
                    action TEXT NOT NULL,
                    target_type TEXT DEFAULT '',
                    target_id TEXT DEFAULT '',
                    details TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS game_waitlist (
                    game_id INTEGER NOT NULL,
                    telegram_id INTEGER NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (game_id, telegram_id),
                    FOREIGN KEY (game_id) REFERENCES game_requests(id),
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS referral_events (
                    referred_telegram_id INTEGER PRIMARY KEY,
                    inviter_telegram_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'registered',
                    registered_at TEXT NOT NULL,
                    activated_at TEXT,
                    reward_points INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (referred_telegram_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (inviter_telegram_id) REFERENCES users(telegram_id)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS city_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL,
                    city_name TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
                )
                """
            )

            # Lightweight migrations from older MVP versions.
            await self._add_column_if_missing(db, "users", "display_name", "TEXT")
            await self._add_column_if_missing(db, "users", "show_telegram_username", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "users", "level", "TEXT DEFAULT 'Средний'")
            await self._add_column_if_missing(db, "users", "bio", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "users", "profile_city", "TEXT DEFAULT 'Минск'")
            await self._add_column_if_missing(db, "users", "photo_data_url", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "users", "rating_avg", "REAL DEFAULT 0")
            await self._add_column_if_missing(db, "users", "rating_count", "INTEGER DEFAULT 0")
            await self._add_column_if_missing(db, "users", "puzzle_streak", "INTEGER DEFAULT 0")
            await self._add_column_if_missing(db, "users", "puzzle_best_streak", "INTEGER DEFAULT 0")
            await self._add_column_if_missing(db, "users", "puzzle_solved_count", "INTEGER DEFAULT 0")
            await self._add_column_if_missing(db, "users", "puzzle_last_solved_date", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "users", "notify_game_reminders", "INTEGER NOT NULL DEFAULT 1")
            await self._add_column_if_missing(db, "users", "notify_new_requests", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "users", "notify_puzzle_streak", "INTEGER NOT NULL DEFAULT 1")
            await self._add_column_if_missing(db, "users", "theme_mode", "TEXT DEFAULT 'light'")
            await self._add_column_if_missing(db, "users", "ui_language", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "users", "puzzle_reminder_sent_date", "TEXT DEFAULT ''")
            # Preserve the flow for all existing accounts; only new registrations see city selection.
            await self._add_column_if_missing(db, "users", "onboarding_completed", "INTEGER NOT NULL DEFAULT 1")
            await self._add_column_if_missing(db, "users", "invited_by", "INTEGER")
            await self._add_column_if_missing(db, "users", "invite_count", "INTEGER DEFAULT 0")
            await self._add_column_if_missing(db, "users", "referral_points", "INTEGER DEFAULT 0")
            await self._add_column_if_missing(db, "users", "subscription_format", "TEXT DEFAULT 'all'")
            await self._add_column_if_missing(db, "users", "subscription_level", "TEXT DEFAULT 'all'")
            await self._add_column_if_missing(db, "daily_puzzle_attempts", "selected_move", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "referral_events", "registration_notified_at", "TEXT")
            await self._add_column_if_missing(db, "referral_events", "activation_notified_at", "TEXT")

            await self._add_column_if_missing(db, "game_requests", "place_id", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "latitude", "REAL")
            await self._add_column_if_missing(db, "game_requests", "longitude", "REAL")
            await self._add_column_if_missing(db, "game_requests", "map_url", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "scheduled_at", "TEXT")
            await self._add_column_if_missing(db, "game_requests", "accepted_response_id", "INTEGER")
            await self._add_column_if_missing(db, "game_requests", "creator_confirmed", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "game_requests", "responder_confirmed", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "game_requests", "reminder_3h_sent", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "game_requests", "reminder_30m_sent", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "game_requests", "is_flexible", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "game_requests", "time_window_start", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "time_window_end", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "cancel_reason", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "no_show_reported_by", "INTEGER")
            await self._add_column_if_missing(db, "game_requests", "no_show_target_id", "INTEGER")
            await self._add_column_if_missing(db, "game_requests", "creator_checked_in_at", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "responder_checked_in_at", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "game_requests", "creator_late_minutes", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "game_requests", "responder_late_minutes", "INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "responses", "proposed_date_label", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "responses", "proposed_time_label", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "responses", "proposed_comment", "TEXT DEFAULT ''")
            await self._add_column_if_missing(db, "user_reports", "status", "TEXT NOT NULL DEFAULT 'open'")
            await self._add_column_if_missing(db, "user_reports", "resolved_by", "INTEGER")
            await self._add_column_if_missing(db, "user_reports", "resolved_at", "TEXT DEFAULT ''")


            # Performance indexes for faster Mini App lists and admin views.
            for sql in [
                "CREATE INDEX IF NOT EXISTS idx_game_requests_city_status_scheduled ON game_requests(city, status, scheduled_at)",
                "CREATE INDEX IF NOT EXISTS idx_game_requests_creator_updated ON game_requests(creator_telegram_id, updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_game_requests_status_scheduled ON game_requests(status, scheduled_at)",
                "CREATE INDEX IF NOT EXISTS idx_responses_game_status ON responses(game_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_responses_responder_updated ON responses(responder_telegram_id, updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_chat_messages_game_created ON chat_messages(game_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_ratings_rated ON ratings(rated_telegram_id)",
                "CREATE INDEX IF NOT EXISTS idx_daily_puzzle_attempts_user_date ON daily_puzzle_attempts(telegram_id, puzzle_date)",
                "CREATE INDEX IF NOT EXISTS idx_user_reports_created ON user_reports(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_game_photos_game_created ON game_photos(game_id, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_game_diary_owner ON game_diary(owner_telegram_id, updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_analytics_event_created ON analytics_events(event_name, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_waitlist_game_position ON game_waitlist(game_id, position, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_referrals_inviter_status ON referral_events(inviter_telegram_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_user_reports_status_created ON user_reports(status, created_at)",
            ]:
                await db.execute(sql)
            await db.execute(
                """
                INSERT OR IGNORE INTO referral_events
                    (referred_telegram_id, inviter_telegram_id, status, registered_at)
                SELECT telegram_id, invited_by, 'registered', created_at
                FROM users
                WHERE invited_by IS NOT NULL AND invited_by != telegram_id
                """
            )

            await db.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            meta_rows = await db.execute_fetchall("SELECT value FROM app_meta WHERE key = 'v081_new_requests_default_off'")
            if not meta_rows:
                await db.execute("UPDATE users SET notify_new_requests = 0 WHERE COALESCE(notify_new_requests, 1) = 1")
                await db.execute("INSERT INTO app_meta (key, value) VALUES ('v081_new_requests_default_off', '1')")

            await db.commit()

        await self.expire_old_games()
        await self.normalize_all_puzzle_streaks()
        await self.refresh_all_user_ratings()

    def _backup_before_migration(self) -> None:
        source_path = Path(self.path)
        if not source_path.exists() or source_path.stat().st_size == 0:
            return
        backup_dir = source_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = backup_dir / f"{source_path.stem}-{timestamp}.sqlite3"
        with sqlite3.connect(str(source_path)) as source, sqlite3.connect(str(destination)) as target:
            source.backup(target)
        backups = sorted(
            backup_dir.glob(f"{source_path.stem}-*.sqlite3"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale_backup in backups[10:]:
            stale_backup.unlink(missing_ok=True)

    def list_local_backups(self) -> List[Dict[str, Any]]:
        source_path = Path(self.path)
        backup_dir = source_path.parent / "backups"
        if not backup_dir.exists():
            return []
        return [
            {
                "name": item.name,
                "size_bytes": item.stat().st_size,
                "modified_at": datetime.fromtimestamp(item.stat().st_mtime, timezone.utc).isoformat(),
            }
            for item in sorted(backup_dir.glob(f"{source_path.stem}-*.sqlite3"), reverse=True)[:10]
        ]

    async def _add_column_if_missing(self, db: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
        rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
        columns = {row[1] for row in rows}
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    async def expire_old_games(self) -> None:
        now = now_dt()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT id, date_label, time_label, scheduled_at, status
                FROM game_requests
                WHERE status IN ('open', 'pending')
                """
            )
            for row in rows:
                game = dict(row)
                scheduled_at = effective_game_datetime(game)
                if scheduled_at and scheduled_at < now:
                    await db.execute(
                        "UPDATE game_requests SET status = 'expired', updated_at = ? WHERE id = ?",
                        (now_iso(), game["id"]),
                    )
            await db.commit()

    async def upsert_user(self, tg_user: Dict[str, Any], default_city: str = "Минск") -> Dict[str, Any]:
        telegram_id = int(tg_user["id"])
        default_city = canonical_city(default_city)
        created_updated = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            existing = await db.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            suggested_display_name = (tg_user.get("first_name") or tg_user.get("username") or "Игрок").strip()
            if existing:
                current = dict(existing[0])
                display_name = current.get("display_name") or suggested_display_name
                await db.execute(
                    """
                    UPDATE users
                    SET username = ?, first_name = ?, last_name = ?, language_code = ?,
                        display_name = ?, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (
                        tg_user.get("username"),
                        tg_user.get("first_name"),
                        tg_user.get("last_name"),
                        tg_user.get("language_code"),
                        display_name,
                        created_updated,
                        telegram_id,
                    ),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO users (
                        telegram_id, username, first_name, last_name, language_code, city,
                        display_name, show_telegram_username, level, bio, profile_city,
                        photo_data_url, rating_avg, rating_count,
                        puzzle_streak, puzzle_best_streak, puzzle_solved_count, puzzle_last_solved_date,
                        notify_game_reminders, notify_new_requests, notify_puzzle_streak, puzzle_reminder_sent_date,
                        onboarding_completed, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'Средний', '', ?, '', 0, 0, 0, 0, 0, '', 1, 0, 1, '', 0, ?, ?)
                    """,
                    (
                        telegram_id,
                        tg_user.get("username"),
                        tg_user.get("first_name"),
                        tg_user.get("last_name"),
                        tg_user.get("language_code"),
                        default_city,
                        suggested_display_name,
                        default_city,
                        created_updated,
                        created_updated,
                    ),
                )
            await db.commit()
            return await self.get_user(telegram_id) or {}

    async def get_user(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            return self._normalize_user(dict(rows[0])) if rows else None

    async def update_user_profile(self, telegram_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        ts = now_iso()
        display_name = (data.get("display_name") or "Игрок").strip()[:80]
        profile_city = canonical_city(data.get("profile_city") or data.get("city") or "Минск")
        level = (data.get("level") or "Средний").strip()[:80]
        bio = (data.get("bio") or "").strip()[:300]
        photo_data_url = validate_image_data_url(data.get("photo_data_url") or "", 2_000_000)
        show_username = 1 if data.get("show_telegram_username") else 0
        notify_game_reminders = 1 if data.get("notify_game_reminders", True) else 0
        notify_new_requests = 1 if data.get("notify_new_requests", False) else 0
        notify_puzzle_streak = 1 if data.get("notify_puzzle_streak", True) else 0
        theme_mode = (data.get("theme_mode") or "light").strip().lower()
        if theme_mode not in {"light", "dark", "system"}:
            theme_mode = "light"
        ui_language = (data.get("ui_language") or "").strip().lower()
        if ui_language not in {"ru", "en"}:
            ui_language = ""
        subscription_format = (data.get("subscription_format") or "all").strip().lower()[:40]
        subscription_level = (data.get("subscription_level") or "all").strip().lower()[:80]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                UPDATE users
                SET display_name = ?, profile_city = ?, city = ?, level = ?, bio = ?,
                    show_telegram_username = ?, photo_data_url = ?,
                    notify_game_reminders = ?, notify_new_requests = ?, notify_puzzle_streak = ?,
                    theme_mode = ?, ui_language = ?, subscription_format = ?,
                    subscription_level = ?, updated_at = ?
                WHERE telegram_id = ?
                """,
                (display_name, profile_city, profile_city, level, bio, show_username, photo_data_url,
                 notify_game_reminders, notify_new_requests, notify_puzzle_streak, theme_mode, ui_language,
                 subscription_format, subscription_level, ts, telegram_id),
            )
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            if not rows:
                raise ValueError("USER_NOT_FOUND")
            return self._normalize_user(dict(rows[0]))

    async def update_user_preferences(
        self,
        telegram_id: int,
        *,
        ui_language: Optional[str] = None,
        profile_city: Optional[str] = None,
        notify_new_requests: Optional[bool] = None,
    ) -> Dict[str, Any]:
        language = (ui_language or "").strip().lower() if ui_language is not None else None
        if language is not None and language not in {"ru", "en"}:
            raise ValueError("INVALID_LANGUAGE")
        city = canonical_city(profile_city) if profile_city is not None else None
        if language is None and city is None and notify_new_requests is None:
            raise ValueError("NO_PREFERENCES")
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            updates, values = [], []
            if language is not None:
                updates.append("ui_language = ?"); values.append(language)
            if city is not None:
                updates.extend(["profile_city = ?", "city = ?", "onboarding_completed = 1"]); values.extend([city, city])
            if notify_new_requests is not None:
                updates.append("notify_new_requests = ?"); values.append(1 if notify_new_requests else 0)
            updates.append("updated_at = ?"); values.append(now_iso())
            values.append(telegram_id)
            await db.execute(f"UPDATE users SET {', '.join(updates)} WHERE telegram_id = ?", values)
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            if not rows:
                raise ValueError("USER_NOT_FOUND")
            return self._normalize_user(dict(rows[0]))

    async def create_city_request(self, telegram_id: int, city_name: str, sender_name: str) -> Dict[str, Any]:
        city_name = city_name.strip()[:80]
        if len(city_name) < 2:
            raise ValueError("INVALID_CITY_NAME")
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            duplicate = await db.execute_fetchall(
                "SELECT id, created_at FROM city_requests WHERE telegram_id = ? "
                "AND lower(city_name) = lower(?) AND status = 'new' ORDER BY id DESC LIMIT 1",
                (telegram_id, city_name),
            )
            if duplicate:
                return {"id": duplicate[0][0], "telegram_id": telegram_id, "city_name": city_name,
                        "sender_name": sender_name, "status": "new", "created_at": duplicate[0][1], "created": False}
            recent = await db.execute_fetchall(
                "SELECT COUNT(*) FROM city_requests WHERE telegram_id = ? AND created_at >= ?",
                (telegram_id, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()),
            )
            if recent and int(recent[0][0]) >= 5:
                raise ValueError("CITY_REQUEST_RATE_LIMIT")
            cursor = await db.execute(
                "INSERT INTO city_requests (telegram_id, city_name, sender_name, created_at) VALUES (?, ?, ?, ?)",
                (telegram_id, city_name, sender_name.strip()[:120] or str(telegram_id), ts),
            )
            await db.execute("UPDATE users SET onboarding_completed = 1, updated_at = ? WHERE telegram_id = ?", (ts, telegram_id))
            await db.commit()
        return {"id": cursor.lastrowid, "telegram_id": telegram_id, "city_name": city_name,
                "sender_name": sender_name, "status": "new", "created_at": ts, "created": True}

    async def city_stats(self, city: str) -> Dict[str, Any]:
        city = canonical_city(city)
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT
                    (SELECT COUNT(*) FROM users WHERE COALESCE(profile_city, city, 'Минск') = ?) AS players,
                    (SELECT COUNT(*) FROM game_requests WHERE city = ? AND status IN ('open','pending')) AS open_games,
                    (SELECT COUNT(*) FROM game_requests WHERE city = ? AND status IN ('confirmed','completed')) AS matched_games
                """,
                (city, city, city),
            )
        row = rows[0] if rows else (0, 0, 0)
        return {"city": city, "players": int(row[0]), "open_games": int(row[1]), "matched_games": int(row[2])}

    async def popular_places(self, city: str, limit: int = 12) -> List[Dict[str, Any]]:
        city = canonical_city(city)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT g.place, g.address, g.map_url, g.latitude, g.longitude,
                       COUNT(DISTINCT g.id) AS games_count,
                       ROUND(AVG(pr.score), 2) AS rating_avg,
                       COUNT(pr.id) AS rating_count
                FROM game_requests g
                LEFT JOIN place_ratings pr ON pr.game_id = g.id
                WHERE g.city = ? AND COALESCE(g.place, '') != ''
                GROUP BY LOWER(g.place), LOWER(COALESCE(g.address, ''))
                ORDER BY rating_count DESC, rating_avg DESC, games_count DESC
                LIMIT ?
                """,
                (city, max(1, min(int(limit), 50))),
            )
        return [dict(row) for row in rows]

    async def track_event(self, telegram_id: int, event_name: str, event_data: str = "{}") -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO analytics_events (telegram_id, event_name, event_data, created_at) VALUES (?, ?, ?, ?)",
                (telegram_id, (event_name or "unknown").strip()[:80], (event_data or "{}")[:1000], now_iso()),
            )
            await db.commit()

    async def check_in_game(self, game_id: int, telegram_id: int, late_minutes: int = 0) -> Dict[str, Any]:
        game = await self.get_game(game_id)
        if not game or game.get("status") != STATUS_CONFIRMED or not game.get("accepted_response"):
            raise ValueError("GAME_NOT_CONFIRMED")
        creator_id = int(game["creator_telegram_id"])
        responder_id = int(game["accepted_response"]["responder_telegram_id"])
        scheduled = effective_game_datetime(game)
        if not scheduled:
            raise ValueError("INVALID_GAME_TIME")
        seconds_from_start = (now_dt() - scheduled).total_seconds()
        if seconds_from_start < -(45 * 60) or seconds_from_start > (2 * 60 * 60):
            raise ValueError("CHECK_IN_NOT_AVAILABLE")
        late_minutes = max(0, min(int(late_minutes or 0), 120))
        if telegram_id == creator_id:
            column = "creator_checked_in_at"
            late_column = "creator_late_minutes"
        elif telegram_id == responder_id:
            column = "responder_checked_in_at"
            late_column = "responder_late_minutes"
        else:
            raise ValueError("NOT_ALLOWED")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE game_requests SET {column} = ?, {late_column} = ?, updated_at = ? WHERE id = ?",
                (now_iso(), late_minutes, now_iso(), game_id),
            )
            await db.commit()
        return await self.get_game(game_id) or {}

    async def create_game(self, creator_telegram_id: int, data: Dict[str, Any], default_city: str = "Минск") -> Dict[str, Any]:
        ts = now_iso()
        scheduled_at = self._normalize_scheduled_at(data.get("scheduled_at"), data.get("date_label"), data.get("time_label"))
        status = STATUS_OPEN
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            active_rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM game_requests WHERE creator_telegram_id = ? AND status IN ('open','pending')",
                (creator_telegram_id,),
            )
            reward_rows = await db.execute_fetchall(
                "SELECT COALESCE(referral_points, 0) FROM users WHERE telegram_id = ?",
                (creator_telegram_id,),
            )
            reward_points = int(reward_rows[0][0] or 0) if reward_rows else 0
            active_limit = 5 if reward_points >= 100 else 4 if reward_points >= 50 else 3
            if active_rows and int(active_rows[0][0]) >= active_limit:
                raise ValueError("TOO_MANY_OPEN_GAMES")
            recent_rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM game_requests WHERE creator_telegram_id = ? AND created_at >= ?",
                (creator_telegram_id, (now_dt() - timedelta(minutes=10)).isoformat()),
            )
            if recent_rows and int(recent_rows[0][0]) >= 5:
                raise ValueError("CREATE_RATE_LIMIT")
            cursor = await db.execute(
                """
                INSERT INTO game_requests (
                    creator_telegram_id, city, place, area, address, place_id, latitude, longitude, map_url,
                    date_label, time_label, scheduled_at, game_format, level, has_board, comment,
                    status, accepted_response_id, creator_confirmed, responder_confirmed, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, ?, ?)
                """,
                (
                    creator_telegram_id,
                    canonical_city(data.get("city") or default_city),
                    data["place"].strip(),
                    (data.get("area") or "").strip(),
                    (data.get("address") or "").strip(),
                    (data.get("place_id") or "").strip(),
                    data.get("latitude"),
                    data.get("longitude"),
                    (data.get("map_url") or "").strip(),
                    data["date_label"].strip(),
                    data["time_label"].strip(),
                    scheduled_at,
                    data["game_format"].strip(),
                    data["level"].strip(),
                    1 if data.get("has_board", True) else 0,
                    (data.get("comment") or "").strip(),
                    status,
                    ts,
                    ts,
                ),
            )
            game_id = int(cursor.lastrowid)
            await db.execute(
                """
                UPDATE game_requests
                SET is_flexible = ?, time_window_start = ?, time_window_end = ?
                WHERE id = ?
                """,
                (
                    1 if data.get("is_flexible") else 0,
                    (data.get("time_window_start") or "").strip(),
                    (data.get("time_window_end") or "").strip(),
                    game_id,
                ),
            )
            await db.commit()
            await self.activate_referral(creator_telegram_id)
            return await self.get_game(game_id) or {}



    async def update_game(self, game_id: int, requester_telegram_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Edit an open/pending game before a response has been accepted."""
        ts = now_iso()
        scheduled_at = self._normalize_scheduled_at(data.get("scheduled_at"), data.get("date_label"), data.get("time_label"))
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM game_requests WHERE id = ?", (game_id,))
            if not rows:
                raise ValueError("GAME_NOT_FOUND")
            game = dict(rows[0])
            if int(game["creator_telegram_id"]) != int(requester_telegram_id):
                raise ValueError("NOT_ALLOWED")
            if game.get("accepted_response_id") or game.get("status") not in (STATUS_OPEN, STATUS_PENDING):
                raise ValueError("GAME_ALREADY_ACCEPTED")
            await db.execute(
                """
                UPDATE game_requests
                SET city = ?, place = ?, area = ?, address = ?, place_id = ?, latitude = ?, longitude = ?, map_url = ?,
                    date_label = ?, time_label = ?, scheduled_at = ?, is_flexible = ?, time_window_start = ?, time_window_end = ?,
                    game_format = ?, level = ?, has_board = ?, comment = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical_city(data.get("city") or game.get("city") or "Минск"),
                    (data.get("place") or game.get("place") or "").strip(),
                    (data.get("area") or "").strip(),
                    (data.get("address") or "").strip(),
                    (data.get("place_id") or "").strip(),
                    data.get("latitude"),
                    data.get("longitude"),
                    (data.get("map_url") or "").strip(),
                    (data.get("date_label") or game.get("date_label") or "").strip(),
                    (data.get("time_label") or game.get("time_label") or "").strip(),
                    scheduled_at,
                    1 if data.get("is_flexible") else 0,
                    (data.get("time_window_start") or "").strip(),
                    (data.get("time_window_end") or "").strip(),
                    (data.get("game_format") or game.get("game_format") or "").strip(),
                    (data.get("level") or game.get("level") or "").strip(),
                    1 if data.get("has_board", True) else 0,
                    (data.get("comment") or "").strip(),
                    ts,
                    game_id,
                ),
            )
            await db.commit()
        return await self.get_game(game_id) or {}

    async def _count_recent(self, db: aiosqlite.Connection, table: str, column: str, telegram_id: int, minutes: int = 10) -> int:
        since = (now_dt() - timedelta(minutes=minutes)).isoformat()
        try:
            rows = await db.execute_fetchall(
                f"SELECT COUNT(*) AS c FROM {table} WHERE {column} = ? AND created_at >= ?",
                (telegram_id, since),
            )
            return int(rows[0][0]) if rows else 0
        except Exception:
            return 0

    async def get_game(self, game_id: int) -> Optional[Dict[str, Any]]:
        await self.expire_old_games()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT
                    g.*,
                    u.username AS creator_username,
                    u.first_name AS creator_first_name,
                    u.last_name AS creator_last_name,
                    u.display_name AS creator_display_name,
                    u.show_telegram_username AS creator_show_telegram_username,
                    u.level AS creator_level,
                    u.profile_city AS creator_profile_city,
                    u.photo_data_url AS creator_photo_data_url,
                    u.rating_avg AS creator_rating_avg,
                    u.rating_count AS creator_rating_count,
                    (SELECT COUNT(*) FROM responses r WHERE r.game_id = g.id AND r.status = 'pending') AS pending_responses_count,
                    (SELECT COUNT(*) FROM responses r WHERE r.game_id = g.id) AS responses_count,
                    (SELECT COUNT(*) FROM game_waitlist w WHERE w.game_id = g.id) AS waitlist_count
                FROM game_requests g
                LEFT JOIN users u ON u.telegram_id = g.creator_telegram_id
                WHERE g.id = ?
                """,
                (game_id,),
            )
            return await self._normalize_game_with_extras(dict(rows[0])) if rows else None

    async def list_games(
        self,
        city: str = "Минск",
        limit: int = 50,
        viewer_telegram_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        await self.expire_old_games()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT
                    g.*,
                    u.username AS creator_username,
                    u.first_name AS creator_first_name,
                    u.last_name AS creator_last_name,
                    u.display_name AS creator_display_name,
                    u.show_telegram_username AS creator_show_telegram_username,
                    u.level AS creator_level,
                    u.profile_city AS creator_profile_city,
                    u.photo_data_url AS creator_photo_data_url,
                    u.rating_avg AS creator_rating_avg,
                    u.rating_count AS creator_rating_count,
                    (SELECT COUNT(*) FROM responses r WHERE r.game_id = g.id AND r.status = 'pending') AS pending_responses_count,
                    (SELECT COUNT(*) FROM responses r WHERE r.game_id = g.id) AS responses_count
                FROM game_requests g
                LEFT JOIN users u ON u.telegram_id = g.creator_telegram_id
                WHERE g.city = ? AND g.status IN ('open', 'pending', 'confirmed')
                ORDER BY COALESCE(g.scheduled_at, g.created_at) ASC, g.id DESC
                LIMIT ?
                """,
                (city, limit),
            )
            result: List[Dict[str, Any]] = []
            for row in rows:
                game = await self._normalize_game_with_extras(dict(row), viewer_telegram_id=viewer_telegram_id)
                if game["status"] in (STATUS_OPEN, STATUS_PENDING, STATUS_CONFIRMED):
                    game["waitlist_available"] = game["status"] == STATUS_CONFIRMED
                    if game["waitlist_available"]:
                        # A filled meetup may be discoverable for its waitlist, but its
                        # exact location and participants stay private.
                        game["address"] = ""
                        game["map_url"] = ""
                        game["latitude"] = None
                        game["longitude"] = None
                        game["accepted_response"] = None
                    result.append(game)
            if viewer_telegram_id:
                viewer = await self.get_user(viewer_telegram_id) or {}
                viewer_level = str(viewer.get("level") or "").lower()
                viewer_rating = float(viewer.get("rating_avg") or 0)
                referral_bonus = 5 if int(viewer.get("referral_points") or 0) >= 50 else 0
                for game in result:
                    score = 45
                    reasons = ["тот же город"]
                    if viewer_level and viewer_level == str(game.get("level") or "").lower():
                        score += 25
                        reasons.append("подходящий уровень")
                    creator_rating = float(game.get("creator", {}).get("rating_avg") or 0)
                    if viewer_rating and creator_rating and abs(viewer_rating - creator_rating) <= 1:
                        score += 15
                        reasons.append("близкий рейтинг")
                    if bool(game.get("has_board")):
                        score += 5
                    score += referral_bonus
                    game["match_score"] = min(score, 100)
                    game["match_reasons"] = reasons
                result.sort(key=lambda item: (-int(item.get("match_score", 0)), item.get("scheduled_at") or item.get("created_at") or ""))
            return result

    async def list_my_games(self, telegram_id: int, limit: int = 100) -> Dict[str, List[Dict[str, Any]]]:
        await self.expire_old_games()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            created_rows = await db.execute_fetchall(
                self._games_select_sql() + " WHERE g.creator_telegram_id = ? ORDER BY g.updated_at DESC LIMIT ?",
                (telegram_id, limit),
            )
            responded_rows = await db.execute_fetchall(
                self._games_select_sql(
                    extra="r.status AS my_response_status"
                )
                + " JOIN responses r ON r.game_id = g.id WHERE r.responder_telegram_id = ? ORDER BY g.updated_at DESC LIMIT ?",
                (telegram_id, limit),
            )
            waitlist_rows = await db.execute_fetchall(
                self._games_select_sql(extra="w.position AS my_waitlist_position")
                + " JOIN game_waitlist w ON w.game_id = g.id WHERE w.telegram_id = ? "
                  "ORDER BY w.created_at DESC LIMIT ?",
                (telegram_id, limit),
            )

            pending_reviews_raw = await db.execute_fetchall(
                self._games_select_sql() + " JOIN responses r ON r.id = g.accepted_response_id "
                "WHERE (g.creator_telegram_id = ? OR r.responder_telegram_id = ?) "
                "AND g.status IN ('confirmed','completed') ORDER BY g.updated_at DESC LIMIT ?",
                (telegram_id, telegram_id, limit),
            )

            created = [await self._normalize_game_with_extras(dict(row), viewer_telegram_id=telegram_id) for row in created_rows]
            responded = [await self._normalize_game_with_extras(dict(row), viewer_telegram_id=telegram_id) for row in responded_rows]
            waitlisted = [await self._normalize_game_with_extras(dict(row), viewer_telegram_id=telegram_id) for row in waitlist_rows]
            pending_reviews_all = [await self._normalize_game_with_extras(dict(row), viewer_telegram_id=telegram_id) for row in pending_reviews_raw]
            pending_reviews = [g for g in pending_reviews_all if g.get("rating_can_submit") and not g.get("my_rating")]

            return {
                "created": created,
                "responded": responded,
                "waitlisted": waitlisted,
                "pending_reviews": pending_reviews,
            }

    async def join_waitlist(self, game_id: int, telegram_id: int) -> Dict[str, Any]:
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM game_requests WHERE id = ?", (game_id,))
            if not rows:
                raise ValueError("GAME_NOT_FOUND")
            game = dict(rows[0])
            if int(game["creator_telegram_id"]) == int(telegram_id):
                raise ValueError("CANNOT_JOIN_OWN_WAITLIST")
            if game["status"] != STATUS_CONFIRMED:
                raise ValueError("WAITLIST_NOT_AVAILABLE")
            blocked = await db.execute_fetchall(
                "SELECT 1 FROM user_blocks WHERE "
                "(blocker_telegram_id = ? AND blocked_telegram_id = ?) OR "
                "(blocker_telegram_id = ? AND blocked_telegram_id = ?)",
                (game["creator_telegram_id"], telegram_id, telegram_id, game["creator_telegram_id"]),
            )
            if blocked:
                raise ValueError("USER_BLOCKED")
            position_rows = await db.execute_fetchall(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM game_waitlist WHERE game_id = ?",
                (game_id,),
            )
            position = int(position_rows[0][0])
            await db.execute(
                "INSERT OR IGNORE INTO game_waitlist (game_id, telegram_id, position, created_at) VALUES (?, ?, ?, ?)",
                (game_id, telegram_id, position, ts),
            )
            await db.commit()
            own = await db.execute_fetchall(
                "SELECT position, created_at FROM game_waitlist WHERE game_id = ? AND telegram_id = ?",
                (game_id, telegram_id),
            )
            return {"game_id": game_id, "position": int(own[0]["position"]), "created_at": own[0]["created_at"]}

    async def leave_waitlist(self, game_id: int, telegram_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM game_waitlist WHERE game_id = ? AND telegram_id = ?", (game_id, telegram_id))
            await db.commit()

    async def create_response(self, game_id: int, responder_telegram_id: int, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        ts = now_iso()
        data = data or {}
        proposed_date_label = (data.get("proposed_date_label") or "").strip()[:80]
        proposed_time_label = (data.get("proposed_time_label") or "").strip()[:40]
        proposed_comment = (data.get("proposed_comment") or data.get("comment") or "").strip()[:300]
        await self.expire_old_games()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            game_rows = await db.execute_fetchall("SELECT * FROM game_requests WHERE id = ?", (game_id,))
            if not game_rows:
                raise ValueError("GAME_NOT_FOUND")
            game = dict(game_rows[0])
            if game["creator_telegram_id"] == responder_telegram_id:
                raise ValueError("CANNOT_RESPOND_TO_OWN_GAME")
            block_rows = await db.execute_fetchall(
                "SELECT 1 FROM user_blocks WHERE (blocker_telegram_id = ? AND blocked_telegram_id = ?) OR (blocker_telegram_id = ? AND blocked_telegram_id = ?) OR (blocker_telegram_id = 0 AND blocked_telegram_id IN (?, ?))",
                (game["creator_telegram_id"], responder_telegram_id, responder_telegram_id, game["creator_telegram_id"], game["creator_telegram_id"], responder_telegram_id),
            )
            if block_rows:
                raise ValueError("USER_BLOCKED")
            if game["status"] not in (STATUS_OPEN, STATUS_PENDING):
                raise ValueError("GAME_IS_NOT_OPEN")
            recent_rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM responses WHERE responder_telegram_id = ? AND created_at >= ?",
                (responder_telegram_id, (now_dt() - timedelta(minutes=10)).isoformat()),
            )
            if recent_rows and int(recent_rows[0][0]) >= 5:
                raise ValueError("RESPONSE_RATE_LIMIT")

            existing = await db.execute_fetchall(
                "SELECT * FROM responses WHERE game_id = ? AND responder_telegram_id = ?",
                (game_id, responder_telegram_id),
            )
            if existing:
                response = dict(existing[0])
                if proposed_date_label or proposed_time_label or proposed_comment:
                    await db.execute(
                        "UPDATE responses SET proposed_date_label = ?, proposed_time_label = ?, proposed_comment = ?, updated_at = ? WHERE id = ?",
                        (proposed_date_label, proposed_time_label, proposed_comment, ts, response["id"]),
                    )
                    await db.commit()
                    updated_rows = await db.execute_fetchall("SELECT * FROM responses WHERE id = ?", (response["id"],))
                    response = dict(updated_rows[0])
            else:
                cursor = await db.execute(
                    """
                    INSERT INTO responses (game_id, responder_telegram_id, status, proposed_date_label, proposed_time_label, proposed_comment, created_at, updated_at)
                    VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)
                    """,
                    (game_id, responder_telegram_id, proposed_date_label, proposed_time_label, proposed_comment, ts, ts),
                )
                await db.execute(
                    "UPDATE game_requests SET status = 'pending', updated_at = ? WHERE id = ? AND status = 'open'",
                    (ts, game_id),
                )
                await db.commit()
                response_rows = await db.execute_fetchall("SELECT * FROM responses WHERE id = ?", (cursor.lastrowid,))
                response = dict(response_rows[0])
            await self.activate_referral(responder_telegram_id)
            return response

    async def get_response_details(self, response_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT
                    r.*,
                    g.place,
                    g.area,
                    g.city,
                    g.address,
                    g.map_url,
                    g.date_label,
                    g.time_label,
                    g.game_format,
                    g.level,
                    g.creator_telegram_id,
                    g.scheduled_at,
                    creator.username AS creator_username,
                    creator.first_name AS creator_first_name,
                    creator.display_name AS creator_display_name,
                    creator.show_telegram_username AS creator_show_telegram_username,
                    responder.username AS responder_username,
                    responder.first_name AS responder_first_name,
                    responder.display_name AS responder_display_name,
                    responder.show_telegram_username AS responder_show_telegram_username
                FROM responses r
                JOIN game_requests g ON g.id = r.game_id
                LEFT JOIN users creator ON creator.telegram_id = g.creator_telegram_id
                LEFT JOIN users responder ON responder.telegram_id = r.responder_telegram_id
                WHERE r.id = ?
                """,
                (response_id,),
            )
            return dict(rows[0]) if rows else None

    async def list_game_responses(self, game_id: int, creator_telegram_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            game_rows = await db.execute_fetchall(
                "SELECT creator_telegram_id FROM game_requests WHERE id = ?",
                (game_id,),
            )
            if not game_rows:
                raise ValueError("GAME_NOT_FOUND")
            if int(game_rows[0]["creator_telegram_id"]) != int(creator_telegram_id):
                raise ValueError("NOT_ALLOWED")
            rows = await db.execute_fetchall(
                """
                SELECT r.*, u.telegram_id, u.username, u.first_name, u.display_name, u.level,
                       u.photo_data_url, u.rating_avg, u.rating_count
                FROM responses r
                JOIN users u ON u.telegram_id = r.responder_telegram_id
                WHERE r.game_id = ?
                ORDER BY CASE r.status WHEN 'pending' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                         r.updated_at DESC
                """,
                (game_id,),
            )
        return [
            {
                **dict(row),
                "responder": self._public_user(self._normalize_user(dict(row))),
            }
            for row in rows
        ]

    async def accept_response_for_creator(
        self,
        response_id: int,
        creator_telegram_id: int,
    ) -> Dict[str, Any]:
        details = await self.get_response_details(response_id)
        if not details:
            raise ValueError("RESPONSE_NOT_FOUND")
        if int(details["creator_telegram_id"]) != int(creator_telegram_id):
            raise ValueError("NOT_ALLOWED")
        if details["status"] != "pending":
            raise ValueError("RESPONSE_ALREADY_PROCESSED")
        return await self.accept_response(response_id)

    async def decline_response_for_creator(
        self,
        response_id: int,
        creator_telegram_id: int,
    ) -> Dict[str, Any]:
        details = await self.get_response_details(response_id)
        if not details:
            raise ValueError("RESPONSE_NOT_FOUND")
        if int(details["creator_telegram_id"]) != int(creator_telegram_id):
            raise ValueError("NOT_ALLOWED")
        if details["status"] != "pending":
            raise ValueError("RESPONSE_ALREADY_PROCESSED")
        return await self.decline_response(response_id)

    async def accept_response(self, response_id: int) -> Dict[str, Any]:
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            details_rows = await db.execute_fetchall("SELECT * FROM responses WHERE id = ?", (response_id,))
            if not details_rows:
                raise ValueError("RESPONSE_NOT_FOUND")
            response = dict(details_rows[0])
            await db.execute("UPDATE responses SET status = 'accepted', updated_at = ? WHERE id = ?", (ts, response_id))
            await db.execute(
                "UPDATE game_requests SET status = 'confirmed', accepted_response_id = ?, creator_confirmed = 1, responder_confirmed = 1, updated_at = ? WHERE id = ?",
                (response_id, ts, response["game_id"]),
            )
            await db.execute(
                "UPDATE responses SET status = 'declined', updated_at = ? WHERE game_id = ? AND id != ? AND status = 'pending'",
                (ts, response["game_id"], response_id),
            )
            await db.commit()
            result = await self.get_response_details(response_id)
            if not result:
                raise ValueError("RESPONSE_NOT_FOUND")
            return result

    async def decline_response(self, response_id: int) -> Dict[str, Any]:
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("UPDATE responses SET status = 'declined', updated_at = ? WHERE id = ?", (ts, response_id))
            row = await db.execute_fetchall(
                "SELECT COUNT(*) AS pending_count, game_id FROM responses WHERE id = ?",
                (response_id,),
            )
            await db.commit()
            result = await self.get_response_details(response_id)
            if not result:
                raise ValueError("RESPONSE_NOT_FOUND")
            pending_rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM responses WHERE game_id = ? AND status = 'pending'", (result["game_id"],)
            )
            if pending_rows and int(pending_rows[0][0]) == 0:
                await db.execute(
                    "UPDATE game_requests SET status = 'open', updated_at = ? WHERE id = ? AND status = 'pending'",
                    (ts, result["game_id"]),
                )
                await db.commit()
            return result

    async def cancel_game(self, game_id: int, requester_telegram_id: int, reason: str = "") -> Dict[str, Any]:
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            game_rows = await db.execute_fetchall("SELECT * FROM game_requests WHERE id = ?", (game_id,))
            if not game_rows:
                raise ValueError("GAME_NOT_FOUND")
            game = dict(game_rows[0])
            accepted_responder_id = None
            if game.get("accepted_response_id"):
                rr = await db.execute_fetchall("SELECT responder_telegram_id FROM responses WHERE id = ?", (game["accepted_response_id"],))
                if rr:
                    accepted_responder_id = rr[0][0]
            if requester_telegram_id not in {game["creator_telegram_id"], accepted_responder_id}:
                raise ValueError("NOT_ALLOWED")
            await db.execute(
                "UPDATE responses SET status = 'declined', updated_at = ? WHERE game_id = ? AND status IN ('pending','accepted')",
                (ts, game_id),
            )
            if requester_telegram_id == accepted_responder_id:
                next_rows = await db.execute_fetchall(
                    "SELECT telegram_id FROM game_waitlist WHERE game_id = ? ORDER BY position, created_at LIMIT 1",
                    (game_id,),
                )
                if next_rows:
                    promoted_id = int(next_rows[0]["telegram_id"])
                    await db.execute(
                        """
                        INSERT INTO responses
                            (game_id, responder_telegram_id, status, proposed_comment, created_at, updated_at)
                        VALUES (?, ?, 'pending', ?, ?, ?)
                        ON CONFLICT(game_id, responder_telegram_id) DO UPDATE SET
                            status = 'pending', proposed_comment = excluded.proposed_comment, updated_at = excluded.updated_at
                        """,
                        (game_id, promoted_id, "Автоматически поднят из листа ожидания", ts, ts),
                    )
                    await db.execute(
                        "DELETE FROM game_waitlist WHERE game_id = ? AND telegram_id = ?",
                        (game_id, promoted_id),
                    )
                    await db.execute(
                        "UPDATE game_requests SET status = 'pending', accepted_response_id = NULL, "
                        "creator_confirmed = 0, responder_confirmed = 0, cancel_reason = ?, updated_at = ? WHERE id = ?",
                        ((reason or "").strip()[:200], ts, game_id),
                    )
                else:
                    await db.execute(
                        "UPDATE game_requests SET status = 'open', accepted_response_id = NULL, "
                        "creator_confirmed = 0, responder_confirmed = 0, cancel_reason = ?, updated_at = ? WHERE id = ?",
                        ((reason or "").strip()[:200], ts, game_id),
                    )
            else:
                await db.execute(
                    "UPDATE game_requests SET status = 'cancelled', cancel_reason = ?, updated_at = ? WHERE id = ?",
                    ((reason or "").strip()[:200], ts, game_id),
                )
                await db.execute("DELETE FROM game_waitlist WHERE game_id = ?", (game_id,))
            await db.commit()
            result = await self.get_game(game_id)
            if not result:
                raise ValueError("GAME_NOT_FOUND")
            return result

    async def confirm_game(self, game_id: int, requester_telegram_id: int) -> Dict[str, Any]:
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            game_rows = await db.execute_fetchall("SELECT * FROM game_requests WHERE id = ?", (game_id,))
            if not game_rows:
                raise ValueError("GAME_NOT_FOUND")
            game = dict(game_rows[0])
            if game["status"] != STATUS_CONFIRMED:
                raise ValueError("GAME_NOT_CONFIRMED")
            responder_id = None
            if not game.get("accepted_response_id"):
                raise ValueError("GAME_NOT_CONFIRMED")
            response_rows = await db.execute_fetchall("SELECT responder_telegram_id FROM responses WHERE id = ?", (game["accepted_response_id"],))
            if response_rows:
                responder_id = response_rows[0][0]
            if requester_telegram_id == game["creator_telegram_id"]:
                await db.execute("UPDATE game_requests SET creator_confirmed = 1, updated_at = ? WHERE id = ?", (ts, game_id))
            elif requester_telegram_id == responder_id:
                await db.execute("UPDATE game_requests SET responder_confirmed = 1, updated_at = ? WHERE id = ?", (ts, game_id))
            else:
                raise ValueError("NOT_ALLOWED")
            await db.commit()
            return await self.get_game(game_id) or {}
    async def submit_rating(self, game_id: int, rater_telegram_id: int, score: int, comment: str = "") -> Dict[str, Any]:
        if score < 1 or score > 5:
            raise ValueError("INVALID_SCORE")
        game = await self.get_game(game_id)
        if not game:
            raise ValueError("GAME_NOT_FOUND")
        if not game.get("accepted_response"):
            raise ValueError("GAME_NOT_CONFIRMED")

        creator_id = int(game["creator_telegram_id"])
        responder_id = int(game["accepted_response"]["responder_telegram_id"])
        if rater_telegram_id == creator_id:
            rated_telegram_id = responder_id
        elif rater_telegram_id == responder_id:
            rated_telegram_id = creator_id
        else:
            raise ValueError("NOT_ALLOWED")

        scheduled_at = effective_game_datetime(game)
        if not scheduled_at or now_dt() < scheduled_at + timedelta(hours=1):
            raise ValueError("RATING_NOT_AVAILABLE_YET")

        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            existing = await db.execute_fetchall(
                "SELECT * FROM ratings WHERE game_id = ? AND rater_telegram_id = ?",
                (game_id, rater_telegram_id),
            )
            if existing:
                raise ValueError("ALREADY_RATED")
            await db.execute(
                "INSERT INTO ratings (game_id, rater_telegram_id, rated_telegram_id, score, comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (game_id, rater_telegram_id, rated_telegram_id, score, comment.strip()[:300], ts),
            )
            await db.commit()
        await self.refresh_user_rating(rated_telegram_id)

        updated_game = await self.get_game(game_id)
        if updated_game and updated_game.get("creator_rating") and updated_game.get("responder_rating"):
            async with aiosqlite.connect(self.path) as db:
                await db.execute(
                    "UPDATE game_requests SET status = 'completed', updated_at = ? WHERE id = ? AND status = 'confirmed'",
                    (now_iso(), game_id),
                )
                await db.commit()
            updated_game = await self.get_game(game_id)
        return updated_game or {}


    async def refresh_user_rating(self, telegram_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall(
                "SELECT AVG(score), COUNT(*) FROM ratings WHERE rated_telegram_id = ?",
                (telegram_id,),
            )
            avg = float(rows[0][0]) if rows and rows[0][0] is not None else 0.0
            count = int(rows[0][1]) if rows else 0
            await db.execute(
                "UPDATE users SET rating_avg = ?, rating_count = ?, updated_at = ? WHERE telegram_id = ?",
                (avg, count, now_iso(), telegram_id),
            )
            await db.commit()

    async def refresh_all_user_ratings(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT telegram_id FROM users")
        for row in rows:
            await self.refresh_user_rating(int(row[0]))

    def _games_select_sql(self, extra: str = "") -> str:
        extra_sql = f", {extra}" if extra else ""
        return (
            """
            SELECT
                g.*,
                u.username AS creator_username,
                u.first_name AS creator_first_name,
                u.last_name AS creator_last_name,
                u.display_name AS creator_display_name,
                u.show_telegram_username AS creator_show_telegram_username,
                u.level AS creator_level,
                u.profile_city AS creator_profile_city,
                u.photo_data_url AS creator_photo_data_url,
                u.rating_avg AS creator_rating_avg,
                u.rating_count AS creator_rating_count,
                (SELECT COUNT(*) FROM responses r WHERE r.game_id = g.id AND r.status = 'pending') AS pending_responses_count,
                (SELECT COUNT(*) FROM responses r WHERE r.game_id = g.id) AS responses_count,
                (SELECT COUNT(*) FROM game_waitlist w WHERE w.game_id = g.id) AS waitlist_count
            """
            + extra_sql
            + """
            FROM game_requests g
            LEFT JOIN users u ON u.telegram_id = g.creator_telegram_id
            """
        )

    async def _normalize_game_with_extras(self, game: Dict[str, Any], viewer_telegram_id: Optional[int] = None) -> Dict[str, Any]:
        game = self._normalize_game(game)
        accepted_response = None
        if game.get("accepted_response_id"):
            accepted_response = await self._get_response_basic(int(game["accepted_response_id"]))
        game["accepted_response"] = accepted_response

        rating_available_at = None
        can_submit = False
        my_rating = None
        creator_rating = None
        responder_rating = None

        scheduled_at = effective_game_datetime(game)
        if scheduled_at:
            rating_available_at = (scheduled_at + timedelta(hours=1)).isoformat()
        game["rating_available_at"] = rating_available_at

        creator_id = int(game["creator_telegram_id"])
        responder_id = int(accepted_response["responder_telegram_id"]) if accepted_response else None

        ratings = await self._get_game_ratings(int(game["id"]))
        for rating in ratings:
            if rating["rater_telegram_id"] == creator_id:
                creator_rating = rating
            if responder_id and rating["rater_telegram_id"] == responder_id:
                responder_rating = rating
            if viewer_telegram_id and rating["rater_telegram_id"] == viewer_telegram_id:
                my_rating = rating
        game["creator_rating"] = creator_rating
        game["responder_rating"] = responder_rating
        game["my_rating"] = my_rating

        if viewer_telegram_id and accepted_response and rating_available_at:
            is_participant = viewer_telegram_id in {creator_id, responder_id}
            can_submit = bool(is_participant and parse_iso(rating_available_at) and now_dt() >= parse_iso(rating_available_at))
        game["rating_can_submit"] = can_submit

        opponent = None
        if viewer_telegram_id and accepted_response:
            if viewer_telegram_id == creator_id:
                opponent = await self.get_user(responder_id)
            elif viewer_telegram_id == responder_id:
                opponent = await self.get_user(creator_id)
        game["opponent"] = opponent
        game["photos"] = await self._get_game_photos(int(game["id"]))
        if viewer_telegram_id and opponent:
            async with aiosqlite.connect(self.path) as db:
                fav = await db.execute_fetchall(
                    "SELECT 1 FROM favorite_players WHERE owner_telegram_id = ? AND favorite_telegram_id = ?",
                    (viewer_telegram_id, int(opponent["telegram_id"])),
                )
            game["opponent_is_favorite"] = bool(fav)
        game["place_rating"] = await self._get_place_rating_summary(game)
        game["my_place_rating"] = await self._get_my_place_rating(int(game["id"]), viewer_telegram_id)
        game["my_diary"] = await self._get_my_diary_entry(int(game["id"]), viewer_telegram_id)
        if viewer_telegram_id:
            async with aiosqlite.connect(self.path) as db:
                own_waitlist = await db.execute_fetchall(
                    "SELECT position FROM game_waitlist WHERE game_id = ? AND telegram_id = ?",
                    (int(game["id"]), viewer_telegram_id),
                )
            game["my_waitlist_position"] = int(own_waitlist[0][0]) if own_waitlist else None
        return game

    async def _get_response_basic(self, response_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM responses WHERE id = ?",
                (response_id,),
            )
            return dict(rows[0]) if rows else None

    async def _get_game_ratings(self, game_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM ratings WHERE game_id = ? ORDER BY created_at ASC",
                (game_id,),
            )
            return [dict(row) for row in rows]


    async def list_users_for_new_request_notifications(
        self,
        exclude_telegram_id: int,
        city: str = "Минск",
        game_format: str = "",
        level: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT * FROM users
                WHERE telegram_id != ?
                  AND COALESCE(notify_new_requests, 0) = 1
                  AND COALESCE(profile_city, city, 'Минск') = ?
                  AND (COALESCE(subscription_format, 'all') = 'all' OR LOWER(?) LIKE '%' || LOWER(subscription_format) || '%')
                  AND (COALESCE(subscription_level, 'all') = 'all' OR LOWER(?) = LOWER(subscription_level))
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (exclude_telegram_id, city, game_format, level, limit),
            )
            return [self._normalize_user(dict(row)) for row in rows]
    async def complete_finished_confirmed_games(self, grace_hours: int = 2) -> int:
        """Move elapsed meetings to history; no rows or user data are deleted."""
        changed = 0
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM game_requests WHERE status = 'confirmed'")
            for row in rows:
                scheduled = effective_game_datetime(dict(row))
                if scheduled and now_dt() >= scheduled + timedelta(hours=grace_hours):
                    cursor = await db.execute(
                        "UPDATE game_requests SET status = 'completed', updated_at = ? WHERE id = ? AND status = 'confirmed'",
                        (now_iso(), row["id"]),
                    )
                    changed += max(0, cursor.rowcount)
            await db.commit()
        return changed

    async def get_due_game_reminders(self) -> List[Dict[str, Any]]:
        now = now_dt()
        due: List[Dict[str, Any]] = []
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT
                    g.*,
                    r.responder_telegram_id,
                    creator.notify_game_reminders AS creator_notify_game_reminders,
                    responder.notify_game_reminders AS responder_notify_game_reminders
                FROM game_requests g
                JOIN responses r ON r.id = g.accepted_response_id
                LEFT JOIN users creator ON creator.telegram_id = g.creator_telegram_id
                LEFT JOIN users responder ON responder.telegram_id = r.responder_telegram_id
                WHERE g.status = 'confirmed'
                """
            )
            for row in rows:
                game = dict(row)
                scheduled = effective_game_datetime(game)
                if not scheduled:
                    continue
                seconds_until = (scheduled - now).total_seconds()

                # Do not send reminders after the game time.
                if seconds_until <= 0:
                    continue

                reminder_type = None

                # Robust windows: if the server/launcher was sleeping, still send the
                # nearest useful reminder while there is time before the game.
                # 30m reminder: from 35 minutes down to the start.
                # 3h reminder: from 3h10m down to 35 minutes before the start.
                if 0 < seconds_until <= (35 * 60) and not game.get("reminder_30m_sent"):
                    reminder_type = "30m"
                    await db.execute(
                        "UPDATE game_requests SET reminder_30m_sent = 1, updated_at = ? WHERE id = ?",
                        (now_iso(), game["id"]),
                    )
                elif (35 * 60) < seconds_until <= (190 * 60) and not game.get("reminder_3h_sent"):
                    reminder_type = "3h"
                    await db.execute(
                        "UPDATE game_requests SET reminder_3h_sent = 1, updated_at = ? WHERE id = ?",
                        (now_iso(), game["id"]),
                    )

                if reminder_type:
                    game["reminder_type"] = reminder_type
                    game["scheduled_at_effective"] = scheduled.isoformat()
                    due.append(game)
            await db.commit()
        return due


    async def get_users_for_streak_reminder(
        self,
        city: Optional[str] = None,
        today_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        today = today_key or self._today_key()
        try:
            yesterday = (datetime.fromisoformat(today).date() - timedelta(days=1)).isoformat()
        except ValueError:
            yesterday = self._yesterday_key()
        await self.normalize_all_puzzle_streaks()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            city_filter = "AND COALESCE(profile_city, city, 'Минск') = ?" if city else ""
            params: tuple[Any, ...] = (yesterday, today, canonical_city(city)) if city else (yesterday, today)
            rows = await db.execute_fetchall(
                f"""
                SELECT * FROM users
                WHERE COALESCE(puzzle_streak, 0) > 0
                  AND COALESCE(notify_puzzle_streak, 1) = 1
                  AND COALESCE(puzzle_last_solved_date, '') = ?
                  AND COALESCE(puzzle_reminder_sent_date, '') != ?
                  {city_filter}
                ORDER BY puzzle_streak DESC
                LIMIT 500
                """,
                params,
            )
            users = [self._normalize_user(dict(row)) for row in rows]
            if users:
                await db.executemany(
                    "UPDATE users SET puzzle_reminder_sent_date = ?, updated_at = ? WHERE telegram_id = ?",
                    [(today, now_iso(), int(user["telegram_id"])) for user in users],
                )
                await db.commit()
            return users

    async def list_badges(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM badges ORDER BY id DESC")
            return [dict(row) for row in rows]

    async def create_badge(self, title: str, icon: str = "🏅", description: str = "", color: str = "#2f8a4b") -> Dict[str, Any]:
        ts = now_iso()
        title = (title or "Значок").strip()[:80]
        icon = (icon or "🏅").strip()[:12]
        description = (description or "").strip()[:240]
        color = (color or "#2f8a4b").strip()[:32]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "INSERT INTO badges (title, icon, description, color, created_at) VALUES (?, ?, ?, ?, ?)",
                (title, icon, description, color, ts),
            )
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM badges WHERE id = ?", (cursor.lastrowid,))
            return dict(rows[0])

    async def award_badge(self, telegram_id: int, badge_id: int, note: str = "") -> Dict[str, Any]:
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            user_rows = await db.execute_fetchall("SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,))
            if not user_rows:
                raise ValueError("USER_NOT_FOUND")
            badge_rows = await db.execute_fetchall("SELECT id FROM badges WHERE id = ?", (badge_id,))
            if not badge_rows:
                raise ValueError("BADGE_NOT_FOUND")
            await db.execute(
                """
                INSERT INTO user_badges (telegram_id, badge_id, is_visible, note, awarded_at)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(telegram_id, badge_id) DO UPDATE SET note = excluded.note
                """,
                (telegram_id, badge_id, (note or "").strip()[:200], ts),
            )
            await db.commit()
        user = await self.get_public_user(telegram_id)
        return {"ok": True, "user": user}

    async def list_user_badges(self, telegram_id: int, public_only: bool = False) -> List[Dict[str, Any]]:
        where_public = "AND ub.is_visible = 1" if public_only else ""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                f"""
                SELECT b.*, ub.is_visible, ub.note, ub.awarded_at
                FROM user_badges ub
                JOIN badges b ON b.id = ub.badge_id
                WHERE ub.telegram_id = ? {where_public}
                ORDER BY ub.awarded_at DESC, b.id DESC
                """,
                (telegram_id,),
            )
            result = []
            for row in rows:
                item = dict(row)
                item["is_visible"] = bool(item.get("is_visible"))
                result.append(item)
            return result

    async def update_visible_badges(self, telegram_id: int, visible_badge_ids: List[int]) -> List[Dict[str, Any]]:
        visible = {int(x) for x in (visible_badge_ids or [])}
        async with aiosqlite.connect(self.path) as db:
            await db.execute("UPDATE user_badges SET is_visible = 0 WHERE telegram_id = ?", (telegram_id,))
            if visible:
                await db.executemany(
                    "UPDATE user_badges SET is_visible = 1 WHERE telegram_id = ? AND badge_id = ?",
                    [(telegram_id, badge_id) for badge_id in visible],
                )
            await db.commit()
        return await self.list_user_badges(telegram_id, public_only=False)

    async def get_public_user(self, telegram_id: int, viewer_telegram_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        user = await self.get_user(telegram_id)
        if not user:
            return None
        user = await self.normalize_puzzle_streak(
            telegram_id,
            today_key=city_today_key(user.get("profile_city") or user.get("city") or "Минск"),
        )
        public_user = self._public_user(user)
        public_user["reliability"] = await self._user_reliability(telegram_id)
        public_user["badges"] = await self.list_user_badges(telegram_id, public_only=True)
        if viewer_telegram_id:
            async with aiosqlite.connect(self.path) as db:
                fav = await db.execute_fetchall(
                    "SELECT 1 FROM favorite_players WHERE owner_telegram_id = ? AND favorite_telegram_id = ?",
                    (viewer_telegram_id, telegram_id),
                )
                blocked = await db.execute_fetchall(
                    "SELECT 1 FROM user_blocks WHERE blocker_telegram_id = ? AND blocked_telegram_id = ?",
                    (viewer_telegram_id, telegram_id),
                )
            public_user["is_favorite"] = bool(fav)
            public_user["is_blocked"] = bool(blocked)
        return public_user

    async def get_chat_context(self, game_id: int, requester_telegram_id: int) -> Dict[str, Any]:
        game = await self.get_game(game_id)
        if not game:
            raise ValueError("GAME_NOT_FOUND")
        if not game.get("accepted_response"):
            raise ValueError("CHAT_NOT_AVAILABLE")
        creator_id = int(game["creator_telegram_id"])
        responder_id = int(game["accepted_response"]["responder_telegram_id"])
        if requester_telegram_id not in {creator_id, responder_id}:
            raise ValueError("NOT_ALLOWED")
        if game["status"] not in {STATUS_CONFIRMED, STATUS_COMPLETED}:
            raise ValueError("CHAT_NOT_AVAILABLE")
        opponent_id = responder_id if requester_telegram_id == creator_id else creator_id
        opponent = await self.get_public_user(opponent_id, requester_telegram_id)
        return {"game": game, "opponent": opponent, "opponent_telegram_id": opponent_id}

    async def list_chat_messages(self, game_id: int, requester_telegram_id: int, limit: int = 100) -> Dict[str, Any]:
        context = await self.get_chat_context(game_id, requester_telegram_id)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT
                    m.*,
                    u.username AS sender_username,
                    u.first_name AS sender_first_name,
                    u.display_name AS sender_display_name,
                    u.show_telegram_username AS sender_show_telegram_username,
                    u.photo_data_url AS sender_photo_data_url
                FROM chat_messages m
                LEFT JOIN users u ON u.telegram_id = m.sender_telegram_id
                WHERE m.game_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (game_id, limit),
            )
        messages = [self._normalize_chat_message(dict(row), requester_telegram_id) for row in reversed(rows)]
        return {"game": context["game"], "opponent": context["opponent"], "messages": messages}

    async def create_chat_message(self, game_id: int, sender_telegram_id: int, text: str) -> Dict[str, Any]:
        context = await self.get_chat_context(game_id, sender_telegram_id)
        clean_text = (text or "").strip()[:1000]
        if len(clean_text) < 1:
            raise ValueError("EMPTY_MESSAGE")
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            recent_rows = await db.execute_fetchall(
                "SELECT COUNT(*) FROM chat_messages WHERE sender_telegram_id = ? AND created_at >= ?",
                (sender_telegram_id, (now_dt() - timedelta(minutes=10)).isoformat()),
            )
            if recent_rows and int(recent_rows[0][0]) >= 10:
                raise ValueError("CHAT_RATE_LIMIT")
            cursor = await db.execute(
                "INSERT INTO chat_messages (game_id, sender_telegram_id, text, created_at) VALUES (?, ?, ?, ?)",
                (game_id, sender_telegram_id, clean_text, ts),
            )
            await db.commit()
            rows = await db.execute_fetchall(
                """
                SELECT
                    m.*,
                    u.username AS sender_username,
                    u.first_name AS sender_first_name,
                    u.display_name AS sender_display_name,
                    u.show_telegram_username AS sender_show_telegram_username,
                    u.photo_data_url AS sender_photo_data_url
                FROM chat_messages m
                LEFT JOIN users u ON u.telegram_id = m.sender_telegram_id
                WHERE m.id = ?
                """,
                (cursor.lastrowid,),
            )
        message = self._normalize_chat_message(dict(rows[0]), sender_telegram_id)
        message["opponent_telegram_id"] = context["opponent_telegram_id"]
        message["game"] = context["game"]
        return message


    def _puzzle_cache_path(self) -> Path:
        base = Path(self.path).parent
        if str(base) in {"", "."}:
            base = Path.cwd()
        return base / PUZZLE_CACHE_FILENAME

    def _load_or_fetch_daily_puzzles(self) -> None:
        """Load cached verified Lichess mate-in-one puzzles or stream-build them.

        This version deliberately does not use model-generated or hand-written
        positions. It filters the official public Lichess puzzle database for
        `mateIn1` puzzles and then verifies every candidate locally:
        from the FEN position, at least one legal move must immediately give
        checkmate. The app accepts ANY such checkmating legal move, because in
        mate-in-one positions several moves can sometimes mate.
        """
        cache_path = self._puzzle_cache_path()
        bundled_paths = [
            cache_path,
            Path(__file__).resolve().parent / PUZZLE_CACHE_FILENAME,
            Path(__file__).resolve().parent.parent / PUZZLE_CACHE_FILENAME,
        ]
        for candidate_path in bundled_paths:
            try:
                if candidate_path.exists():
                    data = json.loads(candidate_path.read_text(encoding="utf-8"))
                    puzzles = data.get("puzzles") if isinstance(data, dict) else data
                    if isinstance(puzzles, list) and len(puzzles) >= 100:
                        self.daily_puzzles = puzzles
                        self.puzzle_source = data.get("source", "bundled-cache") if isinstance(data, dict) else "bundled-cache"
                        if candidate_path != cache_path:
                            try:
                                cache_path.write_text(
                                    json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8",
                                )
                            except Exception:
                                pass
                        return
            except Exception:
                continue

        puzzles = self._fetch_lichess_mate1_puzzles(limit=PUZZLE_TARGET_COUNT)
        if len(puzzles) >= 100:
            self.daily_puzzles = puzzles
            self.puzzle_source = "Lichess public puzzle database — verified mateIn1"
            try:
                cache_path.write_text(
                    json.dumps(
                        {
                            "source": self.puzzle_source,
                            "source_url": LICHESS_PUZZLE_DB_URL,
                            "count": len(puzzles),
                            "generated_at": now_iso(),
                            "verification": "Each FEN has at least one legal move that immediately checkmates. All checkmating legal moves are accepted.",
                            "puzzles": puzzles,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

    def _fetch_lichess_mate1_puzzles(self, limit: int = 300) -> List[Dict[str, Any]]:
        if chess is None or zstd is None:
            return []

        puzzles: List[Dict[str, Any]] = []
        seen_fens: set[str] = set()
        try:
            with urlopen(LICHESS_PUZZLE_DB_URL, timeout=60) as response:
                dctx = zstd.ZstdDecompressor()
                with dctx.stream_reader(response) as reader:
                    text_stream = io.TextIOWrapper(reader, encoding="utf-8", newline="")
                    csv_reader = csv.DictReader(text_stream)
                    for row in csv_reader:
                        if len(puzzles) >= limit:
                            break
                        themes = (row.get("Themes") or "")
                        if "mateIn1" not in themes.split():
                            continue
                        try:
                            rating = int(row.get("Rating") or 0)
                            popularity = int(row.get("Popularity") or 0)
                            # Keep the first pack friendly and reasonably popular.
                            if rating and (rating < 600 or rating > 1800):
                                continue
                            if popularity and popularity < -5:
                                continue
                            fen = (row.get("FEN") or "").strip()
                            if not fen or fen in seen_fens:
                                continue
                            board = chess.Board(fen)
                            mate_moves = self._legal_checkmate_moves(board)
                            if not mate_moves:
                                continue
                            seen_fens.add(fen)
                            first_move = chess.Move.from_uci(mate_moves[0])
                            solution_san = board.san(first_move)
                            side = "Белые начинают" if board.turn == chess.WHITE else "Чёрные начинают"
                            puzzles.append(
                                {
                                    "id": 300000 + len(puzzles) + 1,
                                    "title": f"Мат в 1 — #{len(puzzles) + 1}",
                                    "side": side,
                                    "fen": fen,
                                    "question": "Найди мат в 1 ход. Сделай ход прямо на доске.",
                                    "solution_move": mate_moves[0],
                                    "solution_moves": mate_moves,
                                    "solution_san": solution_san,
                                    "explanation": f"{solution_san} — один из легальных матующих ходов. Позиция взята из открытой базы задач Lichess и проверена локально.",
                                    "source": "Lichess public puzzle database",
                                    "lichess_puzzle_id": row.get("PuzzleId") or "",
                                    "rating": rating,
                                    "popularity": popularity,
                                    "themes": themes,
                                    "game_url": row.get("GameUrl") or "",
                                }
                            )
                        except Exception:
                            continue
        except Exception:
            return []
        return puzzles

    def _legal_checkmate_moves(self, board: "chess.Board") -> List[str]:
        if chess is None:
            return []
        result: List[str] = []
        for move in board.legal_moves:
            candidate = board.copy(stack=False)
            candidate.push(move)
            if candidate.is_checkmate():
                result.append(move.uci())
        return sorted(result)

    def _is_legal_mate_move(self, fen: str, move_uci: str) -> bool:
        if chess is None:
            return False
        try:
            board = chess.Board(fen)
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                return False
            board.push(move)
            return bool(board.is_checkmate())
        except Exception:
            return False

    def _moscow_today(self):
        return (now_dt() + MOSCOW_OFFSET).date()

    def _today_key(self) -> str:
        return self._moscow_today().isoformat()

    def _yesterday_key(self) -> str:
        return (self._moscow_today() - timedelta(days=1)).isoformat()

    def _is_streak_stale(self, last_solved_date: str, today_key: Optional[str] = None) -> bool:
        """A streak survives today if the user solved today or yesterday.

        If the last solved date is older than yesterday in MSK time, the user
        has missed a full day and the current streak must be reset to 0.
        """
        if not last_solved_date:
            return False
        key = today_key or self._today_key()
        try:
            yesterday = (datetime.fromisoformat(key).date() - timedelta(days=1)).isoformat()
        except ValueError:
            yesterday = self._yesterday_key()
        return last_solved_date < yesterday

    async def normalize_puzzle_streak(self, telegram_id: int, today_key: Optional[str] = None) -> Dict[str, Any]:
        """Reset stale puzzle streaks using 00:00 MSK day boundaries.

        Called from bootstrap/profile/daily-puzzle/reminder flows so the profile
        never shows an old streak after a missed day. Best streak and solved
        count are preserved.
        """
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            if not rows:
                return {}
            user = dict(rows[0])
            last_solved = user.get("puzzle_last_solved_date") or ""
            current_streak = int(user.get("puzzle_streak") or 0)
            if current_streak > 0 and self._is_streak_stale(last_solved, today_key):
                await db.execute(
                    "UPDATE users SET puzzle_streak = 0, updated_at = ? WHERE telegram_id = ?",
                    (now_iso(), telegram_id),
                )
                await db.commit()
                user["puzzle_streak"] = 0
            return self._normalize_user(user)

    async def normalize_all_puzzle_streaks(self) -> int:
        """Reset stale streaks in batch; useful for startup/admin/export."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT telegram_id, profile_city, city, puzzle_last_solved_date FROM users WHERE COALESCE(puzzle_streak, 0) > 0"
            )
            ids = [
                int(row["telegram_id"])
                for row in rows
                if self._is_streak_stale(
                    row["puzzle_last_solved_date"] or "",
                    city_today_key(row["profile_city"] or row["city"] or "Минск"),
                )
            ]
            if ids:
                await db.executemany(
                    "UPDATE users SET puzzle_streak = 0, updated_at = ? WHERE telegram_id = ?",
                    [(now_iso(), telegram_id) for telegram_id in ids],
                )
                await db.commit()
            return len(ids)

    def _daily_puzzle_for_date(self, puzzle_date: Optional[str] = None) -> Dict[str, Any]:
        key = puzzle_date or self._today_key()
        try:
            d = datetime.fromisoformat(key).date()
            index = d.toordinal() % len(self.daily_puzzles)
        except Exception:
            index = 0
        puzzle = dict(self.daily_puzzles[index])
        puzzle["date"] = key
        return puzzle

    async def get_daily_puzzle(self, telegram_id: int, puzzle_date: Optional[str] = None) -> Dict[str, Any]:
        key = puzzle_date or self._today_key()
        puzzle = self._daily_puzzle_for_date(key)
        user = await self.normalize_puzzle_streak(telegram_id, today_key=key)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM daily_puzzle_attempts WHERE telegram_id = ? AND puzzle_date = ?",
                (telegram_id, key),
            )
        attempt = dict(rows[0]) if rows else None
        # If the user already opened an older broken puzzle for the same date,
        # ignore that attempt for the new corrected puzzle set.
        if attempt and int(attempt.get("puzzle_id") or 0) != int(puzzle["id"]):
            attempt = None
        public_puzzle = {k: v for k, v in puzzle.items() if k not in {"solution_move", "solution_moves", "solution_san"}}
        if attempt and attempt.get("solved"):
            public_puzzle["solution_san"] = puzzle.get("solution_san")
        return {
            "puzzle": public_puzzle,
            "attempt": attempt,
            "solved": bool(attempt and attempt.get("solved")),
            "stats": {
                "streak": int(user.get("puzzle_streak") or 0) if user else 0,
                "best_streak": int(user.get("puzzle_best_streak") or 0) if user else 0,
                "solved_count": int(user.get("puzzle_solved_count") or 0) if user else 0,
                "last_solved_date": user.get("puzzle_last_solved_date") if user else "",
            },
        }

    async def answer_daily_puzzle(self, telegram_id: int, selected_move: str, puzzle_date: Optional[str] = None) -> Dict[str, Any]:
        key = puzzle_date or self._today_key()
        puzzle = self._daily_puzzle_for_date(key)
        move = (selected_move or "").strip().lower()
        if not re.match(r"^[a-h][1-8][a-h][1-8][qrbn]?$", move):
            raise ValueError("INVALID_MOVE")
        solution_moves = [str(m).lower() for m in puzzle.get("solution_moves", [])]
        if not solution_moves and puzzle.get("solution_move"):
            solution_moves = [str(puzzle["solution_move"]).lower()]
        correct = move in solution_moves
        # Safety net: even if a cached answer list is stale, accept any legal move
        # that mates immediately from the puzzle FEN.
        if not correct:
            correct = self._is_legal_mate_move(str(puzzle.get("fen") or ""), move)
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM daily_puzzle_attempts WHERE telegram_id = ? AND puzzle_date = ?",
                (telegram_id, key),
            )
            already_solved = bool(
                rows and rows[0]["solved"] and int(rows[0]["puzzle_id"] or 0) == int(puzzle["id"])
            )
            if rows:
                attempt_count = int(rows[0]["attempt_count"] or 0) + 1
                solved = 1 if already_solved or correct else 0
                await db.execute(
                    """
                    UPDATE daily_puzzle_attempts
                    SET puzzle_id = ?, selected_move = ?, selected_option = NULL,
                        solved = ?, attempt_count = ?, updated_at = ?
                    WHERE telegram_id = ? AND puzzle_date = ?
                    """,
                    (puzzle["id"], move, solved, attempt_count, ts, telegram_id, key),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO daily_puzzle_attempts (
                        telegram_id, puzzle_date, puzzle_id, selected_option, selected_move,
                        solved, attempt_count, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, ?, ?, 1, ?, ?)
                    """,
                    (telegram_id, key, puzzle["id"], move, 1 if correct else 0, ts, ts),
                )

            if correct and not already_solved:
                user_rows = await db.execute_fetchall("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
                user = dict(user_rows[0]) if user_rows else {}
                last_solved = user.get("puzzle_last_solved_date") or ""
                current_streak = int(user.get("puzzle_streak") or 0)
                if current_streak > 0 and self._is_streak_stale(last_solved, key):
                    current_streak = 0
                try:
                    yesterday_key = (datetime.fromisoformat(key).date() - timedelta(days=1)).isoformat()
                except ValueError:
                    yesterday_key = self._yesterday_key()
                if last_solved == yesterday_key:
                    new_streak = current_streak + 1
                elif last_solved == key:
                    new_streak = current_streak
                else:
                    new_streak = 1
                best = max(int(user.get("puzzle_best_streak") or 0), new_streak)
                solved_count = int(user.get("puzzle_solved_count") or 0) + (0 if last_solved == key else 1)
                await db.execute(
                    """
                    UPDATE users
                    SET puzzle_streak = ?, puzzle_best_streak = ?, puzzle_solved_count = ?,
                        puzzle_last_solved_date = ?, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (new_streak, best, solved_count, key, ts, telegram_id),
                )
            await db.commit()
        result = await self.get_daily_puzzle(telegram_id, key)
        result["correct"] = correct
        result["selected_move"] = move
        result["solution_san"] = puzzle.get("solution_san") if correct else None
        result["explanation"] = puzzle.get("explanation", "Верно — это мат в один ход.") if correct else "Пока нет. Выбери свою фигуру, затем клетку назначения — как на шахматной доске."
        return result



    async def set_invited_by(self, telegram_id: int, invited_by: Optional[int]) -> bool:
        if not invited_by or int(invited_by) == int(telegram_id):
            return False
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT invited_by FROM users WHERE telegram_id = ?", (telegram_id,))
            if not rows:
                return False
            current = rows[0][0] if rows else None
            if current:
                return False
            inviter = await db.execute_fetchall("SELECT telegram_id FROM users WHERE telegram_id = ?", (invited_by,))
            if not inviter:
                return False
            ts = now_iso()
            await db.execute("UPDATE users SET invited_by = ?, updated_at = ? WHERE telegram_id = ?", (invited_by, ts, telegram_id))
            await db.execute(
                """
                INSERT OR IGNORE INTO referral_events
                    (referred_telegram_id, inviter_telegram_id, status, registered_at)
                VALUES (?, ?, 'registered', ?)
                """,
                (telegram_id, invited_by, ts),
            )
            await db.commit()
            return True

    async def activate_referral(self, telegram_id: int) -> bool:
        """Reward an inviter once, after the referred user performs a real action."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM referral_events WHERE referred_telegram_id = ? AND status = 'registered'",
                (telegram_id,),
            )
            if not rows:
                return False
            event = dict(rows[0])
            ts = now_iso()
            cursor = await db.execute(
                "UPDATE referral_events SET status = 'activated', activated_at = ?, reward_points = 10 "
                "WHERE referred_telegram_id = ? AND status = 'registered'",
                (ts, telegram_id),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return False
            await db.execute(
                "UPDATE users SET invite_count = COALESCE(invite_count, 0) + 1, "
                "referral_points = COALESCE(referral_points, 0) + 10, updated_at = ? WHERE telegram_id = ?",
                (ts, int(event["inviter_telegram_id"])),
            )
            await db.commit()
            return True

    async def due_referral_notifications(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT r.*, COALESCE(friend.display_name, friend.first_name, 'Игрок') AS friend_name,
                       COALESCE(inviter.ui_language, inviter.language_code, 'ru') AS inviter_language
                FROM referral_events r
                JOIN users friend ON friend.telegram_id = r.referred_telegram_id
                JOIN users inviter ON inviter.telegram_id = r.inviter_telegram_id
                WHERE r.registration_notified_at IS NULL
                   OR (r.status = 'activated' AND r.activation_notified_at IS NULL)
                ORDER BY r.registered_at LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            )
            return [dict(row) for row in rows]

    async def mark_referral_notification(self, referred_telegram_id: int, kind: str) -> None:
        column = "activation_notified_at" if kind == "activation" else "registration_notified_at"
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                f"UPDATE referral_events SET {column} = ? WHERE referred_telegram_id = ?",
                (now_iso(), referred_telegram_id),
            )
            await db.commit()

    async def referral_stats(self, telegram_id: int) -> Dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT COUNT(*),
                       SUM(CASE WHEN status = 'activated' THEN 1 ELSE 0 END),
                       COALESCE(SUM(reward_points), 0)
                FROM referral_events WHERE inviter_telegram_id = ?
                """,
                (telegram_id,),
            )
            registered = int(rows[0][0] or 0)
            activated = int(rows[0][1] or 0)
            points = int(rows[0][2] or 0)
            recent = await db.execute_fetchall(
                """
                SELECT r.status, r.registered_at, r.activated_at,
                       COALESCE(u.display_name, u.first_name, 'Игрок') AS display_name
                FROM referral_events r
                JOIN users u ON u.telegram_id = r.referred_telegram_id
                WHERE r.inviter_telegram_id = ?
                ORDER BY r.registered_at DESC LIMIT 10
                """,
                (telegram_id,),
            )
        tiers = [
            {"name": "Амбассадор", "required": 10},
            {"name": "Организатор", "required": 5},
            {"name": "Напарник", "required": 1},
        ]
        current = next((tier["name"] for tier in tiers if activated >= tier["required"]), "Новичок")
        next_tier = next(
            ({"name": tier["name"], "required": tier["required"], "remaining": tier["required"] - activated}
             for tier in reversed(tiers) if activated < tier["required"]),
            None,
        )
        return {
            "registered": registered,
            "activated": activated,
            "pending": max(0, registered - activated),
            "points": points,
            "tier": current,
            "next_tier": next_tier,
            "recent": [dict(row) for row in recent],
        }

    async def submit_place_rating(self, game_id: int, rater_telegram_id: int, score: int, comment: str = "") -> Dict[str, Any]:
        if score < 1 or score > 5:
            raise ValueError("INVALID_SCORE")
        game = await self._ensure_after_game_action_available(game_id, rater_telegram_id)
        place_key = game.get("map_url") or f"{game.get('city','')}/{game.get('place','')}/{game.get('address','')}".lower()
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            existing = await db.execute_fetchall("SELECT * FROM place_ratings WHERE game_id = ? AND rater_telegram_id = ?", (game_id, rater_telegram_id))
            if existing:
                raise ValueError("ALREADY_RATED_PLACE")
            cursor = await db.execute(
                """
                INSERT INTO place_ratings (game_id, rater_telegram_id, place_key, place_name, address, map_url, score, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (game_id, rater_telegram_id, place_key, game.get("place") or "", game.get("address") or "", game.get("map_url") or "", score, comment.strip()[:300], ts),
            )
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM place_ratings WHERE id = ?", (cursor.lastrowid,))
            return dict(rows[0])

    async def upsert_game_diary(self, game_id: int, owner_telegram_id: int, result: str = "", notes: str = "") -> Dict[str, Any]:
        game = await self._ensure_after_game_action_available(game_id, owner_telegram_id)
        ts = now_iso()
        result = (result or "").strip()[:80]
        notes = (notes or "").strip()[:1000]
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            existing = await db.execute_fetchall("SELECT * FROM game_diary WHERE game_id = ? AND owner_telegram_id = ?", (game_id, owner_telegram_id))
            if existing:
                await db.execute("UPDATE game_diary SET result = ?, notes = ?, updated_at = ? WHERE game_id = ? AND owner_telegram_id = ?", (result, notes, ts, game_id, owner_telegram_id))
            else:
                await db.execute("INSERT INTO game_diary (game_id, owner_telegram_id, result, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (game_id, owner_telegram_id, result, notes, ts, ts))
            await db.commit()
            rows = await db.execute_fetchall("SELECT * FROM game_diary WHERE game_id = ? AND owner_telegram_id = ?", (game_id, owner_telegram_id))
            return dict(rows[0])

    async def list_diary(self, telegram_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        my = await self.list_my_games(telegram_id=telegram_id, limit=limit)
        games = unique_games = []
        seen = set()
        for g in (my.get("created") or []) + (my.get("responded") or []):
            if g.get("id") not in seen and g.get("status") in (STATUS_CONFIRMED, STATUS_COMPLETED, STATUS_CANCELLED, STATUS_EXPIRED):
                seen.add(g.get("id"))
                unique_games.append(g)
        return unique_games

    async def _get_place_rating_summary(self, game: Dict[str, Any]) -> Dict[str, Any]:
        place_key = game.get("map_url") or f"{game.get('city','')}/{game.get('place','')}/{game.get('address','')}".lower()
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT AVG(score), COUNT(*) FROM place_ratings WHERE place_key = ?", (place_key,))
            avg = float(rows[0][0]) if rows and rows[0][0] is not None else 0.0
            count = int(rows[0][1]) if rows else 0
            return {"avg": round(avg, 2), "count": count}

    async def _get_my_place_rating(self, game_id: int, telegram_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not telegram_id:
            return None
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM place_ratings WHERE game_id = ? AND rater_telegram_id = ?", (game_id, telegram_id))
            return dict(rows[0]) if rows else None

    async def _get_my_diary_entry(self, game_id: int, telegram_id: Optional[int]) -> Optional[Dict[str, Any]]:
        if not telegram_id:
            return None
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM game_diary WHERE game_id = ? AND owner_telegram_id = ?", (game_id, telegram_id))
            return dict(rows[0]) if rows else None

    async def toggle_favorite_player(self, owner_telegram_id: int, favorite_telegram_id: int) -> Dict[str, Any]:
        if owner_telegram_id == favorite_telegram_id:
            raise ValueError("CANNOT_FAVORITE_SELF")
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            existing = await db.execute_fetchall(
                "SELECT 1 FROM favorite_players WHERE owner_telegram_id = ? AND favorite_telegram_id = ?",
                (owner_telegram_id, favorite_telegram_id),
            )
            if existing:
                await db.execute(
                    "DELETE FROM favorite_players WHERE owner_telegram_id = ? AND favorite_telegram_id = ?",
                    (owner_telegram_id, favorite_telegram_id),
                )
                favorited = False
            else:
                await db.execute(
                    "INSERT OR IGNORE INTO favorite_players (owner_telegram_id, favorite_telegram_id, created_at) VALUES (?, ?, ?)",
                    (owner_telegram_id, favorite_telegram_id, ts),
                )
                favorited = True
            await db.commit()
        user = await self.get_public_user(favorite_telegram_id, owner_telegram_id)
        return {"favorited": favorited, "user": user}

    async def block_user(self, blocker_telegram_id: int, blocked_telegram_id: int) -> Dict[str, Any]:
        if blocker_telegram_id == blocked_telegram_id:
            raise ValueError("CANNOT_BLOCK_SELF")
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO user_blocks (blocker_telegram_id, blocked_telegram_id, created_at) VALUES (?, ?, ?)",
                (blocker_telegram_id, blocked_telegram_id, now_iso()),
            )
            await db.commit()
        user = await self.get_public_user(blocked_telegram_id, blocker_telegram_id)
        return {"blocked": True, "user": user}

    async def _ensure_after_game_action_available(self, game_id: int, actor_telegram_id: int, target_telegram_id: Optional[int] = None) -> Dict[str, Any]:
        game = await self.get_game(game_id)
        if not game or not game.get("accepted_response"):
            raise ValueError("GAME_NOT_CONFIRMED")
        creator_id = int(game["creator_telegram_id"])
        responder_id = int(game["accepted_response"]["responder_telegram_id"])
        if actor_telegram_id not in {creator_id, responder_id}:
            raise ValueError("NOT_ALLOWED")
        opponent_id = responder_id if actor_telegram_id == creator_id else creator_id
        if target_telegram_id is not None and int(target_telegram_id) != opponent_id:
            raise ValueError("NOT_ALLOWED")
        scheduled_at = effective_game_datetime(game)
        if not scheduled_at or now_dt() < scheduled_at + timedelta(hours=1):
            raise ValueError("AFTER_GAME_ACTION_NOT_AVAILABLE_YET")
        return game

    async def report_user(self, reporter_telegram_id: int, reported_telegram_id: int, reason: str, comment: str = "", game_id: Optional[int] = None) -> Dict[str, Any]:
        if reporter_telegram_id == reported_telegram_id:
            raise ValueError("CANNOT_REPORT_SELF")
        if game_id is not None:
            await self._ensure_after_game_action_available(game_id, reporter_telegram_id, reported_telegram_id)
        ts = now_iso()
        reason = (reason or "Другое").strip()[:120]
        comment = (comment or "").strip()[:500]
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO user_reports (reporter_telegram_id, reported_telegram_id, game_id, reason, comment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (reporter_telegram_id, reported_telegram_id, game_id, reason, comment, ts),
            )
            await db.commit()
            return {"id": int(cursor.lastrowid), "reason": reason, "comment": comment}

    async def report_no_show(self, game_id: int, reporter_telegram_id: int) -> Dict[str, Any]:
        game = await self._ensure_after_game_action_available(game_id, reporter_telegram_id)
        creator_id = int(game["creator_telegram_id"])
        responder_id = int(game["accepted_response"]["responder_telegram_id"])
        if reporter_telegram_id == creator_id:
            target_id = responder_id
        elif reporter_telegram_id == responder_id:
            target_id = creator_id
        else:
            raise ValueError("NOT_ALLOWED")
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE game_requests SET no_show_reported_by = ?, no_show_target_id = ?, updated_at = ? WHERE id = ?",
                (reporter_telegram_id, target_id, ts, game_id),
            )
            await db.execute(
                "INSERT INTO user_reports (reporter_telegram_id, reported_telegram_id, game_id, reason, comment, created_at) VALUES (?, ?, ?, 'Не пришёл на партию', '', ?)",
                (reporter_telegram_id, target_id, game_id, ts),
            )
            await db.commit()
        return await self.get_game(game_id) or {}

    async def add_game_photo(self, game_id: int, uploader_telegram_id: int, photo_data_url: str, caption: str = "") -> Dict[str, Any]:
        await self._ensure_after_game_action_available(game_id, uploader_telegram_id)
        photo_data_url = validate_image_data_url(photo_data_url, 2_500_000)
        if not photo_data_url:
            raise ValueError("INVALID_PHOTO")
        ts = now_iso()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO game_photos (game_id, uploader_telegram_id, photo_data_url, caption, created_at) VALUES (?, ?, ?, ?, ?)",
                (game_id, uploader_telegram_id, photo_data_url, (caption or "").strip()[:200], ts),
            )
            await db.commit()
            return {"id": int(cursor.lastrowid), "game_id": game_id, "photo_data_url": photo_data_url, "caption": caption, "created_at": ts}

    async def create_rematch(self, game_id: int, requester_telegram_id: int) -> Dict[str, Any]:
        game = await self.get_game(game_id)
        if not game or not game.get("accepted_response"):
            raise ValueError("GAME_NOT_CONFIRMED")
        creator_id = int(game["creator_telegram_id"])
        responder_id = int(game["accepted_response"]["responder_telegram_id"])
        if requester_telegram_id not in {creator_id, responder_id}:
            raise ValueError("NOT_ALLOWED")
        tomorrow = (now_dt() + timedelta(days=1)).date().isoformat()
        data = {
            "city": game.get("city") or "Минск",
            "place": game.get("place") or "Реванш",
            "area": game.get("area") or "",
            "address": game.get("address") or "",
            "place_id": game.get("place_id") or "",
            "latitude": game.get("latitude"),
            "longitude": game.get("longitude"),
            "map_url": game.get("map_url") or "",
            "date_label": tomorrow,
            "time_label": game.get("time_label") or "18:30",
            "scheduled_at": f"{tomorrow}T{(game.get('time_label') or '18:30')}:00Z",
            "game_format": game.get("game_format") or "Рапид 10+5",
            "level": game.get("level") or "Любой",
            "has_board": game.get("has_board", True),
            "comment": "Реванш по прошлой партии.",
        }
        return await self.create_game(requester_telegram_id, data, default_city=data["city"])

    async def _get_game_photos(self, game_id: int) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM game_photos WHERE game_id = ? ORDER BY id DESC LIMIT 12",
                (game_id,),
            )
            return [dict(row) for row in rows]

    async def _user_reliability(self, telegram_id: int) -> Dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            rows = await db.execute_fetchall("SELECT COUNT(*) FROM game_requests WHERE no_show_target_id = ?", (telegram_id,))
            no_show = int(rows[0][0]) if rows else 0
            rows2 = await db.execute_fetchall(
                """
                SELECT COUNT(*) FROM game_requests g
                LEFT JOIN responses r ON r.id = g.accepted_response_id
                WHERE g.status IN ('confirmed','completed') AND (g.creator_telegram_id = ? OR r.responder_telegram_id = ?)
                """,
                (telegram_id, telegram_id),
            )
            games_count = int(rows2[0][0]) if rows2 else 0
            successful = max(0, games_count - no_show)
            # A small Bayesian prior prevents a brand-new account from showing 100%.
            score = round(((successful + 4) / (games_count + 5)) * 100)
            if games_count == 0:
                score = 80
            label = "excellent" if score >= 95 else "good" if score >= 85 else "attention" if score >= 70 else "low"
            return {
                "games_count": games_count,
                "no_show_count": no_show,
                "successful_games": successful,
                "score": max(0, min(score, 100)),
                "label": label,
            }



    async def admin_export_snapshot(self) -> Dict[str, Any]:
        """Small admin helper used by launcher-like clients if needed."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            users = await db.execute_fetchall("SELECT telegram_id, username, display_name, profile_city, rating_avg, rating_count, created_at FROM users ORDER BY created_at DESC")
            games = await db.execute_fetchall("SELECT id, creator_telegram_id, city, place, date_label, time_label, status, created_at FROM game_requests ORDER BY created_at DESC")
            referral_rows = await db.execute_fetchall(
                "SELECT COUNT(*), SUM(CASE WHEN status = 'activated' THEN 1 ELSE 0 END), "
                "COALESCE(SUM(reward_points), 0) FROM referral_events"
            )
            analytics_rows = await db.execute_fetchall(
                """
                SELECT
                    (SELECT COUNT(*) FROM users WHERE created_at >= ?) AS new_users_7d,
                    (SELECT COUNT(DISTINCT telegram_id) FROM analytics_events WHERE created_at >= ?) AS active_users_7d,
                    (SELECT COUNT(*) FROM game_requests WHERE created_at >= ?) AS games_7d,
                    (SELECT COUNT(*) FROM game_requests WHERE status = 'completed') AS completed_games
                """,
                (
                    (now_dt() - timedelta(days=7)).isoformat(),
                    (now_dt() - timedelta(days=7)).isoformat(),
                    (now_dt() - timedelta(days=7)).isoformat(),
                ),
            )
        referral = {
            "registered": int(referral_rows[0][0] or 0),
            "activated": int(referral_rows[0][1] or 0),
            "points": int(referral_rows[0][2] or 0),
        }
        analytics = dict(analytics_rows[0]) if analytics_rows else {}
        return {
            "users": [dict(x) for x in users],
            "games": [dict(x) for x in games],
            "referral": referral,
            "analytics": analytics,
        }

    def _public_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        user = self._normalize_user(dict(user))
        return {
            "telegram_id": user.get("telegram_id"),
            "display_name": user.get("display_name"),
            "username": user.get("username") if user.get("show_telegram_username") else None,
            "public_handle": user.get("public_handle"),
            "profile_city": user.get("profile_city"),
            "level": user.get("level"),
            "bio": user.get("bio"),
            "photo_data_url": user.get("photo_data_url"),
            "rating_avg": user.get("rating_avg"),
            "rating_count": user.get("rating_count"),
            "puzzle_streak": user.get("puzzle_streak"),
            "puzzle_best_streak": user.get("puzzle_best_streak"),
            "puzzle_solved_count": user.get("puzzle_solved_count"),
        }

    def _normalize_chat_message(self, msg: Dict[str, Any], viewer_telegram_id: int) -> Dict[str, Any]:
        show_username = bool(msg.pop("sender_show_telegram_username", 0))
        username = msg.pop("sender_username", None)
        display_name = msg.pop("sender_display_name", None) or msg.pop("sender_first_name", None) or "Игрок"
        msg["mine"] = int(msg.get("sender_telegram_id")) == int(viewer_telegram_id)
        msg["sender"] = {
            "telegram_id": msg.get("sender_telegram_id"),
            "display_name": display_name,
            "username": username if show_username else None,
            "photo_data_url": msg.pop("sender_photo_data_url", "") or "",
        }
        return msg

    def _normalize_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        user["show_telegram_username"] = bool(user.get("show_telegram_username"))
        user["display_name"] = user.get("display_name") or user.get("first_name") or user.get("username") or "Игрок"
        user["profile_city"] = canonical_city(user.get("profile_city") or user.get("city") or "Минск")
        user["level"] = user.get("level") or "Средний"
        user["bio"] = user.get("bio") or ""
        user["photo_data_url"] = user.get("photo_data_url") or ""
        user["rating_avg"] = round(float(user.get("rating_avg") or 0), 2)
        user["rating_count"] = int(user.get("rating_count") or 0)
        user["puzzle_streak"] = int(user.get("puzzle_streak") or 0)
        user["puzzle_best_streak"] = int(user.get("puzzle_best_streak") or 0)
        user["puzzle_solved_count"] = int(user.get("puzzle_solved_count") or 0)
        user["puzzle_last_solved_date"] = user.get("puzzle_last_solved_date") or ""
        user["notify_game_reminders"] = bool(user.get("notify_game_reminders", 1))
        user["notify_new_requests"] = bool(user.get("notify_new_requests", 0))
        user["notify_puzzle_streak"] = bool(user.get("notify_puzzle_streak", 1))
        user["subscription_format"] = user.get("subscription_format") or "all"
        user["subscription_level"] = user.get("subscription_level") or "all"
        user["theme_mode"] = user.get("theme_mode") or "light"
        if user["theme_mode"] not in {"light", "dark", "system"}:
            user["theme_mode"] = "light"
        user["ui_language"] = user.get("ui_language") or ""
        user["puzzle_reminder_sent_date"] = user.get("puzzle_reminder_sent_date") or ""
        user["invited_by"] = user.get("invited_by")
        user["invite_count"] = int(user.get("invite_count") or 0)
        user["referral_points"] = int(user.get("referral_points") or 0)
        user["public_handle"] = f"@{user['username']}" if user.get("show_telegram_username") and user.get("username") else None
        return user

    def _normalize_game(self, game: Dict[str, Any]) -> Dict[str, Any]:
        game["has_board"] = bool(game.get("has_board"))
        game["creator_confirmed"] = bool(game.get("creator_confirmed"))
        game["responder_confirmed"] = bool(game.get("responder_confirmed"))
        game["creator_checked_in"] = bool(game.get("creator_checked_in_at"))
        game["responder_checked_in"] = bool(game.get("responder_checked_in_at"))
        game["reminder_3h_sent"] = bool(game.get("reminder_3h_sent"))
        game["reminder_30m_sent"] = bool(game.get("reminder_30m_sent"))
        game["is_flexible"] = bool(game.get("is_flexible"))
        game["time_window_start"] = game.get("time_window_start") or ""
        game["time_window_end"] = game.get("time_window_end") or ""
        game["cancel_reason"] = game.get("cancel_reason") or ""
        game["responses_count"] = int(game.get("responses_count") or 0)
        game["pending_responses_count"] = int(game.get("pending_responses_count") or 0)
        game["waitlist_count"] = int(game.get("waitlist_count") or 0)
        show_username = bool(game.pop("creator_show_telegram_username", 0))
        username = game.pop("creator_username", None)
        display_name = game.pop("creator_display_name", None) or game.get("creator_first_name") or "Игрок"
        game["creator"] = {
            "telegram_id": game.get("creator_telegram_id"),
            "display_name": display_name,
            "username": username if show_username else None,
            "first_name": game.pop("creator_first_name", None),
            "last_name": game.pop("creator_last_name", None),
            "level": game.pop("creator_level", None),
            "profile_city": game.pop("creator_profile_city", None),
            "photo_data_url": game.pop("creator_photo_data_url", "") or "",
            "rating_avg": round(float(game.pop("creator_rating_avg", 0) or 0), 2),
            "rating_count": int(game.pop("creator_rating_count", 0) or 0),
            "show_telegram_username": show_username,
        }
        game["public_visible"] = game["status"] in (STATUS_OPEN, STATUS_PENDING)
        return game

    def _normalize_scheduled_at(self, scheduled_at: Optional[str], date_label: Optional[str], time_label: Optional[str]) -> Optional[str]:
        # Store the actual intended local time as UTC.
        # ChessMeet currently targets Minsk/Moscow time (UTC+3).
        local_dt = parse_local_game_datetime(date_label, time_label)
        if local_dt:
            return local_dt.isoformat()
        if scheduled_at:
            parsed = parse_iso(scheduled_at)
            if parsed:
                return parsed.isoformat()
        return None
