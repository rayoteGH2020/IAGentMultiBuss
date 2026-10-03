"""Solo se procesa lo que entra en el histórico del plan (D017, punto 9; 2026-10-03).

- Fecha conocida en la subida (texto del PDF o clasificación) y anterior al
  histórico: rechazo como un duplicado, sin R2, cupo ni extracción.
- Fecha desconocida en la subida: si la extracción da una fecha anterior, error
  definitivo visible (`outside_history`) y se devuelve el cupo.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.billing_period import local_date
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.core.entitlement_codes import LIMIT_INVOICES_PER_MONTH
from app.core.errors import ValidationError
from app.core.uploads import UploadValidationError
from app.models import DocTypeCode, Invoice, InvoiceStatus, Tenant
from app.schemas.invoice import Factura, LineaFactura
from app.services import (
    document_panel_service,
    document_processing_service,
    document_upload_service,
    entitlement_service,
    invoice_service,
    monthly_quota_service,
)
from app.services.document_classification import TypeVerificationResult
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_OLD = local_date() - timedelta(days=500)
_RECENT = local_date() - timedelta(days=10)


class _FakeStorage:
    def __init__(self) -> None:
        self.uploads = 0

    async def upload_bytes(self, key: str, data: bytes, content_type: str = "") -> str:
        _ = data, content_type
        self.uploads += 1
        return key

    async def delete(self, key: str) -> None:
        _ = key


@pytest.fixture
def upload_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sin R2, LLM ni ARQ; la fecha que «lee» la clasificación se fija por test."""
    env: dict[str, Any] = {
        "storage": _FakeStorage(),
        "enqueue": AsyncMock(),
        "issue_date": None,
    }

    async def _verify(
        *_args: Any, user_choice: DocTypeCode, **_kwargs: Any
    ) -> TypeVerificationResult:
        return TypeVerificationResult(
            user_choice=user_choice,
            detected=user_choice,
            confidence=0.9,
            needs_confirmation=False,
            method="llm_match",
            issue_date=env["issue_date"],
        )

    monkeypatch.setattr(
        "app.services.document_upload_service.document_classification.verify_user_doc_type",
        _verify,
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.asyncio.to_thread", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing", env["enqueue"]
    )
    monkeypatch.setattr("app.services.invoice_service.get_storage", lambda: env["storage"])
    monkeypatch.setattr("app.jobs.queue.get_arq_pool", AsyncMock(return_value=AsyncMock()))
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.document_quota_service.get_redis", lambda: redis)
    monkeypatch.setattr(
        "app.services.document_quota_service.llm_budget_exhausted", AsyncMock(return_value=False)
    )
    return env


async def _tenant(
    db: AsyncSession, factory: Callable[..., Coroutine[Any, Any, Tenant]], plan: str = "basic"
) -> Tenant:
    tenant = await factory(plan_code=plan)
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def _upload(
    db: AsyncSession, tenant: Tenant, *, content: bytes | None = None
) -> document_upload_service.DocumentIngestResult:
    return await document_upload_service.ingest_uploaded_document(
        db,
        tenant_id=tenant.id,
        filename="vieja.pdf",
        file_bytes=content or f"%PDF-1.4 {uuid4()}".encode(),
        mime_type="application/pdf",
        doc_type=DocTypeCode.factura,
    )


async def _documents_used(db: AsyncSession, tenant: Tenant) -> int:
    ents = await entitlement_service.resolve_tenant(db, tenant.id)
    used, _ = await monthly_quota_service.bag_usage(db, ents, tenant.id, LIMIT_INVOICES_PER_MONTH)
    return used


async def _invoice_count(db: AsyncSession, tenant: Tenant) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.tenant_id == tenant.id)
        )
        or 0
    )


async def test_old_invoice_is_rejected_at_upload_without_r2_quota_or_extraction(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    upload_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    upload_env["issue_date"] = _OLD

    with pytest.raises(UploadValidationError, match="los últimos 12 meses") as exc:
        await _upload(db_session, tenant)

    assert _OLD.strftime("%d/%m/%Y") in str(exc.value)
    assert "No se ha subido ni consume cupo" in str(exc.value)
    assert upload_env["storage"].uploads == 0
    upload_env["enqueue"].assert_not_awaited()
    assert await _invoice_count(db_session, tenant) == 0
    assert await _documents_used(db_session, tenant) == 0


async def test_recent_or_unlimited_history_is_processed(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    upload_env: dict[str, Any],
) -> None:
    basic = await _tenant(db_session, tenant_factory)
    upload_env["issue_date"] = _RECENT
    recent = await _upload(db_session, basic)
    assert recent.invoice is not None and not recent.quota_pending

    premium = await _tenant(db_session, tenant_factory, plan="premium")
    upload_env["issue_date"] = _OLD
    old_in_premium = await _upload(db_session, premium)
    assert old_in_premium.invoice is not None
    assert upload_env["enqueue"].await_count == 2


def _factura(fecha: Any) -> Factura:
    total = Decimal("121")
    return Factura(
        fecha=fecha,
        proveedor="Proveedor Antiguo SL",
        cif_nif="B12345678",  # pragma: allowlist secret
        base_imponible=Decimal("100"),
        iva_percent=Decimal("21"),
        iva_amount=Decimal("21"),
        total=total,
        lineas=[
            LineaFactura(descripcion="x", cantidad=Decimal("1"), precio_unitario=total, total=total)
        ],
        confidence=0.9,
    )


async def test_safety_net_after_extraction_fails_visibly_and_returns_quota(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    upload_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    content = b"%PDF-1.4 sin fecha legible al subir"
    upload_env["issue_date"] = None  # no se pudo leer la fecha sin extraer
    result = await _upload(db_session, tenant, content=content)
    invoice = result.invoice
    assert invoice is not None
    assert await _documents_used(db_session, tenant) == 1

    await invoice_service.apply_extraction_result(
        db_session, invoice=invoice, factura=_factura(_OLD), llm_call_id=uuid4()
    )

    assert invoice.status == InvoiceStatus.failed
    assert invoice.error_code == DocumentErrorCode.outside_history.value
    assert "anterior al histórico que incluye tu plan" in (invoice.error_message or "")
    assert invoice.fecha is None  # no se guardan los datos del documento antiguo
    assert await _documents_used(db_session, tenant) == 0
    # Visible en el panel como error, sin «Reintentar» ni revisión manual.
    ctx = await document_panel_service.build_invoices_panel_ctx(db_session, tenant.id)
    [row] = [r for r in ctx["documents"] if r.id == invoice.id]  # type: ignore[attr-defined]
    assert row.status == "failed" and not row.can_retry and not row.needs_manual_review
    with pytest.raises(ValidationError):
        await document_processing_service.retry_processing(
            db_session, tenant_id=tenant.id, document_kind="invoice", document_id=invoice.id
        )
    # Volver a subirlo: duplicado sin sugerir «Reintentar».
    with pytest.raises(UploadValidationError, match="no admite reintento"):
        await _upload(db_session, tenant, content=content)


async def test_safety_net_lets_recent_invoices_through(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    upload_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    result = await _upload(db_session, tenant)
    assert result.invoice is not None
    # Como el worker: la factura se carga con sus líneas antes de aplicar la extracción.
    invoice = await invoice_service.get_invoice(db_session, tenant.id, result.invoice.id)

    await invoice_service.apply_extraction_result(
        db_session, invoice=invoice, factura=_factura(_RECENT), llm_call_id=uuid4()
    )

    assert invoice.status == InvoiceStatus.ready
    assert invoice.fecha == _RECENT
    assert await _documents_used(db_session, tenant) == 1
