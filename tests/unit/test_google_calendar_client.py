"""Tests unitarios del cliente Google Calendar (httpx mock)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.config import Settings
from app.core.errors import AuthError, ForbiddenError, ValidationError
from app.core.google_calendar_client import GoogleCalendarClient, build_auth_url
from app.schemas.calendar import CalendarEventCreate, CalendarEventUpdate


def _settings(**overrides: object) -> Settings:
    base: dict[str, Any] = {
        "app_secret_key": "test-secret-key-for-unit-tests-only",  # pragma: allowlist secret
        "database_url": "postgresql+asyncpg://saas:saas@localhost:5432/saas",  # pragma: allowlist secret
        "redis_url": "redis://localhost:6379/0",
        "google_oauth_client_id": "test-client-id",
        "google_oauth_client_secret": "test-client-secret",  # pragma: allowlist secret
    }
    base.update(overrides)
    return Settings(**base)


def test_build_auth_url_includes_offline_and_consent() -> None:
    url = build_auth_url(
        client_id="cid",
        redirect_uri="http://localhost/cb",
        state="nonce",
        scopes="https://www.googleapis.com/auth/calendar.readonly",
    )
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=nonce" in url


@pytest.mark.asyncio
async def test_exchange_code_parses_token_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://oauth2.googleapis.com/token"
        return httpx.Response(
            200,
            json={
                "access_token": "access-123",
                "refresh_token": "refresh-456",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "calendar.readonly",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        token = await client.exchange_code("auth-code", "http://localhost/callback")

    assert token.access_token == "access-123"
    assert token.refresh_token == "refresh-456"
    assert token.expires_in == 3600


@pytest.mark.asyncio
async def test_exchange_code_401_raises_auth_error() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(401, json={"error": "invalid"}))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        with pytest.raises(AuthError, match="google_token_expired"):
            await client.exchange_code("bad-code", "http://localhost/callback")


@pytest.mark.asyncio
async def test_list_events_403_raises_forbidden() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(403, text="forbidden"))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        with pytest.raises(ForbiddenError):
            await client.list_events("token", "primary")


@pytest.mark.asyncio
async def test_list_events_parses_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "calendars/primary/events" in str(request.url)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "evt1",
                        "summary": "Reunión",
                        "start": {"dateTime": "2026-05-26T10:00:00+02:00"},
                        "end": {"dateTime": "2026-05-26T11:00:00+02:00"},
                        "htmlLink": "https://calendar.google.com/event?eid=1",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        events = await client.list_events("token", "primary", max_results=10)

    assert len(events) == 1
    assert events[0].id == "evt1"
    assert events[0].summary == "Reunión"


@pytest.mark.asyncio
async def test_create_event_posts_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "new-evt",
                "summary": "Cita",
                "start": {"dateTime": "2026-05-27T09:00:00+02:00"},
                "end": {"dateTime": "2026-05-27T10:00:00+02:00"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        event = await client.create_event(
            "token",
            "primary",
            CalendarEventCreate(
                summary="Cita",
                start="2026-05-27T09:00:00+02:00",
                end="2026-05-27T10:00:00+02:00",
            ),
        )

    assert captured["method"] == "POST"
    assert captured["json"]["summary"] == "Cita"
    assert event.id == "new-evt"


@pytest.mark.asyncio
async def test_update_event_patches_body() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "evt-1",
                "summary": "Actualizado",
                "start": {"dateTime": "2026-05-27T11:00:00+02:00"},
                "end": {"dateTime": "2026-05-27T12:00:00+02:00"},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        event = await client.update_event(
            "token",
            "primary",
            "evt-1",
            CalendarEventUpdate(
                summary="Actualizado",
                start="2026-05-27T11:00:00+02:00",
                end="2026-05-27T12:00:00+02:00",
            ),
        )

    assert captured["method"] == "PATCH"
    assert captured["json"]["summary"] == "Actualizado"
    assert event.summary == "Actualizado"


@pytest.mark.asyncio
async def test_refresh_access_token() -> None:
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        token = await client.refresh_access_token("refresh-old")

    assert token.access_token == "new-access"


@pytest.mark.asyncio
async def test_revoke_token_success() -> None:
    transport = httpx.MockTransport(lambda _req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        await client.revoke_token("some-token")


@pytest.mark.asyncio
async def test_get_user_email() -> None:
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, json={"email": "user@example.com"})
    )
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GoogleCalendarClient(_settings(), http_client=http_client)
        email = await client.get_user_email("access-token")

    assert email == "user@example.com"


@pytest.mark.asyncio
async def test_missing_oauth_config_raises_validation_error() -> None:
    async with httpx.AsyncClient() as http_client:
        client = GoogleCalendarClient(
            _settings(google_oauth_client_id=""),
            http_client=http_client,
        )
        with pytest.raises(ValidationError, match="Google OAuth is not configured"):
            await client.exchange_code("code", "http://localhost/callback")
