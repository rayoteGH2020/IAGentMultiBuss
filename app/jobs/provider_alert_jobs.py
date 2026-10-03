"""Job ARQ: email al SADM cuando un proveedor LLM rechaza por saldo o facturación."""

from __future__ import annotations

from typing import Any

from app.services import llm_provider_alert_service


async def send_llm_provider_billing_alert(ctx: dict[str, Any], provider: str) -> dict[str, Any]:
    """Envía el aviso; la frecuencia ya la limitó quien encoló el job."""
    _ = ctx
    sent = await llm_provider_alert_service.send_provider_billing_alert(provider)
    return {"status": "sent" if sent else "no_sadm_email", "provider": provider}
