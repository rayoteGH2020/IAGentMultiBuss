"""Worker `process_invoice` (Paso 14) contra Postgres + mock de extracción."""

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from app.core.db import set_tenant_context
from app.core.storage import reset_storage_for_tests
from app.jobs import invoice_jobs
from app.models import Tenant
from app.models.invoice import InvoiceStatus
from app.schemas.invoice import Factura
from app.services import invoice_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_PDF_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_01.pdf"
).read_bytes()


class _FakeStorage:
    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        _ = len(data), content_type
        return key

    async def download_bytes(self, key: str) -> bytes:
        _ = key
        return self._blob


@pytest.mark.asyncio
async def test_process_invoice_persists_extraction_mock(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    monkeypatch.setattr(
        invoice_jobs,
        "get_storage",
        lambda: _FakeStorage(_PDF_BYTES),
    )

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(
        invoice_jobs,
        "tenant_invoice_extraction_slot",
        _noop_slot,
    )

    async def fake_extract(
        *,
        file_bytes: bytes,
        mime_type: str,
        tenant_id: UUID,
        db: AsyncSession,
    ) -> Factura:
        _ = file_bytes, mime_type, tenant_id, db
        return Factura(
            fecha=date(2025, 1, 15),
            proveedor="Test S.L.",
            cif_nif="00000000T",
            base_imponible=Decimal("100.00"),
            iva_percent=Decimal("21.00"),
            iva_amount=Decimal("21.00"),
            total=Decimal("121.00"),
            confidence=0.95,
        )

    monkeypatch.setattr(invoice_jobs, "extract_invoice", fake_extract)

    invoice = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key="test/invoices/mock.pdf",
        source_filename="mock.pdf",
        source_mime="application/pdf",
    )
    invoice.status = InvoiceStatus.processing
    await db_session.commit()

    tenant_id = tenant.id
    invoice_id = invoice.id
    await invoice_jobs.process_invoice({}, str(invoice_id), str(tenant_id))

    try:
        db_session.expire(invoice)
        await set_tenant_context(db_session, str(tenant_id))
        refreshed = await invoice_service.get_invoice(db_session, tenant_id, invoice_id)
        assert refreshed.status == InvoiceStatus.ready
        assert refreshed.proveedor == "Test S.L."
        assert refreshed.total == Decimal("121.00")
    finally:
        reset_storage_for_tests()
