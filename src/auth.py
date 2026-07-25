from __future__ import annotations

import hashlib
import hmac
import json
import time
import base64
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
    if not auth_date_raw:
        raise TelegramAuthError("Missing auth_date")
    try:
        auth_date = int(auth_date_raw)
        age = time.time() - auth_date
        if age < -60:
            raise TelegramAuthError("initData auth_date is in the future")
        if max_age_seconds > 0 and age > max_age_seconds:
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


def create_webapp_auth_token(user: Dict[str, Any], bot_token: str) -> str:
    payload = {
        "id": int(user["id"]),
        "first_name": str(user.get("first_name") or "")[:64],
        "last_name": str(user.get("last_name") or "")[:64],
        "username": str(user.get("username") or "")[:64],
        "language_code": str(user.get("language_code") or "")[:12],
        "iat": int(time.time()),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(
        hashlib.sha256(bot_token.encode("utf-8")).digest(),
        b"ChessMeetWebAppAuth:" + encoded,
        hashlib.sha256,
    ).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def validate_webapp_auth_token(token: str, bot_token: str, max_age_seconds: int = 3600) -> Dict[str, Any]:
    try:
        encoded_text, signature_text = token.split(".", 1)
        encoded = encoded_text.encode("ascii")
        signature = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        expected = hmac.new(
            hashlib.sha256(bot_token.encode("utf-8")).digest(),
            b"ChessMeetWebAppAuth:" + encoded,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(signature, expected):
            raise TelegramAuthError("Invalid fallback auth signature")
        raw = base64.urlsafe_b64decode(encoded_text + "=" * (-len(encoded_text) % 4))
        payload = json.loads(raw)
        age = time.time() - int(payload["iat"])
        if age < -60 or age > max_age_seconds:
            raise TelegramAuthError("Fallback auth token expired")
        if not isinstance(payload.get("id"), int):
            raise TelegramAuthError("Invalid fallback auth user")
        return payload
    except TelegramAuthError:
        raise
    except Exception as exc:
        raise TelegramAuthError("Invalid fallback auth token") from exc


def demo_user() -> Dict[str, Any]:
    return {
        "id": 100000001,
        "first_name": "Demo",
        "last_name": "Player",
        "username": "demo_chess_player",
        "language_code": "ru",
    }
