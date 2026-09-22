"""Integracion del catalogo de planes (Paso02)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.entitlement_codes import (
    FEATURE_ANALYTICS,
    FEATURE_KNOWLEDGE,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_LLM_BUDGET_EUR_MONTH,
    OVERRIDE_SETTINGS_KEY,
    PLAN_CODES,
)
from app.core.errors import ValidationError
from app.models import Tenant
from app.services import entitlement_service, plan_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@pytest.fixture
async def plans_catalog_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'plans'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run migration p64_plans_entitlements_01 (`alembic upgrade head`).")
    # Idempotente por si el entorno de test no aplico el seed SQL.
    await plan_service.seed_plan_catalog(db_session)


@pytest.mark.asyncio
async def test_seed_creates_four_plans(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    plans = await plan_service.list_plans(db_session, active_only=False)
    codes = {plan.code for plan in plans}
    assert codes >= PLAN_CODES


@pytest.mark.asyncio
async def test_basic_has_no_knowledge_total_has_analytics(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    basic = await plan_service.require_plan_by_code(db_session, "basic")
    total = await plan_service.require_plan_by_code(db_session, "total")

    basic_ents = entitlement_service.entitlements_from_rows("basic", list(basic.entitlements))
    total_ents = entitlement_service.entitlements_from_rows("total", list(total.entitlements))

    assert basic_ents.has(FEATURE_KNOWLEDGE) is False
    assert total_ents.has(FEATURE_ANALYTICS) is True
    assert total_ents.limit(LIMIT_LLM_BUDGET_EUR_MONTH) is None
    assert basic_ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("30")


@pytest.mark.asyncio
async def test_resolve_tenant_uses_plan_code_and_override(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Plan resolve {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
        settings={
            OVERRIDE_SETTINGS_KEY: {
                "features": {"knowledge": True},
                "limits": {LIMIT_DOCUMENTS_PER_DAY: 12},
            }
        },
    )
    db_session.add(tenant)
    await db_session.flush()

    ents = await entitlement_service.resolve_entitlements(db_session, tenant)
    assert ents.plan_code == "basic"
    assert ents.fail_closed is False
    assert ents.has(FEATURE_KNOWLEDGE) is True
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("12")


@pytest.mark.asyncio
async def test_legacy_free_plan_field_resolves_as_basic(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    """Si solo queda el campo legacy ``plan=free``, se resuelve como basic."""
    tenant = Tenant(
        name=f"Legacy free {uuid4().hex[:8]}",
        plan="free",
        plan_code="free",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()

    ents = await entitlement_service.resolve_entitlements(db_session, tenant)
    assert ents.plan_code == "basic"
    assert ents.fail_closed is False
    assert ents.has("documents") is True


@pytest.mark.asyncio
async def test_missing_plan_fail_closed(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Ghost plan {uuid4().hex[:8]}",
        plan="ghost",
        plan_code="ghost",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()

    ents = await entitlement_service.resolve_entitlements(db_session, tenant)
    assert ents.fail_closed is True
    assert ents.has("documents") is False
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("0")


@pytest.mark.asyncio
async def test_override_unknown_code_fails_on_resolve(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Bad override {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
        settings={OVERRIDE_SETTINGS_KEY: {"features": {"telepathy": True}}},
    )
    db_session.add(tenant)
    await db_session.flush()

    with pytest.raises(ValidationError):
        await entitlement_service.resolve_entitlements(db_session, tenant)
