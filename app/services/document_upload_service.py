"""Orquestación de subida: clasificación de tipo y enrutado a factura o ticket."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

from app.jobs.queue import enqueue_invoice_processing, enqueue_ticket_processing
from app.models import DocTypeCode, Invoice, Ticket
from app.services import invoice_service, ticket_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentIngestResult:
    kind: Literal["invoice", "ticket"]
    doc_type: DocTypeCode
    invoice: Invoice | None = None
    ticket: Ticket | None = None

    @property
    def record_id(self) -> UUID:
        if self.invoice is not None:
            return self.invoice.id
        if self.ticket is not None:
            return self.ticket.id
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
) -> DocumentIngestResult:
    """Crea el stub correspondiente al tipo indicado y encola el job ARQ."""
    if doc_type == DocTypeCode.factura:
        invoice = await invoice_service.create_invoice_from_upload(
            db,
            tenant_id=tenant_id,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime_type,
            doc_type=DocTypeCode.factura,
        )
        await db.flush()
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

    ticket = await ticket_service.create_ticket_from_upload(
        db,
        tenant_id=tenant_id,
        filename=filename,
        file_bytes=file_bytes,
        mime_type=mime_type,
        doc_type=DocTypeCode.ticket,
    )
    await db.flush()
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
