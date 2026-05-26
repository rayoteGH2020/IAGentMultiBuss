"""Tests unitarios de oauth_state (Redis)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core import oauth_state


class _FakeRedis:
    """Redis mínimo en memoria para probar generate/consume."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        _ = ex
        self.store[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)


@pytest.mark.asyncio
async def test_generate_and_consume_once() -> None:
    redis_conn = _FakeRedis()
    tenant_id = uuid4()
    user_id = uuid4()

    nonce = await oauth_state.generate_state(redis_conn, user_id, tenant_id)

    assert len(nonce) == 64
    ctx = await oauth_state.consume_state(redis_conn, nonce)
    assert ctx == {"user_id": str(user_id), "tenant_id": str(tenant_id)}


@pytest.mark.asyncio
async def test_consume_second_time_returns_none() -> None:
    redis_conn = _FakeRedis()
    nonce = await oauth_state.generate_state(redis_conn, uuid4(), uuid4())

    first = await oauth_state.consume_state(redis_conn, nonce)
    second = await oauth_state.consume_state(redis_conn, nonce)

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_consume_invalid_nonce_returns_none() -> None:
    redis_conn = _FakeRedis()

    assert await oauth_state.consume_state(redis_conn, "") is None
    assert await oauth_state.consume_state(redis_conn, "not-a-valid-nonce") is None
    assert await oauth_state.consume_state(redis_conn, "g" * 64) is None
