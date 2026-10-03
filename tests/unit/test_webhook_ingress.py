"""Limite de body y dedupe Redis SET NX para webhooks (Paso01 §4)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.core.webhook_ingress import (
    WebhookBodyTooLarge,
    channel_job_id,
    claim_webhook_event,
    read_request_body_limited,
)
from starlette.requests import Request


def _request_with_body(body: bytes) -> Request:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/webhooks/test",
            "headers": [],
        },
        receive,
    )


@pytest.mark.asyncio
async def test_read_body_within_limit() -> None:
    body = b'{"ok":true}'
    got = await read_request_body_limited(_request_with_body(body), max_bytes=1024)
    assert got == body


@pytest.mark.asyncio
async def test_read_body_rejects_oversized_without_full_buffer() -> None:
    body = b"x" * 50
    with pytest.raises(WebhookBodyTooLarge) as exc:
        await read_request_body_limited(_request_with_body(body), max_bytes=16)
    assert exc.value.max_bytes == 16


@pytest.mark.asyncio
async def test_claim_first_event_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr("app.core.webhook_ingress.get_redis", lambda: redis)
    monkeypatch.setattr(
        "app.core.webhook_ingress.get_settings",
        lambda: MagicMock(webhook_dedupe_ttl_seconds=3600),
    )

    assert await claim_webhook_event(provider="whatsapp", event_id="wamid.1") is True
    redis.set.assert_awaited_once_with(
        "webhook:dedupe:whatsapp:wamid.1",
        "1",
        nx=True,
        ex=3600,
    )


@pytest.mark.asyncio
async def test_claim_replay_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)
    monkeypatch.setattr("app.core.webhook_ingress.get_redis", lambda: redis)
    monkeypatch.setattr(
        "app.core.webhook_ingress.get_settings",
        lambda: MagicMock(webhook_dedupe_ttl_seconds=3600),
    )

    assert await claim_webhook_event(provider="telegram", event_id="42") is False


@pytest.mark.asyncio
async def test_claim_empty_event_id_allows_process(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = AsyncMock()
    monkeypatch.setattr("app.core.webhook_ingress.get_redis", lambda: redis)

    assert await claim_webhook_event(provider="clerk", event_id="  ") is True
    redis.set.assert_not_awaited()


def test_channel_job_id_deterministic() -> None:
    assert channel_job_id("whatsapp", "wamid.abc") == "channel:whatsapp:wamid.abc"
    assert channel_job_id("telegram", "99") == "channel:telegram:99"
