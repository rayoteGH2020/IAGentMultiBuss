"""Revisión y procesado excepcional de documentos rechazados (consola SADM).

`authorize_processing` hace commit, así que los tests limpian sus filas de forma
explícita al terminar en lugar de apoyarse en el rollback de `db_session`.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.core.errors import ValidationError
from app.models import (
    DocTypeCode,
    Invoice,
    InvoiceStatus,
    ProcessingCharge,
    ProcessingChargeStatus,
    Tenant,
    User,
)
from app.services import doc_type_service, document_override_service
from pypdf import PdfWriter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class _FakeStorage:
    """Storage en memoria: devuelve siempre el mismo PDF y una URL fija."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.downloaded: list[str] = []

    async def download_bytes(self, key: str) -> bytes:
        self.downloaded.append(key)
        return self.payload

    async def presigned_url_get(self, key: str, expires_in: int = 900) -> str:
        _ = expires_in
        return f"https://r2.test/{key}?signed=1"


async def _rejected_invoice(
    db: AsyncSession,
    tenant: Tenant,
    *,
    error_code: str = DocumentErrorCode.too_many_pages.value,
) -> Invoice:
    doc_type_id = await doc_type_service.get_doc_type_id(db, DocTypeCode.factura)
    invoice = Invoice(
        tenant_id=tenant.id,
        doc_type_id=doc_type_id,
        status=InvoiceStatus.failed,
        source_file_key=f"invoices/{uuid4()}.pdf",
        source_filename="anual.pdf",
        source_mime="application/pdf",
        error_code=error_code,
        error_message="Rechazado en la subida.",
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def _superadmin_user(db: AsyncSession) -> User:
    user = User(
        clerk_user_id=f"user_sadm_{uuid4().hex[:12]}",
        email=f"sadm_{uuid4().hex[:8]}@test.local",
        name="Superadmin",
    )
    db.add(user)
    await db.flush()
    return user


async def _cleanup(db: AsyncSession, *, tenant_id: UUID, user_id: UUID) -> None:
    """Borra lo que quedó comprometido por el commit del servicio."""
    await set_tenant_context(db, str(tenant_id))
    await db.execute(delete(ProcessingCharge).where(ProcessingCharge.tenant_id == tenant_id))
    await db.execute(delete(Invoice).where(Invoice.tenant_id == tenant_id))
    await db.execute(delete(User).where(User.id == user_id))
    await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
    await db.commit()


async def test_list_rejected_only_includes_non_retryable_failures(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    rejected = await _rejected_invoice(db_session, tenant)
    transient = await _rejected_invoice(
        db_session,
        tenant,
        error_code=DocumentErrorCode.extraction_failed.value,
    )

    rows = await document_override_service.list_rejected_documents(db_session)

    ids = {row.id for row in rows}
    assert rejected.id in ids
    assert transient.id not in ids


async def test_build_review_measures_real_pages_and_cost(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La estimación se calcula sobre el fichero, no sobre el mensaje de error."""
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _rejected_invoice(db_session, tenant)

    storage = _FakeStorage(_pdf_bytes(7))
    monkeypatch.setattr(document_override_service, "get_storage", lambda: storage)

    review = await document_override_service.build_review(
        db_session,
        kind="invoice",
        document_id=invoice.id,
    )

    assert review.pages == 7
    assert review.estimate.pages == 7
    assert review.estimate.seconds > 0
    assert review.estimate.provider_cost_eur > 0
    assert review.over_hard_limit is False
    assert storage.downloaded == [invoice.source_file_key]


async def test_original_file_url_is_presigned(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _rejected_invoice(db_session, tenant)

    monkeypatch.setattr(
        document_override_service,
        "get_storage",
        lambda: _FakeStorage(_pdf_bytes(1)),
    )

    url = await document_override_service.original_file_url(
        db_session,
        kind="invoice",
        document_id=invoice.id,
    )

    assert url.startswith("https://r2.test/")
    assert invoice.source_file_key in url


async def test_authorize_processing_requeues_and_records_charge(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _rejected_invoice(db_session, tenant)
    admin = await _superadmin_user(db_session)

    monkeypatch.setattr(
        document_override_service,
        "get_storage",
        lambda: _FakeStorage(_pdf_bytes(6)),
    )
    enqueue = AsyncMock(return_value="job-override")
    monkeypatch.setattr(document_override_service, "enqueue_invoice_processing", enqueue)

    try:
        review = await document_override_service.authorize_processing(
            db_session,
            kind="invoice",
            document_id=invoice.id,
            authorized_by=admin.id,
            reason="Factura anual del cliente clave",
        )

        assert review.pages == 6
        enqueue.assert_awaited_once()
        _, kwargs = enqueue.await_args
        assert kwargs["max_pdf_pages"] == get_settings().document_override_max_pdf_pages
        assert kwargs["replace_existing"] is True

        # El commit del servicio cierra la transacción y con ella el contexto
        # RLS: para leer lo escrito hay que volver a situarse en el tenant.
        await set_tenant_context(db_session, str(tenant.id))
        refreshed = (
            await db_session.execute(select(Invoice).where(Invoice.id == invoice.id))
        ).scalar_one()
        assert refreshed.status == InvoiceStatus.processing
        assert refreshed.error_code is None
        assert refreshed.error_message is None

        charge = (
            await db_session.execute(
                select(ProcessingCharge).where(ProcessingCharge.document_id == invoice.id),
            )
        ).scalar_one()
        assert charge.tenant_id == tenant.id
        assert charge.pages == 6
        assert charge.status == ProcessingChargeStatus.pending.value
        assert charge.authorized_by == admin.id
        assert charge.reason == "Factura anual del cliente clave"
        # El coste real llega en settle_charge, cuando el worker termina.
        assert charge.provider_cost_eur is None
        assert charge.estimated_cost_eur > 0
    finally:
        await _cleanup(db_session, tenant_id=tenant.id, user_id=admin.id)


async def test_authorize_processing_refuses_over_hard_limit(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El override salta los límites de negocio, no el techo que protege al worker."""
    monkeypatch.setenv("DOCUMENT_OVERRIDE_MAX_PDF_PAGES", "3")
    get_settings.cache_clear()

    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _rejected_invoice(db_session, tenant)
    admin = await _superadmin_user(db_session)

    monkeypatch.setattr(
        document_override_service,
        "get_storage",
        lambda: _FakeStorage(_pdf_bytes(9)),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(document_override_service, "enqueue_invoice_processing", enqueue)

    try:
        with pytest.raises(ValidationError):
            await document_override_service.authorize_processing(
                db_session,
                kind="invoice",
                document_id=invoice.id,
                authorized_by=admin.id,
            )

        enqueue.assert_not_awaited()
        charges = (
            (
                await db_session.execute(
                    select(ProcessingCharge).where(ProcessingCharge.document_id == invoice.id),
                )
            )
            .scalars()
            .all()
        )
        assert charges == []
    finally:
        get_settings.cache_clear()


async def test_authorize_processing_rejects_retryable_documents(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo transitorio se reintenta por la vía normal, no generando un cargo."""
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    invoice = await _rejected_invoice(
        db_session,
        tenant,
        error_code=DocumentErrorCode.extraction_failed.value,
    )
    admin = await _superadmin_user(db_session)

    monkeypatch.setattr(
        document_override_service,
        "get_storage",
        lambda: _FakeStorage(_pdf_bytes(2)),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(document_override_service, "enqueue_invoice_processing", enqueue)

    with pytest.raises(ValidationError):
        await document_override_service.authorize_processing(
            db_session,
            kind="invoice",
            document_id=invoice.id,
            authorized_by=admin.id,
        )

    enqueue.assert_not_awaited()
