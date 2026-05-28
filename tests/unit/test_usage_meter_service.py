"""Tests de usage_meter (Paso 20 H)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.core.db import set_tenant_context
from app.models.usage_meter import UsageMeter
from app.services.usage_meter_service import current_billing_period, increment_rag_messages_count
from sqlalchemy import select

pytestmark = pytest.mark.integration


def test_current_billing_period_first_of_month() -> None:
    instant = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)

    assert current_billing_period(now=instant) == date(2026, 5, 1)


@pytest.mark.asyncio
async def test_increment_rag_messages_count_upserts(
    usage_meter_schema_ready: None,
    db_session,
    tenant_factory,
) -> None:
    tenant = await tenant_factory()

    await set_tenant_context(db_session, str(tenant.id))

    period = date(2026, 4, 1)

    await increment_rag_messages_count(
        db_session,
        tenant_id=tenant.id,
        period=period,
        delta=1,
    )

    await increment_rag_messages_count(
        db_session,
        tenant_id=tenant.id,
        period=period,
        delta=2,
    )

    await db_session.flush()

    row = (
        await db_session.execute(
            select(UsageMeter).where(
                UsageMeter.tenant_id == tenant.id,
                UsageMeter.period == period,
            ),
        )
    ).scalar_one()

    assert row.rag_messages_count == 3

    assert row.invoices_count == 0

    assert row.llm_cost_eur == Decimal("0")
