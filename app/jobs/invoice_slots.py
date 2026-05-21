"""Cupos Redis por tenant para extracción de facturas (módulo ARQ).

Implementa un semáforo de conteo por tenant usando operaciones atómicas de
Redis (INCR/DECR). Evita que un tenant con muchas facturas monopolice todos
los slots del worker y bloquee a otros tenants.

Cuando el tenant alcanza el límite, el job se difiere (arq.worker.Retry) en
lugar de fallar: ARQ lo re-encola automáticamente sin consumir un intento
de max_tries, por lo que el job se ejecutará en cuanto haya un slot libre.
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

# Máximo de extracciones LLM simultáneas por tenant (arquitectura.md §6).
# Equilibrio entre throughput (más slots = más rápido para el tenant) y
# equidad entre tenants (más slots = un tenant puede bloquear al resto).
MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT = 5

# Clave por tenant en Redis; el contador refleja cuántos jobs de ese tenant
# están ejecutándose en este momento. Una clave por tenant garantiza aislamiento
# total: el contador de un tenant no afecta al de otro.
_SLOT_KEY_TEMPLATE = "invoice:extract:active:{tenant_id}"

# Tiempo de espera antes de reintentar cuando el cupo está lleno.
# 2 segundos: corto suficiente para no retrasar la cola, largo suficiente
# para que alguno de los jobs en curso pueda terminar y liberar un slot.
_RETRY_DEFER_SECONDS = 2


@asynccontextmanager
async def tenant_invoice_extraction_slot(
    redis_conn: redis_ai.Redis,
    tenant_id: UUID,
) -> AsyncIterator[None]:
    """Limita paralelismo de extracción por tenant; si no hay cupo, difiere el job ARQ."""
    key = _SLOT_KEY_TEMPLATE.format(tenant_id=tenant_id)

    # INCR atómico: incrementa el contador y obtiene el nuevo valor en una
    # operación. La atomicidad evita la condición de carrera TOCTOU ("check
    # then act"): si dos workers leyesen el contador antes de incrementar,
    # ambos verían 4 y ambos procederían, superando el límite de 5.
    n_raw = await redis_conn.incr(key)

    # hold_slot controla si este contexto llegó a adquirir el slot.
    # Es necesario en finally para no hacer DECR si nunca se incrementó
    # (p. ej. si INCR falló o si lanzamos Retry justo después de DECR).
    hold_slot = False
    try:
        if int(n_raw) > MAX_PARALLEL_INVOICE_EXTRACTION_PER_TENANT:
            # Slot lleno: revertir el INCR especulativo antes de salir.
            # Sin este DECR el contador quedaría inflado y nunca llegaría
            # al límite real de jobs concurrentes, bloqueando slots fantasma.
            await redis_conn.decr(key)
            logger.info(
                "worker.invoice.slot_full",
                tenant_id=str(tenant_id),
                key=key,
            )
            # Retry (arq.worker.Retry): le indica a ARQ que re-encole el job
            # después de _RETRY_DEFER_SECONDS sin consumir un intento de
            # max_tries. Es distinto a lanzar una excepción normal, que sí
            # consumiría un intento y podría llegar a marcar el job como failed.
            raise Retry(defer=_RETRY_DEFER_SECONDS)

        # El slot se adquirió; se marca hold_slot=True para que finally lo libere.
        hold_slot = True
        yield  # El código del caller (process_invoice) se ejecuta aquí.
    finally:
        if hold_slot:
            try:
                # DECR libera el slot al terminar el job (éxito o excepción).
                # El try/except interior protege el DECR: si Redis no está
                # disponible en este punto, no se puede hacer nada más (el job
                # ya terminó), pero al menos el error queda en los logs.
                # Limitación conocida: si el worker muere (SIGKILL, OOM) antes
                # de llegar aquí, el contador no se decrementa y el slot queda
                # "bloqueado" hasta que se reinicie el worker o se resetee la
                # clave manualmente. Sin TTL en la clave, esto es permanente.
                await redis_conn.decr(key)
            except Exception as exc:  # pragma: no cover - defensivo
                logger.warning(
                    "worker.invoice.slot_decr_failed",
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
