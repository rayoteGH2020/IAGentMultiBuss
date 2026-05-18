"""Facturas — listado y creación inicial (stub) antes del pipeline LLM/upload."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.core.keys import invoice_key
from app.core.storage import get_storage
from app.models import Invoice, InvoiceLine, InvoiceStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.invoice import Factura

logger = structlog.get_logger(__name__)


async def list_invoices(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    status: InvoiceStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Invoice]:
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id)
        .order_by(Invoice.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Invoice.status == status)
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_invoice(
    db: AsyncSession,
    tenant_id: UUID,
    invoice_id: UUID,
) -> Invoice:
    stmt = (
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id, Invoice.id == invoice_id)
        .options(selectinload(Invoice.lines))
    )
    result = await db.execute(stmt)
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise NotFoundError(f"Invoice {invoice_id} not found")
    return invoice


async def create_invoice_stub(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    source_file_key: str,
    source_filename: str,
    source_mime: str,
) -> Invoice:
    """Crea una factura en estado pending, sin datos extraídos."""
    invoice = Invoice(
        tenant_id=tenant_id,
        status=InvoiceStatus.pending,
        source_file_key=source_file_key,
        source_filename=source_filename,
        source_mime=source_mime,
    )
    db.add(invoice)
    await db.flush()
    logger.info("invoice.created", invoice_id=str(invoice.id), tenant_id=str(tenant_id))
    return invoice


async def create_invoice_from_upload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
) -> Invoice:
    """Sube bytes a R2 y crea `Invoice` en estado procesando."""
    storage = get_storage()
    key = invoice_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)
    invoice = await create_invoice_stub(
        db,
        tenant_id,
        source_file_key=key,
        source_filename=filename[:300],
        source_mime=mime_type,
    )
    invoice.status = InvoiceStatus.processing
    await db.flush()
    return invoice


async def apply_extraction_result(
    db: AsyncSession,
    *,
    invoice: Invoice,
    factura: Factura,
) -> Invoice:
    """Persiste el resultado structured output del extractor LLM sobre `invoice`."""
    invoice.fecha = factura.fecha
    invoice.proveedor = factura.proveedor[:300]
    invoice.cif_nif = factura.cif_nif
    invoice.base_imponible = factura.base_imponible
    invoice.iva_percent = factura.iva_percent
    invoice.iva_amount = factura.iva_amount
    invoice.total = factura.total
    invoice.currency = factura.currency[:3]
    invoice.confidence = Decimal(str(factura.confidence)).quantize(Decimal("0.01"))
    invoice.raw_extraction = factura.model_dump(mode="json")
    invoice.status = InvoiceStatus.ready
    invoice.updated_at = datetime.now(tz=UTC)
    invoice.error_message = None

    invoice.lines = []
    await db.flush()
    for idx, linea in enumerate(factura.lineas):
        invoice.lines.append(
            InvoiceLine(
                tenant_id=invoice.tenant_id,
                descripcion=linea.descripcion[:1000],
                cantidad=linea.cantidad,
                precio_unitario=linea.precio_unitario,
                total=linea.total,
                position=idx,
            ),
        )
    await db.flush()
    return invoice


async def mark_failed(
    db: AsyncSession,
    *,
    invoice_id: UUID,
    tenant_id: UUID,
    error: str,
) -> None:
    """Marca una factura como fallida tras error en pipeline (worker/jobs)."""
    invoice = await get_invoice(db, tenant_id, invoice_id)
    invoice.status = InvoiceStatus.failed
    invoice.error_message = error[:2000]
    invoice.updated_at = datetime.now(tz=UTC)
