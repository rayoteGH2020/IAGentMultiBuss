"""Webhook WhatsApp Business API (Paso 21 E).

Responsabilidades únicas de este módulo (SRP):
  - Verificar la firma HMAC-SHA256 de Meta en cada POST.
  - Responder HTTP 200 inmediato a Meta (siempre, incluso en error interno).
  - Extraer phone_number_id + texto del mensaje y encolar el job ARQ.

No contiene lógica RAG ni de envío de respuestas; esas responsabilidades
pertenecen a channel_chat_service y channel_jobs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import get_db_no_tenant
from app.jobs.queue import enqueue_channel_message
from app.services import channel_integration_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks/whatsapp", tags=["webhooks-whatsapp"])


# ---------------------------------------------------------------------------
# Verificación GET (Meta Developer Console)
# ---------------------------------------------------------------------------


@router.get("")
async def whatsapp_verify(
    hub_mode: Annotated[str, Query(alias="hub.mode")] = "",
    hub_challenge: Annotated[str, Query(alias="hub.challenge")] = "",
    hub_verify_token: Annotated[str, Query(alias="hub.verify_token")] = "",
) -> PlainTextResponse:
    """Endpoint de verificación que Meta llama al registrar el webhook."""
    settings = get_settings()
    expected = settings.whatsapp_verify_token.get_secret_value()
    if hub_mode == "subscribe" and hub_verify_token == expected and expected:
        return PlainTextResponse(hub_challenge)
    logger.warning(
        "whatsapp.verify.failed",
        extra={"hub_mode": hub_mode, "token_match": hub_verify_token == expected},
    )
    return PlainTextResponse("Forbidden", status_code=403)


# ---------------------------------------------------------------------------
# Webhook POST (mensajes entrantes)
# ---------------------------------------------------------------------------


def _verify_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    """Valida X-Hub-Signature-256: sha256=<hmac> con HMAC-SHA256(body, app_secret)."""
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)


def _extract_message(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Extrae (phone_number_id, from_number, text) del payload de Meta.

    Devuelve (None, None, None) si no contiene un mensaje de texto.
    Meta puede enviar status updates u otros eventos que se ignoran silenciosamente.
    """
    try:
        entry = payload.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0].get("value", {})
        messages = change.get("messages", [])
        if not messages:
            return None, None, None
        msg = messages[0]
        if msg.get("type") != "text":
            return None, None, None
        phone_number_id = change.get("metadata", {}).get("phone_number_id")
        from_number = msg.get("from")
        text = msg.get("text", {}).get("body", "").strip()
        if not text:
            return None, None, None
        return phone_number_id, from_number, text
    except (KeyError, IndexError, TypeError):
        return None, None, None


@router.post("")
async def whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db_no_tenant),
    x_hub_signature_256: Annotated[str, Header(alias="X-Hub-Signature-256")] = "",
) -> Response:
    """Recibe eventos de mensajería de Meta. Siempre responde HTTP 200 salvo misconfig prod."""
    body = await request.body()
    settings = get_settings()
    app_secret = settings.whatsapp_app_secret.get_secret_value().strip()

    if app_secret:
        if not _verify_signature(body, x_hub_signature_256, app_secret):
            logger.warning("whatsapp.webhook.invalid_signature")
            return Response(status_code=200)  # No exponer el fallo a Meta
    elif settings.allows_unsigned_webhooks:
        logger.warning(
            "whatsapp.webhook.unsigned_allowed",
            extra={"app_env": settings.app_env},
        )
    else:
        logger.critical(
            "whatsapp.webhook.no_app_secret",
            extra={"app_env": settings.app_env},
        )
        return Response(status_code=503)

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("whatsapp.webhook.invalid_json")
        return Response(status_code=200)

    phone_number_id, from_number, text = _extract_message(payload)
    if not phone_number_id or not from_number or not text:
        return Response(status_code=200)  # Status update u otro evento — ignorar

    try:
        integration = await channel_integration_service.get_integration_by_phone_number_id(
            db, phone_number_id
        )
        if integration is None:
            logger.warning(
                "whatsapp.webhook.unknown_phone_number_id",
                extra={"phone_number_id": phone_number_id},
            )
            return Response(status_code=200)

        tenant_id = str(integration.tenant_id)
        integration_id = str(integration.id)
    except Exception:
        logger.exception("whatsapp.webhook.lookup_failed")
        return Response(status_code=200)

    try:
        await enqueue_channel_message(
            tenant_id=tenant_id,
            channel="whatsapp",
            customer_identifier=from_number,
            message_text=text,
            integration_id=integration_id,
        )
    except Exception:
        logger.exception("whatsapp.webhook.enqueue_failed")

    return Response(status_code=200)
