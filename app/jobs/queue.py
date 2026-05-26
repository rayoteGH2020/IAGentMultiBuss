"""Cola ARQ: encolado de jobs desde el proceso web hacia el worker."""

from __future__ import annotations

from functools import lru_cache
from uuid import UUID

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

logger = structlog.get_logger(__name__)

# Singleton del pool ARQ a nivel de módulo. No se gestiona con lru_cache
# porque create_pool es una coroutine (async); lru_cache no soporta funciones
# async. El patrón manual con variable global es equivalente pero explícito.
_pool: ArqRedis | None = None


@lru_cache(maxsize=1)
def _redis_settings() -> RedisSettings:
    # Función síncrona separada para cachear el objeto RedisSettings (parseo
    # del DSN) independientemente del pool async. Así los tests pueden limpiar
    # el pool sin necesariamente limpiar la configuración, y viceversa.
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_arq_pool() -> ArqRedis:
    # Singleton perezoso: el pool se crea en la primera llamada y se reutiliza.
    # ArqRedis mantiene conexiones persistentes a Redis (similar al pool de
    # asyncpg); crear un pool por request sería costoso e innecesario.
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


def reset_arq_pool_for_tests() -> None:
    """Limpia pool y cache para tests que cambian REDIS_URL entre casos.

    Se limpia también _redis_settings porque los tests pueden arrancar con
    una URL de Redis de test y otra de producción; sin limpiar el cache, el
    segundo test usaría la URL anterior aunque se haya cambiado la variable.
    """
    global _pool
    _pool = None
    _redis_settings.cache_clear()


async def enqueue_invoice_processing(invoice_id: UUID, tenant_id: UUID) -> str:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        # El string debe coincidir exactamente con el nombre de la función
        # registrada en WorkerSettings.functions. ARQ enruta por nombre;
        # un typo aquí haría que el job se encole pero nunca se ejecute.
        "process_invoice",
        # ARQ serializa los argumentos como JSON: UUID no es serializable,
        # se convierte a str aquí y se parsea de vuelta a UUID en el worker.
        str(invoice_id),
        str(tenant_id),
        # _job_id determinista por invoice_id: ARQ usa este ID para deduplicar.
        # Si el route llama a enqueue dos veces para la misma factura (doble
        # click, retry del usuario), la segunda llamada devuelve None en lugar
        # de crear un segundo job, evitando doble extracción LLM y doble coste.
        _job_id=f"invoice:{invoice_id}",
    )
    # job es None cuando ya existe un job con el mismo _job_id en la cola.
    # En el flujo normal esto no debería ocurrir porque la UI deshabilita el
    # botón tras la primera subida; se eleva RuntimeError para que sea visible
    # en logs si la deduplicación se activa de forma inesperada.
    if job is None:
        logger.warning(
            "arq.enqueue_invoice_duplicate",
            invoice_id=str(invoice_id),
            tenant_id=str(tenant_id),
        )
        return f"invoice:{invoice_id}"
    return str(job.job_id)


async def enqueue_ticket_processing(ticket_id: UUID, tenant_id: UUID) -> str:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "process_ticket",
        str(ticket_id),
        str(tenant_id),
        _job_id=f"ticket:{ticket_id}",
    )
    if job is None:
        logger.warning(
            "arq.enqueue_ticket_duplicate",
            ticket_id=str(ticket_id),
            tenant_id=str(tenant_id),
        )
        return f"ticket:{ticket_id}"
    return str(job.job_id)


async def enqueue_knowledge_indexing(document_id: UUID, tenant_id: UUID) -> str:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "index_knowledge_document",
        str(document_id),
        str(tenant_id),
        _job_id=f"knowledge:{document_id}",
    )
    if job is None:
        logger.warning(
            "arq.enqueue_knowledge_duplicate",
            document_id=str(document_id),
            tenant_id=str(tenant_id),
        )
        return f"knowledge:{document_id}"
    return str(job.job_id)
