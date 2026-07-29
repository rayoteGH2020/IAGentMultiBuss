"""Matriz de permisos HTTP en rutas de citas (Paso 30 §E.4)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.integration.scheduling_test_helpers import (
    auth_headers,
    csrf_headers,
    future_appointment_start,
)

pytestmark = pytest.mark.integration


def _mock_scheduling_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.scheduling import TenantSchedulingSettingsRead

    async def _settings(db: object, tenant_id: object) -> TenantSchedulingSettingsRead:
        return TenantSchedulingSettingsRead()

    monkeypatch.setattr(
        "app.routes.web.appointments.business_hours_service.get_scheduling_settings",
        _settings,
    )


@pytest.fixture
def client_with_auth(monkeypatch: pytest.MonkeyPatch) -> object:
    """Factory: devuelve (TestClient, user, tenant) con sesión Clerk mockeada."""

    class AuthClient:
        user: object
        tenant: object

        def __init__(self) -> None:
            self.user = None
            self.tenant = None

        def mount(
            self,
            *,
            permissions: dict[str, dict[str, bool]],
            role: str = "member",
        ) -> TestClient:
            from datetime import UTC, datetime

            from app.main import app
            from app.models import Membership, Tenant, User

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

            async def _resolve(request: object) -> None:
                request.state.user = user  # type: ignore[attr-defined]
                request.state.tenant = tenant  # type: ignore[attr-defined]
                request.state.membership = membership  # type: ignore[attr-defined]
                request.state.is_superadmin = False  # type: ignore[attr-defined]
                request.state.force_password_reset = False  # type: ignore[attr-defined]

            _mock_scheduling_settings(monkeypatch)
            monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", _resolve)
            self.user = user
            self.tenant = tenant
            return TestClient(app, raise_server_exceptions=False)

    return AuthClient()


def _assert_forbidden(response: object) -> None:
    assert response.status_code == 403  # type: ignore[attr-defined]
    assert response.json()["code"] == "forbidden"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("permissions", "expected_status"),
    [
        ({"appointments": {"view": False, "create": True, "edit": True, "cancel": True}}, 403),
        ({"appointments": {"view": True, "create": False, "edit": False, "cancel": False}}, 200),
    ],
)
def test_appointments_index_respects_view_permission(
    client_with_auth: object,
    permissions: dict[str, dict[str, bool]],
    expected_status: int,
) -> None:
    client = client_with_auth.mount(permissions=permissions)
    response = client.get(
        "/appointments?view=day&date=2026-07-15",
        headers=auth_headers(),
    )
    assert response.status_code == expected_status


def test_appointments_calendar_requires_view(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={"appointments": {"view": False, "create": True, "edit": True, "cancel": True}},
    )
    response = client.get(
        "/appointments/calendar?view=day&date=2026-07-15",
        headers=auth_headers(),
    )
    _assert_forbidden(response)


def test_appointment_new_form_requires_create(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={
            "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
        },
    )
    response = client.get("/appointments/new", headers=auth_headers())
    _assert_forbidden(response)


def test_appointment_create_requires_create(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={"appointments": {"view": True, "create": False, "edit": True, "cancel": True}},
    )
    start = future_appointment_start()
    response = client.post(
        "/appointments",
        headers={**auth_headers(), **csrf_headers(client_with_auth.user, client_with_auth.tenant)},
        data={
            "appointment_date": start.date().isoformat(),
            "start_time": start.strftime("%H:%M"),
            "duration_minutes": "30",
            "client_name": "Ana",
            "client_phone": "600000000",
        },
    )
    _assert_forbidden(response)


def test_appointment_update_requires_edit(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={"appointments": {"view": True, "create": True, "edit": False, "cancel": True}},
    )
    start = future_appointment_start()
    response = client.post(
        f"/appointments/{uuid4()}",
        headers={**auth_headers(), **csrf_headers(client_with_auth.user, client_with_auth.tenant)},
        data={
            "appointment_date": start.date().isoformat(),
            "start_time": start.strftime("%H:%M"),
            "duration_minutes": "30",
            "client_name": "Ana",
            "client_phone": "600000000",
        },
    )
    _assert_forbidden(response)


def test_appointment_cancel_requires_cancel(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={"appointments": {"view": True, "create": True, "edit": True, "cancel": False}},
    )
    response = client.post(
        f"/appointments/{uuid4()}/cancel",
        headers={**auth_headers(), **csrf_headers(client_with_auth.user, client_with_auth.tenant)},
        data={},
    )
    _assert_forbidden(response)


def test_find_slots_requires_create_or_edit(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={
            "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
        },
    )
    response = client.get(
        f"/appointments/find-slots?service_id={uuid4()}&after=2026-07-15T10%3A00%3A00%2B02%3A00",
        headers=auth_headers(),
    )
    _assert_forbidden(response)


def test_find_slots_allowed_with_create(
    client_with_auth: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.scheduling import FindSlotsResponse

    async def _fake_find(db: object, tenant_id: object, request: object) -> FindSlotsResponse:
        return FindSlotsResponse(slots=[])

    monkeypatch.setattr(
        "app.routes.web.appointments.appointment_slot_service.find_next_available_slots",
        _fake_find,
    )
    client = client_with_auth.mount(
        permissions={
            "appointments": {"view": True, "create": True, "edit": False, "cancel": False}
        },
    )
    response = client.get(
        f"/appointments/find-slots?service_id={uuid4()}&after=2026-07-15T10%3A00%3A00%2B02%3A00",
        headers=auth_headers(),
    )
    assert response.status_code == 200


def test_admin_bypasses_missing_view_on_detail(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={
            "appointments": {"view": False, "create": False, "edit": False, "cancel": False}
        },
        role="admin",
    )
    response = client.get(f"/appointments/{uuid4()}", headers=auth_headers())
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_view_only_member_gets_index_200(client_with_auth: object) -> None:
    client = client_with_auth.mount(
        permissions={
            "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
        },
    )
    response = client.get(
        "/appointments?view=day&date=2026-07-15",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert "calendar-grid" in response.text or "Citas" in response.text
