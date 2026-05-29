"""Cliente HTTP para la Telegram Bot API (Paso 21 F)."""

from __future__ import annotations

import hmac

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)

_MAX_MESSAGE_CHARS = 4096


def _bot_url(bot_token: str, method: str) -> str:
    base = get_settings().telegram_api_url.rstrip("/")
    return f"{base}/bot{bot_token}/{method}"


async def set_webhook(
    bot_token: str,
    webhook_url: str,
    *,
    webhook_secret: str | None = None,
) -> None:
    """Registra webhook_url en la Telegram Bot API para el bot dado.

    Si se proporciona webhook_secret, Telegram lo incluirá en cada POST como
    X-Telegram-Bot-Api-Secret-Token para verificación en nuestro endpoint.
    """
    payload: dict[str, object] = {"url": webhook_url}
    if webhook_secret:
        payload["secret_token"] = webhook_secret

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(_bot_url(bot_token, "setWebhook"), json=payload)
        data = resp.json()
        if not resp.is_success or not data.get("ok"):
            logger.warning(
                "telegram.set_webhook.failed",
                status=resp.status_code,
                description=data.get("description"),
            )
            resp.raise_for_status()
    logger.info("telegram.webhook_registered", webhook_url=webhook_url)


async def delete_webhook(bot_token: str) -> None:
    """Desregistra el webhook del bot en Telegram."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(_bot_url(bot_token, "deleteWebhook"))
        data = resp.json()
        if not resp.is_success or not data.get("ok"):
            logger.warning(
                "telegram.delete_webhook.failed",
                status=resp.status_code,
                description=data.get("description"),
            )
            resp.raise_for_status()
    logger.info("telegram.webhook_deleted")


async def send_message(bot_token: str, chat_id: int | str, text: str) -> None:
    """Envía un mensaje de texto al chat indicado.

    Trunca el texto a 4096 chars (límite de Telegram).
    Lanza httpx.HTTPStatusError en caso de respuesta de error de la API.
    """
    truncated = text[:_MAX_MESSAGE_CHARS]
    payload: dict[str, object] = {"chat_id": chat_id, "text": truncated}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(_bot_url(bot_token, "sendMessage"), json=payload)
        if not resp.is_success:
            data = resp.json()
            logger.warning(
                "telegram.send_failed",
                status=resp.status_code,
                chat_id=str(chat_id),
                description=data.get("description"),
            )
        resp.raise_for_status()
    logger.info("telegram.message_sent", chat_id=str(chat_id), chars=len(truncated))


def verify_webhook_secret(token_header: str, expected_secret: str) -> bool:
    """Compara X-Telegram-Bot-Api-Secret-Token con el secreto almacenado."""
    if not token_header or not expected_secret:
        return False
    return hmac.compare_digest(token_header, expected_secret)
