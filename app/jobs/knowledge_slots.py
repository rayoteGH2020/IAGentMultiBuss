"""Cupos Redis por tenant para indexación de documentos de conocimiento (Paso 18).

Implementa el mismo patrón de semáforo de conteo que invoice_slots.py:
INCR especulativo + DECR en finally. Ver ese módulo para comentarios
detallados del razonamiento de diseño (atomicidad, hold_slot, Retry).
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

MAX_PARALLEL_KNOWLEDGE_INDEXING_PER_TENANT = 3

_SLOT_KEY_TEMPLATE = "knowledge:index:active:{tenant_id}"

# 5 segundos: más largo que el de facturas (2 s) porque la indexación es
# más pesada y tarda más en liberar un slot; diferir menos tiempo no ayudaría.
_RETRY_DEFER_SECONDS = 5


@asynccontextmanager
async def tenant_knowledge_indexing_slot(
    redis_conn: redis_ai.Redis,
    tenant_id: UUID,
) -> AsyncIterator[None]:
    """Limita paralelismo de indexación por tenant; si no hay cupo, difiere el job ARQ."""
    key = _SLOT_KEY_TEMPLATE.format(tenant_id=tenant_id)
    n_raw = await redis_conn.incr(key)
    hold_slot = False
    try:
        if int(n_raw) > MAX_PARALLEL_KNOWLEDGE_INDEXING_PER_TENANT:
            await redis_conn.decr(key)
            logger.info(
                "worker.knowledge.slot_full",
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
                    "worker.knowledge.slot_decr_failed",
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
