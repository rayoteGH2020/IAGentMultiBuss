"""Tests del worker process_invoice contra Postgres real con mocks de LLM y storage.

Este test verifica el flujo completo del worker (sin cola ARQ real):
  1. Existe un Invoice en BD en estado processing con una key de R2.
  2. El worker descarga el fichero (fake storage), extrae (fake LLM), persiste.
  3. El Invoice queda en estado ready con los datos del Factura fake.

Se mockean: storage (evita R2), LLM (evita coste), semáforo Redis (evita conexión).
Se usa la BD real para verificar que SQLAlchemy y RLS funcionan correctamente.
"""

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.core.media_limits import MediaLimitExceeded
from app.core.storage import reset_storage_for_tests
from app.jobs import invoice_jobs
from app.llm.extraction import ExtractionResult
from app.models import LLMCall, ProcessingCharge, ProcessingChargeStatus, Tenant
from app.models.invoice import InvoiceStatus
from app.schemas.invoice import Factura
from app.services import invoice_service
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_PDF_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_12.pdf"
).read_bytes()


class _FakeStorage:
    """Storage que devuelve bytes fijos sin contactar R2.

    Implementa upload_bytes (no usado por el worker, pero necesario si el
    service lo llama internamente) y download_bytes (el worker lo llama para
    obtener el fichero antes de la extracción).
    """

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
        # Ignora la key y devuelve siempre el blob configurado en __init__.
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

    # Se parchea en invoice_jobs (no en app.core.storage) porque el worker
    # importa get_storage directamente; parchear el módulo original no afectaría
    # a la referencia ya capturada en invoice_jobs.
    monkeypatch.setattr(
        invoice_jobs,
        "get_storage",
        lambda: _FakeStorage(_PDF_BYTES),
    )

    # El semáforo Redis usa INCR/DECR sobre una clave de Redis real.
    # _noop_slot lo reemplaza por un context manager que simplemente cede el
    # control sin verificar ningún límite, evitando la necesidad de Redis en el test.
    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(
        invoice_jobs,
        "tenant_invoice_extraction_slot",
        _noop_slot,
    )

    # fake_extract: coroutine que devuelve un Factura determinista. Los valores
    # son coherentes (base + iva == total) para que el model_validator de Factura
    # no penalice la confidence, lo que haría fallar las aserciones posteriores.
    async def fake_extract(
        *,
        file_bytes: bytes,
        mime_type: str,
        tenant_id: UUID,
        db: AsyncSession,
        source_filename: str | None = None,
        max_pdf_pages: int | None = None,
    ) -> ExtractionResult:
        _ = file_bytes, mime_type, tenant_id, db, source_filename, max_pdf_pages
        return ExtractionResult(
            factura=Factura(
                fecha=date(2025, 1, 15),
                proveedor="Test S.L.",
                cif_nif="00000000T",
                base_imponible=Decimal("100.00"),
                iva_percent=Decimal("21.00"),
                iva_amount=Decimal("21.00"),
                total=Decimal("121.00"),
                confidence=0.95,
            ),
            llm_call_id=UUID("00000000-0000-0000-0000-000000000001"),
        )

    monkeypatch.setattr(invoice_jobs, "extract_invoice", fake_extract)

    # Setup del estado inicial en BD: el worker espera encontrar un Invoice
    # en estado processing con source_file_key rellena.
    invoice = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key="test/invoices/mock.pdf",
        source_filename="mock.pdf",
        source_mime="application/pdf",
    )
    invoice.status = InvoiceStatus.processing
    # Commit antes de llamar al worker: el worker abre su propia sesión de BD
    # y necesita encontrar el registro persistido, no solo en memoria.
    await db_session.commit()

    tenant_id = tenant.id
    invoice_id = invoice.id
    # Se llama al job directamente (sin ARQ). El primer argumento ({}) es el ctx
    # que ARQ inyectaría normalmente; aquí está vacío porque el semáforo está
    # mockeado y no necesita Redis.
    await invoice_jobs.process_invoice({}, str(invoice_id), str(tenant_id))

    try:
        # expire() fuerza a SQLAlchemy a descartar el estado en memoria del objeto
        # invoice y recargarlo desde BD en el siguiente acceso. Sin esto,
        # la sesión devolvería el estado anterior al commit del worker.
        db_session.expire(invoice)
        # El worker hace su propio commit, que puede revocar el SET LOCAL de tenant
        # context. Se re-establece antes de la query de verificación.
        await set_tenant_context(db_session, str(tenant_id))
        refreshed = await invoice_service.get_invoice(db_session, tenant_id, invoice_id)
        assert refreshed.status == InvoiceStatus.ready
        assert refreshed.proveedor == "Test S.L."
        assert refreshed.total == Decimal("121.00")
    finally:
        # El storage singleton puede haber sido modificado por el patch;
        # reset garantiza que tests posteriores usen el storage real.
        reset_storage_for_tests()


@pytest.mark.asyncio
async def test_process_invoice_rejected_by_limits_is_not_retryable(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el fichero incumple límites, el worker cierra con motivo estructurado."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    monkeypatch.setattr(invoice_jobs, "get_storage", lambda: _FakeStorage(_PDF_BYTES))

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(invoice_jobs, "tenant_invoice_extraction_slot", _noop_slot)

    async def fake_extract(**_kwargs: object) -> ExtractionResult:
        raise MediaLimitExceeded(
            "PDF with 12 pages exceeds limit of 3",
            error_code=DocumentErrorCode.too_many_pages,
            detail="12 páginas; el máximo admitido son 3",
        )

    monkeypatch.setattr(invoice_jobs, "extract_invoice", fake_extract)

    invoice = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key="test/invoices/big.pdf",
        source_filename="anual.pdf",
        source_mime="application/pdf",
    )
    invoice.status = InvoiceStatus.processing
    tenant_id = tenant.id
    invoice_id = invoice.id
    await db_session.commit()

    result = await invoice_jobs.process_invoice({}, str(invoice_id), str(tenant_id))

    try:
        assert result["status"] == "rejected"
        db_session.expire(invoice)
        await set_tenant_context(db_session, str(tenant_id))
        refreshed = await invoice_service.get_invoice(db_session, tenant_id, invoice_id)
        assert refreshed.status == InvoiceStatus.failed
        assert refreshed.error_code == DocumentErrorCode.too_many_pages.value
        assert refreshed.error_message is not None
        assert "12 páginas" in refreshed.error_message
    finally:
        reset_storage_for_tests()


@pytest.mark.asyncio
async def test_process_invoice_settles_authorized_charge(
    invoices_schema_ready: None,
    processing_charges_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tras un procesado autorizado, el cargo pasa de estimado a coste real."""
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    monkeypatch.setattr(invoice_jobs, "get_storage", lambda: _FakeStorage(_PDF_BYTES))

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(invoice_jobs, "tenant_invoice_extraction_slot", _noop_slot)

    invoice = await invoice_service.create_invoice_stub(
        db_session,
        tenant.id,
        source_file_key="test/invoices/override.pdf",
        source_filename="anual.pdf",
        source_mime="application/pdf",
    )
    invoice.status = InvoiceStatus.processing

    llm_call = LLMCall(
        tenant_id=tenant.id,
        task="extraction",
        model="gemini-2.5-flash",
        provider="google",
        input_tokens=20_000,
        output_tokens=6_000,
        cost_eur=Decimal("0.040000"),
        latency_ms=9000,
        status="ok",
    )
    db_session.add(llm_call)
    charge = ProcessingCharge(
        tenant_id=tenant.id,
        document_kind="invoice",
        document_id=invoice.id,
        period=date.today().replace(day=1),
        pages=8,
        estimated_cost_eur=Decimal("0.030000"),
        status=ProcessingChargeStatus.pending.value,
    )
    db_session.add(charge)
    await db_session.flush()

    tenant_id = tenant.id
    invoice_id = invoice.id
    charge_id = charge.id
    llm_call_id = llm_call.id
    await db_session.commit()

    received: dict[str, Any] = {}

    async def fake_extract(**kwargs: Any) -> ExtractionResult:
        received.update(kwargs)
        return ExtractionResult(
            factura=Factura(
                fecha=date(2025, 1, 15),
                proveedor="Test S.L.",
                cif_nif="00000000T",
                base_imponible=Decimal("100.00"),
                iva_percent=Decimal("21.00"),
                iva_amount=Decimal("21.00"),
                total=Decimal("121.00"),
                confidence=0.95,
            ),
            llm_call_id=llm_call_id,
        )

    monkeypatch.setattr(invoice_jobs, "extract_invoice", fake_extract)

    try:
        await invoice_jobs.process_invoice(
            {},
            str(invoice_id),
            str(tenant_id),
            max_pdf_pages=100,
        )

        assert received["max_pdf_pages"] == 100

        await set_tenant_context(db_session, str(tenant_id))
        db_session.expire(charge)
        settled = (
            await db_session.execute(
                select(ProcessingCharge).where(ProcessingCharge.id == charge_id),
            )
        ).scalar_one()
        assert settled.provider_cost_eur == Decimal("0.040000")
        assert settled.billable_eur is not None
        assert settled.llm_call_id == llm_call_id
    finally:
        await set_tenant_context(db_session, str(tenant_id))
        await db_session.execute(
            delete(ProcessingCharge).where(ProcessingCharge.tenant_id == tenant_id),
        )
        await db_session.execute(delete(LLMCall).where(LLMCall.tenant_id == tenant_id))
        await db_session.commit()
        reset_storage_for_tests()
