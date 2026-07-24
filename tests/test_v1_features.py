from datetime import datetime, timezone

from src.cities import city_catalog, city_today_key
from src.main import GameCreate, PreferencesUpdate


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
