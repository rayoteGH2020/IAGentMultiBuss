"""Tests de asignacion de planes SADM (Paso05)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.core.entitlement_codes import OVERRIDE_SETTINGS_KEY
from app.core.errors import ValidationError
from app.models import AuditLog, Tenant
from app.schemas.entitlements import EntitlementsOverride
from app.services import plan_service
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
async def plans_catalog_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'plans'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run migration p64_plans_entitlements_01.")
    await plan_service.seed_plan_catalog(db_session)


async def test_assign_tenant_plan_writes_audit(
    db_session: AsyncSession,
    plans_catalog_ready: None,
    audit_schema_ready: None,
) -> None:
    from app.core.db import set_tenant_context

    tenant = Tenant(
        name=f"Plan assign {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    actor = None
    updated = await plan_service.assign_tenant_plan(
        db_session,
        tenant_id=tenant.id,
        plan_code="medium",
        actor_user_id=actor,
        reason="upgrade test",
    )
    assert updated.plan_code == "medium"
    assert updated.plan == "medium"

    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.action == plan_service.ACTION_PLAN_ASSIGNED,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].metadata_["to_plan_code"] == "medium"
    assert rows[0].metadata_["from_plan_code"] == "basic"


async def test_assign_invalid_plan_raises(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(name=f"Bad plan {uuid4().hex[:8]}", plan_code="basic", plan="basic")
    db_session.add(tenant)
    await db_session.flush()

    with pytest.raises(ValidationError):
        await plan_service.assign_tenant_plan(
            db_session,
            tenant_id=tenant.id,
            plan_code="not_a_plan",
            actor_user_id=None,
        )


async def test_set_and_clear_override(
    db_session: AsyncSession,
    plans_catalog_ready: None,
    audit_schema_ready: None,
) -> None:
    from app.core.db import set_tenant_context

    tenant = Tenant(
        name=f"Override {uuid4().hex[:8]}",
        plan_code="basic",
        plan="basic",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    override = EntitlementsOverride(features={"knowledge": True})
    updated = await plan_service.set_tenant_entitlements_override(
        db_session,
        tenant_id=tenant.id,
        override=override,
        actor_user_id=None,
    )
    assert OVERRIDE_SETTINGS_KEY in updated.settings
    assert updated.settings[OVERRIDE_SETTINGS_KEY]["features"]["knowledge"] is True

    cleared = await plan_service.set_tenant_entitlements_override(
        db_session,
        tenant_id=tenant.id,
        override=None,
        actor_user_id=None,
    )
    assert OVERRIDE_SETTINGS_KEY not in (cleared.settings or {})
