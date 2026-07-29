"""Reintento y ocultación de documentos fallidos en el panel /documents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import func, select

from app.core.document_processing_errors import is_retryable
from app.core.errors import ValidationError
from app.jobs.queue import enqueue_invoice_processing, enqueue_ticket_processing
from app.models.document_processing_attempt import (
    DocumentKind,
    DocumentProcessingAttempt,
    ProcessingAttemptStatus,
)
from app.models.invoice import InvoiceStatus
from app.models.ticket import TicketStatus

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

DocumentKindLiteral = Literal["invoice", "ticket"]

_NOT_RETRYABLE_MESSAGE = (
    "Este documento no se puede reintentar porque no cumple los límites de procesado. "
    "Ponte en contacto con el administrador del sitio."
)


def _ensure_retryable(error_code: str | None) -> None:
    """Impide reintentar rechazos que volverían a fallar con el mismo fichero."""
    if not is_retryable(error_code):
        raise ValidationError(_NOT_RETRYABLE_MESSAGE)


async def _next_attempt_number(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> int:
    stmt = select(func.coalesce(func.max(DocumentProcessingAttempt.attempt_number), 0)).where(
        DocumentProcessingAttempt.tenant_id == tenant_id,
        DocumentProcessingAttempt.document_kind == document_kind,
        DocumentProcessingAttempt.document_id == document_id,
    )
    result = await db.execute(stmt)
    current_max = result.scalar_one()
    return int(current_max) + 1


async def _open_processing_attempt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> DocumentProcessingAttempt | None:
    stmt = (
        select(DocumentProcessingAttempt)
        .where(
            DocumentProcessingAttempt.tenant_id == tenant_id,
            DocumentProcessingAttempt.document_kind == document_kind,
            DocumentProcessingAttempt.document_id == document_id,
            DocumentProcessingAttempt.status == ProcessingAttemptStatus.processing.value,
        )
        .order_by(DocumentProcessingAttempt.attempt_number.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def begin_processing_attempt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> DocumentProcessingAttempt:
    """Abre un intento de extracción (idempotente si ya hay uno en curso)."""
    open_attempt = await _open_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    if open_attempt is not None:
        return open_attempt

    attempt_number = await _next_attempt_number(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    attempt = DocumentProcessingAttempt(
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        attempt_number=attempt_number,
        status=ProcessingAttemptStatus.processing.value,
    )
    db.add(attempt)
    await db.flush()
    logger.info(
        "document_processing.attempt_started",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        attempt_number=attempt_number,
    )
    return attempt


async def finalize_processing_attempt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    status: ProcessingAttemptStatus,
    llm_call_id: UUID | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
) -> None:
    """Cierra el intento en curso o crea uno retroactivo si no existía."""
    attempt = await _open_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    now = datetime.now(tz=UTC)
    if attempt is None:
        attempt_number = await _next_attempt_number(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        attempt = DocumentProcessingAttempt(
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
            attempt_number=attempt_number,
            status=status.value,
            llm_call_id=llm_call_id,
            error_message=error_message,
            error_code=error_code,
            finished_at=now,
        )
        db.add(attempt)
    else:
        attempt.status = status.value
        attempt.llm_call_id = llm_call_id
        attempt.error_message = error_message
        attempt.error_code = error_code
        attempt.finished_at = now
    await db.flush()
    logger.info(
        "document_processing.attempt_finished",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        attempt_number=attempt.attempt_number,
        status=status.value,
        llm_call_id=str(llm_call_id) if llm_call_id else None,
        error_code=error_code,
    )


async def dismiss_from_panel(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> None:
    """Oculta un documento fallido del listado sin borrarlo de BD."""
    if document_kind == DocumentKind.invoice.value:
        from app.services import invoice_service

        invoice = await invoice_service.get_invoice(db, tenant_id, document_id)
        if invoice.status != InvoiceStatus.failed:
            raise ValidationError("Solo se pueden ocultar documentos con error de procesamiento.")
        if invoice.dismissed_at is not None:
            return
        invoice.dismissed_at = datetime.now(tz=UTC)
        invoice.updated_at = datetime.now(tz=UTC)
        await db.flush()
        logger.info(
            "document.dismissed_from_panel",
            tenant_id=str(tenant_id),
            document_kind=document_kind,
            document_id=str(document_id),
        )
        return

    from app.services import ticket_service

    ticket = await ticket_service.get_ticket(db, tenant_id, document_id)
    if ticket.status != TicketStatus.failed:
        raise ValidationError("Solo se pueden ocultar documentos con error de procesamiento.")
    if ticket.dismissed_at is not None:
        return
    ticket.dismissed_at = datetime.now(tz=UTC)
    ticket.updated_at = datetime.now(tz=UTC)
    await db.flush()
    logger.info(
        "document.dismissed_from_panel",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
    )


async def retry_processing(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> None:
    """Reencola extracción sobre el mismo registro y fichero R2."""
    if document_kind == DocumentKind.invoice.value:
        from app.services import invoice_service

        invoice = await invoice_service.get_invoice(db, tenant_id, document_id)
        if invoice.status != InvoiceStatus.failed:
            raise ValidationError("Solo se puede reintentar un documento en estado de error.")
        if not invoice.source_file_key:
            raise ValidationError("El documento no tiene fichero asociado para reintentar.")
        _ensure_retryable(invoice.error_code)
        invoice.status = InvoiceStatus.processing
        invoice.error_code = None
        invoice.error_message = None
        invoice.dismissed_at = None
        invoice.updated_at = datetime.now(tz=UTC)
        await db.flush()
        await begin_processing_attempt(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        try:
            await enqueue_invoice_processing(invoice.id, tenant_id)
        except Exception as exc:
            raise RuntimeError("No se pudo encolar el reintento.") from exc
        logger.info(
            "document.retry_enqueued",
            tenant_id=str(tenant_id),
            document_kind=document_kind,
            document_id=str(document_id),
        )
        return

    from app.services import ticket_service

    ticket = await ticket_service.get_ticket(db, tenant_id, document_id)
    if ticket.status != TicketStatus.failed:
        raise ValidationError("Solo se puede reintentar un documento en estado de error.")
    if not ticket.source_file_key:
        raise ValidationError("El documento no tiene fichero asociado para reintentar.")
    _ensure_retryable(ticket.error_code)
    ticket.status = TicketStatus.processing
    ticket.error_code = None
    ticket.error_message = None
    ticket.dismissed_at = None
    ticket.updated_at = datetime.now(tz=UTC)
    await db.flush()
    await begin_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    try:
        await enqueue_ticket_processing(ticket.id, tenant_id)
    except Exception as exc:
        raise RuntimeError("No se pudo encolar el reintento.") from exc
    logger.info(
        "document.retry_enqueued",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
    )
