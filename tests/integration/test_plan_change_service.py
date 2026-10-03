"""Cambios de plan del SADM (D027): primera asignación inmediata, el resto el día 1 siguiente."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from app.core.billing_period import current_period_start, next_period_start
from app.core.db import set_tenant_context
from app.core.entitlement_codes import SCHEDULED_PLAN_CHANGE_KEY
from app.models import AuditLog, Tenant, TenantPlanChange
from app.services import entitlement_service, plan_change_service, plan_service
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
async def plans_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'tenant_plan_changes'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run migrations (`alembic upgrade head`).")
    await plan_service.seed_plan_catalog(db_session)


async def _tenant(db: AsyncSession) -> Tenant:
    tenant = Tenant(name=f"Plan change {uuid4().hex[:8]}", plan="basic", plan_code="basic")
    db.add(tenant)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def _history(db: AsyncSession, tenant: Tenant) -> list[TenantPlanChange]:
    await set_tenant_context(db, str(tenant.id))
    rows = await db.execute(
        select(TenantPlanChange)
        .where(TenantPlanChange.tenant_id == tenant.id)
        .order_by(TenantPlanChange.created_at)
    )
    return list(rows.scalars().all())


async def test_first_assignment_is_immediate_even_for_default_plan(
    db_session: AsyncSession, plans_ready: None
) -> None:
    tenant = await _tenant(db_session)

    result = await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="basic", actor_user_id=None
    )

    assert result.applied_now is True
    history = await _history(db_session, tenant)
    assert len(history) == 1
    assert history[0].metadata_ is not None
    assert history[0].metadata_["initial_assignment"] is True


async def test_later_change_is_scheduled_for_next_month(
    db_session: AsyncSession, plans_ready: None
) -> None:
    tenant = await _tenant(db_session)
    await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="basic", actor_user_id=None
    )

    result = await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="advanced", actor_user_id=None, reason="upsell"
    )

    expected = next_period_start(current_period_start())
    assert result.applied_now is False
    assert result.effective_date == expected
    assert tenant.plan_code == "basic"
    change = plan_change_service.scheduled_plan_change(tenant)
    assert change is not None
    assert (change.plan_code, change.effective_date, change.reason) == (
        "advanced",
        expected,
        "upsell",
    )
    # Hoy sigue rigiendo el plan actual.
    assert entitlement_service.resolve_plan_code_for_tenant(tenant) == "basic"
    audits = await db_session.execute(
        select(AuditLog.action).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == plan_change_service.ACTION_PLAN_CHANGE_SCHEDULED,
        )
    )
    assert len(audits.scalars().all()) == 1


async def test_requesting_current_plan_cancels_schedule(
    db_session: AsyncSession, plans_ready: None
) -> None:
    tenant = await _tenant(db_session)
    await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="basic", actor_user_id=None
    )
    await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="premium", actor_user_id=None
    )

    result = await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="basic", actor_user_id=None
    )

    assert result.applied_now is False
    assert result.effective_date is None
    assert plan_change_service.scheduled_plan_change(tenant) is None


async def test_cancel_scheduled_change_is_audited_and_idempotent(
    db_session: AsyncSession, plans_ready: None
) -> None:
    tenant = await _tenant(db_session)
    await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="basic", actor_user_id=None
    )
    await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="advanced", actor_user_id=None
    )

    await plan_change_service.cancel_scheduled_plan_change(
        db_session, tenant_id=tenant.id, actor_user_id=None
    )
    await plan_change_service.cancel_scheduled_plan_change(
        db_session, tenant_id=tenant.id, actor_user_id=None
    )

    assert SCHEDULED_PLAN_CHANGE_KEY not in tenant.settings
    audits = await db_session.execute(
        select(AuditLog.action).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == plan_change_service.ACTION_PLAN_CHANGE_CANCELLED,
        )
    )
    assert len(audits.scalars().all()) == 1


async def test_due_change_rules_by_read_and_cron_persists_it(
    db_session: AsyncSession, plans_ready: None
) -> None:
    tenant = await _tenant(db_session)
    await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="basic", actor_user_id=None
    )
    result = await plan_change_service.request_plan_change(
        db_session, tenant_id=tenant.id, plan_code="advanced", actor_user_id=None
    )
    assert result.effective_date is not None
    effective = result.effective_date

    assert (
        plan_change_service.due_scheduled_plan_code(tenant, today=effective - timedelta(days=1))
        is None
    )
    assert plan_change_service.due_scheduled_plan_code(tenant, today=effective) == "advanced"

    assert (
        await plan_change_service.apply_due_plan_changes(
            db_session, today=effective - timedelta(days=1)
        )
        == 0
    )
    applied = await plan_change_service.apply_due_plan_changes(db_session, today=effective)

    assert applied >= 1
    assert tenant.plan_code == "advanced"
    assert plan_change_service.scheduled_plan_change(tenant) is None
    history = await _history(db_session, tenant)
    assert [h.to_plan_code for h in history] == ["basic", "advanced"]
    assert history[-1].metadata_ is not None
    assert history[-1].metadata_["source"] == plan_change_service.SOURCE_SCHEDULED


async def test_invalid_scheduled_payload_is_ignored(
    db_session: AsyncSession, plans_ready: None
) -> None:
    tenant = await _tenant(db_session)
    tenant.settings = {SCHEDULED_PLAN_CHANGE_KEY: {"plan_code": "advanced"}}

    assert plan_change_service.scheduled_plan_change(tenant) is None
    assert plan_change_service.due_scheduled_plan_code(tenant, today=date(2099, 1, 1)) is None
    assert entitlement_service.resolve_plan_code_for_tenant(tenant) == "basic"
