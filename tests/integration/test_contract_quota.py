"""Contratos (bloque 5, D027): altas por tramos, carga inicial, archivo de activos y sustitución."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.billing_period import (
    current_period_start,
    local_date,
    next_period_start,
    period_start,
    renewal_date,
)
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode
from app.core.entitlement_codes import (
    LIMIT_CONTRACT_MAX_PAGES,
    LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD,
    LIMIT_CONTRACT_UPLOADS_PER_MONTH,
    LIMIT_CONTRACTS_ACTIVE_MAX,
    LIMIT_DOCUMENT_RETRIES_PER_MONTH,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_INVOICES_PER_MONTH,
    LIMIT_TICKETS_PER_MONTH,
)
from app.core.errors import ValidationError
from app.core.media_limits import MediaInspection, MediaLimitExceeded
from app.core.uploads import UploadValidationError
from app.models import (
    AuditLog,
    Contract,
    ContractLifecycle,
    ContractStatus,
    DocTypeCode,
    InvoiceStatus,
    Tenant,
)
from app.schemas.document_query import AggregateGroupBy, AggregateMetric, DocumentSearchFilters
from app.schemas.entitlements import Entitlements
from app.services import (
    contract_quota_service,
    contract_service,
    document_delete_service,
    document_processing_service,
    document_quota_service,
    document_upload_service,
    invoice_service,
    monthly_quota_service,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _FakeStorage:
    def __init__(self) -> None:
        self.uploads = 0

    async def upload_bytes(self, key: str, data: bytes, content_type: str = "") -> str:
        _ = data, content_type
        self.uploads += 1
        return key

    async def delete(self, key: str) -> None:
        _ = key


def _ents(
    *,
    first: int = 15,
    monthly: int = 5,
    active: int = 15,
    max_pages: int = 100,
    invoices: int = 40,
) -> Entitlements:
    return Entitlements(
        plan_code="basic",
        features=frozenset({"documents"}),
        limits={
            LIMIT_DOCUMENTS_PER_DAY: Decimal("1000"),
            LIMIT_INVOICES_PER_MONTH: Decimal(invoices),
            LIMIT_TICKETS_PER_MONTH: Decimal("0"),
            LIMIT_DOCUMENT_RETRIES_PER_MONTH: Decimal("10"),
            LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD: Decimal(first),
            LIMIT_CONTRACT_UPLOADS_PER_MONTH: Decimal(monthly),
            LIMIT_CONTRACTS_ACTIVE_MAX: Decimal(active),
            LIMIT_CONTRACT_MAX_PAGES: Decimal(max_pages),
        },
    )


@pytest.fixture
def contract_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sin R2, LLM, ARQ ni Redis reales; las páginas se fijan por test."""
    env: dict[str, Any] = {
        "ents": _ents(),
        "storage": _FakeStorage(),
        "enqueue_contract": AsyncMock(),
        "enqueue_invoice": AsyncMock(),
        "arq_pool": AsyncMock(),
        "redis": AsyncMock(),
        "pages": 10,
    }
    env["redis"].set = AsyncMock(return_value=True)

    async def _inspect(_fn: Any, _data: bytes, mime_type: str, **_kwargs: Any) -> Any:
        return MediaInspection(mime_type=mime_type, size_bytes=1, pages=env["pages"])

    monkeypatch.setattr("app.services.document_upload_service.asyncio.to_thread", _inspect)
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_contract_processing",
        env["enqueue_contract"],
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing", env["enqueue_invoice"]
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.document_classification.verify_user_doc_type",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("app.services.contract_service.get_storage", lambda: env["storage"])
    monkeypatch.setattr("app.services.invoice_service.get_storage", lambda: env["storage"])
    monkeypatch.setattr("app.services.document_delete_service.get_storage", lambda: env["storage"])
    monkeypatch.setattr("app.jobs.queue.get_arq_pool", AsyncMock(return_value=env["arq_pool"]))
    monkeypatch.setattr("app.services.document_quota_service.get_redis", lambda: env["redis"])

    async def _resolve(_db: AsyncSession, _tenant_id: Any) -> Entitlements:
        return env["ents"]

    monkeypatch.setattr("app.services.entitlement_service.resolve_tenant", _resolve)
    monkeypatch.setattr(
        "app.services.document_quota_service.llm_budget_exhausted", AsyncMock(return_value=False)
    )
    return env


def _previous_month_start() -> date:
    return period_start(current_period_start() - timedelta(days=1))


async def _tenant(
    db: AsyncSession,
    factory: Callable[..., Coroutine[Any, Any, Tenant]],
    *,
    created_at: datetime | None = None,
) -> Tenant:
    """Por defecto, alta hace un año: fuera de la carga inicial (bolsa mensual)."""
    tenant = await factory()
    tenant.created_at = created_at or datetime.now(UTC) - timedelta(days=365)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def _upload(
    db: AsyncSession,
    env: dict[str, Any],
    tenant: Tenant,
    *,
    pages: int = 10,
    content: bytes | None = None,
    doc_type: DocTypeCode = DocTypeCode.contrato,
) -> document_upload_service.DocumentIngestResult:
    env["pages"] = pages
    return await document_upload_service.ingest_uploaded_document(
        db,
        tenant_id=tenant.id,
        filename="contrato.pdf",
        file_bytes=content or f"%PDF-1.4 {uuid4()}".encode(),
        mime_type="application/pdf",
        doc_type=doc_type,
        redis=AsyncMock(incrby=AsyncMock(return_value=1)),
        ents=env["ents"],
    )


async def _used(db: AsyncSession, tenant: Tenant, code: str) -> int:
    used, _ = await monthly_quota_service.bag_usage(db, _ents(), tenant.id, code)
    return used


def _contract(result: document_upload_service.DocumentIngestResult) -> Contract:
    assert result.contract is not None
    return result.contract


async def _mark_ready(
    db: AsyncSession, contract: Contract, *, fecha_fin: date | None = None
) -> None:
    contract.status = ContractStatus.ready
    contract.fecha_fin = fecha_fin
    await db.flush()


# ── Bolsa: carga inicial y mensual, tramos ───────────────────────────────────


async def test_initial_load_counts_in_signup_month_during_window(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    signup = _previous_month_start()
    tenant = await _tenant(
        db_session,
        tenant_factory,
        created_at=datetime(signup.year, signup.month, 6, 12, tzinfo=UTC),
    )

    contract = _contract(await _upload(db_session, contract_env, tenant, pages=45))

    # Alta el día 6 del mes pasado: la ventana dura hasta fin de este mes.
    assert contract.quota_code == LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD
    assert contract.quota_period == signup
    assert contract.upload_units == 2
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD) == 2
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 0
    usage = await monthly_quota_service.get_usage(db_session, contract_env["ents"], tenant.id)
    assert usage[LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD].used == 2


async def test_renewal_day_waits_for_end_of_initial_load(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    this_month = current_period_start()
    tenant = await _tenant(
        db_session,
        tenant_factory,
        created_at=datetime(this_month.year, this_month.month, 2, 12, tzinfo=UTC),
    )

    # Alta el día 2: la carga inicial dura hasta fin del mes siguiente.
    expected = next_period_start(next_period_start(this_month))
    assert await contract_quota_service.renewal_day(db_session, tenant.id) == expected
    assert renewal_date() == next_period_start(this_month)


async def test_after_window_page_tiers_consume_monthly_and_hold_at_cap(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)

    long_one = _contract(await _upload(db_session, contract_env, tenant, pages=80))
    medium = _contract(await _upload(db_session, contract_env, tenant, pages=45))
    pending = await _upload(db_session, contract_env, tenant, pages=5)

    assert (long_one.upload_units, long_one.page_count) == (3, 80)
    assert (medium.upload_units, medium.page_count) == (2, 45)
    assert long_one.quota_code == LIMIT_CONTRACT_UPLOADS_PER_MONTH
    assert long_one.quota_period == current_period_start()
    # 3 + 2 = 5 (tope mensual): el tercero queda pendiente, sin encolar.
    assert pending.quota_pending
    held = _contract(pending)
    assert held.status == ContractStatus.quota_pending
    assert held.error_code == DocumentErrorCode.monthly_quota.value
    assert "altas de contratos" in (held.error_message or "")
    assert pending.pending_message == held.error_message
    assert held.quota_period is None
    assert contract_env["enqueue_contract"].await_count == 2
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 5


async def test_too_many_pages_is_rejected_without_r2_or_record(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_env["ents"] = _ents(max_pages=50)
    tenant = await _tenant(db_session, tenant_factory)
    seen: dict[str, Any] = {}

    async def _too_long(_fn: Any, _data: bytes, _mime: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise MediaLimitExceeded(
            "too many",
            error_code=DocumentErrorCode.too_many_pages,
            detail="120 páginas; el máximo admitido son 50",
        )

    monkeypatch.setattr("app.services.document_upload_service.asyncio.to_thread", _too_long)

    with pytest.raises(UploadValidationError, match="hasta 50 páginas"):
        await _upload(db_session, contract_env, tenant)

    assert seen["max_pdf_pages"] == 50
    assert contract_env["storage"].uploads == 0
    count = await db_session.scalar(
        select(func.count()).select_from(Contract).where(Contract.tenant_id == tenant.id)
    )
    assert count == 0


async def test_duplicate_contract_is_rejected_even_when_replaced(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    content = b"%PDF-1.4 same contract"
    first = _contract(await _upload(db_session, contract_env, tenant, content=content))
    assert first.file_sha256 == document_quota_service.file_sha256(content)
    await _mark_ready(db_session, first)
    await contract_quota_service.mark_replaced(
        db_session, tenant_id=tenant.id, contract_id=first.id, user_id=None
    )

    with pytest.raises(UploadValidationError, match="ya está subido como contrato"):
        await _upload(db_session, contract_env, tenant, content=content)

    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 1


# ── Archivo de activos ───────────────────────────────────────────────────────


async def test_active_archive_full_rejects_until_a_slot_is_freed(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    contract_env["ents"] = _ents(active=2, monthly=20)
    tenant = await _tenant(db_session, tenant_factory)
    first = _contract(await _upload(db_session, contract_env, tenant))
    second = _contract(await _upload(db_session, contract_env, tenant))

    # En proceso también ocupan hueco: evita pasarse con subidas simultáneas.
    with pytest.raises(UploadValidationError, match="marca primero el contrato anterior"):
        await _upload(db_session, contract_env, tenant)
    assert contract_env["storage"].uploads == 2

    # Sustituido: libera su hueco.
    await _mark_ready(db_session, first)
    await contract_quota_service.mark_replaced(
        db_session, tenant_id=tenant.id, contract_id=first.id, user_id=None
    )
    third = _contract(await _upload(db_session, contract_env, tenant))

    # Vencido: libera su hueco solo.
    await _mark_ready(db_session, second, fecha_fin=local_date() - timedelta(days=1))
    fourth = _contract(await _upload(db_session, contract_env, tenant))

    # Fallido: no ocupa hueco.
    await contract_service.mark_failed(
        db_session, contract_id=third.id, tenant_id=tenant.id, error="x"
    )
    await _upload(db_session, contract_env, tenant)
    assert await contract_quota_service.active_count(db_session, tenant.id) == 2
    assert fourth.lifecycle == ContractLifecycle.active


async def test_reactivate_requires_slot_and_both_actions_are_audited(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    contract_env["ents"] = _ents(active=1, monthly=20)
    tenant = await _tenant(db_session, tenant_factory)
    old = _contract(await _upload(db_session, contract_env, tenant))
    with pytest.raises(ValidationError, match="ya procesado"):
        await contract_quota_service.mark_replaced(
            db_session, tenant_id=tenant.id, contract_id=old.id, user_id=None
        )
    await _mark_ready(db_session, old)
    await contract_quota_service.mark_replaced(
        db_session, tenant_id=tenant.id, contract_id=old.id, user_id=None
    )
    renewal = _contract(await _upload(db_session, contract_env, tenant))
    await _mark_ready(db_session, renewal)

    with pytest.raises(ValidationError, match="Marca antes otro como sustituido"):
        await contract_quota_service.reactivate(
            db_session, contract_env["ents"], tenant_id=tenant.id, contract_id=old.id, user_id=None
        )
    assert old.lifecycle == ContractLifecycle.replaced

    await contract_quota_service.mark_replaced(
        db_session, tenant_id=tenant.id, contract_id=renewal.id, user_id=None
    )
    await contract_quota_service.reactivate(
        db_session, contract_env["ents"], tenant_id=tenant.id, contract_id=old.id, user_id=None
    )
    assert old.lifecycle == ContractLifecycle.active

    actions = (
        (
            await db_session.execute(
                select(AuditLog.action).where(
                    AuditLog.tenant_id == tenant.id, AuditLog.action.like("contract.%")
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(actions) == ["contract.reactivated", "contract.replaced", "contract.replaced"]


# ── Devoluciones y pendientes ────────────────────────────────────────────────


async def test_failure_returns_exact_units_and_pending_contract_is_released(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    contract_env["ents"] = _ents(monthly=3)
    tenant = await _tenant(db_session, tenant_factory)
    big = _contract(await _upload(db_session, contract_env, tenant, pages=80))
    waiting = await _upload(db_session, contract_env, tenant, pages=10)
    assert waiting.quota_pending

    assert await document_quota_service.process_pending(db_session, tenant.id) == []
    await contract_service.mark_failed(
        db_session, contract_id=big.id, tenant_id=tenant.id, error="x"
    )
    await contract_service.mark_failed(
        db_session, contract_id=big.id, tenant_id=tenant.id, error="x"
    )
    assert big.quota_period is None and big.quota_code is None
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 0

    released = await document_quota_service.process_pending(db_session, tenant.id)

    assert [(item.kind, item.document_id) for item in released] == [("contract", waiting.record_id)]
    assert _contract(waiting).status == ContractStatus.processing
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 1


async def test_contracts_without_altas_do_not_block_pending_invoices(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    contract_env["ents"] = _ents(monthly=1, invoices=1)
    tenant = await _tenant(db_session, tenant_factory)
    await _upload(db_session, contract_env, tenant)
    contract_waiting = await _upload(db_session, contract_env, tenant)
    first_invoice = await _upload(db_session, contract_env, tenant, doc_type=DocTypeCode.factura)
    invoice_waiting = await _upload(db_session, contract_env, tenant, doc_type=DocTypeCode.factura)
    assert contract_waiting.quota_pending and invoice_waiting.quota_pending
    assert first_invoice.invoice is not None

    await invoice_service.mark_failed(
        db_session, invoice_id=first_invoice.invoice.id, tenant_id=tenant.id, error="x"
    )
    released = await document_quota_service.process_pending(db_session, tenant.id)

    # El contrato (más antiguo) sigue sin altas, pero no frena a la factura.
    assert [item.kind for item in released] == ["invoice"]
    assert invoice_waiting.invoice is not None
    assert invoice_waiting.invoice.status == InvoiceStatus.processing
    assert _contract(contract_waiting).status == ContractStatus.quota_pending


async def test_delete_returns_altas_only_for_unfinished_contracts(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    done = _contract(await _upload(db_session, contract_env, tenant, pages=45))
    await _mark_ready(db_session, done)
    in_flight = _contract(await _upload(db_session, contract_env, tenant))
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 3

    for contract in (done, in_flight):
        await document_delete_service.delete_document(
            db_session,
            tenant_id=tenant.id,
            user_id=None,
            document_kind="contract",
            document_id=contract.id,
        )

    # Borrar uno procesado nunca devuelve sus altas; el que no terminó, sí.
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 2


async def test_budget_exhausted_holds_contract_and_returns_altas(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    contract = _contract(await _upload(db_session, contract_env, tenant, pages=45))
    monkeypatch.setattr(
        "app.services.document_quota_service.llm_budget_exhausted", AsyncMock(return_value=True)
    )

    assert await document_quota_service.hold_if_budget_exhausted(db_session, contract)

    assert contract.status == ContractStatus.quota_pending
    assert contract.error_code == DocumentErrorCode.llm_budget.value
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 0


async def test_sadm_extra_on_initial_load_goes_to_signup_month(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    contract_env["ents"] = _ents(first=1)
    signup = _previous_month_start()
    tenant = await _tenant(
        db_session,
        tenant_factory,
        created_at=datetime(signup.year, signup.month, 6, 12, tzinfo=UTC),
    )
    await _upload(db_session, contract_env, tenant)
    waiting = await _upload(db_session, contract_env, tenant)
    assert waiting.quota_pending

    total = await monthly_quota_service.add_extra(
        db_session,
        tenant_id=tenant.id,
        code=LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD,
        amount=1,
        actor_user_id=None,
    )
    released = await document_quota_service.process_pending(db_session, tenant.id)

    assert total == 1
    assert [item.document_id for item in released] == [waiting.record_id]
    assert _contract(waiting).quota_period == signup


async def test_alerts_use_contract_kinds(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    await _upload(db_session, contract_env, tenant, pages=80)  # 3 de 5
    await _upload(db_session, contract_env, tenant, pages=10)  # 4 de 5 = 80 %
    await _upload(db_session, contract_env, tenant, pages=45)  # no cabe: pendiente

    kinds = [
        call.args[2]
        for call in contract_env["arq_pool"].enqueue_job.await_args_list
        if call.args[0] == "send_documents_quota_alert"
    ]
    assert "contracts_warning" in kinds
    assert "contracts_exhausted" in kinds
    assert "documents_warning" not in kinds


# ── Reintento ────────────────────────────────────────────────────────────────


async def test_retry_needs_active_slot_and_reserves_units_again(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enqueue = AsyncMock()
    monkeypatch.setattr(document_processing_service, "enqueue_contract_processing", enqueue)
    contract_env["ents"] = _ents(active=1, monthly=10)
    tenant = await _tenant(db_session, tenant_factory)
    failed = _contract(await _upload(db_session, contract_env, tenant, pages=80))
    await contract_service.mark_failed(
        db_session, contract_id=failed.id, tenant_id=tenant.id, error="x"
    )
    other = _contract(await _upload(db_session, contract_env, tenant))

    with pytest.raises(ValidationError, match="contratos vigentes"):
        await document_processing_service.retry_processing(
            db_session, tenant_id=tenant.id, document_kind="contract", document_id=failed.id
        )

    await contract_service.mark_failed(
        db_session, contract_id=other.id, tenant_id=tenant.id, error="x"
    )
    await document_processing_service.retry_processing(
        db_session, tenant_id=tenant.id, document_kind="contract", document_id=failed.id
    )

    assert failed.status == ContractStatus.processing
    assert failed.quota_code == LIMIT_CONTRACT_UPLOADS_PER_MONTH
    assert await _used(db_session, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH) == 3
    enqueue.assert_awaited_once()


# ── Chat ─────────────────────────────────────────────────────────────────────


async def test_chat_queries_exclude_replaced_contracts_by_default(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    current = _contract(await _upload(db_session, contract_env, tenant))
    old = _contract(await _upload(db_session, contract_env, tenant))
    for contract in (current, old):
        await _mark_ready(db_session, contract)
    await contract_quota_service.mark_replaced(
        db_session, tenant_id=tenant.id, contract_id=old.id, user_id=None
    )

    default = await contract_service.search_contracts(
        db_session, tenant.id, filters=DocumentSearchFilters()
    )
    history = await contract_service.search_contracts(
        db_session, tenant.id, filters=DocumentSearchFilters(incluir_sustituidos=True)
    )
    count = await contract_service.aggregate_contracts(
        db_session,
        tenant.id,
        filters=DocumentSearchFilters(),
        metric=AggregateMetric.metric_count,
        group_by=AggregateGroupBy.none,
    )

    assert [item.id for item in default.items] == [current.id]
    assert {item.id for item in history.items} == {current.id, old.id}
    assert {item.lifecycle for item in history.items} == {"active", "replaced"}
    assert count.total_value == 1


async def test_sadm_extra_follows_the_bag_in_use(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    contract_env: dict[str, Any],
) -> None:
    in_window = await _tenant(db_session, tenant_factory, created_at=datetime.now(UTC))
    with pytest.raises(ValidationError, match="carga inicial de contratos hasta el"):
        await monthly_quota_service.add_extra(
            db_session,
            tenant_id=in_window.id,
            code=LIMIT_CONTRACT_UPLOADS_PER_MONTH,
            amount=1,
            actor_user_id=None,
        )
    usage = await monthly_quota_service.get_usage(db_session, _ents(), in_window.id)
    assert LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD in usage

    after_window = await _tenant(db_session, tenant_factory)
    with pytest.raises(ValidationError, match="terminó el"):
        await monthly_quota_service.add_extra(
            db_session,
            tenant_id=after_window.id,
            code=LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD,
            amount=1,
            actor_user_id=None,
        )
    assert (
        await monthly_quota_service.add_extra(
            db_session,
            tenant_id=after_window.id,
            code=LIMIT_CONTRACT_UPLOADS_PER_MONTH,
            amount=2,
            actor_user_id=None,
        )
        == 2
    )
    usage = await monthly_quota_service.get_usage(db_session, _ents(), after_window.id)
    assert LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD not in usage
    assert usage[LIMIT_CONTRACT_UPLOADS_PER_MONTH].extra == 2
