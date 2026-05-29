"""Tests unitarios del cliente WhatsApp (Paso 21 E.7)."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config import get_settings
from app.core.whatsapp_client import send_text_message, verify_webhook_signature

# ---------------------------------------------------------------------------
# verify_webhook_signature — función pura, sin I/O
# ---------------------------------------------------------------------------


def test_verify_signature_valid() -> None:
    secret = "my_app_secret"
    body = b'{"entry":[]}'
    expected_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    header = f"sha256={expected_hex}"
    assert verify_webhook_signature(body, header, secret) is True


def test_verify_signature_invalid_wrong_hmac() -> None:
    secret = "my_app_secret"
    body = b'{"entry":[]}'
    assert verify_webhook_signature(body, "sha256=deadbeef", secret) is False


def test_verify_signature_missing_prefix() -> None:
    secret = "my_app_secret"
    body = b'{"entry":[]}'
    # Sin el prefijo "sha256="
    hex_only = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, hex_only, secret) is False


def test_verify_signature_empty_header() -> None:
    assert verify_webhook_signature(b"body", "", "secret") is False


# ---------------------------------------------------------------------------
# send_text_message — truncado y llamada HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_truncates_long_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """El texto se trunca a whatsapp_max_response_chars antes de enviar."""
    monkeypatch.setenv("WHATSAPP_MAX_RESPONSE_CHARS", "10")
    get_settings.cache_clear()

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.raise_for_status = MagicMock()

    mock_post = AsyncMock(return_value=mock_resp)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = mock_post

    with patch("app.core.whatsapp_client.httpx.AsyncClient", return_value=mock_client):
        await send_text_message(
            to="34600000001",
            text="A" * 50,  # más largo que el límite
            phone_number_id="111",
            api_token="fake_token",
        )

    call_kwargs = mock_post.call_args
    sent_body: str = call_kwargs.kwargs["json"]["text"]["body"]
    max_chars = get_settings().whatsapp_max_response_chars
    assert len(sent_body) <= max_chars

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_message_sends_to_correct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """La URL construida usa phone_number_id y whatsapp_api_url."""
    get_settings.cache_clear()

    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.raise_for_status = MagicMock()

    mock_post = AsyncMock(return_value=mock_resp)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = mock_post

    with patch("app.core.whatsapp_client.httpx.AsyncClient", return_value=mock_client):
        await send_text_message(
            to="34600000002",
            text="Hola",
            phone_number_id="MY_PHONE_ID",
            api_token="MY_TOKEN",
        )

    call_url: str = mock_post.call_args.args[0]
    assert "MY_PHONE_ID" in call_url
    assert "messages" in call_url
    auth_header: str = mock_post.call_args.kwargs["headers"]["Authorization"]
    assert auth_header == "Bearer MY_TOKEN"
