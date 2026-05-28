"""Incremento de contadores en ``usage_meter`` (Paso 20 H / billing)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert

from app.models.usage_meter import UsageMeter

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def current_billing_period(*, now: datetime | None = None) -> date:
    """Primer día del mes calendario del instante dado (UTC)."""
    instant = now or datetime.now(tz=UTC)
    return date(instant.year, instant.month, 1)


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
