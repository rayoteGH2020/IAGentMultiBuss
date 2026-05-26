"""Estado OAuth en Redis para prevenir CSRF (Paso 17)."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    import redis.asyncio as redis

OAUTH_STATE_PREFIX = "oauth:state:"
OAUTH_STATE_TTL_SECONDS = 600
_STATE_NONCE_HEX_LEN = 64  # secrets.token_hex(32)


def _state_key(nonce: str) -> str:
    return f"{OAUTH_STATE_PREFIX}{nonce}"


def _is_valid_nonce(state: str) -> bool:
    if len(state) != _STATE_NONCE_HEX_LEN:
        return False
    try:
        int(state, 16)
    except ValueError:
        return False
    return True


async def generate_state(
    redis_client: redis.Redis,
    user_id: UUID,
    tenant_id: UUID,
) -> str:
    """Genera nonce CSRF y persiste ``user_id`` + ``tenant_id`` en Redis (TTL 10 min)."""
    nonce = secrets.token_hex(32)
    payload = json.dumps(
        {"user_id": str(user_id), "tenant_id": str(tenant_id)},
        separators=(",", ":"),
    )
    await redis_client.set(
        _state_key(nonce),
        payload,
        ex=OAUTH_STATE_TTL_SECONDS,
    )
    return nonce


async def consume_state(
    redis_client: redis.Redis,
    state: str,
) -> dict[str, str] | None:
    """Lee y elimina el state; ``None`` si expiró, es inválido o ya fue usado."""
    if not _is_valid_nonce(state):
        return None

    key = _state_key(state)
    raw = await redis_client.getdel(key)
    if raw is None:
        return None

    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    user_id = data.get("user_id")
    tenant_id = data.get("tenant_id")
    if not isinstance(user_id, str) or not isinstance(tenant_id, str):
        return None
    if not user_id.strip() or not tenant_id.strip():
        return None

    return {"user_id": user_id, "tenant_id": tenant_id}
