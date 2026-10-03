"""Tope de reintentos automáticos de extracción por documento.

Regla de producto (especificacion-planes-y-cuotas.md §4.2): como máximo
2 reintentos automáticos por documento, es decir, hasta 3 llamadas de
extracción al LLM. Esos reintentos los hace Instructor dentro de una misma
ejecución (``llm_extraction_max_retries``).

ARQ vuelve a ejecutar un job en dos casos: ``Retry`` del semáforo por tenant
(antes de llamar al LLM, sin coste) y ``CancelledError`` (worker reiniciado o
parado a mitad del job). En el segundo, repetir la extracción duplicaría las
llamadas ya hechas. Este módulo marca en Redis el momento en que una ejecución
empieza a llamar al LLM; si una ejecución posterior del mismo job encuentra la
marca, no vuelve a extraer y el documento queda en ``processing_interrupted``,
reintentable a mano (cuenta en ``document_retries_*``).

La marca incluye ``enqueue_time``: un reintento manual re-encola el mismo
``job_id`` pero con otra hora de encolado, así que no hereda la marca.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from app.services import document_processing_service

if TYPE_CHECKING:
    from uuid import UUID

    import redis.asyncio as redis_ai
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.document_processing_service import DocumentKindLiteral

logger = structlog.get_logger(__name__)

_MARK_KEY_TEMPLATE = "doc_extraction:llm_started:{job_id}:{enqueue_ms}"
# Igual que la clave de reintentos de ARQ (arq:retry:*): cubre cualquier
# re-ejecución posible del job y luego caduca sola.
_MARK_TTL_SECONDS = 88_400


def _mark_key(ctx: dict[str, Any]) -> str | None:
    """Clave de la ejecución encolada, o None fuera de ARQ (tests, llamadas directas)."""
    job_id = ctx.get("job_id")
    enqueue_time = ctx.get("enqueue_time")
    if not job_id or enqueue_time is None:
        return None
    enqueue_ms = int(enqueue_time.timestamp() * 1000)
    return _MARK_KEY_TEMPLATE.format(job_id=job_id, enqueue_ms=enqueue_ms)


async def mark_llm_started(ctx: dict[str, Any], redis_conn: redis_ai.Redis) -> None:
    """Anota que esta ejecución va a llamar al LLM (justo antes de extraer)."""
    key = _mark_key(ctx)
    if key is None:
        return
    await redis_conn.set(key, "1", ex=_MARK_TTL_SECONDS)


async def close_if_interrupted_after_llm(
    ctx: dict[str, Any],
    redis_conn: redis_ai.Redis,
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> bool:
    """Cierra el documento si una ejecución anterior de este job ya llamó al LLM.

    Returns:
        True si el documento se ha marcado como interrumpido y el job debe
        terminar sin extraer; False si puede continuar.
    """
    key = _mark_key(ctx)
    if key is None or int(ctx.get("job_try", 1)) <= 1:
        return False
    if not await redis_conn.exists(key):
        return False

    await document_processing_service.abandon_stale_processing(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        force=True,
    )
    await db.commit()
    logger.warning(
        "worker.extraction.not_repeated_after_interruption",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        job_try=ctx.get("job_try"),
    )
    return True
