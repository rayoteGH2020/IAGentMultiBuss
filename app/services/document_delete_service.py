"""Borrado completo de documentos administrativos (BD + R2 + llm_call)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.storage import get_storage
from app.models import LLMCall
from app.models.document_processing_attempt import DocumentProcessingAttempt
from app.services import (
    audit_service,
    contract_service,
    insurance_service,
    invoice_service,
    ticket_service,
)

if TYPE_CHECKING:
    from app.services.audit_service import AuditRequestContext

logger = structlog.get_logger(__name__)

DocumentKindLiteral = Literal["invoice", "ticket", "contract", "insurance"]

ACTION_DOCUMENT_DELETE = "document.delete"
RESOURCE_DOCUMENT = "document"

_VALID_KINDS = frozenset({"invoice", "ticket", "contract", "insurance"})


@dataclass(frozen=True, slots=True)
class _LoadedDocument:
    entity: Any
    source_file_key: str | None
    source_filename: str | None
    llm_call_id: UUID | None


async def delete_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    request_ctx: AuditRequestContext | None = None,
) -> None:
    """Elimina el documento, sus intentos, el llm_call asociado y el objeto en R2.

    Orden: audit → BD (intentos, documento, llm_calls) → R2.
    Las líneas de factura se borran por CASCADE ORM/BD.
    Langfuse no se toca (solo telemetría; el trace_id queda huérfano).
    """
    if document_kind not in _VALID_KINDS:
        raise ValidationError("Tipo de documento no válido.")

    loaded = await _load_document(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    llm_call_ids = await _collect_llm_call_ids(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        primary_llm_call_id=loaded.llm_call_id,
    )

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_DOCUMENT_DELETE,
        resource_type=RESOURCE_DOCUMENT,
        resource_id=document_id,
        metadata={
            "document_kind": document_kind,
            "filename": loaded.source_filename,
            "had_r2_object": bool(loaded.source_file_key),
            "llm_calls_deleted": len(llm_call_ids),
        },
        request_ctx=request_ctx,
    )

    await db.execute(
        delete(DocumentProcessingAttempt).where(
            DocumentProcessingAttempt.tenant_id == tenant_id,
            DocumentProcessingAttempt.document_kind == document_kind,
            DocumentProcessingAttempt.document_id == document_id,
        )
    )
    await db.delete(loaded.entity)

    if llm_call_ids:
        await db.execute(
            delete(LLMCall).where(
                LLMCall.tenant_id == tenant_id,
                LLMCall.id.in_(llm_call_ids),
            )
        )

    await db.flush()

    if loaded.source_file_key:
        storage = get_storage()
        await storage.delete(loaded.source_file_key)

    logger.info(
        "document.deleted",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        r2_deleted=bool(loaded.source_file_key),
        llm_calls_deleted=len(llm_call_ids),
    )


async def _load_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> _LoadedDocument:
    entity: Any
    if document_kind == "invoice":
        entity = await invoice_service.get_invoice(db, tenant_id, document_id)
    elif document_kind == "ticket":
        entity = await ticket_service.get_ticket(db, tenant_id, document_id)
    elif document_kind == "contract":
        entity = await contract_service.get_contract(db, tenant_id, document_id)
    elif document_kind == "insurance":
        entity = await insurance_service.get_insurance(db, tenant_id, document_id)
    else:
        raise ValidationError("Tipo de documento no válido.")

    return _LoadedDocument(
        entity=entity,
        source_file_key=entity.source_file_key,
        source_filename=entity.source_filename,
        llm_call_id=entity.llm_call_id,
    )


async def _collect_llm_call_ids(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: str,
    document_id: UUID,
    primary_llm_call_id: UUID | None,
) -> list[UUID]:
    """Une el llm_call del documento con los referenciados en intentos."""
    ids: set[UUID] = set()
    if primary_llm_call_id is not None:
        ids.add(primary_llm_call_id)

    result = await db.execute(
        select(DocumentProcessingAttempt.llm_call_id).where(
            DocumentProcessingAttempt.tenant_id == tenant_id,
            DocumentProcessingAttempt.document_kind == document_kind,
            DocumentProcessingAttempt.document_id == document_id,
            DocumentProcessingAttempt.llm_call_id.is_not(None),
        )
    )
    for call_id in result.scalars().all():
        if call_id is not None:
            ids.add(call_id)
    return list(ids)
