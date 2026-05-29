"""Tests unitarios del cliente Telegram (Paso 21 F.4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.telegram_client import (
    delete_webhook,
    send_message,
    set_webhook,
    verify_webhook_secret,
)

# ---------------------------------------------------------------------------
# verify_webhook_secret — función pura
# ---------------------------------------------------------------------------


def test_verify_webhook_secret_valid() -> None:
    assert verify_webhook_secret("my_secret_token", "my_secret_token") is True


def test_verify_webhook_secret_invalid() -> None:
    assert verify_webhook_secret("wrong_token", "correct_token") is False


def test_verify_webhook_secret_empty_header() -> None:
    assert verify_webhook_secret("", "some_secret") is False


def test_verify_webhook_secret_empty_expected() -> None:
    assert verify_webhook_secret("some_header", "") is False


# ---------------------------------------------------------------------------
# Helpers para mockear httpx
# ---------------------------------------------------------------------------


def _mock_http_client(*, ok: bool = True) -> tuple[AsyncMock, MagicMock]:
    """Devuelve (mock_client, mock_post) para parchear httpx.AsyncClient."""
    mock_resp = MagicMock()
    mock_resp.is_success = ok
    mock_resp.json = MagicMock(return_value={"ok": ok, "description": "OK" if ok else "Error"})
    mock_resp.raise_for_status = MagicMock()

    mock_post = AsyncMock(return_value=mock_resp)
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = mock_post
    return mock_client, mock_post


# ---------------------------------------------------------------------------
# set_webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_webhook_posts_to_correct_url() -> None:
    """set_webhook hace POST a /bot{token}/setWebhook con la URL correcta."""
    mock_client, mock_post = _mock_http_client()

    with patch("app.core.telegram_client.httpx.AsyncClient", return_value=mock_client):
        await set_webhook("BOT_TOKEN_123", "https://example.com/webhook")

    call_url: str = mock_post.call_args.args[0]
    assert "BOT_TOKEN_123" in call_url
    assert "setWebhook" in call_url

    payload: dict[str, object] = mock_post.call_args.kwargs["json"]
    assert payload["url"] == "https://example.com/webhook"


@pytest.mark.asyncio
async def test_set_webhook_includes_secret_token_when_provided() -> None:
    """set_webhook incluye secret_token en el payload cuando se proporciona."""
    mock_client, mock_post = _mock_http_client()

    with patch("app.core.telegram_client.httpx.AsyncClient", return_value=mock_client):
        await set_webhook("TOKEN", "https://example.com/wh", webhook_secret="abc123")

    payload: dict[str, object] = mock_post.call_args.kwargs["json"]
    assert payload["secret_token"] == "abc123"


@pytest.mark.asyncio
async def test_set_webhook_omits_secret_when_not_provided() -> None:
    """set_webhook NO incluye secret_token si no se pasa."""
    mock_client, mock_post = _mock_http_client()

    with patch("app.core.telegram_client.httpx.AsyncClient", return_value=mock_client):
        await set_webhook("TOKEN", "https://example.com/wh")

    payload: dict[str, object] = mock_post.call_args.kwargs["json"]
    assert "secret_token" not in payload


# ---------------------------------------------------------------------------
# delete_webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_webhook_posts_to_correct_url() -> None:
    """delete_webhook hace POST a /bot{token}/deleteWebhook."""
    mock_client, mock_post = _mock_http_client()

    with patch("app.core.telegram_client.httpx.AsyncClient", return_value=mock_client):
        await delete_webhook("MY_BOT_TOKEN")

    call_url: str = mock_post.call_args.args[0]
    assert "MY_BOT_TOKEN" in call_url
    assert "deleteWebhook" in call_url


# ---------------------------------------------------------------------------
# send_message — truncado a 4096 chars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_truncates_at_4096() -> None:
    """send_message trunca el texto a 4096 caracteres (límite de Telegram)."""
    mock_client, mock_post = _mock_http_client()

    long_text = "A" * 5000  # claramente más largo que el límite

    with patch("app.core.telegram_client.httpx.AsyncClient", return_value=mock_client):
        await send_message("TOKEN", chat_id=123456, text=long_text)

    payload: dict[str, object] = mock_post.call_args.kwargs["json"]
    assert len(payload["text"]) == 4096


@pytest.mark.asyncio
async def test_send_message_does_not_truncate_short_text() -> None:
    """send_message no modifica texto corto."""
    mock_client, mock_post = _mock_http_client()
    short_text = "Hola, ¿en qué puedo ayudarte?"

    with patch("app.core.telegram_client.httpx.AsyncClient", return_value=mock_client):
        await send_message("TOKEN", chat_id="987654321", text=short_text)

    payload: dict[str, object] = mock_post.call_args.kwargs["json"]
    assert payload["text"] == short_text
    assert payload["chat_id"] == "987654321"
