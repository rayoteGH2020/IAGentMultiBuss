"""Histórico visible por plan (``history_months``; D017, bloque 6)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.core.billing_period import history_visible_from, local_date
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.core.uploads import UploadValidationError
from app.models import (
    Contract,
    ContractLifecycle,
    ContractStatus,
    DocTypeCode,
    Invoice,
    InvoiceStatus,
    Tenant,
    Ticket,
    TicketStatus,
)
from app.schemas.document_query import AggregateGroupBy, AggregateMetric, DocumentSearchFilters
from app.services import (
    contract_quota_service,
    contract_service,
    document_panel_service,
    document_query_service,
    document_quota_service,
    document_upload_service,
    invoice_service,
    ticket_service,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_TODAY = local_date()
_OLD = _TODAY - timedelta(days=500)  # fuera de 12 meses, dentro de 36
_RECENT = _TODAY - timedelta(days=20)


async def _tenant(
    db: AsyncSession, factory: Callable[..., Coroutine[Any, Any, Tenant]], plan: str = "basic"
) -> Tenant:
    tenant = await factory(plan_code=plan)
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def _invoice(
    db: AsyncSession, tenant: Tenant, *, proveedor: str, fecha: date | None
) -> Invoice:
    invoice = await invoice_service.create_invoice_stub(
        db,
        tenant.id,
        source_file_key=f"t/{uuid4()}.pdf",
        source_filename="f.pdf",
        source_mime="application/pdf",
    )
    invoice.proveedor = proveedor
    invoice.fecha = fecha
    invoice.status = InvoiceStatus.ready
    await db.flush()
    return invoice


async def _ticket(db: AsyncSession, tenant: Tenant, *, comercio: str, fecha: date) -> Ticket:
    ticket = await ticket_service.create_ticket_stub(
        db,
        tenant.id,
        source_file_key=f"t/{uuid4()}.jpg",
        source_filename="t.jpg",
        source_mime="image/jpeg",
    )
    ticket.comercio = comercio
    ticket.fecha = fecha
    ticket.status = TicketStatus.ready
    await db.flush()
    return ticket


async def _contract(
    db: AsyncSession,
    tenant: Tenant,
    *,
    parte: str,
    fecha_fin: date | None,
    replaced_at: datetime | None = None,
) -> Contract:
    contract = await contract_service.create_contract_stub(
        db,
        tenant.id,
        source_file_key=f"t/{uuid4()}.pdf",
        source_filename="c.pdf",
        source_mime="application/pdf",
    )
    contract.parte_contraria = parte
    contract.fecha_inicio = _TODAY - timedelta(days=2000)
    contract.fecha_fin = fecha_fin
    contract.status = ContractStatus.ready
    if replaced_at is not None:
        contract.lifecycle = ContractLifecycle.replaced
        contract.replaced_at = replaced_at
    await db.flush()
    return contract


async def _panel_ids(db: AsyncSession, tenant: Tenant) -> set[Any]:
    ctx = await document_panel_service.build_invoices_panel_ctx(db, tenant.id)
    return {row.id for row in ctx["documents"]}  # type: ignore[attr-defined]


async def _search(db: AsyncSession, tenant: Tenant, doc_type: DocTypeCode) -> set[Any]:
    page = await document_query_service.search_documents(
        db,
        tenant.id,
        doc_type_code=doc_type.value,
        filters=DocumentSearchFilters(limit=100, incluir_sustituidos=True),
    )
    return {item.id for item in page.items}


async def test_basic_hides_invoices_and_tickets_older_than_12_months(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    recent = await _invoice(db_session, tenant, proveedor="Reciente SL", fecha=_RECENT)
    old = await _invoice(db_session, tenant, proveedor="Antiguo SL", fecha=_OLD)
    undated = await _invoice(db_session, tenant, proveedor="Sin fecha SL", fecha=None)
    old_ticket = await _ticket(db_session, tenant, comercio="Bar Viejo", fecha=_OLD)
    new_ticket = await _ticket(db_session, tenant, comercio="Bar Nuevo", fecha=_RECENT)

    panel = await _panel_ids(db_session, tenant)
    assert {recent.id, undated.id, new_ticket.id} <= panel
    assert old.id not in panel and old_ticket.id not in panel

    assert await _search(db_session, tenant, DocTypeCode.factura) == {recent.id, undated.id}
    assert await _search(db_session, tenant, DocTypeCode.ticket) == {new_ticket.id}
    count = await document_query_service.aggregate_documents(
        db_session,
        tenant.id,
        doc_type_code=DocTypeCode.factura.value,
        filters=DocumentSearchFilters(),
        metric=AggregateMetric.metric_count,
        group_by=AggregateGroupBy.none,
    )
    assert count.total_value == 2
    parties = await document_query_service.list_document_parties(
        db_session, tenant.id, doc_type_code=DocTypeCode.factura.value
    )
    assert "Antiguo SL" not in parties and "Reciente SL" in parties

    # Fuera del histórico, para el chat no existe.
    with pytest.raises(NotFoundError):
        await document_query_service.get_document(
            db_session, tenant.id, doc_type_code=DocTypeCode.factura.value, document_id=old.id
        )
    detail = await document_query_service.get_document(
        db_session, tenant.id, doc_type_code=DocTypeCode.factura.value, document_id=recent.id
    )
    assert detail.id == recent.id


async def test_upgrading_plan_shows_hidden_history_again(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    old = await _invoice(db_session, tenant, proveedor="Antiguo SL", fecha=_OLD)
    ancient = await _invoice(
        db_session, tenant, proveedor="Muy antiguo SL", fecha=_TODAY - timedelta(days=1500)
    )
    assert old.id not in await _panel_ids(db_session, tenant)

    tenant.plan_code = "advanced"  # 36 meses
    await db_session.flush()
    visible = await _search(db_session, tenant, DocTypeCode.factura)
    assert old.id in visible and ancient.id not in visible

    tenant.plan_code = "premium"  # sin límite
    await db_session.flush()
    assert {old.id, ancient.id} <= await _search(db_session, tenant, DocTypeCode.factura)


async def test_contracts_follow_validity_rule(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    long_ago = datetime.now(UTC) - timedelta(days=800)
    in_force = await _contract(db_session, tenant, parte="Vigente", fecha_fin=None)
    future_end = await _contract(
        db_session, tenant, parte="Vence luego", fecha_fin=_TODAY + timedelta(days=90)
    )
    recently_expired = await _contract(
        db_session, tenant, parte="Vencido reciente", fecha_fin=_RECENT
    )
    long_expired = await _contract(db_session, tenant, parte="Vencido antiguo", fecha_fin=_OLD)
    replaced_old = await _contract(
        db_session, tenant, parte="Sustituido antiguo", fecha_fin=None, replaced_at=long_ago
    )
    replaced_future_end = await _contract(
        db_session,
        tenant,
        parte="Sustituido con fin",
        fecha_fin=_TODAY + timedelta(days=30),
        replaced_at=long_ago,
    )

    visible = await _search(db_session, tenant, DocTypeCode.contrato)

    assert visible == {in_force.id, future_end.id, recently_expired.id, replaced_future_end.id}
    assert long_expired.id not in await _panel_ids(db_session, tenant)
    assert replaced_old.id not in await _panel_ids(db_session, tenant)


async def test_replaced_at_is_set_and_cleared(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    contract = await _contract(db_session, tenant, parte="Acme", fecha_fin=None)

    await contract_quota_service.mark_replaced(
        db_session, tenant_id=tenant.id, contract_id=contract.id, user_id=None
    )
    assert contract.replaced_at is not None

    ents = await document_upload_service.entitlement_service.resolve_tenant(db_session, tenant.id)
    await contract_quota_service.reactivate(
        db_session, ents, tenant_id=tenant.id, contract_id=contract.id, user_id=None
    )
    assert contract.replaced_at is None


async def test_duplicate_of_hidden_invoice_explains_history_limit(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    content = b"%PDF-1.4 old invoice"
    old = await _invoice(db_session, tenant, proveedor="Antiguo SL", fecha=_OLD)
    old.file_sha256 = document_quota_service.file_sha256(content)
    await db_session.flush()

    with pytest.raises(UploadValidationError, match="fuera de los 12 meses de histórico"):
        await document_upload_service.ingest_uploaded_document(
            db_session,
            tenant_id=tenant.id,
            filename="vieja.pdf",
            file_bytes=content,
            mime_type="application/pdf",
            doc_type=DocTypeCode.factura,
        )

    match = await document_quota_service.find_duplicate(
        db_session,
        tenant.id,
        old.file_sha256 or "",
        visible_from=history_visible_from(12),
    )
    assert match is not None and match.hidden_by_history
