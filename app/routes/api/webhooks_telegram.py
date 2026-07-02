"""Webhook Telegram Bot API (Paso 21 F).

Responsabilidades únicas de este módulo (SRP):
  - Verificar X-Telegram-Bot-Api-Secret-Token en cada POST.
  - Responder HTTP 200 inmediato a Telegram (siempre, incluso en error interno).
  - Extraer chat_id + texto del mensaje y encolar el job ARQ.

El integration_id en la URL identifica al tenant sin necesitar JWT ni sesión:
cada bot de Telegram apunta a una URL única por integración.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.crypto import decrypt_token
from app.core.telegram_client import verify_webhook_secret
from app.deps import get_db_no_tenant
from app.jobs.queue import enqueue_channel_message
from app.services import channel_integration_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/webhooks/telegram", tags=["webhooks-telegram"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_message(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extrae (customer_identifier, text) del Update de Telegram.

    Devuelve (None, None) si el update no contiene un mensaje de texto.
    Telegram puede enviar edited_message, inline_query, etc., que se ignoran.
    """
    try:
        msg = payload.get("message", {})
        if not msg:
            return None, None
        text = msg.get("text", "").strip()
        if not text:
            return None, None
        chat_id = str(msg["chat"]["id"])
        return chat_id, text
    except (KeyError, TypeError):
        return None, None


# ---------------------------------------------------------------------------
# Webhook POST
# ---------------------------------------------------------------------------


@router.post("/{integration_id}")
async def telegram_webhook(
    integration_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_no_tenant),
    x_telegram_bot_api_secret_token: Annotated[
        str, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = "",
) -> Response:
    """Recibe updates de Telegram. Siempre responde HTTP 200."""
    body = await request.body()

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("telegram.webhook.invalid_json")
        return Response(status_code=200)

    try:
        integration = await channel_integration_service.get_integration_by_id(db, integration_id)
        if integration is None or integration.status != "active":
            logger.warning(
                "telegram.webhook.unknown_integration",
                integration_id=str(integration_id),
            )
            return Response(status_code=200)

        # Verificar webhook secret (X-Telegram-Bot-Api-Secret-Token)
        if integration.webhook_secret_enc:
            enc_key = get_settings().encryption_key.get_secret_value()
            expected_secret = decrypt_token(integration.webhook_secret_enc, enc_key)
            if not verify_webhook_secret(x_telegram_bot_api_secret_token, expected_secret):
                logger.warning(
                    "telegram.webhook.invalid_secret",
                    integration_id=str(integration_id),
                )
                return Response(status_code=200)  # No exponer el fallo a Telegram

        tenant_id = str(integration.tenant_id)
        integration_id_str = str(integration_id)
    except Exception:
        logger.exception("telegram.webhook.lookup_failed")
        return Response(status_code=200)

    customer_identifier, text = _extract_message(payload)
    if not customer_identifier or not text:
        return Response(status_code=200)

    try:
        await enqueue_channel_message(
            tenant_id=tenant_id,
            channel="telegram",
            customer_identifier=customer_identifier,
            message_text=text,
            integration_id=integration_id_str,
        )
    except Exception:
        logger.exception("telegram.webhook.enqueue_failed")

    return Response(status_code=200)
