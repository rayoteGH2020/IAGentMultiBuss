"""Jobs ARQ: indexación de documentos de conocimiento por URL (Paso 21 A).

Flujo:
  1. Cargar documento de BD para obtener source_url y source_file_key.
  2. Scraping de la URL con web_scraper.scrape_url().
  3. Upload del texto extraído a R2 en la key pre-generada.
  4. Actualizar nombre del documento con el título de la página.
  5. Llamar a run_index_pipeline() (mismo pipeline que documentos fichero).
  6. Commit atómico.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.core.db import session_factory_for_worker, set_tenant_context
from app.core.errors import ScrapingError
from app.core.storage import get_storage
from app.core.web_scraper import scrape_url
from app.jobs.knowledge_slots import tenant_knowledge_indexing_slot
from app.models.knowledge import KnowledgeDocument
from app.services.knowledge_document_service import mark_failed
from app.services.knowledge_index_service import run_index_pipeline

logger = structlog.get_logger(__name__)


async def index_knowledge_url(
    ctx: dict[str, Any],
    document_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Scraping de URL + indexación en la base de conocimiento."""
    from app.core.cache import get_redis

    doc_uuid = uuid.UUID(document_id)
    t_uuid = uuid.UUID(tenant_id)
    logger.info("worker.knowledge_url.start", document_id=document_id, tenant_id=tenant_id)

    redis_conn = ctx.get("redis") or get_redis()

    async with (
        tenant_knowledge_indexing_slot(redis_conn, t_uuid),
        session_factory_for_worker(t_uuid) as db,
    ):
        doc = (
            await db.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == doc_uuid,
                    KnowledgeDocument.tenant_id == t_uuid,
                )
            )
        ).scalar_one_or_none()

        if doc is None:
            logger.warning("worker.knowledge_url.doc_not_found", document_id=document_id)
            return {"status": "not_found", "document_id": document_id}

        source_url = doc.source_url
        if not source_url:
            logger.error("worker.knowledge_url.missing_source_url", document_id=document_id)
            await mark_failed(
                db, tenant_id=t_uuid, document_id=doc_uuid, error_message="source_url is empty"
            )
            await db.commit()
            return {"status": "failed", "document_id": document_id}

        try:
            # 1. Scraping
            scraped = await scrape_url(source_url)

            # 2. Upload del texto a R2 en la key pre-generada
            text_bytes = scraped.text.encode("utf-8")
            storage = get_storage()
            await storage.upload_bytes(doc.source_file_key, text_bytes, content_type="text/plain")

            # 3. Actualizar nombre del documento con el título de la página
            if scraped.title:
                doc.name = scraped.title[:300]
            doc.file_size_bytes = len(text_bytes)

            # 4. Indexar con el pipeline estándar (descarga de R2 + chunks + embed)
            await run_index_pipeline(
                db,
                tenant_id=t_uuid,
                document_id=doc_uuid,
                source_file_key=doc.source_file_key,
                source_mime="text/plain",
            )
            await db.commit()
            logger.info(
                "worker.knowledge_url.done",
                document_id=document_id,
                url=source_url,
                chars=scraped.char_count,
            )
            return {"status": "ok", "document_id": document_id}

        except ScrapingError as exc:
            await db.rollback()
            await set_tenant_context(db, str(t_uuid))
            await mark_failed(db, tenant_id=t_uuid, document_id=doc_uuid, error_message=str(exc))
            await db.commit()
            logger.warning(
                "worker.knowledge_url.scraping_failed",
                document_id=document_id,
                url=source_url,
                error=str(exc),
            )
            return {"status": "failed", "document_id": document_id}

        except Exception as exc:
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
                "worker.knowledge_url.failed",
                document_id=document_id,
                url=source_url,
            )
            return {"status": "failed", "document_id": document_id}
