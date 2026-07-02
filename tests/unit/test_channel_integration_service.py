"""Tests unitarios de channel_integration_service (Paso 21 C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.errors import ValidationError
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
