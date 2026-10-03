"""Máximo de miembros por plan (D022): 3 / 9 / 20, admin incluido."""

from __future__ import annotations

import re
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.entitlement_codes import LIMIT_MEMBERS_MAX, PLAN_FEATURES, PLAN_LIMITS
from app.core.errors import ValidationError
from app.core.templating import templates
from app.schemas.entitlements import Entitlements, QuotaUsage
from app.services import plan_quota_service


def _ents(plan_code: str, **overrides: Decimal | None) -> Entitlements:
    limits = dict(PLAN_LIMITS[plan_code])
    limits.update(overrides)
    return Entitlements(plan_code=plan_code, features=PLAN_FEATURES[plan_code], limits=limits)


def test_catalog_members_max_per_plan() -> None:
    assert PLAN_LIMITS["basic"][LIMIT_MEMBERS_MAX] == Decimal("3")
    assert PLAN_LIMITS["advanced"][LIMIT_MEMBERS_MAX] == Decimal("9")
    assert PLAN_LIMITS["premium"][LIMIT_MEMBERS_MAX] == Decimal("20")


@pytest.mark.asyncio
@pytest.mark.parametrize(("plan_code", "cap"), [("basic", 3), ("advanced", 9), ("premium", 20)])
async def test_ensure_member_capacity_allows_up_to_cap_and_rejects_next(
    monkeypatch: pytest.MonkeyPatch, plan_code: str, cap: int
) -> None:
    ents = _ents(plan_code)
    count = AsyncMock(return_value=cap - 1)
    monkeypatch.setattr(plan_quota_service, "_count_active_members", count)
    await plan_quota_service.ensure_member_capacity(AsyncMock(), ents, uuid4())

    count.return_value = cap
    with pytest.raises(ValidationError):
        await plan_quota_service.ensure_member_capacity(AsyncMock(), ents, uuid4())


@pytest.mark.asyncio
async def test_get_member_usage_counts_active_members_against_plan_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plan_quota_service, "_count_active_members", AsyncMock(return_value=5))

    usage = await plan_quota_service.get_member_usage(AsyncMock(), _ents("basic"), uuid4())

    assert usage == QuotaUsage(used=5, cap=3)


@pytest.mark.asyncio
async def test_get_member_usage_respects_tenant_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_quota_service, "_count_active_members", AsyncMock(return_value=5))

    raised = await plan_quota_service.get_member_usage(
        AsyncMock(), _ents("basic", members_max=Decimal("6")), uuid4()
    )
    unlimited = await plan_quota_service.get_member_usage(
        AsyncMock(), _ents("basic", members_max=None), uuid4()
    )

    assert raised.cap == 6
    assert unlimited.cap is None


# ── Plantilla /settings/members ────────────────────────────────────────────


def _render(member_usage: QuotaUsage | None) -> str:
    template = templates.env.get_template("pages/settings/members.html")
    ctx = template.new_context(
        {"members": [], "removable_ids": set(), "member_usage": member_usage}
    )
    return re.sub(r"\s+", " ", "".join(template.blocks["settings_content"](ctx)))


def test_members_page_shows_usage_without_warning_below_cap() -> None:
    html = _render(QuotaUsage(used=2, cap=3))

    assert "Miembros: 2 de 3 que admite tu plan." in html
    assert "member-usage-full" not in html
    assert "member-usage-over" not in html


def test_members_page_warns_when_cap_reached() -> None:
    html = _render(QuotaUsage(used=3, cap=3))

    assert "member-usage-full" in html
    assert "member-usage-over" not in html


def test_members_page_keeps_existing_members_when_over_cap() -> None:
    html = _render(QuotaUsage(used=5, cap=3))

    assert "member-usage-over" in html
    assert "Los actuales siguen activos" in html
    assert "member-usage-full" not in html


@pytest.mark.parametrize("usage", [None, QuotaUsage(used=7, cap=None)])
def test_members_page_without_cap_shows_no_usage(usage: QuotaUsage | None) -> None:
    html = _render(usage)

    assert 'id="member-usage"' not in html
