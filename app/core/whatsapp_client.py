"""Cliente HTTP para la WhatsApp Business API (Paso 21 E)."""

from __future__ import annotations

import hashlib
import hmac

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


async def send_text_message(
    *,
    to: str,
    text: str,
    phone_number_id: str,
    api_token: str,
) -> None:
    """Envía un mensaje de texto al número WhatsApp indicado.

    Trunca el texto a whatsapp_max_response_chars si es necesario.
    Lanza httpx.HTTPStatusError en caso de respuesta 4xx/5xx de Meta.
    """
    settings = get_settings()
    truncated = text[: settings.whatsapp_max_response_chars]
    url = f"{settings.whatsapp_api_url}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": truncated},
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_token}"},
        )
        if not resp.is_success:
            logger.warning(
                "whatsapp.send_failed",
                status=resp.status_code,
                to=to,
                phone_number_id=phone_number_id,
            )
        resp.raise_for_status()
    logger.info("whatsapp.message_sent", to=to, chars=len(truncated))


def verify_webhook_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    """Valida X-Hub-Signature-256: sha256=<hmac> con HMAC-SHA256(body, app_secret)."""
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)
