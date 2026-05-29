"""Tests de integración del webhook WhatsApp (Paso 21 E.7).

Prueba la cadena HTTP → webhooks_whatsapp → channel_integration_service (mock) → ARQ (mock).
No se llama al LLM ni a la API de WhatsApp. El worker es un mock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.config import get_settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_VERIFY_TOKEN = "test_verify_token_e7"
_APP_SECRET = "test_app_secret_e7"  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def wa_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inyecta tokens de WhatsApp y limpia el cache de settings entre tests."""
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", _VERIFY_TOKEN)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", _APP_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = _APP_SECRET) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _wa_payload(phone_number_id: str = "123456789", message_text: str = "Hola") -> bytes:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "type": "text",
                                    "from": "34600000001",
                                    "text": {"body": message_text},
                                }
                            ],
                            "metadata": {"phone_number_id": phone_number_id},
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode()


def _fake_integration(tenant_id_str: str) -> MagicMock:
    integ = MagicMock()
    integ.tenant_id = uuid4()
    integ.id = uuid4()
    return integ


# ---------------------------------------------------------------------------
# Tests: GET verificación Meta
# ---------------------------------------------------------------------------


async def test_webhook_get_verification_ok(app_client: AsyncClient) -> None:
    """GET con token correcto devuelve el hub.challenge en el cuerpo."""
    async with app_client as client:
        resp = await client.get(
            "/api/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_xyz_123",
                "hub.verify_token": _VERIFY_TOKEN,
            },
        )

    assert resp.status_code == 200
    assert resp.text == "challenge_xyz_123"


async def test_webhook_get_verification_wrong_token(app_client: AsyncClient) -> None:
    """GET con token incorrecto devuelve 403."""
    async with app_client as client:
        resp = await client.get(
            "/api/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_xyz",
                "hub.verify_token": "wrong_token",
            },
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: POST mensajes entrantes
# ---------------------------------------------------------------------------


async def test_webhook_post_enqueues_job(app_client: AsyncClient) -> None:
    """POST con firma válida y phone_number_id conocido encola un job ARQ."""
    phone_number_id = "999111222"
    body = _wa_payload(phone_number_id=phone_number_id)

    mock_enqueue = AsyncMock(return_value="job_id_abc")
    fake_integration = _fake_integration("tenant_uuid")

    with (
        patch(
            "app.routes.api.webhooks_whatsapp.channel_integration_service"
            ".get_integration_by_phone_number_id",
            new=AsyncMock(return_value=fake_integration),
        ),
        patch(
            "app.routes.api.webhooks_whatsapp.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                "/api/webhooks/whatsapp",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign(body),
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_awaited_once()
    call_kwargs = mock_enqueue.call_args.kwargs
    assert call_kwargs["channel"] == "whatsapp"
    assert call_kwargs["customer_identifier"] == "34600000001"
    assert call_kwargs["message_text"] == "Hola"


async def test_webhook_post_invalid_signature_returns_200_silently(
    app_client: AsyncClient,
) -> None:
    """POST con firma HMAC inválida devuelve 200 (no se expone el error a Meta)."""
    body = _wa_payload()
    mock_enqueue = AsyncMock()

    with patch(
        "app.routes.api.webhooks_whatsapp.enqueue_channel_message",
        new=mock_enqueue,
    ):
        async with app_client as client:
            resp = await client.post(
                "/api/webhooks/whatsapp",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": "sha256=badfirmabadbadbad",
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()


async def test_webhook_post_unknown_phone_number_id_returns_200(
    app_client: AsyncClient,
) -> None:
    """POST con phone_number_id no registrado devuelve 200 sin encolar job."""
    body = _wa_payload(phone_number_id="UNKNOWN_ID")
    mock_enqueue = AsyncMock()

    with (
        patch(
            "app.routes.api.webhooks_whatsapp.channel_integration_service"
            ".get_integration_by_phone_number_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.routes.api.webhooks_whatsapp.enqueue_channel_message",
            new=mock_enqueue,
        ),
    ):
        async with app_client as client:
            resp = await client.post(
                "/api/webhooks/whatsapp",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": _sign(body),
                },
            )

    assert resp.status_code == 200
    mock_enqueue.assert_not_awaited()
