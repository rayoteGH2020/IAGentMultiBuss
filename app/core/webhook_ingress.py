"""Ingress seguro para webhooks externos: limite de body y dedupe anti-replay.

Orden obligatorio (Paso01 §4 / Seguridad_V2):
1. Leer el body con tope de bytes (sin parsear JSON todavia).
2. Validar firma sobre bytes crudos.
3. Parsear JSON solo si el body cabe y la firma es valida.
4. Claim Redis ``SET key NX EX ttl`` con el id del proveedor.
5. Encolar jobs con ``_job_id`` determinista cuando exista id estable.

Proveedores: ``whatsapp``, ``telegram``, ``clerk``. Stripe futuro: ``stripe``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from starlette.requests import Request

from app.config import get_settings
from app.core.cache import get_redis

log = structlog.get_logger(__name__)

PROVIDER_WHATSAPP: Final = "whatsapp"
PROVIDER_TELEGRAM: Final = "telegram"
PROVIDER_CLERK: Final = "clerk"
PROVIDER_STRIPE: Final = "stripe"  # reserva Paso09


class WebhookBodyTooLarge(Exception):
    """El cuerpo supera el maximo configurado; no se debe parsear ni loguear."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"Webhook body exceeds {max_bytes} bytes")


async def read_request_body_limited(request: Request, max_bytes: int) -> bytes:
    """Lee el body hasta ``max_bytes`` inclusive; si se supera, aborta sin parsear.

    Usa el stream ASGI para no materializar un payload enorme en memoria de golpe
    mas alla del tope. No loguea el contenido.
    """
    if max_bytes <= 0:
        raise WebhookBodyTooLarge(max_bytes)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            log.warning(
                "webhook.body_too_large",
                max_bytes=max_bytes,
                observed_gt=max_bytes,
            )
            raise WebhookBodyTooLarge(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


async def claim_webhook_event(
    *,
    provider: str,
    event_id: str,
    ttl_seconds: int | None = None,
) -> bool:
    """Intenta reclamar un evento para procesamiento unico.

    Returns:
        True si este proceso debe procesar el evento (primera vez en la ventana TTL).
        False si es un replay (clave ya existia); el caller debe responder
        idempotente sin side effects.
    """
    cleaned = event_id.strip()
    if not cleaned:
        log.warning("webhook.dedupe_missing_event_id", provider=provider)
        # Sin id estable no hay dedupe fiable: se procesa una vez y se deja rastro.
        return True

    settings = get_settings()
    ttl = ttl_seconds if ttl_seconds is not None else settings.webhook_dedupe_ttl_seconds
    key = f"webhook:dedupe:{provider}:{cleaned}"
    redis = get_redis()
    created = await redis.set(key, "1", nx=True, ex=ttl)
    if created:
        return True
    log.info("webhook.dedupe_replay", provider=provider, event_id=cleaned)
    return False


def channel_job_id(channel: str, provider_event_id: str) -> str:
    """``_job_id`` ARQ determinista para un mensaje de canal."""
    return f"channel:{channel}:{provider_event_id.strip()}"
