"""Tests de reintento y ocultación de documentos fallidos."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.core.errors import ValidationError
from app.models import DocTypeCode, Invoice, InvoiceStatus, Tenant
from app.models.document_processing_attempt import (
    DocumentProcessingAttempt,
    ProcessingAttemptStatus,
)
from app.services import doc_type_service, document_processing_service, invoice_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _failed_invoice(
    db: AsyncSession,
    tenant: Tenant,
) -> Invoice:
    doc_type_id = await doc_type_service.get_doc_type_id(db, DocTypeCode.factura)
    invoice = Invoice(
        tenant_id=tenant.id,
        doc_type_id=doc_type_id,
        status=InvoiceStatus.failed,
        source_file_key=f"test/{uuid4()}.pdf",
        source_filename="factura-error.pdf",
        source_mime="application/pdf",
        error_message="Error de prueba",
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def test_dismiss_hides_failed_invoice_from_list(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _failed_invoice(db_session, tenant)

    visible_before = await invoice_service.list_invoices(db_session, tenant.id)
    assert any(inv.id == invoice.id for inv in visible_before)

    await document_processing_service.dismiss_from_panel(
        db_session,
        tenant_id=tenant.id,
        document_kind="invoice",
        document_id=invoice.id,
    )
    await db_session.flush()

    visible_after = await invoice_service.list_invoices(db_session, tenant.id)
    assert not any(inv.id == invoice.id for inv in visible_after)

    still_there = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)
    assert still_there.dismissed_at is not None


async def test_dismiss_rejects_non_failed_invoice(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    doc_type_id = await doc_type_service.get_doc_type_id(db_session, DocTypeCode.factura)
    invoice = Invoice(
        tenant_id=tenant.id,
        doc_type_id=doc_type_id,
        status=InvoiceStatus.ready,
        source_file_key="test/ok.pdf",
        source_filename="ok.pdf",
        source_mime="application/pdf",
    )
    db_session.add(invoice)
    await db_session.flush()

    with pytest.raises(ValidationError):
        await document_processing_service.dismiss_from_panel(
            db_session,
            tenant_id=tenant.id,
            document_kind="invoice",
            document_id=invoice.id,
        )


async def test_retry_sets_processing_and_records_attempt(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _failed_invoice(db_session, tenant)

    with patch(
        "app.services.document_processing_service.enqueue_invoice_processing",
        new=AsyncMock(return_value="job-1"),
    ):
        await document_processing_service.retry_processing(
            db_session,
            tenant_id=tenant.id,
            document_kind="invoice",
            document_id=invoice.id,
        )
    await db_session.flush()

    updated = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)
    assert updated.status == InvoiceStatus.processing
    assert updated.error_message is None
    assert updated.dismissed_at is None

    result = await db_session.execute(
        select(DocumentProcessingAttempt).where(
            DocumentProcessingAttempt.tenant_id == tenant.id,
            DocumentProcessingAttempt.document_id == invoice.id,
            DocumentProcessingAttempt.status == ProcessingAttemptStatus.processing.value,
        ),
    )
    assert result.scalar_one_or_none() is not None


async def test_retry_is_blocked_for_limit_rejections(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    """Un rechazo por límites no se puede reintentar: el fichero no cambia."""
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _failed_invoice(db_session, tenant)
    invoice.error_code = DocumentErrorCode.too_many_pages.value
    await db_session.flush()

    enqueue = AsyncMock(return_value="job-nope")
    with (
        patch("app.services.document_processing_service.enqueue_invoice_processing", new=enqueue),
        pytest.raises(ValidationError),
    ):
        await document_processing_service.retry_processing(
            db_session,
            tenant_id=tenant.id,
            document_kind="invoice",
            document_id=invoice.id,
        )

    enqueue.assert_not_awaited()
    unchanged = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)
    assert unchanged.status == InvoiceStatus.failed


async def test_mark_failed_persists_error_code(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _failed_invoice(db_session, tenant)

    await invoice_service.mark_failed(
        db_session,
        invoice_id=invoice.id,
        tenant_id=tenant.id,
        error="PDF with 12 pages exceeds limit of 3",
        error_code=DocumentErrorCode.too_many_pages,
        detail="12 páginas; el máximo admitido son 3",
    )
    await db_session.flush()

    updated = await invoice_service.get_invoice(db_session, tenant.id, invoice.id)
    assert updated.error_code == DocumentErrorCode.too_many_pages.value
    assert updated.error_message is not None
    assert "12 páginas" in updated.error_message
    assert "administrador del sitio" in updated.error_message

    result = await db_session.execute(
        select(DocumentProcessingAttempt).where(
            DocumentProcessingAttempt.tenant_id == tenant.id,
            DocumentProcessingAttempt.document_id == invoice.id,
        ),
    )
    attempts = result.scalars().all()
    assert any(a.error_code == DocumentErrorCode.too_many_pages.value for a in attempts)


async def test_mark_failed_finalizes_processing_attempt(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _failed_invoice(db_session, tenant)

    await document_processing_service.begin_processing_attempt(
        db_session,
        tenant_id=tenant.id,
        document_kind="invoice",
        document_id=invoice.id,
    )
    await invoice_service.mark_failed(
        db_session,
        invoice_id=invoice.id,
        tenant_id=tenant.id,
        error="fallo simulado",
    )
    await db_session.flush()

    result = await db_session.execute(
        select(DocumentProcessingAttempt).where(
            DocumentProcessingAttempt.tenant_id == tenant.id,
            DocumentProcessingAttempt.document_id == invoice.id,
        ),
    )
    attempts = result.scalars().all()
    assert len(attempts) >= 1
    assert any(a.status == ProcessingAttemptStatus.failed.value for a in attempts)
