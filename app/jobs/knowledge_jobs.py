"""Jobs ARQ: indexación de documentos de conocimiento (módulo 2, Paso 18).

Flujo completo de un job:
  1. Adquirir slot de concurrencia por tenant (semáforo Redis, máx. 3).
  2. Abrir sesión de BD con contexto de tenant (RLS activo).
  3. Leer el documento de BD para obtener source_file_key y source_mime.
  4. Delegar en run_index_pipeline (descarga R2 → texto → chunks → embed → BD).
  5. Commit único que persiste chunks, LLMCalls y estado del documento.
  En caso de error inesperado: rollback → re-establecer contexto de tenant
  → mark_failed → commit.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.core.cache import get_redis
from app.core.db import session_factory_for_worker, set_tenant_context
from app.jobs.knowledge_slots import tenant_knowledge_indexing_slot
from app.models.knowledge import KnowledgeDocument
from app.services.knowledge_document_service import mark_failed
from app.services.knowledge_index_service import run_index_pipeline

logger = structlog.get_logger(__name__)


async def index_knowledge_document(
    ctx: dict[str, Any],
    document_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Descarga el documento de R2, lo trocea, lo embebe y persiste los chunks."""
    doc_uuid = uuid.UUID(document_id)
    t_uuid = uuid.UUID(tenant_id)
    logger.info(
        "worker.knowledge.start",
        document_id=document_id,
        tenant_id=tenant_id,
    )

    # ARQ inyecta su conexión Redis en ctx["redis"]. En tests sin ARQ real
    # se usa el cliente Redis de la app como fallback para el semáforo.
    redis_conn = ctx.get("redis")
    if redis_conn is None:
        redis_conn = get_redis()

    async with (
        # Orden importa: el slot se adquiere antes de abrir sesión de BD para
        # no consumir conexiones de Postgres cuando el tenant está al límite.
        tenant_knowledge_indexing_slot(redis_conn, t_uuid),
        session_factory_for_worker(t_uuid) as db,
    ):
        # Leer el documento en el worker aunque el route lo creó momentos antes:
        # el worker corre en un proceso separado sin estado compartido con la app.
        doc = (
            await db.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == doc_uuid,
                    KnowledgeDocument.tenant_id == t_uuid,
                )
            )
        ).scalar_one_or_none()

        if doc is None:
            logger.warning(
                "worker.knowledge.doc_not_found",
                document_id=document_id,
                tenant_id=tenant_id,
            )
            return {"status": "not_found", "document_id": document_id}

        try:
            # run_index_pipeline gestiona sus propios errores de extracción,
            # chunking y embeddings: llama a mark_failed internamente y retorna
            # sin lanzar excepción en esos casos. Solo escapa de este bloque
            # una excepción inesperada (fallo de BD, R2 caído, etc.).
            await run_index_pipeline(
                db,
                tenant_id=t_uuid,
                document_id=doc_uuid,
                source_file_key=doc.source_file_key,
                source_mime=doc.source_mime,
            )
            await db.commit()
            logger.info(
                "worker.knowledge.done",
                document_id=document_id,
                tenant_id=tenant_id,
            )
            return {"status": "ok", "document_id": document_id}

        except Exception as exc:
            # Rollback deshace toda escritura parcial, incluidos los SET LOCAL
            # de contexto de tenant. Es necesario re-establecer el contexto
            # antes de llamar a mark_failed para que RLS aplique correctamente.
            await db.rollback()
            await set_tenant_context(db, str(t_uuid))
            await mark_failed(
                db,
                tenant_id=t_uuid,
                document_id=doc_uuid,
                error_message=str(exc)[:500],
            )
            await db.commit()
            logger.exception(
                "worker.knowledge.failed",
                document_id=document_id,
                tenant_id=tenant_id,
            )
            return {"status": "failed", "document_id": document_id}
