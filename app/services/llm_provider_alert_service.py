"""Aviso al SADM cuando un proveedor LLM rechaza por saldo agotado o facturación (402).

Es un problema de la plataforma, no de un tenant: todas las llamadas a ese
proveedor fallan hasta que se recargue. Sin aviso, los clientes solo verían un
error y nadie se enteraría. Un email como mucho cada ``_ALERT_TTL_SECONDS`` por
proveedor (clave Redis con NX), enviado fuera de la petición por un job ARQ.
El email no lleva datos de clientes.
"""

from __future__ import annotations

import structlog

from app.config import get_settings
from app.core.cache import get_redis
from app.core.email import send_email

logger = structlog.get_logger(__name__)

_ALERT_TTL_SECONDS = 6 * 3600

_PROVIDER_CONSOLES: dict[str, str] = {
    "google": "Google AI Studio → Billing (https://aistudio.google.com)",
    "anthropic": "Anthropic Console → Billing (https://console.anthropic.com)",
    "voyage": "Voyage AI Dashboard → Billing (https://dashboard.voyageai.com)",
}

SUBJECT = "El proveedor de IA {provider} rechaza las llamadas por saldo o facturación"
BODY = (
    "Las llamadas a {provider} están fallando con un error de saldo o facturación "
    "(HTTP 402: créditos agotados o facturación desactivada). Mientras no se resuelva, "
    "no funcionan la extracción de documentos ni los chats que usan ese proveedor, "
    "en ningún tenant.\n"
    "Revisa y recarga: {console}.\n"
    "Los usuarios ven «El servicio de IA no está disponible en este momento» y pueden "
    "reintentar cuando se restablezca. No se repetirá este aviso en {hours} h."
)


def _dedupe_key(provider: str) -> str:
    return f"llm_provider:billing_alert:{provider}"


async def _enqueue_alert(provider: str) -> None:
    from app.jobs.queue import get_arq_pool

    pool = await get_arq_pool()
    await pool.enqueue_job("send_llm_provider_billing_alert", provider)


async def notify_provider_billing_error(provider: str) -> None:
    """Encola el email al SADM si no se ha avisado ya en la ventana actual.

    Nunca lanza: un fallo del aviso no debe tapar el error original de la llamada.
    """
    try:
        if not await get_redis().set(_dedupe_key(provider), "1", nx=True, ex=_ALERT_TTL_SECONDS):
            return
        await _enqueue_alert(provider)
        logger.warning("llm_provider.billing_alert_enqueued", provider=provider)
    except Exception as exc:
        logger.warning(
            "llm_provider.billing_alert_enqueue_failed",
            provider=provider,
            error_type=type(exc).__name__,
        )


async def send_provider_billing_alert(provider: str) -> bool:
    """Envía el email al SADM (``EMAIL_SADM``). False si no hay destinatario."""
    to = get_settings().email_sadm.strip()
    if not to:
        logger.warning("llm_provider.billing_alert_sadm_email_missing", provider=provider)
        return False
    console = _PROVIDER_CONSOLES.get(provider, "la consola de facturación del proveedor")
    await send_email(
        to=to,
        subject=SUBJECT.format(provider=provider),
        body=BODY.format(provider=provider, console=console, hours=_ALERT_TTL_SECONDS // 3600),
    )
    logger.info("llm_provider.billing_alert_sent", provider=provider)
    return True
