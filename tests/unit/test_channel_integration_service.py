"""Tests unitarios de channel_integration_service (Paso 21 C + WA PNID único)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.errors import ValidationError
from app.models.channel_integration import ChannelIntegration
from app.schemas.channel import ChannelIntegrationStatus
from app.services import channel_integration_service
from cryptography.fernet import Fernet


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_save_whatsapp_integration_requires_app_secret_in_production(
    encryption_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "")
    get_settings.cache_clear()

    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = lambda row: None

    with (
        patch(
            "app.services.channel_integration_service.get_integration",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ValidationError, match="WHATSAPP_APP_SECRET"),
    ):
        await channel_integration_service.save_integration(
            db,
            tenant_id=uuid4(),
            channel="whatsapp",
            api_token="token",
            phone_number_id="123",
        )


@pytest.mark.asyncio
async def test_save_whatsapp_requires_phone_number_id(encryption_key: str) -> None:
    db = AsyncMock()
    with pytest.raises(ValidationError, match="Phone Number ID"):
        await channel_integration_service.save_integration(
            db,
            tenant_id=uuid4(),
            channel="whatsapp",
            api_token="token",
            phone_number_id="   ",
        )


@pytest.mark.asyncio
async def test_save_whatsapp_rejects_phone_number_id_in_use(
    encryption_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()

    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())

    existing = ChannelIntegration(tenant_id=uuid4(), channel="whatsapp")
    existing.id = uuid4()

    with (
        patch(
            "app.services.channel_integration_service.get_integration",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.channel_integration_service._assert_whatsapp_phone_number_id_available",
            AsyncMock(
                side_effect=ValidationError(
                    "Este Phone Number ID de WhatsApp ya está en uso por otra organización."
                )
            ),
        ),
        pytest.raises(ValidationError, match="ya está en uso"),
    ):
        await channel_integration_service.save_integration(
            db,
            tenant_id=uuid4(),
            channel="whatsapp",
            api_token="token",
            phone_number_id="dup-phone-id",
        )


@pytest.mark.asyncio
async def test_assert_phone_number_id_available_raises_on_conflict() -> None:
    conflict_result = MagicMock()
    conflict_result.first.return_value = SimpleNamespace(tenant_id=uuid4())
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[AsyncMock(), conflict_result])
    with pytest.raises(ValidationError, match="ya está en uso"):
        await channel_integration_service._assert_whatsapp_phone_number_id_available(
            db,
            phone_number_id="shared-pnid",
            exclude_integration_id=None,
        )


@pytest.mark.asyncio
async def test_get_integration_by_phone_number_id_fail_closed_on_ambiguity() -> None:
    row_a = ChannelIntegration(tenant_id=uuid4(), channel="whatsapp")
    row_a.id = uuid4()
    row_a.phone_number_id = "ambiguous"
    row_a.status = ChannelIntegrationStatus.active.value
    row_b = ChannelIntegration(tenant_id=uuid4(), channel="whatsapp")
    row_b.id = uuid4()
    row_b.phone_number_id = "ambiguous"
    row_b.status = ChannelIntegrationStatus.active.value

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [row_a, row_b]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[AsyncMock(), result_mock])

    found = await channel_integration_service.get_integration_by_phone_number_id(db, "ambiguous")
    assert found is None


@pytest.mark.asyncio
async def test_get_integration_by_phone_number_id_returns_single_match() -> None:
    row = ChannelIntegration(tenant_id=uuid4(), channel="whatsapp")
    row.id = uuid4()
    row.phone_number_id = "unique-pnid"
    row.status = ChannelIntegrationStatus.active.value

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [row]

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[AsyncMock(), result_mock])

    found = await channel_integration_service.get_integration_by_phone_number_id(db, "unique-pnid")
    assert found is row
