from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict
from urllib.parse import parse_qsl


class TelegramAuthError(Exception):
    pass


def _parse_init_data(init_data: str) -> Dict[str, str]:
    return dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))


def validate_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> Dict[str, Any]:
    """
    Validates Telegram Mini App initData according to Telegram's documented HMAC flow.
    Returns parsed data with user dict.
    """
    if not init_data:
        raise TelegramAuthError("Missing Telegram initData")

    parsed = _parse_init_data(init_data)
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise TelegramAuthError("Missing initData hash")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise TelegramAuthError("Invalid initData signature")

    auth_date_raw = parsed.get("auth_date")
    if auth_date_raw:
        try:
            auth_date = int(auth_date_raw)
            if max_age_seconds > 0 and time.time() - auth_date > max_age_seconds:
                raise TelegramAuthError("initData is too old")
        except ValueError as exc:
            raise TelegramAuthError("Invalid auth_date") from exc

    user_raw = parsed.get("user")
    if not user_raw:
        raise TelegramAuthError("Missing user payload")

    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("Invalid user JSON") from exc

    if "id" not in user:
        raise TelegramAuthError("Missing user id")

    parsed["user"] = user
    return parsed


def demo_user() -> Dict[str, Any]:
    return {
        "id": 100000001,
        "first_name": "Demo",
        "last_name": "Player",
        "username": "demo_chess_player",
        "language_code": "ru",
    }
