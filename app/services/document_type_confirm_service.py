"""Confirmación HTMX de tipo documental antes de encolar extracción cara."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog

from app.core.document_processing_errors import DocumentErrorCode
from app.core.errors import ValidationError
from app.jobs.queue import enqueue_invoice_processing, enqueue_ticket_processing
from app.models import DocTypeCode, InvoiceStatus, Ticket, TicketStatus
from app.services import (
    document_quota_service,
    entitlement_service,
    invoice_service,
    ticket_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Invoice
    from app.services.document_classification import TypeVerificationResult

logger = structlog.get_logger(__name__)

TYPE_CONFIRM_META_KEY = "_type_confirm"

ConfirmChoice = Literal["keep", "suggested"]
ConfirmKind = Literal["invoice", "ticket"]


@dataclass(frozen=True, slots=True)
class TypeConfirmMeta:
    user_choice: DocTypeCode
    suggested: DocTypeCode
    confidence: float


@dataclass(frozen=True, slots=True)
class ConfirmTypeResult:
    kind: ConfirmKind
    document_id: UUID


def build_type_confirm_meta(verification: TypeVerificationResult) -> dict[str, Any]:
    if verification.detected is None or verification.confidence is None:
        msg = "Type verification missing detected type for confirmation"
        raise ValidationError(msg)
    return {
        TYPE_CONFIRM_META_KEY: {
            "user_choice": verification.user_choice.value,
            "suggested": verification.detected.value,
            "confidence": verification.confidence,
        },
    }


def parse_type_confirm_meta(raw: dict[str, Any] | None) -> TypeConfirmMeta | None:
    if not raw or not isinstance(raw, dict):
        return None
    payload = raw.get(TYPE_CONFIRM_META_KEY)
    if not isinstance(payload, dict):
        return None
    try:
        return TypeConfirmMeta(
            user_choice=DocTypeCode(str(payload["user_choice"])),
            suggested=DocTypeCode(str(payload["suggested"])),
            confidence=float(payload["confidence"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def type_confirm_user_message(
    *,
    filename: str | None,
    user_choice: DocTypeCode,
    suggested: DocTypeCode,
) -> str:
    display = (filename or "").strip() or "documento"
    return (
        f'Detectamos que "{display}" parece un {suggested.value}, '
        f"pero lo subiste como {user_choice.value}. "
        "Confirma el tipo antes de procesarlo."
    )


async def mark_invoice_awaiting_type_confirmation(
    db: AsyncSession,
    *,
    invoice: Invoice,
    verification: TypeVerificationResult,
) -> Invoice:
    meta = build_type_confirm_meta(verification)
    invoice.status = InvoiceStatus.pending
    invoice.error_code = DocumentErrorCode.type_confirmation_required.value
    invoice.error_message = type_confirm_user_message(
        filename=invoice.source_filename,
        user_choice=verification.user_choice,
        suggested=verification.detected,  # type: ignore[arg-type]
    )
    invoice.raw_extraction = meta
    await db.flush()
    logger.info(
        "document_type_confirm.awaiting",
        kind="invoice",
        document_id=str(invoice.id),
        user_choice=verification.user_choice.value,
        suggested=verification.detected.value if verification.detected else None,
    )
    return invoice


async def mark_ticket_awaiting_type_confirmation(
    db: AsyncSession,
    *,
    ticket: Ticket,
    verification: TypeVerificationResult,
) -> Ticket:
    meta = build_type_confirm_meta(verification)
    ticket.status = TicketStatus.pending
    ticket.error_code = DocumentErrorCode.type_confirmation_required.value
    ticket.error_message = type_confirm_user_message(
        filename=ticket.source_filename,
        user_choice=verification.user_choice,
        suggested=verification.detected,  # type: ignore[arg-type]
    )
    ticket.raw_extraction = meta
    await db.flush()
    logger.info(
        "document_type_confirm.awaiting",
        kind="ticket",
        document_id=str(ticket.id),
        user_choice=verification.user_choice.value,
        suggested=verification.detected.value if verification.detected else None,
    )
    return ticket


async def confirm_document_type(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    kind: ConfirmKind,
    document_id: UUID,
    choice: ConfirmChoice,
) -> ConfirmTypeResult:
    """Confirma el tipo: mantiene el elegido o cambia al sugerido y encola.

    El documento a la espera de confirmar no tiene reserva de cupo: se reserva aquí,
    sobre el documento que se va a procesar; sin hueco, queda en ``quota_pending``.
    """
    if kind == "invoice":
        invoice = await invoice_service.get_invoice(db, tenant_id, document_id)
        meta = _pending_meta(invoice)
        if choice == "keep" or meta.suggested == DocTypeCode.factura:
            return await _process_as_is(db, tenant_id=tenant_id, document=invoice, meta=meta)
        source_key = _require_file(invoice)
        ticket = await ticket_service.create_ticket_from_existing_storage(
            db,
            tenant_id=tenant_id,
            source_file_key=source_key,
            source_filename=invoice.source_filename or "document",
            source_mime=invoice.source_mime or "application/pdf",
            doc_type=DocTypeCode.ticket,
        )
        return await _switch(db, tenant_id=tenant_id, source=invoice, target=ticket)

    ticket = await ticket_service.get_ticket(db, tenant_id, document_id)
    meta = _pending_meta(ticket)
    if choice == "keep" or meta.suggested == DocTypeCode.ticket:
        return await _process_as_is(db, tenant_id=tenant_id, document=ticket, meta=meta)
    source_key = _require_file(ticket)
    invoice = await invoice_service.create_invoice_from_existing_storage(
        db,
        tenant_id=tenant_id,
        source_file_key=source_key,
        source_filename=ticket.source_filename or "document",
        source_mime=ticket.source_mime or "application/pdf",
        doc_type=DocTypeCode.factura,
    )
    return await _switch(db, tenant_id=tenant_id, source=ticket, target=invoice)


def _pending_meta(document: Invoice | Ticket) -> TypeConfirmMeta:
    meta = parse_type_confirm_meta(document.raw_extraction)
    if document.error_code != DocumentErrorCode.type_confirmation_required.value or meta is None:
        raise ValidationError("Este documento no espera confirmación de tipo.")
    return meta


def _require_file(document: Invoice | Ticket) -> str:
    if not document.source_file_key:
        raise ValidationError("El documento no tiene fichero almacenado.")
    return document.source_file_key


def _kind(document: Invoice | Ticket) -> ConfirmKind:
    return "ticket" if isinstance(document, Ticket) else "invoice"


async def _reserve_and_enqueue(
    db: AsyncSession, *, tenant_id: UUID, document: Invoice | Ticket
) -> None:
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    if not await document_quota_service.reserve_or_hold(db, ents, document):
        return
    if isinstance(document, Ticket):
        await enqueue_ticket_processing(document.id, tenant_id)
    else:
        await enqueue_invoice_processing(document.id, tenant_id)


async def _process_as_is(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document: Invoice | Ticket,
    meta: TypeConfirmMeta,
) -> ConfirmTypeResult:
    document.error_code = None
    document.error_message = None
    document.raw_extraction = None
    if isinstance(document, Ticket):
        document.status = TicketStatus.processing
    else:
        document.status = InvoiceStatus.processing
    await db.flush()
    await _reserve_and_enqueue(db, tenant_id=tenant_id, document=document)
    logger.info(
        "document_type_confirm.keep",
        kind=_kind(document),
        document_id=str(document.id),
        doc_type=meta.user_choice.value,
    )
    return ConfirmTypeResult(kind=_kind(document), document_id=document.id)


async def _switch(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    source: Invoice | Ticket,
    target: Invoice | Ticket,
) -> ConfirmTypeResult:
    """Procesa el fichero como el tipo sugerido y oculta el registro original.

    El original está en ``pending`` (a la espera de confirmar), así que se oculta
    directamente: ``dismiss_from_panel`` solo admite documentos fallidos.
    """
    target.file_sha256 = source.file_sha256
    source.file_sha256 = None
    source.dismissed_at = datetime.now(tz=UTC)
    source.updated_at = source.dismissed_at
    await db.flush()
    await _reserve_and_enqueue(db, tenant_id=tenant_id, document=target)
    logger.info(
        "document_type_confirm.switch",
        from_kind=_kind(source),
        to_kind=_kind(target),
        from_id=str(source.id),
        to_id=str(target.id),
    )
    return ConfirmTypeResult(kind=_kind(target), document_id=target.id)
