"""Confirmación HTMX de tipo documental antes de encolar extracción cara."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import structlog

from app.core.document_processing_errors import DocumentErrorCode
from app.core.errors import ValidationError
from app.jobs.queue import enqueue_invoice_processing, enqueue_ticket_processing
from app.models import DocTypeCode, InvoiceStatus, TicketStatus
from app.services import invoice_service, ticket_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models import Invoice, Ticket
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
    """Confirma el tipo: mantiene el elegido o cambia al sugerido y encola."""
    if kind == "invoice":
        return await _confirm_invoice(
            db,
            tenant_id=tenant_id,
            invoice_id=document_id,
            choice=choice,
        )
    return await _confirm_ticket(
        db,
        tenant_id=tenant_id,
        ticket_id=document_id,
        choice=choice,
    )


async def _confirm_invoice(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    invoice_id: UUID,
    choice: ConfirmChoice,
) -> ConfirmTypeResult:
    invoice = await invoice_service.get_invoice(db, tenant_id, invoice_id)
    meta = parse_type_confirm_meta(invoice.raw_extraction)
    if invoice.error_code != DocumentErrorCode.type_confirmation_required.value or meta is None:
        raise ValidationError("Este documento no espera confirmación de tipo.")

    if choice == "keep":
        invoice.error_code = None
        invoice.error_message = None
        invoice.raw_extraction = None
        invoice.status = InvoiceStatus.processing
        await db.flush()
        await enqueue_invoice_processing(invoice.id, tenant_id)
        logger.info(
            "document_type_confirm.keep",
            kind="invoice",
            document_id=str(invoice.id),
            doc_type=meta.user_choice.value,
        )
        return ConfirmTypeResult(kind="invoice", document_id=invoice.id)

    if meta.suggested == DocTypeCode.factura:
        invoice.error_code = None
        invoice.error_message = None
        invoice.raw_extraction = None
        invoice.status = InvoiceStatus.processing
        await db.flush()
        await enqueue_invoice_processing(invoice.id, tenant_id)
        return ConfirmTypeResult(kind="invoice", document_id=invoice.id)

    source_file_key = invoice.source_file_key
    if not source_file_key:
        raise ValidationError("El documento no tiene fichero almacenado.")
    ticket = await ticket_service.create_ticket_from_existing_storage(
        db,
        tenant_id=tenant_id,
        source_file_key=source_file_key,
        source_filename=invoice.source_filename or "document",
        source_mime=invoice.source_mime or "application/pdf",
        doc_type=DocTypeCode.ticket,
    )
    from app.services import document_processing_service

    await document_processing_service.dismiss_from_panel(
        db,
        tenant_id=tenant_id,
        document_kind="invoice",
        document_id=invoice.id,
    )
    await enqueue_ticket_processing(ticket.id, tenant_id)
    logger.info(
        "document_type_confirm.switch",
        from_kind="invoice",
        to_kind="ticket",
        from_id=str(invoice.id),
        to_id=str(ticket.id),
    )
    return ConfirmTypeResult(kind="ticket", document_id=ticket.id)


async def _confirm_ticket(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    ticket_id: UUID,
    choice: ConfirmChoice,
) -> ConfirmTypeResult:
    ticket = await ticket_service.get_ticket(db, tenant_id, ticket_id)
    meta = parse_type_confirm_meta(ticket.raw_extraction)
    if ticket.error_code != DocumentErrorCode.type_confirmation_required.value or meta is None:
        raise ValidationError("Este documento no espera confirmación de tipo.")

    if choice == "keep":
        ticket.error_code = None
        ticket.error_message = None
        ticket.raw_extraction = None
        ticket.status = TicketStatus.processing
        await db.flush()
        await enqueue_ticket_processing(ticket.id, tenant_id)
        logger.info(
            "document_type_confirm.keep",
            kind="ticket",
            document_id=str(ticket.id),
            doc_type=meta.user_choice.value,
        )
        return ConfirmTypeResult(kind="ticket", document_id=ticket.id)

    if meta.suggested == DocTypeCode.ticket:
        ticket.error_code = None
        ticket.error_message = None
        ticket.raw_extraction = None
        ticket.status = TicketStatus.processing
        await db.flush()
        await enqueue_ticket_processing(ticket.id, tenant_id)
        return ConfirmTypeResult(kind="ticket", document_id=ticket.id)

    source_file_key = ticket.source_file_key
    if not source_file_key:
        raise ValidationError("El documento no tiene fichero almacenado.")
    invoice = await invoice_service.create_invoice_from_existing_storage(
        db,
        tenant_id=tenant_id,
        source_file_key=source_file_key,
        source_filename=ticket.source_filename or "document",
        source_mime=ticket.source_mime or "application/pdf",
        doc_type=DocTypeCode.factura,
    )
    from app.services import document_processing_service

    await document_processing_service.dismiss_from_panel(
        db,
        tenant_id=tenant_id,
        document_kind="ticket",
        document_id=ticket.id,
    )
    await enqueue_invoice_processing(invoice.id, tenant_id)
    logger.info(
        "document_type_confirm.switch",
        from_kind="ticket",
        to_kind="invoice",
        from_id=str(ticket.id),
        to_id=str(invoice.id),
    )
    return ConfirmTypeResult(kind="invoice", document_id=invoice.id)
