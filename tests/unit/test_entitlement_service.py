"""Tests unitarios de resolucion de entitlements (Paso02)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.entitlement_codes import (
    FEATURE_ANALYTICS,
    FEATURE_APPOINTMENTS,
    FEATURE_CALENDAR_GOOGLE,
    FEATURE_KNOWLEDGE,
    LIMIT_DOCUMENTS_PER_DAY,
    LIMIT_LLM_BUDGET_EUR_MONTH,
    PLAN_CODE_ADVANCED,
    PLAN_CODE_BASIC,
    PLAN_CODE_PREMIUM,
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


def test_normalize_legacy_aliases() -> None:
    assert normalize_plan_code("free") == PLAN_CODE_BASIC
    assert normalize_plan_code("FREE") == PLAN_CODE_BASIC
    assert normalize_plan_code("medium") == PLAN_CODE_BASIC
    assert normalize_plan_code("high") == PLAN_CODE_ADVANCED
    assert normalize_plan_code("total") == PLAN_CODE_PREMIUM
    assert normalize_plan_code("basic") == PLAN_CODE_BASIC
    assert normalize_plan_code(None) == PLAN_CODE_BASIC
    assert normalize_plan_code("ghost") == "ghost"


def test_basic_catalog_includes_knowledge_excludes_channels() -> None:
    feats = catalog_features_for(PLAN_CODE_BASIC)
    assert FEATURE_KNOWLEDGE in feats
    assert FEATURE_APPOINTMENTS not in feats
    assert FEATURE_CALENDAR_GOOGLE not in feats
    assert FEATURE_ANALYTICS not in feats


def test_advanced_and_premium_share_features_differ_limits() -> None:
    assert catalog_features_for(PLAN_CODE_ADVANCED) == catalog_features_for(PLAN_CODE_PREMIUM)
    assert FEATURE_APPOINTMENTS in catalog_features_for(PLAN_CODE_ADVANCED)
    assert FEATURE_CALENDAR_GOOGLE not in catalog_features_for(PLAN_CODE_PREMIUM)
    assert catalog_limits_for(PLAN_CODE_BASIC)[LIMIT_LLM_BUDGET_EUR_MONTH] == Decimal("30")
    assert catalog_limits_for(PLAN_CODE_ADVANCED)[LIMIT_LLM_BUDGET_EUR_MONTH] == Decimal("100")
    assert catalog_limits_for(PLAN_CODE_PREMIUM)[LIMIT_LLM_BUDGET_EUR_MONTH] == Decimal("250")
    assert catalog_limits_for(PLAN_CODE_PREMIUM)[LIMIT_DOCUMENTS_PER_DAY] == Decimal("800")


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
        plan_code=PLAN_CODE_PREMIUM,
        features=frozenset(),
        limits={LIMIT_LLM_BUDGET_EUR_MONTH: None},
    )
    assert ents.limit(LIMIT_LLM_BUDGET_EUR_MONTH) is None


def test_entitlements_from_rows_parses_features_and_limits() -> None:
    plan_id = uuid4()
    rows = [
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="feature",
            code="documents",
            enabled=True,
            limit_value=None,
        ),
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="feature",
            code="knowledge",
            enabled=False,
            limit_value=None,
        ),
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="limit",
            code=LIMIT_DOCUMENTS_PER_DAY,
            enabled=None,
            limit_value=Decimal("30"),
        ),
        PlanEntitlement(
            id=uuid4(),
            plan_id=plan_id,
            kind="limit",
            code=LIMIT_LLM_BUDGET_EUR_MONTH,
            enabled=None,
            limit_value=None,
        ),
    ]
    ents = entitlements_from_rows("basic", rows)
    assert ents.has("documents") is True
    assert ents.has("knowledge") is False
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("30")
    assert ents.limit(LIMIT_LLM_BUDGET_EUR_MONTH) is None


def test_parse_override_rejects_unknown_feature() -> None:
    with pytest.raises(ValidationError, match="Invalid entitlements_override"):
        parse_override({"features": {"not_real": True}})


def test_parse_override_rejects_unknown_limit() -> None:
    with pytest.raises(ValidationError, match="Invalid entitlements_override"):
        parse_override({"limits": {"not_real": 1}})


def test_apply_override_can_disable_and_raise_limits() -> None:
    base = Entitlements(
        plan_code=PLAN_CODE_BASIC,
        features=frozenset({"documents", "knowledge"}),
        limits={LIMIT_DOCUMENTS_PER_DAY: Decimal("30")},
    )
    override = EntitlementsOverride(
        features={"knowledge": False, "appointments": True},
        limits={LIMIT_DOCUMENTS_PER_DAY: Decimal("12")},
    )
    merged = apply_override(base, override)
    assert merged.has("documents") is True
    assert merged.has("knowledge") is False
    assert merged.has("appointments") is True
    assert merged.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("12")


def test_apply_kill_switch_removes_features() -> None:
    base = Entitlements(
        plan_code=PLAN_CODE_PREMIUM,
        features=frozenset({"documents", "analytics"}),
        limits={},
    )
    gated = apply_kill_switch(base, ["analytics", "not_a_feature"])
    assert gated.has("documents") is True
    assert gated.has(FEATURE_ANALYTICS) is False


def test_fail_closed_denies_everything() -> None:
    ents = fail_closed_entitlements("ghost")
    assert ents.features == frozenset()
    assert ents.has("documents") is False
    assert ents.limit(LIMIT_DOCUMENTS_PER_DAY) == Decimal("0")
