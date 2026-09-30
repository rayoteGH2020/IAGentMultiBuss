"""/settings/members muestra miembros frente al máximo del plan (D022)."""

# F811: la fixture client_with_auth se importa para reutilizarla y pytest la
# inyecta con el mismo nombre en cada test.
# ruff: noqa: F811

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.core.entitlement_codes import PLAN_FEATURES, PLAN_LIMITS
from app.schemas.entitlements import Entitlements, QuotaUsage

from tests.integration.scheduling_test_helpers import auth_headers
from tests.integration.test_appointments_routes_permissions import (  # noqa: F401 (fixture)
    client_with_auth,
)

pytestmark = pytest.mark.integration

_ALL_PERMS = {"appointments": {"view": True, "create": True, "edit": True, "cancel": True}}
_BASIC = Entitlements(
    plan_code="basic", features=PLAN_FEATURES["basic"], limits=dict(PLAN_LIMITS["basic"])
)


@pytest.mark.parametrize(
    ("used", "marker"),
    [(2, None), (3, "member-usage-full"), (5, "member-usage-over")],
)
def test_members_page_renders_member_usage(
    client_with_auth: object, used: int, marker: str | None
) -> None:
    client = client_with_auth.mount(permissions=_ALL_PERMS, role="admin")  # type: ignore[attr-defined]
    usage = AsyncMock(return_value=QuotaUsage(used=used, cap=3))
    with (
        patch("app.deps.entitlement_service.resolve_entitlements", AsyncMock(return_value=_BASIC)),
        patch(
            "app.routes.web.settings_scheduling.membership_service.list_tenant_members",
            AsyncMock(return_value=[]),
        ),
        patch("app.routes.web.settings_scheduling.plan_quota_service.get_member_usage", usage),
    ):
        r = client.get("/settings/members", headers=auth_headers())

    assert r.status_code == 200
    assert f"Miembros: {used} de 3 que admite tu plan." in r.text
    for other in ("member-usage-full", "member-usage-over"):
        assert (other in r.text) is (other == marker)
    assert usage.await_args.args[1] is _BASIC
