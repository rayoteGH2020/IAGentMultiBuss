"""Cupo mensual de facturas y tickets, duplicados por hash y reintentos (bloques 2 y 3)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.billing_period import current_period_start
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.core.entitlement_codes import (
    LIMIT_DOCUMENT_RETRIES_PER_MONTH,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_INVOICES_PER_MONTH,
    LIMIT_TICKETS_PER_MONTH,
)
from app.core.errors import RateLimitError, ValidationError
from app.core.uploads import UploadValidationError
from app.models import (
    DocTypeCode,
    Invoice,
    InvoiceStatus,
    QuotaUsage,
    Tenant,
    Ticket,
    TicketStatus,
)
from app.schemas.entitlements import Entitlements
from app.services import (
    doc_type_service,
    document_delete_service,
    document_processing_service,
    document_quota_service,
    document_type_confirm_service,
    document_upload_service,
    invoice_service,
    monthly_quota_service,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _FakeStorage:
    async def upload_bytes(self, key: str, data: bytes, content_type: str = "") -> str:
        _ = data, content_type
        return key

    async def delete(self, key: str) -> None:
        _ = key


def _ents(*, invoices: int = 2, tickets: int = 1, retries: int = 5) -> Entitlements:
    return Entitlements(
        plan_code="basic",
        features=frozenset({"documents"}),
        limits={
            LIMIT_DOCUMENTS_PER_DAY: Decimal("1000"),
            LIMIT_INVOICES_PER_MONTH: Decimal(invoices),
            LIMIT_TICKETS_PER_MONTH: Decimal(tickets),
            LIMIT_DOCUMENT_RETRIES_PER_MONTH: Decimal(retries),
        },
    )


@pytest.fixture
def quota_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sin R2, LLM, ARQ ni Redis reales; entitlements con topes pequeños."""
    env: dict[str, Any] = {
        "ents": _ents(),
        "enqueue_invoice": AsyncMock(),
        "enqueue_ticket": AsyncMock(),
        "arq_pool": AsyncMock(),
        "redis": AsyncMock(),
    }
    env["redis"].set = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing", env["enqueue_invoice"]
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_ticket_processing", env["enqueue_ticket"]
    )
    monkeypatch.setattr(
        "app.services.document_type_confirm_service.enqueue_invoice_processing",
        env["enqueue_invoice"],
    )
    monkeypatch.setattr(
        "app.services.document_type_confirm_service.enqueue_ticket_processing",
        env["enqueue_ticket"],
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.asyncio.to_thread", AsyncMock(return_value=None)
    )
    # Sin verificación de tipo (heurística/LLM): fuera del alcance de estos tests.
    monkeypatch.setattr(
        "app.services.document_upload_service.document_classification.verify_user_doc_type",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("app.services.invoice_service.get_storage", lambda: _FakeStorage())
    monkeypatch.setattr("app.services.ticket_service.get_storage", lambda: _FakeStorage())
    monkeypatch.setattr("app.services.document_delete_service.get_storage", lambda: _FakeStorage())
    monkeypatch.setattr("app.jobs.queue.get_arq_pool", AsyncMock(return_value=env["arq_pool"]))
    monkeypatch.setattr("app.services.document_quota_service.get_redis", lambda: env["redis"])

    async def _resolve(_db: AsyncSession, _tenant_id: Any) -> Entitlements:
        return env["ents"]

    monkeypatch.setattr("app.services.entitlement_service.resolve_tenant", _resolve)
    monkeypatch.setattr(
        "app.services.document_quota_service.llm_budget_exhausted", AsyncMock(return_value=False)
    )
    return env


async def _tenant(db: AsyncSession, factory: Callable[..., Coroutine[Any, Any, Tenant]]) -> Tenant:
    tenant = await factory()
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def _upload(
    db: AsyncSession,
    env: dict[str, Any],
    tenant: Tenant,
    *,
    doc_type: DocTypeCode = DocTypeCode.factura,
    content: bytes | None = None,
) -> document_upload_service.DocumentIngestResult:
    return await document_upload_service.ingest_uploaded_document(
        db,
        tenant_id=tenant.id,
        filename="doc.pdf",
        file_bytes=content or f"%PDF-1.4 {uuid4()}".encode(),
        mime_type="application/pdf",
        doc_type=doc_type,
        redis=AsyncMock(incrby=AsyncMock(return_value=1)),
        ents=env["ents"],
    )


async def _bag_used(db: AsyncSession, tenant: Tenant, period: date | None = None) -> int:
    used, _ = await monthly_quota_service.bag_usage(
        db, _ents(), tenant.id, LIMIT_INVOICES_PER_MONTH, period=period
    )
    return used


async def _reserved_documents(db: AsyncSession, tenant: Tenant) -> int:
    """Documentos con reserva en el mes: debe coincidir siempre con quota_usage."""
    period = current_period_start()
    total = 0
    for model in (Invoice, Ticket):
        total += int(
            await db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == tenant.id, model.quota_period == period)
            )
            or 0
        )
    return total


# ── Subida: reserva, pendiente y duplicados ──────────────────────────────────


async def test_upload_reserves_shared_bag_and_holds_at_cap(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)

    first = await _upload(db_session, quota_env, tenant)
    second = await _upload(db_session, quota_env, tenant, doc_type=DocTypeCode.ticket)
    third = await _upload(db_session, quota_env, tenant, doc_type=DocTypeCode.ticket)
    fourth = await _upload(db_session, quota_env, tenant)

    # Bolsa 2 + 1 = 3: el cuarto queda pendiente, sin encolar.
    assert [r.quota_pending for r in (first, second, third, fourth)] == [False, False, False, True]
    assert fourth.invoice is not None
    assert fourth.invoice.status == InvoiceStatus.quota_pending
    assert fourth.invoice.error_code == DocumentErrorCode.monthly_quota.value
    assert fourth.invoice.quota_period is None
    assert quota_env["enqueue_invoice"].await_count == 1
    assert quota_env["enqueue_ticket"].await_count == 2
    assert await _bag_used(db_session, tenant) == 3
    assert await _reserved_documents(db_session, tenant) == 3


async def test_duplicate_file_is_rejected_without_new_document(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    content = b"%PDF-1.4 same file"
    first = await _upload(db_session, quota_env, tenant, content=content)

    with pytest.raises(UploadValidationError, match="ya está subido como factura"):
        await _upload(db_session, quota_env, tenant, content=content, doc_type=DocTypeCode.ticket)

    assert first.invoice is not None
    assert first.invoice.file_sha256 == document_quota_service.file_sha256(content)
    assert await _bag_used(db_session, tenant) == 1
    count = await db_session.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.tenant_id == tenant.id)
    )
    assert count == 0


async def test_duplicate_of_failed_document_points_to_retry(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    content = b"%PDF-1.4 failed file"
    first = await _upload(db_session, quota_env, tenant, content=content)
    assert first.invoice is not None
    await invoice_service.mark_failed(
        db_session, invoice_id=first.invoice.id, tenant_id=tenant.id, error="x"
    )

    with pytest.raises(UploadValidationError, match="Reintentar"):
        await _upload(db_session, quota_env, tenant, content=content)

    # Oculto: ya no bloquea.
    first.invoice.dismissed_at = datetime.now(UTC)
    await db_session.flush()
    again = await _upload(db_session, quota_env, tenant, content=content)
    assert again.invoice is not None


# ── Devoluciones ─────────────────────────────────────────────────────────────


async def test_failure_releases_reservation_once(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    result = await _upload(db_session, quota_env, tenant)
    assert result.invoice is not None

    await invoice_service.mark_failed(
        db_session, invoice_id=result.invoice.id, tenant_id=tenant.id, error="boom"
    )
    await invoice_service.mark_failed(
        db_session, invoice_id=result.invoice.id, tenant_id=tenant.id, error="boom"
    )

    assert result.invoice.quota_period is None
    assert await _bag_used(db_session, tenant) == 0


async def test_stale_abandon_releases_reservation(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.document_processing_service.purge_document_processing_job", AsyncMock()
    )
    tenant = await _tenant(db_session, tenant_factory)
    result = await _upload(db_session, quota_env, tenant)
    assert result.invoice is not None

    abandoned = await document_processing_service.abandon_stale_processing(
        db_session,
        tenant_id=tenant.id,
        document_kind="invoice",
        document_id=result.invoice.id,
        force=True,
    )

    assert abandoned is True
    assert await _bag_used(db_session, tenant) == 0


async def test_delete_returns_unit_in_current_month_only(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    current = await _upload(db_session, quota_env, tenant)
    old = await _upload(db_session, quota_env, tenant)
    assert current.invoice is not None and old.invoice is not None
    current.invoice.status = InvoiceStatus.ready
    # Procesada en un mes ya cerrado: su unidad se queda en ese mes.
    old.invoice.status = InvoiceStatus.ready
    old.invoice.quota_period = date(2020, 1, 1)
    await db_session.flush()

    for invoice in (current.invoice, old.invoice):
        await document_delete_service.delete_document(
            db_session,
            tenant_id=tenant.id,
            user_id=None,
            document_kind="invoice",
            document_id=invoice.id,
        )

    # Las 2 reservas eran del mes en curso en quota_usage; solo vuelve la del borrado
    # del mes en curso (la otra se apuntó a 2020 a mano y no existe en quota_usage).
    assert await _bag_used(db_session, tenant) == 1


# ── Pendientes ───────────────────────────────────────────────────────────────


async def test_process_pending_releases_oldest_when_room_appears(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    quota_env["ents"] = _ents(invoices=1, tickets=0)
    tenant = await _tenant(db_session, tenant_factory)
    first = await _upload(db_session, quota_env, tenant)
    waiting_old = await _upload(db_session, quota_env, tenant)
    waiting_new = await _upload(db_session, quota_env, tenant)
    assert waiting_old.quota_pending and waiting_new.quota_pending
    assert first.invoice is not None

    # Sin hueco: nada sale.
    assert await document_quota_service.process_pending(db_session, tenant.id) == []

    # El primero falla y devuelve su unidad: sale el pendiente más antiguo.
    await invoice_service.mark_failed(
        db_session, invoice_id=first.invoice.id, tenant_id=tenant.id, error="x"
    )
    released = await document_quota_service.process_pending(db_session, tenant.id)

    assert [item.document_id for item in released] == [waiting_old.record_id]
    assert waiting_old.invoice is not None and waiting_new.invoice is not None
    assert waiting_old.invoice.status == InvoiceStatus.processing
    assert waiting_new.invoice.status == InvoiceStatus.quota_pending
    assert await _bag_used(db_session, tenant) == 1
    assert await _reserved_documents(db_session, tenant) == 1
    # La devolución programó el job de pendientes del tenant.
    quota_env["arq_pool"].enqueue_job.assert_any_await(
        "process_quota_pending", str(tenant.id), _defer_by=5
    )


async def test_budget_exhausted_holds_and_returns_unit(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    result = await _upload(db_session, quota_env, tenant)
    assert result.invoice is not None
    monkeypatch.setattr(
        "app.services.document_quota_service.llm_budget_exhausted", AsyncMock(return_value=True)
    )

    held = await document_quota_service.hold_if_budget_exhausted(db_session, result.invoice)

    assert held is True
    assert result.invoice.status == InvoiceStatus.quota_pending
    assert result.invoice.error_code == DocumentErrorCode.llm_budget.value
    assert "presupuesto de IA" in (result.invoice.error_message or "")
    assert await _bag_used(db_session, tenant) == 0
    # Con el presupuesto agotado, la cola de pendientes no avanza.
    assert await document_quota_service.process_pending(db_session, tenant.id) == []


# ── Avisos ───────────────────────────────────────────────────────────────────


async def test_warning_at_80_percent_and_exhausted_alert_are_enqueued(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    quota_env["ents"] = _ents(invoices=5, tickets=0)
    tenant = await _tenant(db_session, tenant_factory)
    for _ in range(3):
        await _upload(db_session, quota_env, tenant)
    assert quota_env["arq_pool"].enqueue_job.await_count == 0

    await _upload(db_session, quota_env, tenant)  # 4 de 5 = 80 %
    await _upload(db_session, quota_env, tenant)
    await _upload(db_session, quota_env, tenant)  # pendiente

    kinds = [
        call.args[2]
        for call in quota_env["arq_pool"].enqueue_job.await_args_list
        if call.args[0] == "send_documents_quota_alert"
    ]
    # La deduplicación mensual la hace Redis (SET NX); aquí el mock acepta siempre.
    assert "documents_warning" in kinds
    assert "documents_exhausted" in kinds


# ── Reintentos ───────────────────────────────────────────────────────────────


async def _failed_invoice(
    db: AsyncSession, env: dict[str, Any], tenant: Tenant, *, error_code: DocumentErrorCode
) -> Invoice:
    result = await _upload(db, env, tenant)
    assert result.invoice is not None
    await invoice_service.mark_failed(
        db, invoice_id=result.invoice.id, tenant_id=tenant.id, error="x", error_code=error_code
    )
    return result.invoice


@pytest.fixture
def retry_enqueue(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(document_processing_service, "enqueue_invoice_processing", mock)
    return mock


async def test_retry_consumes_monthly_and_document_limits(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
    retry_enqueue: AsyncMock,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    invoice = await _failed_invoice(
        db_session, quota_env, tenant, error_code=DocumentErrorCode.extraction_failed
    )

    for attempt in range(1, 4):
        await document_processing_service.retry_processing(
            db_session, tenant_id=tenant.id, document_kind="invoice", document_id=invoice.id
        )
        assert invoice.manual_retry_count == attempt
        assert invoice.quota_period == current_period_start()
        await invoice_service.mark_failed(
            db_session, invoice_id=invoice.id, tenant_id=tenant.id, error="x"
        )

    with pytest.raises(ValidationError, match="revisión manual"):
        await document_processing_service.retry_processing(
            db_session, tenant_id=tenant.id, document_kind="invoice", document_id=invoice.id
        )
    assert retry_enqueue.await_count == 3
    usage = await monthly_quota_service.get_usage(db_session, quota_env["ents"], tenant.id)
    assert usage[LIMIT_DOCUMENT_RETRIES_PER_MONTH].used == 3


async def test_retry_of_failure_not_caused_by_user_is_free(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
    retry_enqueue: AsyncMock,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    invoice = await _failed_invoice(
        db_session, quota_env, tenant, error_code=DocumentErrorCode.provider_overload
    )
    invoice.manual_retry_count = 3
    await db_session.flush()

    await document_processing_service.retry_processing(
        db_session, tenant_id=tenant.id, document_kind="invoice", document_id=invoice.id
    )

    assert invoice.manual_retry_count == 3
    usage = await monthly_quota_service.get_usage(db_session, quota_env["ents"], tenant.id)
    assert usage[LIMIT_DOCUMENT_RETRIES_PER_MONTH].used == 0
    retry_enqueue.assert_awaited_once()


async def test_retry_rejected_when_monthly_retries_exhausted(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
    retry_enqueue: AsyncMock,
) -> None:
    quota_env["ents"] = _ents(invoices=1, tickets=0, retries=0)
    tenant = await _tenant(db_session, tenant_factory)
    invoice = await _failed_invoice(
        db_session, quota_env, tenant, error_code=DocumentErrorCode.extraction_failed
    )

    with pytest.raises(RateLimitError, match="reintentos de procesado"):
        await document_processing_service.retry_processing(
            db_session, tenant_id=tenant.id, document_kind="invoice", document_id=invoice.id
        )
    retry_enqueue.assert_not_awaited()


async def test_retry_rejected_without_document_quota(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
    retry_enqueue: AsyncMock,
) -> None:
    quota_env["ents"] = _ents(invoices=1, tickets=0, retries=5)
    tenant = await _tenant(db_session, tenant_factory)
    blocked = await _failed_invoice(
        db_session, quota_env, tenant, error_code=DocumentErrorCode.extraction_failed
    )
    await _upload(db_session, quota_env, tenant)  # ocupa el único hueco

    with pytest.raises(RateLimitError, match="facturas y tickets"):
        await document_processing_service.retry_processing(
            db_session, tenant_id=tenant.id, document_kind="invoice", document_id=blocked.id
        )
    retry_enqueue.assert_not_awaited()
    assert blocked.manual_retry_count == 0


# ── Confirmación de tipo ─────────────────────────────────────────────────────


async def test_switching_type_hides_original_and_reserves_new_document(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    quota_env: dict[str, Any],
) -> None:
    """Antes fallaba: dismiss_from_panel no admite documentos pendientes."""
    tenant = await _tenant(db_session, tenant_factory)
    doc_type_id = await doc_type_service.get_doc_type_id(db_session, DocTypeCode.factura)
    invoice = Invoice(
        tenant_id=tenant.id,
        doc_type_id=doc_type_id,
        status=InvoiceStatus.pending,
        source_file_key="t/k.pdf",
        source_filename="k.pdf",
        source_mime="application/pdf",
        error_code=DocumentErrorCode.type_confirmation_required.value,
        raw_extraction={
            document_type_confirm_service.TYPE_CONFIRM_META_KEY: {
                "user_choice": "factura",
                "suggested": "ticket",
                "confidence": 0.9,
            }
        },
        file_sha256="a" * 64,
    )
    db_session.add(invoice)
    await db_session.flush()

    result = await document_type_confirm_service.confirm_document_type(
        db_session,
        tenant_id=tenant.id,
        kind="invoice",
        document_id=invoice.id,
        choice="suggested",
    )

    ticket = await db_session.get(Ticket, result.document_id)
    assert result.kind == "ticket"
    assert ticket is not None
    assert ticket.status == TicketStatus.processing
    assert ticket.file_sha256 == "a" * 64
    assert ticket.quota_period == current_period_start()
    assert invoice.dismissed_at is not None
    assert invoice.file_sha256 is None
    quota_env["enqueue_ticket"].assert_awaited_once()
    rows = (
        await db_session.execute(select(QuotaUsage).where(QuotaUsage.tenant_id == tenant.id))
    ).scalars()
    assert {row.code: row.used for row in rows} == {LIMIT_TICKETS_PER_MONTH: 1}
