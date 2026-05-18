"""Facturas — listado y creación inicial (stub) antes del pipeline LLM/upload."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.errors import NotFoundError
from app.core.keys import invoice_key
from app.core.storage import get_storage
from app.models import Invoice, InvoiceStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

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
