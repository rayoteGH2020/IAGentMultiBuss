"""Orquestación de subida: enrutado por tipo de documento."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

from app.core.errors import ValidationError
from app.core.media_limits import MediaLimitExceeded, inspect_document
from app.jobs.queue import (
    enqueue_contract_processing,
    enqueue_insurance_processing,
    enqueue_invoice_processing,
    enqueue_ticket_processing,
)
from app.models import Contract, DocTypeCode, Insurance, Invoice, Ticket
from app.services import (
    contract_service,
    document_classification,
    document_type_confirm_service,
    insurance_service,
    invoice_service,
    ticket_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

DocumentKindResult = Literal["invoice", "ticket", "contract", "insurance"]


@dataclass(frozen=True, slots=True)
class DocumentIngestResult:
    kind: DocumentKindResult
    doc_type: DocTypeCode
    invoice: Invoice | None = None
    ticket: Ticket | None = None
    contract: Contract | None = None
    insurance: Insurance | None = None
    # True cuando el documento se guardó pero no se encoló por incumplir los
    # límites: existe en R2 y en BD para que el superadmin pueda revisarlo.
    rejected: bool = False
    # True cuando hay mismatch de tipo y se espera confirmación HTMX.
    awaiting_type_confirmation: bool = False

    @property
    def record_id(self) -> UUID:
        if self.invoice is not None:
            return self.invoice.id
        if self.ticket is not None:
            return self.ticket.id
        if self.contract is not None:
            return self.contract.id
        if self.insurance is not None:
            return self.insurance.id
        msg = "DocumentIngestResult has no record"
        raise RuntimeError(msg)


async def ingest_uploaded_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    doc_type: DocTypeCode,
    redis: object | None = None,
    ents: Entitlements | None = None,
) -> DocumentIngestResult:
    """Crea el stub correspondiente al tipo indicado y encola el job ARQ.

    Para factura/ticket: verifica el tipo (heurística → LLM si hace falta).
    Si hay mismatch con confianza alta, guarda el stub sin encolar y pide
    confirmación HTMX.

    Los límites de recursos se comprueban aquí, antes de encolar: si el
    documento no los cumple se sube igualmente a R2 y se registra en estado
    fallido, pero no llega al worker.
    """
    if redis is not None and ents is not None:
        from app.services import plan_quota_service

        await plan_quota_service.ensure_documents_upload(
            redis,
            ents,
            tenant_id,
            n_files=1,
        )

    rejection: MediaLimitExceeded | None = None
    try:
        await asyncio.to_thread(inspect_document, file_bytes, mime_type)
    except MediaLimitExceeded as exc:
        rejection = exc
        logger.warning(
            "document_ingest.rejected_by_limits",
            tenant_id=str(tenant_id),
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(file_bytes),
            error_code=exc.error_code.value,
            reason=exc.message,
        )

    verification = None
    if rejection is None and doc_type in {DocTypeCode.factura, DocTypeCode.ticket}:
        verification = await document_classification.verify_user_doc_type(
            db,
            file_bytes=file_bytes,
            mime_type=mime_type,
            tenant_id=tenant_id,
            user_choice=doc_type,
            source_filename=filename,
        )

    if doc_type == DocTypeCode.factura:
        return await _ingest_invoice(
            db,
            tenant_id=tenant_id,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
            rejection=rejection,
            verification=verification,
        )

    if doc_type == DocTypeCode.ticket:
        return await _ingest_ticket(
            db,
            tenant_id=tenant_id,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
            rejection=rejection,
            verification=verification,
        )

    if doc_type == DocTypeCode.contrato:
        contract = await contract_service.create_contract_from_upload(
            db,
            tenant_id=tenant_id,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
            doc_type=DocTypeCode.contrato,
        )
        await db.flush()
        if rejection is not None:
            await contract_service.mark_failed(
                db,
                contract_id=contract.id,
                tenant_id=tenant_id,
                error=rejection.message,
                error_code=rejection.error_code,
                detail=rejection.detail,
            )
            await db.flush()
            return DocumentIngestResult(
                kind="contract",
                doc_type=doc_type,
                contract=contract,
                rejected=True,
            )
        await enqueue_contract_processing(contract.id, tenant_id)
        logger.info(
            "document_ingest.contract",
            tenant_id=str(tenant_id),
            contract_id=str(contract.id),
            doc_type=doc_type.value,
        )
        return DocumentIngestResult(
            kind="contract",
            doc_type=doc_type,
            contract=contract,
        )

    if doc_type == DocTypeCode.seguro:
        insurance = await insurance_service.create_insurance_from_upload(
            db,
            tenant_id=tenant_id,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
            doc_type=DocTypeCode.seguro,
        )
        await db.flush()
        if rejection is not None:
            await insurance_service.mark_failed(
                db,
                insurance_id=insurance.id,
                tenant_id=tenant_id,
                error=rejection.message,
                error_code=rejection.error_code,
                detail=rejection.detail,
            )
            await db.flush()
            return DocumentIngestResult(
                kind="insurance",
                doc_type=doc_type,
                insurance=insurance,
                rejected=True,
            )
        await enqueue_insurance_processing(insurance.id, tenant_id)
        logger.info(
            "document_ingest.insurance",
            tenant_id=str(tenant_id),
            insurance_id=str(insurance.id),
            doc_type=doc_type.value,
        )
        return DocumentIngestResult(
            kind="insurance",
            doc_type=doc_type,
            insurance=insurance,
        )

    raise ValidationError(f"Unsupported document type: {doc_type.value!r}")


async def _ingest_invoice(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    rejection: MediaLimitExceeded | None,
    verification: document_classification.TypeVerificationResult | None,
) -> DocumentIngestResult:
    doc_type = DocTypeCode.factura
    invoice = await invoice_service.create_invoice_from_upload(
        db,
        tenant_id=tenant_id,
        filename=filename,
        file_bytes=file_bytes,
        mime_type=mime_type,
        doc_type=doc_type,
    )
    await db.flush()
    if rejection is not None:
        await invoice_service.mark_failed(
            db,
            invoice_id=invoice.id,
            tenant_id=tenant_id,
            error=rejection.message,
            error_code=rejection.error_code,
            detail=rejection.detail,
        )
        await db.flush()
        return DocumentIngestResult(
            kind="invoice",
            doc_type=doc_type,
            invoice=invoice,
            rejected=True,
        )

    if verification is not None and verification.needs_confirmation:
        invoice = await document_type_confirm_service.mark_invoice_awaiting_type_confirmation(
            db,
            invoice=invoice,
            verification=verification,
        )
        return DocumentIngestResult(
            kind="invoice",
            doc_type=doc_type,
            invoice=invoice,
            awaiting_type_confirmation=True,
        )

    await enqueue_invoice_processing(invoice.id, tenant_id)
    logger.info(
        "document_ingest.invoice",
        tenant_id=str(tenant_id),
        invoice_id=str(invoice.id),
        doc_type=doc_type.value,
    )
    return DocumentIngestResult(
        kind="invoice",
        doc_type=doc_type,
        invoice=invoice,
    )


async def _ingest_ticket(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    rejection: MediaLimitExceeded | None,
    verification: document_classification.TypeVerificationResult | None,
) -> DocumentIngestResult:
    doc_type = DocTypeCode.ticket
    ticket = await ticket_service.create_ticket_from_upload(
        db,
        tenant_id=tenant_id,
        filename=filename,
        file_bytes=file_bytes,
        mime_type=mime_type,
        doc_type=doc_type,
    )
    await db.flush()
    if rejection is not None:
        await ticket_service.mark_failed(
            db,
            ticket_id=ticket.id,
            tenant_id=tenant_id,
            error=rejection.message,
            error_code=rejection.error_code,
            detail=rejection.detail,
        )
        await db.flush()
        return DocumentIngestResult(
            kind="ticket",
            doc_type=doc_type,
            ticket=ticket,
            rejected=True,
        )

    if verification is not None and verification.needs_confirmation:
        ticket = await document_type_confirm_service.mark_ticket_awaiting_type_confirmation(
            db,
            ticket=ticket,
            verification=verification,
        )
        return DocumentIngestResult(
            kind="ticket",
            doc_type=doc_type,
            ticket=ticket,
            awaiting_type_confirmation=True,
        )

    await enqueue_ticket_processing(ticket.id, tenant_id)
    logger.info(
        "document_ingest.ticket",
        tenant_id=str(tenant_id),
        ticket_id=str(ticket.id),
        doc_type=doc_type.value,
    )
    return DocumentIngestResult(
        kind="ticket",
        doc_type=doc_type,
        ticket=ticket,
    )
