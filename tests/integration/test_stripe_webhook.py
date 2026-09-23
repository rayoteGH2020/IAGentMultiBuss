"""Integracion ligera: assign_tenant_plan escribe tenant_plan_changes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import Tenant, TenantPlanChange
from app.services import plan_service
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
async def plans_and_history_ready(db_session: AsyncSession) -> None:
    plans = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'plans'"
        )
    )
    if plans.scalar_one_or_none() is None:
        pytest.skip("Run migration p64_plans_entitlements_01.")
    history = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'tenant_plan_changes'"
        )
    )
    if history.scalar_one_or_none() is None:
        pytest.skip("Run migration p65_stripe_billing_01.")
    await plan_service.seed_plan_catalog(db_session)


@pytest.mark.asyncio
async def test_assign_plan_writes_history_row(
    db_session: AsyncSession,
    plans_and_history_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Stripe hist {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
    )
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    await plan_service.assign_tenant_plan(
        db_session,
        tenant_id=tenant.id,
        plan_code="medium",
        actor_user_id=None,
        reason="test",
        source=plan_service.SOURCE_STRIPE,
        metadata={"stripe_event_id": "evt_test"},
    )
    await db_session.flush()

    rows = (
        (
            await db_session.execute(
                select(TenantPlanChange).where(TenantPlanChange.tenant_id == tenant.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].from_plan_code == "basic"
    assert rows[0].to_plan_code == "medium"
    assert rows[0].metadata_ is not None
    assert rows[0].metadata_["source"] == "stripe"

    # Idempotente: mismo plan no duplica historial.
    await plan_service.assign_tenant_plan(
        db_session,
        tenant_id=tenant.id,
        plan_code="medium",
        actor_user_id=None,
        reason="noop",
        source=plan_service.SOURCE_STRIPE,
    )
    await db_session.flush()
    rows2 = (
        (
            await db_session.execute(
                select(TenantPlanChange).where(TenantPlanChange.tenant_id == tenant.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows2) == 1
