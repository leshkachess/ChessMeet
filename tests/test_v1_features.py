from datetime import datetime, timezone
import asyncio

from src.cities import city_catalog, city_today_key
from src.main import GameCreate, PreferencesUpdate, resolve_database_path
from src.database import Database


def test_city_catalog_has_expected_groups():
    cities = city_catalog()
    assert len(cities) == 16
    assert sum(city["country"] == "BY" for city in cities) == 6
    assert sum(city["country"] == "RU" for city in cities) == 10


def test_city_dates_follow_local_timezone():
    instant = datetime(2026, 7, 24, 19, 30, tzinfo=timezone.utc)
    assert city_today_key("Минск", instant) == "2026-07-24"
    assert city_today_key("Новосибирск", instant) == "2026-07-25"


def test_unsupported_game_city_is_rejected():
    try:
        GameCreate(
            city="Лондон",
            place="Cafe",
            date_label="2026-08-01",
            time_label="18:00",
            game_format="Рапид 10+5",
            level="Средний",
        )
    except ValueError:
        return
    raise AssertionError("Unsupported city was accepted")


def test_preferences_accept_alert_only_update():
    update = PreferencesUpdate(notify_new_requests=True)
    assert update.notify_new_requests is True


def test_response_list_is_private_to_creator(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "privacy.sqlite3"))
        await db.init()
        await db.upsert_user({"id": 1, "first_name": "Owner", "language_code": "ru"})
        await db.upsert_user({"id": 2, "first_name": "Player", "language_code": "ru"})
        await db.upsert_user({"id": 3, "first_name": "Other", "language_code": "ru"})
        game = await db.create_game(1, {
            "city": "Минск", "place": "Cafe", "date_label": "2099-08-01",
            "time_label": "18:00", "game_format": "Рапид", "level": "Средний",
        })
        await db.create_response(game["id"], 2, {"proposed_comment": "Ready"})
        assert len(await db.list_game_responses(game["id"], 1)) == 1
        try:
            await db.list_game_responses(game["id"], 3)
        except ValueError as exc:
            assert str(exc) == "NOT_ALLOWED"
        else:
            raise AssertionError("Another user could read private responses")

    asyncio.run(scenario())


def test_new_request_subscription_filters_format_and_level(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "subscriptions.sqlite3"))
        await db.init()
        city = "\u041c\u0438\u043d\u0441\u043a"
        rapid = "\u0440\u0430\u043f\u0438\u0434"
        middle = "\u0441\u0440\u0435\u0434\u043d\u0438\u0439"
        blitz = "\u0431\u043b\u0438\u0446"
        await db.upsert_user({"id": 10, "first_name": "Subscriber"}, default_city=city)
        await db.update_user_profile(10, {
            "display_name": "Subscriber",
            "profile_city": city,
            "level": middle,
            "notify_new_requests": True,
            "subscription_format": rapid,
            "subscription_level": middle,
        })
        matching = await db.list_users_for_new_request_notifications(
            20, city, f"{rapid} 10+0", middle,
        )
        wrong_format = await db.list_users_for_new_request_notifications(
            20, city, f"{blitz} 3+2", middle,
        )
        assert len(matching) == 1
        assert wrong_format == []

    asyncio.run(scenario())


def test_railway_database_requires_attached_volume(tmp_path):
    try:
        resolve_database_path({
            "RAILWAY_SERVICE_ID": "service",
            "DATABASE_PATH": str(tmp_path / "ephemeral.sqlite3"),
        })
    except RuntimeError as exc:
        assert "Persistent Railway Volume" in str(exc)
    else:
        raise AssertionError("Ephemeral Railway database was accepted")

    mount = tmp_path / "volume"
    resolved = resolve_database_path({
        "RAILWAY_SERVICE_ID": "service",
        "RAILWAY_VOLUME_MOUNT_PATH": str(mount),
    })
    assert resolved == str((mount / "chess_irl.sqlite3").resolve())


def test_database_creates_pre_migration_backup(tmp_path):
    async def scenario():
        path = tmp_path / "persistent.sqlite3"
        db = Database(str(path))
        await db.init()
        await db.upsert_user({"id": 100, "first_name": "Persistent"})
        await db.init()
        backups = db.list_local_backups()
        assert backups
        assert backups[0]["size_bytes"] > 0

    asyncio.run(scenario())


def test_admin_audit_and_report_workflow_schema(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "admin.sqlite3"))
        await db.init()
        import aiosqlite
        async with aiosqlite.connect(db.path) as conn:
            tables = {row[0] for row in await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            report_columns = {row[1] for row in await conn.execute_fetchall(
                "PRAGMA table_info(user_reports)"
            )}
        assert "admin_audit_log" in tables
        assert {"status", "resolved_by", "resolved_at"} <= report_columns

    asyncio.run(scenario())
