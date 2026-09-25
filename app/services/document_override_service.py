"""Revisión y procesado excepcional de documentos rechazados (consola SADM).

Un documento rechazado por límites es un callejón sin salida para el cliente:
la UI no le deja reintentar y el mensaje le remite al administrador. Este
servicio es la otra mitad de ese contrato: permite al superadmin ver el
original, saber cuánto costará y autorizar el procesado saltándose los límites
de negocio —nunca el techo duro que protege al worker—.

Las lecturas son cross-tenant y se apoyan en la política RLS permisiva
`superadmin_select`, activada por transacción. Las escrituras se hacen siempre
con `app.current_tenant` del tenant dueño del documento, así que la política de
aislamiento normal sigue aplicando.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import structlog
from sqlalchemy import select, text

from app.config import get_settings
from app.core.db import set_tenant_context
from app.core.document_processing_errors import is_retryable
from app.core.errors import NotFoundError, ValidationError
from app.core.media_limits import PDF_MIME, MediaLimitExceeded, pdf_page_count
from app.core.storage import get_storage
from app.jobs.queue import (
    enqueue_contract_processing,
    enqueue_insurance_processing,
    enqueue_invoice_processing,
    enqueue_ticket_processing,
)
from app.models import (
    Contract,
    ContractStatus,
    Insurance,
    InsuranceStatus,
    Invoice,
    InvoiceStatus,
    Tenant,
    Ticket,
    TicketStatus,
)
from app.services import (
    contract_service,
    insurance_service,
    invoice_service,
    processing_charge_service,
    ticket_service,
)
from app.services.processing_charge_service import ProcessingEstimate

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

DocumentKindLiteral = Literal["invoice", "ticket", "contract", "insurance"]


@dataclass(frozen=True, slots=True)
class RejectedDocument:
    """Documento pendiente de decisión del superadmin."""

    kind: DocumentKindLiteral
    id: UUID
    tenant_id: UUID
    tenant_name: str
    source_filename: str | None
    source_mime: str | None
    source_file_key: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def detail_url(self) -> str:
        return f"/sadm/documents/{self.kind}/{self.id}"


@dataclass(frozen=True, slots=True)
class RejectedDocumentReview:
    """Ficha de revisión: documento, tamaño real y coste de procesarlo."""

    document: RejectedDocument
    pages: int
    estimate: ProcessingEstimate
    file_url: str
    over_hard_limit: bool


async def enable_superadmin_lookup(db: AsyncSession) -> None:
    """Activa la política RLS de lectura cross-tenant para esta transacción.

    `is_local=true`: el flag muere en el commit/rollback, así que no puede
    filtrarse a otra petición que reutilice la conexión del pool.
    """
    await db.execute(text("SELECT set_config('app.superadmin_lookup', 'true', true)"))


async def list_rejected_documents(db: AsyncSession, *, limit: int = 100) -> list[RejectedDocument]:
    """Documentos fallidos por límites, de todos los tenants, recientes primero."""
    await enable_superadmin_lookup(db)

    invoice_stmt = (
        select(Invoice, Tenant.name)
        .join(Tenant, Tenant.id == Invoice.tenant_id)
        .where(Invoice.status == InvoiceStatus.failed, Invoice.error_code.is_not(None))
        .order_by(Invoice.updated_at.desc())
        .limit(limit)
    )
    ticket_stmt = (
        select(Ticket, Tenant.name)
        .join(Tenant, Tenant.id == Ticket.tenant_id)
        .where(Ticket.status == TicketStatus.failed, Ticket.error_code.is_not(None))
        .order_by(Ticket.updated_at.desc())
        .limit(limit)
    )
    contract_stmt = (
        select(Contract, Tenant.name)
        .join(Tenant, Tenant.id == Contract.tenant_id)
        .where(Contract.status == ContractStatus.failed, Contract.error_code.is_not(None))
        .order_by(Contract.updated_at.desc())
        .limit(limit)
    )
    insurance_stmt = (
        select(Insurance, Tenant.name)
        .join(Tenant, Tenant.id == Insurance.tenant_id)
        .where(Insurance.status == InsuranceStatus.failed, Insurance.error_code.is_not(None))
        .order_by(Insurance.updated_at.desc())
        .limit(limit)
    )

    rows: list[RejectedDocument] = [
        _row_from_invoice(invoice, tenant_name)
        for invoice, tenant_name in (await db.execute(invoice_stmt)).all()
        if not is_retryable(invoice.error_code)
    ]
    rows.extend(
        _row_from_ticket(ticket, tenant_name)
        for ticket, tenant_name in (await db.execute(ticket_stmt)).all()
        if not is_retryable(ticket.error_code)
    )
    rows.extend(
        _row_from_contract(contract, tenant_name)
        for contract, tenant_name in (await db.execute(contract_stmt)).all()
        if not is_retryable(contract.error_code)
    )
    rows.extend(
        _row_from_insurance(insurance, tenant_name)
        for insurance, tenant_name in (await db.execute(insurance_stmt)).all()
        if not is_retryable(insurance.error_code)
    )
    rows.sort(key=lambda row: row.updated_at, reverse=True)
    return rows[:limit]


async def get_rejected_document(
    db: AsyncSession,
    *,
    kind: DocumentKindLiteral,
    document_id: UUID,
) -> RejectedDocument:
    """Un documento rechazado concreto, sin conocer su tenant de antemano."""
    await enable_superadmin_lookup(db)

    if kind == "invoice":
        stmt = (
            select(Invoice, Tenant.name)
            .join(Tenant, Tenant.id == Invoice.tenant_id)
            .where(Invoice.id == document_id)
        )
        row = (await db.execute(stmt)).first()
        if row is None:
            raise NotFoundError("Documento no encontrado.")
        return _row_from_invoice(row[0], row[1])

    if kind == "ticket":
        stmt_ticket = (
            select(Ticket, Tenant.name)
            .join(Tenant, Tenant.id == Ticket.tenant_id)
            .where(Ticket.id == document_id)
        )
        row_ticket = (await db.execute(stmt_ticket)).first()
        if row_ticket is None:
            raise NotFoundError("Documento no encontrado.")
        return _row_from_ticket(row_ticket[0], row_ticket[1])

    if kind == "contract":
        stmt_contract = (
            select(Contract, Tenant.name)
            .join(Tenant, Tenant.id == Contract.tenant_id)
            .where(Contract.id == document_id)
        )
        row_contract = (await db.execute(stmt_contract)).first()
        if row_contract is None:
            raise NotFoundError("Documento no encontrado.")
        return _row_from_contract(row_contract[0], row_contract[1])

    stmt_insurance = (
        select(Insurance, Tenant.name)
        .join(Tenant, Tenant.id == Insurance.tenant_id)
        .where(Insurance.id == document_id)
    )
    row_insurance = (await db.execute(stmt_insurance)).first()
    if row_insurance is None:
        raise NotFoundError("Documento no encontrado.")
    return _row_from_insurance(row_insurance[0], row_insurance[1])


async def build_review(
    db: AsyncSession,
    *,
    kind: DocumentKindLiteral,
    document_id: UUID,
) -> RejectedDocumentReview:
    """Reúne todo lo que el superadmin necesita para decidir.

    Mide el documento real descargándolo de R2 en lugar de fiarse del mensaje
    de error: la decisión de gastar dinero se toma sobre el fichero, no sobre
    un texto guardado hace días.
    """
    document = await get_rejected_document(db, kind=kind, document_id=document_id)
    pages = await _measure_pages(document)
    estimate = processing_charge_service.estimate_processing(pages)
    hard_limit = get_settings().document_override_max_pdf_pages
    return RejectedDocumentReview(
        document=document,
        pages=pages,
        estimate=estimate,
        file_url=f"/sadm/documents/{kind}/{document_id}/file",
        over_hard_limit=pages > hard_limit,
    )


async def original_file_url(
    db: AsyncSession,
    *,
    kind: DocumentKindLiteral,
    document_id: UUID,
) -> str:
    """URL prefirmada del documento original, para revisarlo antes de decidir."""
    document = await get_rejected_document(db, kind=kind, document_id=document_id)
    key = _source_key(document)
    storage = get_storage()
    url = await storage.presigned_url_get(key)
    logger.info(
        "sadm.document.original_viewed",
        tenant_id=str(document.tenant_id),
        document_kind=kind,
        document_id=str(document_id),
    )
    return url


async def authorize_processing(
    db: AsyncSession,
    *,
    kind: DocumentKindLiteral,
    document_id: UUID,
    authorized_by: UUID,
    reason: str | None = None,
) -> RejectedDocumentReview:
    """Autoriza el procesado saltándose los límites de negocio.

    Deja constancia del cargo antes de encolar: si el worker falla, el registro
    de que alguien autorizó gasto para este documento no se pierde.

    Raises:
        ValidationError: El documento no está rechazado o supera el techo duro.
    """
    review = await build_review(db, kind=kind, document_id=document_id)
    document = review.document

    if is_retryable(document.error_code):
        raise ValidationError(
            "Este documento no está rechazado por límites; usa el reintento normal.",
        )
    if review.over_hard_limit:
        hard_limit = get_settings().document_override_max_pdf_pages
        raise ValidationError(
            f"El documento tiene {review.pages} páginas y el techo de procesado "
            f"excepcional son {hard_limit}. Divídelo antes de procesarlo.",
        )

    # A partir de aquí se escribe: contexto de tenant explícito para que la
    # política de aislamiento normal valide cada INSERT/UPDATE.
    await set_tenant_context(db, str(document.tenant_id))

    await processing_charge_service.create_authorized_charge(
        db,
        tenant_id=document.tenant_id,
        document_kind=kind,
        document_id=document_id,
        estimate=review.estimate,
        authorized_by=authorized_by,
        reason=reason,
    )
    await _reset_for_processing(db, kind=kind, document=document)
    await db.commit()

    max_pages = get_settings().document_override_max_pdf_pages
    if kind == "invoice":
        await enqueue_invoice_processing(
            document_id,
            document.tenant_id,
            max_pdf_pages=max_pages,
            replace_existing=True,
        )
    elif kind == "ticket":
        await enqueue_ticket_processing(
            document_id,
            document.tenant_id,
            max_pdf_pages=max_pages,
            replace_existing=True,
        )
    elif kind == "contract":
        await enqueue_contract_processing(
            document_id,
            document.tenant_id,
            max_pdf_pages=max_pages,
            replace_existing=True,
        )
    else:
        await enqueue_insurance_processing(
            document_id,
            document.tenant_id,
            max_pdf_pages=max_pages,
            replace_existing=True,
        )

    logger.info(
        "sadm.document.override_authorized",
        tenant_id=str(document.tenant_id),
        document_kind=kind,
        document_id=str(document_id),
        pages=review.pages,
        estimated_cost_eur=str(review.estimate.provider_cost_eur),
        authorized_by=str(authorized_by),
    )
    return review


async def _reset_for_processing(
    db: AsyncSession,
    *,
    kind: DocumentKindLiteral,
    document: RejectedDocument,
) -> None:
    """Devuelve el documento a la cola limpiando el rechazo anterior."""
    if kind == "invoice":
        invoice = await invoice_service.get_invoice(db, document.tenant_id, document.id)
        invoice.status = InvoiceStatus.processing
        invoice.error_code = None
        invoice.error_message = None
        invoice.dismissed_at = None
    elif kind == "ticket":
        ticket = await ticket_service.get_ticket(db, document.tenant_id, document.id)
        ticket.status = TicketStatus.processing
        ticket.error_code = None
        ticket.error_message = None
        ticket.dismissed_at = None
    elif kind == "contract":
        contract = await contract_service.get_contract(db, document.tenant_id, document.id)
        contract.status = ContractStatus.processing
        contract.error_code = None
        contract.error_message = None
        contract.dismissed_at = None
    else:
        insurance = await insurance_service.get_insurance(db, document.tenant_id, document.id)
        insurance.status = InsuranceStatus.processing
        insurance.error_code = None
        insurance.error_message = None
        insurance.dismissed_at = None
    await db.flush()


async def _measure_pages(document: RejectedDocument) -> int:
    """Páginas reales del documento; 1 para imágenes o si el PDF es ilegible."""
    if document.source_mime != PDF_MIME or not document.source_file_key:
        return 1
    storage = get_storage()
    file_bytes = await storage.download_bytes(document.source_file_key)
    try:
        return await asyncio.to_thread(pdf_page_count, file_bytes)
    except MediaLimitExceeded:
        return 1


def _source_key(document: RejectedDocument) -> str:
    if not document.source_file_key:
        raise NotFoundError("El documento no tiene fichero original asociado.")
    return document.source_file_key


def _row_from_invoice(invoice: Invoice, tenant_name: str) -> RejectedDocument:
    return RejectedDocument(
        kind="invoice",
        id=invoice.id,
        tenant_id=invoice.tenant_id,
        tenant_name=tenant_name,
        source_filename=invoice.source_filename,
        source_mime=invoice.source_mime,
        source_file_key=invoice.source_file_key,
        error_code=invoice.error_code,
        error_message=invoice.error_message,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )


def _row_from_ticket(ticket: Ticket, tenant_name: str) -> RejectedDocument:
    return RejectedDocument(
        kind="ticket",
        id=ticket.id,
        tenant_id=ticket.tenant_id,
        tenant_name=tenant_name,
        source_filename=ticket.source_filename,
        source_mime=ticket.source_mime,
        source_file_key=ticket.source_file_key,
        error_code=ticket.error_code,
        error_message=ticket.error_message,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def _row_from_contract(contract: Contract, tenant_name: str) -> RejectedDocument:
    return RejectedDocument(
        kind="contract",
        id=contract.id,
        tenant_id=contract.tenant_id,
        tenant_name=tenant_name,
        source_filename=contract.source_filename,
        source_mime=contract.source_mime,
        source_file_key=contract.source_file_key,
        error_code=contract.error_code,
        error_message=contract.error_message,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


def _row_from_insurance(insurance: Insurance, tenant_name: str) -> RejectedDocument:
    return RejectedDocument(
        kind="insurance",
        id=insurance.id,
        tenant_id=insurance.tenant_id,
        tenant_name=tenant_name,
        source_filename=insurance.source_filename,
        source_mime=insurance.source_mime,
        source_file_key=insurance.source_file_key,
        error_code=insurance.error_code,
        error_message=insurance.error_message,
        created_at=insurance.created_at,
        updated_at=insurance.updated_at,
    )


__all__ = [
    "RejectedDocument",
    "RejectedDocumentReview",
    "authorize_processing",
    "build_review",
    "list_rejected_documents",
    "original_file_url",
]
