"""Tests de eventos Clerk que sincronizan memberships locales."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest
from app.core.errors import AuthError
from app.core.webhook_ingress import WebhookBodyTooLarge
from app.routes.api import webhooks
from pydantic import SecretStr
from starlette.requests import Request


def _request() -> Request:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/"}, receive)


@asynccontextmanager
async def _session_scope() -> AsyncIterator[object]:
    yield object()


class _VerifiedWebhook:
    event: ClassVar[dict[str, Any]] = {}

    def __init__(self, _secret: str) -> None:
        pass

    def verify(self, _payload: bytes, _headers: dict[str, str]) -> dict[str, Any]:
        return self.event


def _membership_data(role: str = "org:member") -> dict[str, Any]:
    return {
        "organization": {"id": "org_test"},
        "public_user_data": {"user_id": "user_test"},
        "role": role,
    }


def _patch_clerk_common(monkeypatch: pytest.MonkeyPatch, *, claim: bool = True) -> None:
    monkeypatch.setattr(webhooks, "Webhook", _VerifiedWebhook)
    monkeypatch.setattr(webhooks, "session_scope", _session_scope)
    monkeypatch.setattr(webhooks, "claim_webhook_event", AsyncMock(return_value=claim))
    monkeypatch.setattr(
        webhooks,
        "read_request_body_limited",
        AsyncMock(return_value=b"{}"),
    )
    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(
            clerk_webhook_secret=SecretStr("whsec_test"),
            webhook_max_body_bytes=262_144,
        ),
    )


@pytest.mark.asyncio
async def test_membership_created_synchronizes_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _VerifiedWebhook.event = {
        "type": "organizationMembership.created",
        "data": _membership_data("org:member"),
    }
    calls: list[tuple[str, str, str]] = []

    async def sync(
        _db: object,
        clerk_user_id: str,
        clerk_org_id: str,
        role: str,
    ) -> None:
        calls.append((clerk_user_id, clerk_org_id, role))

    _patch_clerk_common(monkeypatch)
    monkeypatch.setattr(webhooks, "sync_clerk_membership", sync)

    result = await webhooks.clerk_webhook(_request(), "id", "timestamp", "signature")

    assert result == {"received": True}
    assert calls == [("user_test", "org_test", "org:member")]


@pytest.mark.asyncio
async def test_membership_updated_synchronizes_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _VerifiedWebhook.event = {
        "type": "organizationMembership.updated",
        "data": _membership_data("org:admin"),
    }
    calls: list[tuple[str, str, str]] = []

    async def sync(
        _db: object,
        clerk_user_id: str,
        clerk_org_id: str,
        role: str,
    ) -> None:
        calls.append((clerk_user_id, clerk_org_id, role))

    _patch_clerk_common(monkeypatch)
    monkeypatch.setattr(webhooks, "sync_clerk_membership", sync)

    result = await webhooks.clerk_webhook(_request(), "id", "timestamp", "signature")

    assert result == {"received": True}
    assert calls == [("user_test", "org_test", "org:admin")]


@pytest.mark.asyncio
async def test_membership_deleted_revokes_access(monkeypatch: pytest.MonkeyPatch) -> None:
    _VerifiedWebhook.event = {
        "type": "organizationMembership.deleted",
        "data": _membership_data(),
    }
    calls: list[tuple[str, str]] = []

    async def revoke(_db: object, clerk_user_id: str, clerk_org_id: str) -> bool:
        calls.append((clerk_user_id, clerk_org_id))
        return True

    _patch_clerk_common(monkeypatch)
    monkeypatch.setattr(webhooks, "revoke_clerk_membership", revoke)

    result = await webhooks.clerk_webhook(_request(), "id", "timestamp", "signature")

    assert result == {"received": True}
    assert calls == [("user_test", "org_test")]


@pytest.mark.asyncio
async def test_clerk_replay_skips_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    _VerifiedWebhook.event = {
        "type": "organizationMembership.updated",
        "data": _membership_data("org:admin"),
    }
    sync = AsyncMock()
    _patch_clerk_common(monkeypatch, claim=False)
    monkeypatch.setattr(webhooks, "sync_clerk_membership", sync)

    result = await webhooks.clerk_webhook(_request(), "svix_replay", "timestamp", "signature")

    assert result == {"received": True}
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_clerk_body_too_large_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        webhooks,
        "get_settings",
        lambda: SimpleNamespace(
            clerk_webhook_secret=SecretStr("whsec_test"),
            webhook_max_body_bytes=10,
        ),
    )
    monkeypatch.setattr(
        webhooks,
        "read_request_body_limited",
        AsyncMock(side_effect=WebhookBodyTooLarge(10)),
    )

    with pytest.raises(AuthError, match="too large"):
        await webhooks.clerk_webhook(_request(), "id", "timestamp", "signature")
