"""Tests for onboarding missing-org notification."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.errors import ExternalServiceError, RateLimitError, ValidationError
from app.services import onboarding_notify_service


@pytest.mark.asyncio
async def test_notify_missing_org_sends_expected_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.get_settings",
        lambda: MagicMock(
            email_sadm="sadm@example.com",
            smtp_host="smtp.example.com",
        ),
    )
    send = AsyncMock()
    monkeypatch.setattr("app.services.onboarding_notify_service.send_email", send)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    user_id = uuid4()
    result = await onboarding_notify_service.notify_superadmin_missing_organization(
        user_email="user@acme.test",
        user_id=user_id,
        redis=redis,
    )

    assert result.mode == "smtp"
    assert result.mailto_url is None
    send.assert_awaited_once_with(
        to="sadm@example.com",
        subject="Falta organización en Clerk",
        body="El email user@acme.test no tiene organización asignada en Clerk",
    )
    redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_missing_org_mailto_fallback_without_smtp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.get_settings",
        lambda: MagicMock(email_sadm="sadm@example.com", smtp_host=""),
    )
    send = AsyncMock()
    monkeypatch.setattr("app.services.onboarding_notify_service.send_email", send)
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    result = await onboarding_notify_service.notify_superadmin_missing_organization(
        user_email="user@acme.test",
        user_id=uuid4(),
        redis=redis,
    )

    assert result.mode == "mailto"
    assert result.mailto_url is not None
    assert result.mailto_url.startswith("mailto:sadm@example.com?")
    assert "Falta%20organizaci" in result.mailto_url
    assert "user%40acme.test" in result.mailto_url
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_missing_org_requires_email_sadm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.get_settings",
        lambda: MagicMock(email_sadm="  ", smtp_host="smtp.example.com"),
    )
    with pytest.raises(ValidationError) as exc_info:
        await onboarding_notify_service.notify_superadmin_missing_organization(
            user_email="user@acme.test",
            user_id=uuid4(),
            redis=None,
        )
    assert exc_info.value.details.get("code") == "email_sadm_missing"


@pytest.mark.asyncio
async def test_notify_missing_org_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.get_settings",
        lambda: MagicMock(email_sadm="sadm@example.com", smtp_host="smtp.example.com"),
    )
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.send_email",
        AsyncMock(),
    )
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)

    with pytest.raises(RateLimitError):
        await onboarding_notify_service.notify_superadmin_missing_organization(
            user_email="user@acme.test",
            user_id=uuid4(),
            redis=redis,
        )


@pytest.mark.asyncio
async def test_notify_missing_org_smtp_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.get_settings",
        lambda: MagicMock(email_sadm="sadm@example.com", smtp_host="smtp.example.com"),
    )
    monkeypatch.setattr(
        "app.services.onboarding_notify_service.send_email",
        AsyncMock(side_effect=RuntimeError("smtp down")),
    )
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock()

    with pytest.raises(ExternalServiceError) as exc_info:
        await onboarding_notify_service.notify_superadmin_missing_organization(
            user_email="user@acme.test",
            user_id=uuid4(),
            redis=redis,
        )
    assert exc_info.value.details.get("code") == "missing_org_notify_send_failed"
    redis.delete.assert_awaited_once()
