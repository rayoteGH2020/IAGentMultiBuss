"""Servicio de gestión de documentos de conocimiento (Paso 18).

CRUD y transiciones de estado para KnowledgeDocument. No orquesta el pipeline
de indexación (eso es knowledge_index_service.py).
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit_pseudonym import file_metadata
from app.core.errors import NotFoundError, ValidationError
from app.core.faq_serializer import FaqPair, deserialize_faq, serialize_faq
from app.core.keys import document_key, knowledge_faq_key
from app.core.knowledge_uploads import validate_knowledge_upload
from app.core.media_limits import IMAGE_MIMES, MediaLimitExceeded, inspect_document
from app.core.storage import get_storage
from app.core.uploads import UploadValidationError, original_upload_filename
from app.models.knowledge import KnowledgeDocument
from app.schemas.knowledge import (
    KnowledgeDocumentFilters,
    KnowledgeDocumentKind,
    KnowledgeDocumentRead,
    KnowledgeDocumentStatus,
)
from app.schemas.pagination import Page
from app.services import audit_service, entitlement_service, plan_quota_service

logger = structlog.get_logger(__name__)

ACTION_KNOWLEDGE_UPLOAD = "knowledge.upload"
ACTION_KNOWLEDGE_FAQ_CREATE = "knowledge.faq_create"
ACTION_KNOWLEDGE_FAQ_EDIT = "knowledge.faq_edit"
ACTION_KNOWLEDGE_DELETE = "knowledge.delete"
ACTION_KNOWLEDGE_REINDEX = "knowledge.reindex"
ACTION_KNOWLEDGE_VIEW = "knowledge.view"
ACTION_KNOWLEDGE_DOWNLOAD = "knowledge.download"
RESOURCE_KNOWLEDGE_DOCUMENT = "knowledge_document"

# La URL prefirmada solo se usa en el redirect inmediato de /knowledge/{id}/file:
# vida corta para que no sirva como enlace reutilizable sin pasar por la auditoría.
DOWNLOAD_URL_TTL_SECONDS = 60

# Zonas del modal de subida: igual que /documents/upload.
MAX_FILES_PER_UPLOAD = 10


def resolve_per_file_kinds(
    *,
    file_count: int,
    kinds: list[str] | str | None,
) -> list[KnowledgeDocumentKind]:
    """Exige una categoría válida por fichero, en el mismo orden que los ficheros.

    Raises:
        ValidationError: número de categorías distinto al de ficheros o valor
            fuera de ``KnowledgeDocumentKind``.
    """
    # Multipart repetido: FastAPI entrega str (un valor) o list (varios).
    values = [kinds] if isinstance(kinds, str) else list(kinds or [])
    if len(values) != file_count:
        raise ValidationError("Cada fichero debe tener una categoría.")
    try:
        return [KnowledgeDocumentKind(value) for value in values]
    except ValueError as exc:
        raise ValidationError("Categoría de documento no válida.") from exc


async def create_from_upload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    filename: str,
    file_bytes: bytes,
    kind: KnowledgeDocumentKind,
    name: str | None = None,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> KnowledgeDocument:
    """Valida, sube a R2 e inserta un documento en estado pending.

    Orden deliberado: primero R2, luego BD. Si el upload a R2 falla, no queda
    ningún registro huérfano en BD. Si BD falla tras el upload, queda un objeto
    huérfano en R2 (aceptable: no se referencia desde ningún tenant y el job
    de retención GDPR lo eliminará según la política de retención configurada).
    """
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    await plan_quota_service.ensure_knowledge_docs_capacity(db, ents, tenant_id)

    settings = get_settings()
    mime_type = validate_knowledge_upload(
        filename,
        file_bytes,
        max_size_bytes=settings.knowledge_max_file_size_bytes,
        allowed_mimes=settings.knowledge_allowed_mimes,
    )

    # Imágenes: media_limits antes de R2/OCR (píxeles, edge, cabecera legible).
    # Bytes y MIME ya los cubre validate_knowledge_upload; esto cierra el gap
    # de decompression bombs que pasan el tope de MB pero explotan al abrir.
    if mime_type in IMAGE_MIMES:
        try:
            await asyncio.to_thread(inspect_document, file_bytes, mime_type)
        except MediaLimitExceeded as exc:
            logger.warning(
                "knowledge.upload.rejected_by_limits",
                tenant_id=str(tenant_id),
                mime_type=mime_type,
                size_bytes=len(file_bytes),
                error_code=exc.error_code.value,
                reason=exc.message,
            )
            raise UploadValidationError(
                exc.detail or "La imagen supera los límites permitidos o no se puede inspeccionar."
            ) from exc

    storage = get_storage()
    key = document_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)

    doc_name = (name or original_upload_filename(filename))[:300]
    doc = KnowledgeDocument(
        tenant_id=tenant_id,
        kind=kind,
        name=doc_name,
        original_filename=original_upload_filename(filename),
        source_file_key=key,
        source_mime=mime_type,
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=len(file_bytes),
        uploaded_by=user_id,
    )
    db.add(doc)
    await db.flush()

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_UPLOAD,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=doc.id,
        metadata={
            **file_metadata(doc.original_filename, sha256=hashlib.sha256(file_bytes).hexdigest()),
            "kind": kind.value,
            "size": len(file_bytes),
        },
        request_ctx=request_ctx,
    )

    logger.info(
        "knowledge.document.created",
        document_id=str(doc.id),
        tenant_id=str(tenant_id),
        kind=kind.value,
    )
    return doc


async def create_from_faq(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    pairs: list[FaqPair],
    kind: KnowledgeDocumentKind,
    name: str | None = None,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> KnowledgeDocument:
    """Crea un KnowledgeDocument de tipo FAQ desde pares Q/A (Paso 21 B).

    Serializa los pares al formato P:/R:, los sube a R2 y crea el documento
    en estado pending. La key R2 es determinista (incluye doc UUID) para que
    update_faq_pairs pueda sobreescribirla sin dejar huérfanos.
    """
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    await plan_quota_service.ensure_knowledge_docs_capacity(db, ents, tenant_id)

    from uuid import uuid4

    text = serialize_faq(pairs)
    doc_id = uuid4()
    key = knowledge_faq_key(tenant_id, doc_id)

    storage = get_storage()
    await storage.upload_bytes(key, text.encode("utf-8"), content_type="text/plain")

    doc_name = (name or f"FAQ ({len(pairs)} pares)")[:300]
    doc = KnowledgeDocument(
        id=doc_id,
        tenant_id=tenant_id,
        kind=kind,
        name=doc_name,
        original_filename=f"faq_{doc_id}.txt",
        source_file_key=key,
        source_mime="text/plain",
        faq_content=text,
        status=KnowledgeDocumentStatus.pending,
        chunk_count=0,
        file_size_bytes=len(text.encode("utf-8")),
        uploaded_by=user_id,
    )
    db.add(doc)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_FAQ_CREATE,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=doc.id,
        metadata={"kind": kind.value, "pairs": len(pairs)},
        request_ctx=request_ctx,
    )
    logger.info("knowledge.faq.created", document_id=str(doc_id), tenant_id=str(tenant_id))
    return doc


async def update_faq_pairs(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    pairs: list[FaqPair],
    user_id: UUID | None,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> KnowledgeDocument:
    """Actualiza los pares Q/A de un FAQ existente y marca el documento como pending.

    Sube el nuevo texto a la misma key R2 (sobreescribe) y actualiza faq_content.
    El caller es responsable de encolar el reindexado.
    """
    doc = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)

    text = serialize_faq(pairs)
    storage = get_storage()
    await storage.upload_bytes(doc.source_file_key, text.encode("utf-8"), content_type="text/plain")

    doc.faq_content = text
    doc.file_size_bytes = len(text.encode("utf-8"))
    doc.status = KnowledgeDocumentStatus.pending
    doc.error_message = None
    await db.flush()

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_FAQ_EDIT,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata={"pairs": len(pairs)},
        request_ctx=request_ctx,
    )
    logger.info("knowledge.faq.updated", document_id=str(document_id), tenant_id=str(tenant_id))
    return doc


def get_faq_pairs(doc: KnowledgeDocument) -> list[FaqPair]:
    """Parsea faq_content de un documento FAQ a lista de FaqPair."""
    return get_faq_pairs_from_content(doc.faq_content)


def get_faq_pairs_from_content(faq_content: str | None) -> list[FaqPair]:
    """Parsea faq_content serializado a pares Q/A."""
    return deserialize_faq(faq_content or "")


async def get_faq_edit_context(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> tuple[KnowledgeDocumentRead, list[FaqPair]]:
    """Documento FAQ y pares Q/A para el panel de edición (sin ORM en rutas).

    Muestra el contenido del FAQ, así que se audita como ``knowledge.view``.
    """
    doc = await get_document(db, tenant_id=tenant_id, document_id=document_id)
    await _log_view(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        section="faq",
        request_ctx=request_ctx,
    )
    pairs = get_faq_pairs_from_content(doc.faq_content)
    return doc, pairs


async def list_documents(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filters: KnowledgeDocumentFilters | None = None,
) -> Page[KnowledgeDocumentRead]:
    """Devuelve una página de documentos con filtros opcionales por kind y status."""
    f = filters or KnowledgeDocumentFilters()

    base_q = select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id)
    if f.kind is not None:
        base_q = base_q.where(KnowledgeDocument.kind == f.kind)
    if f.status is not None:
        base_q = base_q.where(KnowledgeDocument.status == f.status)

    total: int = (
        await db.execute(select(func.count()).select_from(base_q.subquery()))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                base_q.order_by(KnowledgeDocument.created_at.desc()).limit(f.limit).offset(f.offset)
            )
        )
        .scalars()
        .all()
    )

    items = [KnowledgeDocumentRead.model_validate(r) for r in rows]
    return Page(items=items, total=total, limit=f.limit, offset=f.offset)


async def get_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> KnowledgeDocumentRead:
    """Lectura interna de un documento, sin auditar (refrescos tras una acción).

    Para mostrarlo a un usuario usar ``view_document``; para el fichero, ``download_url``.
    """
    row = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)
    return KnowledgeDocumentRead.model_validate(row)


async def view_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> KnowledgeDocumentRead:
    """Detalle de un documento para la UI; deja ``knowledge.view`` en audit_log."""
    doc = await get_document(db, tenant_id=tenant_id, document_id=document_id)
    await _log_view(
        db,
        tenant_id=tenant_id,
        document_id=document_id,
        user_id=user_id,
        section="detail",
        request_ctx=request_ctx,
    )
    return doc


async def download_url(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> str:
    """URL prefirmada de vida corta del original; deja ``knowledge.download`` en audit_log.

    Raises:
        NotFoundError: el documento no existe en el tenant.
    """
    row = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)
    url = await get_storage().presigned_url_get(row.source_file_key, ttl=DOWNLOAD_URL_TTL_SECONDS)
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_DOWNLOAD,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata={"kind": row.kind.value, "mime": row.source_mime},
        request_ctx=request_ctx,
    )
    return url


async def _log_view(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID,
    section: str,
    request_ctx: audit_service.AuditRequestContext | None,
) -> None:
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_VIEW,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata={"section": section},
        request_ctx=request_ctx,
    )


async def mark_indexing(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> KnowledgeDocument:
    """Avanza el estado a `indexing`. Llamado al inicio del pipeline de indexación."""
    doc = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)
    doc.status = KnowledgeDocumentStatus.indexing
    doc.error_message = None
    await db.flush()
    return doc


async def apply_index_result(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    chunk_count: int,
) -> KnowledgeDocument:
    """Marca el documento como `ready` tras indexación exitosa."""
    doc = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)
    doc.status = KnowledgeDocumentStatus.ready
    doc.chunk_count = chunk_count
    doc.error_message = None
    doc.ingested_at = datetime.now(UTC)
    await db.flush()

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action="knowledge.index",
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata={"chunk_count": chunk_count},
    )
    return doc


async def mark_failed(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    error_message: str,
) -> KnowledgeDocument:
    """Marca el documento como `failed` con el mensaje de error visible en UI."""
    doc = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)
    doc.status = KnowledgeDocumentStatus.failed
    doc.error_message = error_message[:2000]
    await db.flush()

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=None,
        action="knowledge.index_failed",
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata={"error": error_message[:500]},
    )
    return doc


async def delete_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID | None,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> None:
    """Borra el documento (cascade elimina los chunks) y el objeto en R2.

    Orden deliberado: audit + BD primero, R2 después. Si BD falla, no se
    borra el fichero en R2. Si R2 falla tras la BD, el registro ya no existe
    (objeto huérfano en R2 aceptable; job de retención GDPR lo limpiará).
    """
    doc = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()

    if doc is None:
        raise NotFoundError(f"KnowledgeDocument {document_id} not found")

    key = doc.source_file_key

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_DELETE,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata=file_metadata(doc.original_filename),
        request_ctx=request_ctx,
    )

    await db.delete(doc)
    await db.flush()

    from app.services import channel_chat_service

    await channel_chat_service.invalidate_response_cache_for_tenant(
        db,
        tenant_id=tenant_id,
    )

    storage = get_storage()
    await storage.delete(key)

    logger.info(
        "knowledge.document.deleted",
        document_id=str(document_id),
        tenant_id=str(tenant_id),
    )


async def request_reindex(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
    user_id: UUID | None,
    request_ctx: audit_service.AuditRequestContext | None = None,
) -> KnowledgeDocument:
    """Re-encola la indexación: status → pending y job ARQ."""
    from app.jobs.queue import enqueue_knowledge_indexing
    from app.services import channel_chat_service

    doc = await _get_orm(db, tenant_id=tenant_id, document_id=document_id)
    doc.status = KnowledgeDocumentStatus.pending
    doc.error_message = None
    await db.flush()

    await channel_chat_service.invalidate_response_cache_for_tenant(
        db,
        tenant_id=tenant_id,
    )

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_KNOWLEDGE_REINDEX,
        resource_type=RESOURCE_KNOWLEDGE_DOCUMENT,
        resource_id=document_id,
        metadata={},
        request_ctx=request_ctx,
    )

    await enqueue_knowledge_indexing(document_id, tenant_id, replace_existing=True)
    logger.info(
        "knowledge.document.reindex_requested",
        document_id=str(document_id),
        tenant_id=str(tenant_id),
    )
    return doc


async def _get_orm(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_id: UUID,
) -> KnowledgeDocument:
    """Carga el ORM object o lanza NotFoundError."""
    row = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"KnowledgeDocument {document_id} not found")
    return row
