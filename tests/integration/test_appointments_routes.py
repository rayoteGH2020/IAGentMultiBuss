"""Tests de permisos en rutas web de citas (Paso 30)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import Membership, Tenant, User
from fastapi import Request
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _fake_session(
    *,
    permissions: dict[str, dict[str, bool]],
    role: str = "member",
) -> object:
    async def _resolve(request: Request) -> None:
        now = datetime.now(tz=UTC)
        user_id = uuid4()
        tenant_id = uuid4()
        user = User(
            clerk_user_id=f"user_{uuid4().hex[:12]}",
            email=f"{uuid4().hex[:8]}@test.local",
            name="Test",
            created_at=now,
            updated_at=now,
        )
        user.id = user_id
        tenant = Tenant(
            clerk_org_id=f"org_{uuid4().hex[:12]}",
            name="Test Org",
            plan="free",
            settings={},
            created_at=now,
            updated_at=now,
        )
        tenant.id = tenant_id
        membership = Membership(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            permissions=permissions,
            created_at=now,
            updated_at=now,
        )
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership
        request.state.is_superadmin = False
        request.state.force_password_reset = False

    return _resolve


def test_appointment_detail_requires_view_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session(
            permissions={
                "appointments": {
                    "view": False,
                    "create": True,
                    "edit": True,
                    "cancel": False,
                }
            },
        ),
    )
    from app.main import app

    appointment_id = uuid4()
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            f"/appointments/{appointment_id}",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_appointment_detail_allowed_with_view_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session(
            permissions={
                "appointments": {
                    "view": True,
                    "create": False,
                    "edit": False,
                    "cancel": False,
                }
            },
        ),
    )
    from app.main import app

    appointment_id = uuid4()
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            f"/appointments/{appointment_id}",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_appointments_index_defaults_to_day_view(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.scheduling import TenantSchedulingSettingsRead

    async def _mock_scheduling_settings(
        db: object, tenant_id: object
    ) -> TenantSchedulingSettingsRead:
        return TenantSchedulingSettingsRead()

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session(
            permissions={
                "appointments": {
                    "view": True,
                    "create": False,
                    "edit": False,
                    "cancel": False,
                }
            },
        ),
    )
    monkeypatch.setattr(
        "app.routes.web.appointments.business_hours_service.get_scheduling_settings",
        _mock_scheduling_settings,
    )
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            "/appointments",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert "view=day" in r.headers["location"]
    assert "date=" in r.headers["location"]
