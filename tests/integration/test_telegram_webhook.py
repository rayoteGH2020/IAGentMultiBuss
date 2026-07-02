"""Tests de integración del webhook Telegram (Paso 21 F.4).

Prueba la cadena HTTP → webhooks_telegram → channel_integration_service (mock) → ARQ (mock).
No se llama al LLM ni a la API de Telegram. El worker es un mock.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.crypto import encrypt_token
from app.main import create_app
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_PLAIN_WEBHOOK_SECRET = "telegram_webhook_secret_for_tests"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def encryption_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inyecta ENCRYPTION_KEY y limpia la caché de settings entre tests."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tg_payload(chat_id: int = 123456789, text: str = "¿Cuál es vuestro horario?") -> bytes:
    return json.dumps({"message": {"chat": {"id": chat_id}, "text": text}}).encode()


def _fake_integration(
    *,
    with_secret: bool = True,
    status: str = "active",
) -> MagicMock:
    integ = MagicMock()
    integ.id = uuid4()
    integ.tenant_id = uuid4()
    integ.status = status
    if with_secret:
        enc_key = get_settings().encryption_key.get_secret_value()
        integ.webhook_secret_enc = encrypt_token(_PLAIN_WEBHOOK_SECRET, enc_key)
    else:
        integ.webhook_secret_enc = None
    return integ


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_webhook_post_valid_secret_enqueues_job(app_client: AsyncClient) -> None:
    """POST con secret válido encola process_channel_message."""
    integration_id = uuid4()
    body = _tg_payload()
    mock_enqueue = AsyncMock(return_value="job_id_tg")

    with (
        patch(
            "app.routes.api.webhooks_telegram.channel_integration_service.get_integration_by_id",
            new=AsyncMock(return_value=_fake_integration(with_secret=True)),
        ),
        patch(
            "app.routes.api.webhooks_telegram.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                f"/api/webhooks/telegram/{integration_id}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": _PLAIN_WEBHOOK_SECRET,
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["channel"] == "telegram"
    assert call_kwargs["customer_identifier"] == "123456789"
    assert call_kwargs["message_text"] == "¿Cuál es vuestro horario?"


async def test_webhook_post_invalid_secret_returns_200_silently(
    app_client: AsyncClient,
) -> None:
    """POST con secret incorrecto devuelve 200 sin encolar (no exponer error a Telegram)."""
    integration_id = uuid4()
    body = _tg_payload()
    mock_enqueue = AsyncMock()

    with (
        patch(
            "app.routes.api.webhooks_telegram.channel_integration_service.get_integration_by_id",
            new=AsyncMock(return_value=_fake_integration(with_secret=True)),
        ),
        patch(
            "app.routes.api.webhooks_telegram.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                f"/api/webhooks/telegram/{integration_id}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": "wrong_secret",
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


async def test_webhook_post_unknown_integration_id_returns_200(
    app_client: AsyncClient,
) -> None:
    """POST con integration_id desconocido devuelve 200 sin encolar."""
    unknown_id = uuid4()
    body = _tg_payload()
    mock_enqueue = AsyncMock()

    with (
        patch(
            "app.routes.api.webhooks_telegram.channel_integration_service.get_integration_by_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.routes.api.webhooks_telegram.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                f"/api/webhooks/telegram/{unknown_id}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": _PLAIN_WEBHOOK_SECRET,
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


async def test_webhook_post_no_text_message_returns_200(app_client: AsyncClient) -> None:
    """Update sin mensaje de texto (p. ej. sticker) devuelve 200 sin encolar."""
    integration_id = uuid4()
    # Payload sin campo "text" → _extract_message devuelve (None, None)
    body = json.dumps({"message": {"chat": {"id": 123}, "sticker": {"file_id": "abc"}}}).encode()
    mock_enqueue = AsyncMock()

    with (
        patch(
            "app.routes.api.webhooks_telegram.channel_integration_service.get_integration_by_id",
            new=AsyncMock(return_value=_fake_integration(with_secret=True)),
        ),
        patch(
            "app.routes.api.webhooks_telegram.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                f"/api/webhooks/telegram/{integration_id}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": _PLAIN_WEBHOOK_SECRET,
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


async def test_webhook_post_production_no_webhook_secret_does_not_enqueue(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En production, integración sin webhook_secret_enc no encola aunque haya texto."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBHOOK_ALLOW_UNSIGNED", "false")
    get_settings.cache_clear()

    integration_id = uuid4()
    body = _tg_payload()
    mock_enqueue = AsyncMock()

    with (
        patch(
            "app.routes.api.webhooks_telegram.channel_integration_service.get_integration_by_id",
            new=AsyncMock(return_value=_fake_integration(with_secret=False)),
        ),
        patch(
            "app.routes.api.webhooks_telegram.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                f"/api/webhooks/telegram/{integration_id}",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


async def test_webhook_post_dev_allow_unsigned_without_secret_enqueues(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En dev con WEBHOOK_ALLOW_UNSIGNED=true se encola sin secret por integración."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WEBHOOK_ALLOW_UNSIGNED", "true")
    get_settings.cache_clear()

    integration_id = uuid4()
    body = _tg_payload()
    mock_enqueue = AsyncMock(return_value="job_id_dev")

    with (
        patch(
            "app.routes.api.webhooks_telegram.channel_integration_service.get_integration_by_id",
            new=AsyncMock(return_value=_fake_integration(with_secret=False)),
        ),
        patch(
            "app.routes.api.webhooks_telegram.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                f"/api/webhooks/telegram/{integration_id}",
                content=body,
                headers={"Content-Type": "application/json"},
            )

    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
