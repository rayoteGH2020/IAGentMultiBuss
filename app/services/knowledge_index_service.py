"""Pipeline de indexación de documentos de conocimiento (Paso 18).

Orquesta el flujo completo para un documento:
  1. Descarga bytes de R2.
  2. Extrae texto (PDF/TXT/MD).
  3. Valida texto no vacío (scanned PDF → failed con hint comprensible).
  4. Chunking por párrafos con solape configurable.
  5. Embeddings via Voyage AI (LLMClient.embed).
  6. Transacción: borra chunks anteriores + bulk insert nuevos.
  7. Actualiza documento (ready, chunk_count, ingested_at).

Diseñado para ser invocado desde el ARQ job `index_knowledge_document`.
No hace commit; el job wrapper gestiona la transacción.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.document_text import extract_knowledge_text
from app.core.storage import get_storage
from app.core.text_chunking import TooManyChunksError, chunk_text
from app.llm.client import get_llm_client
from app.models.knowledge import KnowledgeChunk
from app.services.knowledge_document_service import (
    apply_index_result,
    mark_failed,
    mark_indexing,
)

logger = structlog.get_logger(__name__)

# Prefijos de error_message: permiten que la UI muestre mensajes concretos
# sin parsear texto libre (p. ej. distinguir scanned PDF de error de red).
_ERR_EMPTY_TEXT = "empty_text"
_ERR_TOO_MANY_CHUNKS = "too_many_chunks"
_ERR_EXTRACT = "extract_error"
_ERR_EMBED = "embed_error"


async def run_index_pipeline(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    source_file_key: str,
    source_mime: str,
) -> None:
    """Ejecuta el pipeline completo de indexación para un documento.

    Muta el estado del documento: pending → indexing → ready | failed.
    El caller (ARQ job) es responsable del commit de la transacción.

    Args:
        db: Sesión async con la transacción activa del job.
        tenant_id: Tenant propietario del documento.
        document_id: ID del documento a indexar.
        source_file_key: Clave R2 del fichero fuente.
        source_mime: MIME type del fichero fuente.
    """
    await mark_indexing(db, tenant_id=tenant_id, document_id=document_id)

    # --- Paso 1: Descarga ---
    storage = get_storage()
    file_bytes = await storage.download_bytes(source_file_key)

    # --- Paso 2: Extracción de texto ---
    try:
        extracted = extract_knowledge_text(file_bytes, source_mime)
    except Exception as exc:
        logger.warning(
            "knowledge.index.extract_failed",
            document_id=str(document_id),
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        await mark_failed(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            error_message=f"{_ERR_EXTRACT}: {exc}",
        )
        return

    # --- Paso 3: Validación de texto no vacío ---
    if not extracted.text.strip():
        hint = (
            "El PDF no contiene texto extraíble (posible escaneado). "
            "Sube una versión con capa de texto o contacta con soporte."
            if source_mime == "application/pdf"
            else "El documento está vacío."
        )
        await mark_failed(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            error_message=f"{_ERR_EMPTY_TEXT}: {hint}",
        )
        return

    # --- Paso 4: Chunking ---
    s = get_settings()
    try:
        chunks = chunk_text(
            extracted.text,
            target_tokens=s.knowledge_chunk_target_tokens,
            overlap_tokens=s.knowledge_chunk_overlap_tokens,
        )
    except TooManyChunksError as exc:
        await mark_failed(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            error_message=(
                f"{_ERR_TOO_MANY_CHUNKS}: {exc.count} chunks superan el límite "
                f"de {exc.max_chunks}. El documento es demasiado largo."
            ),
        )
        return

    # --- Paso 5: Embeddings ---
    texts = [c.text for c in chunks]
    try:
        embeddings = await get_llm_client().embed(
            texts,
            tenant_id=tenant_id,
            db=db,
        )
    except Exception as exc:
        logger.error(
            "knowledge.index.embed_failed",
            document_id=str(document_id),
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        await mark_failed(
            db,
            tenant_id=tenant_id,
            document_id=document_id,
            error_message=f"{_ERR_EMBED}: {exc}",
        )
        return

    # --- Paso 6: Transacción de chunks ---
    # Borra chunks previos antes de insertar para soportar re-indexación limpia.
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))

    chunk_rows = [
        KnowledgeChunk(
            id=uuid4(),
            tenant_id=tenant_id,
            document_id=document_id,
            content=chunk.text,
            context=None,
            embedding=emb,
            position=chunk.position,
            chunk_metadata={
                "token_estimate": chunk.token_estimate,
                "char_start": chunk.char_start,
            },
        )
        for chunk, emb in zip(chunks, embeddings, strict=True)
    ]
    db.add_all(chunk_rows)
    await db.flush()

    # --- Paso 7: Actualizar documento ---
    await apply_index_result(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        chunk_count=len(chunk_rows),
    )

    logger.info(
        "knowledge.index.complete",
        document_id=str(document_id),
        tenant_id=str(tenant_id),
        chunk_count=len(chunk_rows),
    )
