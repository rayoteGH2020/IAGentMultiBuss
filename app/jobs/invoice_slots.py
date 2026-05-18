"""Cupos Redis por tenant para extracción de facturas (módulo ARQ).

Mínimo viable: cuenta de jobs concurrentes por tenant (`INCR`/`DECR`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from arq.worker import Retry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    import redis.asyncio as redis_ai

logger = structlog.get_logger(__name__)

# Alineado con arquitectura.md §6 — Módulo 1 (concurrencia por tenant).
MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT = 5
_SLOT_KEY_TEMPLATE = "invoice:extract:active:{tenant_id}"
_RETRY_DEFER_SECONDS = 2


@asynccontextmanager
async def tenant_invoice_extraction_slot(
    redis_conn: redis_ai.Redis,
    tenant_id: UUID,
) -> AsyncIterator[None]:
    """Limita paralelismo de extracción por tenant; si no hay cupo, difiere el job ARQ."""
    key = _SLOT_KEY_TEMPLATE.format(tenant_id=tenant_id)
    n_raw = await redis_conn.incr(key)
    hold_slot = False
    try:
        if int(n_raw) > MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT:
            await redis_conn.decr(key)
            logger.info(
                "worker.invoice.slot_full",
                tenant_id=str(tenant_id),
                key=key,
            )
            raise Retry(defer=_RETRY_DEFER_SECONDS)
        hold_slot = True
        yield
    finally:
        if hold_slot:
            try:
                await redis_conn.decr(key)
            except Exception as exc:  # pragma: no cover - defensivo
                logger.warning(
                    "worker.invoice.slot_decr_failed",
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
