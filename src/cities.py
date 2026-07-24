from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


CITIES: tuple[dict[str, Any], ...] = (
    {"id": "minsk", "name": "Минск", "country": "BY", "timezone": "Europe/Minsk", "latitude": 53.9006, "longitude": 27.5590},
    {"id": "brest", "name": "Брест", "country": "BY", "timezone": "Europe/Minsk", "latitude": 52.0976, "longitude": 23.7341},
    {"id": "vitebsk", "name": "Витебск", "country": "BY", "timezone": "Europe/Minsk", "latitude": 55.1904, "longitude": 30.2049},
    {"id": "gomel", "name": "Гомель", "country": "BY", "timezone": "Europe/Minsk", "latitude": 52.4345, "longitude": 30.9754},
    {"id": "grodno", "name": "Гродно", "country": "BY", "timezone": "Europe/Minsk", "latitude": 53.6694, "longitude": 23.8131},
    {"id": "mogilev", "name": "Могилёв", "country": "BY", "timezone": "Europe/Minsk", "latitude": 53.9007, "longitude": 30.3314},
    {"id": "moscow", "name": "Москва", "country": "RU", "timezone": "Europe/Moscow", "latitude": 55.7558, "longitude": 37.6173},
    {"id": "saint-petersburg", "name": "Санкт-Петербург", "country": "RU", "timezone": "Europe/Moscow", "latitude": 59.9343, "longitude": 30.3351},
    {"id": "novosibirsk", "name": "Новосибирск", "country": "RU", "timezone": "Asia/Novosibirsk", "latitude": 55.0084, "longitude": 82.9357},
    {"id": "yekaterinburg", "name": "Екатеринбург", "country": "RU", "timezone": "Asia/Yekaterinburg", "latitude": 56.8389, "longitude": 60.6057},
    {"id": "kazan", "name": "Казань", "country": "RU", "timezone": "Europe/Moscow", "latitude": 55.7961, "longitude": 49.1064},
    {"id": "krasnoyarsk", "name": "Красноярск", "country": "RU", "timezone": "Asia/Krasnoyarsk", "latitude": 56.0153, "longitude": 92.8932},
    {"id": "nizhny-novgorod", "name": "Нижний Новгород", "country": "RU", "timezone": "Europe/Moscow", "latitude": 56.2965, "longitude": 43.9361},
    {"id": "chelyabinsk", "name": "Челябинск", "country": "RU", "timezone": "Asia/Yekaterinburg", "latitude": 55.1644, "longitude": 61.4368},
    {"id": "samara", "name": "Самара", "country": "RU", "timezone": "Europe/Samara", "latitude": 53.1959, "longitude": 50.1002},
    {"id": "ufa", "name": "Уфа", "country": "RU", "timezone": "Asia/Yekaterinburg", "latitude": 54.7388, "longitude": 55.9721},
)

_BY_NAME = {city["name"].casefold(): city for city in CITIES}
_ALIASES = {
    "могилев": "Могилёв",
    "питер": "Санкт-Петербург",
    "санкт петербург": "Санкт-Петербург",
    "нижний": "Нижний Новгород",
}


def city_catalog() -> list[dict[str, Any]]:
    return [dict(city) for city in CITIES]


def canonical_city(value: str | None, default: str = "Минск") -> str:
    raw = (value or "").strip()
    alias = _ALIASES.get(raw.casefold(), raw)
    city = _BY_NAME.get(alias.casefold())
    if city:
        return city["name"]
    fallback = _BY_NAME.get((default or "").strip().casefold())
    return fallback["name"] if fallback else "Минск"


def is_supported_city(value: str | None) -> bool:
    raw = (value or "").strip()
    alias = _ALIASES.get(raw.casefold(), raw)
    return alias.casefold() in _BY_NAME


def city_info(value: str | None) -> dict[str, Any]:
    name = canonical_city(value)
    return dict(_BY_NAME[name.casefold()])


def city_today_key(value: str | None, at: datetime | None = None) -> str:
    instant = at or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(ZoneInfo(city_info(value)["timezone"])).date().isoformat()


def city_local_hour(value: str | None, at: datetime | None = None) -> int:
    instant = at or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(ZoneInfo(city_info(value)["timezone"])).hour


def public_city_config() -> list[dict[str, Any]]:
    return city_catalog()
