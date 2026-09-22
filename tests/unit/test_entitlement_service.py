"""Tests unitarios de resolucion de entitlements (Paso02)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.entitlement_codes import (
    FEATURE_ANALYTICS,
    FEATURE_KNOWLEDGE,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_LLM_BUDGET_EUR_MONTH,
    PLAN_CODE_BASIC,
    PLAN_CODE_TOTAL,
    normalize_plan_code,
)
from app.core.errors import ValidationError
from app.models.plan import PlanEntitlement
from app.schemas.entitlements import Entitlements, EntitlementsOverride
from app.services.entitlement_service import (
    apply_kill_switch,
    apply_override,
    entitlements_from_rows,
    fail_closed_entitlements,
    parse_override,
)
from app.services.plan_service import catalog_features_for, catalog_limits_for


def test_normalize_free_legacy_to_basic() -> None:
    assert normalize_plan_code("free") == PLAN_CODE_BASIC
    assert normalize_plan_code("FREE") == PLAN_CODE_BASIC
    assert normalize_plan_code("basic") == PLAN_CODE_BASIC
    assert normalize_plan_code(None) == PLAN_CODE_BASIC
    assert normalize_plan_code("ghost") == "ghost"


def test_basic_catalog_has_no_knowledge() -> None:
    assert FEATURE_KNOWLEDGE not in catalog_features_for(PLAN_CODE_BASIC)
    assert FEATURE_ANALYTICS not in catalog_features_for(PLAN_CODE_BASIC)


def test_total_catalog_has_analytics_and_null_budget() -> None:
    assert FEATURE_ANALYTICS in catalog_features_for(PLAN_CODE_TOTAL)
    assert catalog_limits_for(PLAN_CODE_TOTAL)[LIMIT_LLM_BUDGET_EUR_MONTH] is None


def test_entitlements_unknown_feature_denied() -> None:
    ents = Entitlements(
        plan_code=PLAN_CODE_BASIC,
        features=frozenset({"documents"}),
        limits={},
    )
    assert ents.has("documents") is True
    assert ents.has("not_a_real_feature") is False
    assert ents.has(FEATURE_KNOWLEDGE) is False


def test_entitlements_unknown_limit_is_zero() -> None:
    ents = Entitlements(
        plan_code=PLAN_CODE_BASIC,
        features=frozenset(),
        limits={LIMIT_DOCUMENTS_PER_DAY: Decimal("30")},
    )
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("30")
    assert ents.limit("not_a_real_limit") == Decimal("0")
    assert ents.limit("members_max") == Decimal("0")


def test_null_limit_means_unlimited() -> None:
    ents = Entitlements(
        plan_code=PLAN_CODE_TOTAL,
        features=frozenset(),
        limits={LIMIT_LLM_BUDGET_EUR_MONTH: None},
    )
    assert ents.limit(LIMIT_LLM_BUDGET_EUR_MONTH) is None


def test_fail_closed_has_no_features() -> None:
    ents = fail_closed_entitlements("ghost")
    assert ents.fail_closed is True
    assert ents.has("documents") is False
    assert ents.has(FEATURE_ANALYTICS) is False
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("0")


def test_entitlements_from_rows_maps_features_and_limits() -> None:
    plan_id = uuid4()
    rows = [
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="feature",
            code="documents",
            enabled=True,
        ),
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="feature",
            code="knowledge",
            enabled=False,
        ),
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="limit",
            code=LIMIT_DOCUMENTS_PER_DAY,
            limit_value=Decimal("30"),
        ),
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="limit",
            code=LIMIT_LLM_BUDGET_EUR_MONTH,
            limit_value=None,
        ),
    ]
    ents = entitlements_from_rows(PLAN_CODE_BASIC, rows)
    assert ents.has("documents") is True
    assert ents.has(FEATURE_KNOWLEDGE) is False
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("30")
    assert ents.limit(LIMIT_LLM_BUDGET_EUR_MONTH) is None


def test_override_can_reduce_and_amplify() -> None:
    base = Entitlements(
        plan_code=PLAN_CODE_BASIC,
        features=frozenset({"documents", "documents_chat"}),
        limits={
            LIMIT_DOCUMENTS_PER_DAY: Decimal("30"),
            LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("5"),
        },
    )
    override = EntitlementsOverride(
        features={"documents_chat": False, "knowledge": True},
        limits={LIMIT_DOCUMENTS_PER_DAY: Decimal("10"), LIMIT_LLM_BUDGET_EUR_MONTH: None},
    )
    merged = apply_override(base, override)
    assert merged.has("documents") is True
    assert merged.has("documents_chat") is False
    assert merged.has(FEATURE_KNOWLEDGE) is True
    assert merged.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("10")
    assert merged.limit(LIMIT_LLM_BUDGET_EUR_MONTH) is None


def test_override_unknown_code_fails() -> None:
    with pytest.raises(ValidationError) as exc:
        parse_override({"features": {"telepathy": True}})
    assert "entitlements_override" in str(exc.value.message).lower() or "Invalid" in str(
        exc.value.message
    )

    with pytest.raises(ValidationError):
        parse_override({"limits": {"unicorns_per_day": 1}})


def test_kill_switch_removes_feature() -> None:
    base = Entitlements(
        plan_code=PLAN_CODE_TOTAL,
        features=frozenset({"documents", "analytics"}),
        limits={},
    )
    gated = apply_kill_switch(base, ["analytics", "not_a_feature"])
    assert gated.has("documents") is True
    assert gated.has(FEATURE_ANALYTICS) is False
