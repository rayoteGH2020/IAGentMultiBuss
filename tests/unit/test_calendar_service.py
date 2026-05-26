"""Tests unitarios del servicio de integración Google Calendar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.crypto import decrypt_token, encrypt_token
from app.models.audit_log import AuditLog
from app.models.calendar_integration import CalendarIntegration, CalendarIntegrationStatus
from app.schemas.calendar import CalendarEvent, TokenResponse
from app.services import calendar_service
from cryptography.fernet import Fernet


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def test_encrypt_decrypt_roundtrip(encryption_key: str) -> None:
    cipher = encrypt_token("secret-access-token", encryption_key)
    assert decrypt_token(cipher, encryption_key) == "secret-access-token"


@pytest.mark.asyncio
async def test_save_integration_creates_encrypted_row(
    encryption_key: str,
) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    tenant_id = uuid4()
    user_id = uuid4()
    captured: list[CalendarIntegration] = []

    def add(row: CalendarIntegration) -> None:
        captured.append(row)

    db.add = add

    with patch(
        "app.services.calendar_service.get_integration",
        AsyncMock(return_value=None),
    ):
        integration = await calendar_service.save_integration(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            token_response=TokenResponse(
                access_token="access-plain",
                refresh_token="refresh-plain",
                expires_in=3600,
                scope="calendar.readonly",
            ),
            google_email="user@example.com",
        )

    assert integration.tenant_id == tenant_id
    assert integration.user_id == user_id
    assert integration.status == CalendarIntegrationStatus.active.value
    assert integration.google_email == "user@example.com"
    assert integration.access_token_enc is not None
    assert integration.refresh_token_enc is not None
    assert decrypt_token(integration.access_token_enc, encryption_key) == "access-plain"
    assert decrypt_token(integration.refresh_token_enc, encryption_key) == "refresh-plain"
    assert len(captured) == 2
    assert isinstance(captured[0], CalendarIntegration)
    assert isinstance(captured[1], AuditLog)
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_get_decrypted_tokens(encryption_key: str) -> None:
    db = AsyncMock()
    row = CalendarIntegration(
        tenant_id=uuid4(),
        user_id=uuid4(),
        access_token_enc=encrypt_token("access", encryption_key),
        refresh_token_enc=encrypt_token("refresh", encryption_key),
    )
    access, refresh = await calendar_service.get_decrypted_tokens(db, row)
    assert access == "access"
    assert refresh == "refresh"


@pytest.mark.asyncio
async def test_ensure_fresh_token_skips_refresh_when_valid(
    encryption_key: str,
) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    row = CalendarIntegration(
        tenant_id=uuid4(),
        user_id=uuid4(),
        access_token_enc=encrypt_token("still-valid", encryption_key),
        refresh_token_enc=encrypt_token("refresh", encryption_key),
        token_expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    mock_client = AsyncMock()

    token = await calendar_service.ensure_fresh_token(db, row, client=mock_client)

    assert token == "still-valid"
    mock_client.refresh_access_token.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_fresh_token_refreshes_when_expiring(
    encryption_key: str,
) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    row = CalendarIntegration(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        access_token_enc=encrypt_token("old-access", encryption_key),
        refresh_token_enc=encrypt_token("refresh", encryption_key),
        token_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    mock_client = AsyncMock()
    mock_client.refresh_access_token.return_value = TokenResponse(
        access_token="new-access",
        expires_in=3600,
    )

    token = await calendar_service.ensure_fresh_token(db, row, client=mock_client)

    assert token == "new-access"
    mock_client.refresh_access_token.assert_awaited_once_with("refresh")
    assert decrypt_token(row.access_token_enc, encryption_key) == "new-access"
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_list_upcoming_events_delegates_to_client(
    encryption_key: str,
) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    row = CalendarIntegration(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        status=CalendarIntegrationStatus.active.value,
        google_calendar_id="primary",
        access_token_enc=encrypt_token("access", encryption_key),
        refresh_token_enc=encrypt_token("refresh", encryption_key),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    mock_client = AsyncMock()
    expected = [
        CalendarEvent(
            id="evt-1",
            summary="Demo",
            start="2026-05-26T10:00:00Z",
            end="2026-05-26T11:00:00Z",
        )
    ]
    mock_client.list_events.return_value = expected

    with patch(
        "app.services.calendar_service.get_integration",
        AsyncMock(return_value=row),
    ):
        events = await calendar_service.list_upcoming_events(
            db,
            row.tenant_id,
            row.user_id,
            days_ahead=7,
            client=mock_client,
        )

    assert events == expected
    mock_client.list_events.assert_awaited_once()
