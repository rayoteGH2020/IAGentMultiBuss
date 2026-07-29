"""CSRF tokens for browser-originated mutable requests."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

from app.config import get_settings

if TYPE_CHECKING:
    from uuid import UUID

CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_TTL_SECONDS = 8 * 60 * 60


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _secret_bytes() -> bytes:
    return get_settings().app_secret_key.get_secret_value().encode("utf-8")


def _sign(payload: bytes) -> str:
    return _b64_encode(hmac.new(_secret_bytes(), payload, hashlib.sha256).digest())


def generate_csrf_token(*, user_id: UUID, tenant_id: UUID, now: int | None = None) -> str:
    """Create a signed CSRF token bound to the current user and tenant."""
    issued_at = int(time.time()) if now is None else now
    payload = json.dumps(
        {"u": str(user_id), "t": str(tenant_id), "iat": issued_at},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = _b64_encode(payload)
    return f"{encoded_payload}.{_sign(payload)}"


def validate_csrf_token(
    token: str,
    *,
    user_id: UUID,
    tenant_id: UUID,
    now: int | None = None,
) -> bool:
    """Return True when token is signed, fresh, and bound to user+tenant."""
    try:
        encoded_payload, signature = token.split(".", maxsplit=1)
        payload = _b64_decode(encoded_payload)
    except (ValueError, TypeError, binascii.Error):
        return False

    if not hmac.compare_digest(signature, _sign(payload)):
        return False

    try:
        data: Any = json.loads(payload)
    except json.JSONDecodeError:
        return False

    if not isinstance(data, dict):
        return False
    if data.get("u") != str(user_id) or data.get("t") != str(tenant_id):
        return False

    issued_at = data.get("iat")
    if not isinstance(issued_at, int):
        return False

    current = int(time.time()) if now is None else now
    return 0 <= current - issued_at <= CSRF_TOKEN_TTL_SECONDS
