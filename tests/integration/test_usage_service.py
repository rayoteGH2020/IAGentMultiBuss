"""Agregación de consumo por tenant para la consola SADM."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import DocTypeCode, Invoice, InvoiceStatus, LLMCall, Tenant
from app.services import doc_type_service, usage_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _llm_call(
    db: AsyncSession,
    tenant: Tenant,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_eur: str,
) -> LLMCall:
    call = LLMCall(
        tenant_id=tenant.id,
        task="extraction",
        model="gemini-2.5-flash",
        provider="google",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_eur=Decimal(cost_eur),
        latency_ms=1200,
        status="ok",
    )
    db.add(call)
    await db.flush()
    return call


async def _invoice(db: AsyncSession, tenant: Tenant, *, status: InvoiceStatus) -> Invoice:
    doc_type_id = await doc_type_service.get_doc_type_id(db, DocTypeCode.factura)
    invoice = Invoice(
        tenant_id=tenant.id,
        doc_type_id=doc_type_id,
        status=status,
        source_file_key=f"invoices/{uuid4()}.pdf",
        source_filename="factura.pdf",
        source_mime="application/pdf",
    )
    db.add(invoice)
    await db.flush()
    return invoice


async def test_usage_aggregates_tokens_cost_and_documents(
    invoices_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await _llm_call(db_session, tenant, input_tokens=1000, output_tokens=400, cost_eur="0.002000")
    await _llm_call(db_session, tenant, input_tokens=500, output_tokens=100, cost_eur="0.001000")
    await _invoice(db_session, tenant, status=InvoiceStatus.ready)
    # Los fallidos no cuentan como documento procesado, aunque hayan costado tokens.
    await _invoice(db_session, tenant, status=InvoiceStatus.failed)

    usage = await usage_service.get_tenant_usage(db_session)

    row = next(item for item in usage if item.tenant_id == tenant.id)
    assert row.llm_calls == 2
    assert row.input_tokens == 1500
    assert row.output_tokens == 500
    assert row.total_tokens == 2000
    assert row.cost_eur == Decimal("0.003000")
    assert row.documents == 1


async def test_usage_lists_tenants_without_consumption(
    invoices_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    """Un tenant sin actividad aparece a cero, no desaparece del listado."""
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    usage = await usage_service.get_tenant_usage(db_session)

    row = next(item for item in usage if item.tenant_id == tenant.id)
    assert row.llm_calls == 0
    assert row.documents == 0
    assert row.cost_eur == Decimal("0")


async def test_usage_ignores_other_periods(
    invoices_schema_ready: None,
    llm_calls_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    await _llm_call(db_session, tenant, input_tokens=900, output_tokens=100, cost_eur="0.005000")

    previous = usage_service.current_period()
    older = (
        previous.replace(year=previous.year - 1, month=12, day=1)
        if previous.month == 1
        else previous.replace(month=previous.month - 1, day=1)
    )
    usage = await usage_service.get_tenant_usage(db_session, period=older)

    row = next(item for item in usage if item.tenant_id == tenant.id)
    assert row.llm_calls == 0
    assert row.cost_eur == Decimal("0")
