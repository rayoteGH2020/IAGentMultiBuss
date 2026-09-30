"""Aviso al SADM ante errores de saldo/facturación del proveedor LLM.

Módulo aparte para que lo usen ``client.py`` y ``chat_loop.py`` sin import
circular (``client`` ya importa ``chat_loop``).
"""

from __future__ import annotations

from app.core.document_processing_errors import is_provider_billing_error


async def alert_if_provider_billing_error(provider: str, raw_error: str | None) -> None:
    """Avisa al SADM si el proveedor rechaza por saldo o facturación (402)."""
    if is_provider_billing_error(raw_error):
        from app.services import llm_provider_alert_service

        await llm_provider_alert_service.notify_provider_billing_error(provider)
