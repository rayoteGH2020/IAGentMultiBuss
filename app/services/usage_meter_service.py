"""Incremento y lectura de contadores en ``usage_meter`` (Paso20 / Paso04)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.usage_meter import UsageMeter

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def current_billing_period(*, now: datetime | None = None) -> date:
    """Primer dia del mes calendario del instante dado (UTC)."""
    instant = now or datetime.now(tz=UTC)
    return date(instant.year, instant.month, 1)


async def get_meter(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    period: date | None = None,
) -> UsageMeter | None:
    billing_period = period or current_billing_period()
    result = await db.execute(
        select(UsageMeter).where(
            UsageMeter.tenant_id == tenant_id,
            UsageMeter.period == billing_period,
        )
    )
    return result.scalar_one_or_none()


async def get_llm_cost_eur(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    period: date | None = None,
) -> Decimal:
    row = await get_meter(db, tenant_id=tenant_id, period=period)
    if row is None:
        return Decimal("0")
    return row.llm_cost_eur


async def add_llm_cost_eur(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    delta: Decimal,
    period: date | None = None,
) -> None:
    if delta <= 0:
        return
    billing_period = period or current_billing_period()
    insert_stmt = insert(UsageMeter).values(
        tenant_id=tenant_id,
        period=billing_period,
        invoices_count=0,
        rag_messages_count=0,
        # D011: columna reservada del modulo 3 Analytics; no incrementar.
        analytics_queries_count=0,
        llm_cost_eur=delta,
    )
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["tenant_id", "period"],
        set_={
            "llm_cost_eur": UsageMeter.llm_cost_eur + insert_stmt.excluded.llm_cost_eur,
        },
    )
    await db.execute(upsert)


async def increment_rag_messages_count(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    period: date | None = None,
    delta: int = 1,
) -> None:
    """Suma ``delta`` a ``rag_messages_count`` del periodo actual (upsert)."""
    if delta <= 0:
        return
    billing_period = period or current_billing_period()
    insert_stmt = insert(UsageMeter).values(
        tenant_id=tenant_id,
        period=billing_period,
        invoices_count=0,
        rag_messages_count=delta,
        # D011: columna reservada del modulo 3 Analytics; no incrementar.
        analytics_queries_count=0,
        llm_cost_eur=Decimal("0"),
    )
    upsert = insert_stmt.on_conflict_do_update(
        index_elements=["tenant_id", "period"],
        set_={
            "rag_messages_count": (
                UsageMeter.rag_messages_count + insert_stmt.excluded.rag_messages_count
            ),
        },
    )
    await db.execute(upsert)
