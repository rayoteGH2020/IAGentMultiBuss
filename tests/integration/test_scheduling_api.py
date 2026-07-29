"""Tests HTTP del endpoint JSON find-slots (Paso 30 D.2)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from tests.integration.scheduling_test_helpers import auth_headers, csrf_headers

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Europe/Madrid")


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> object:
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
            from datetime import UTC
            from datetime import datetime as dt

            from app.main import app
            from app.models import Membership, Tenant, User

            now = dt.now(tz=UTC)
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

            monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", _resolve)
            self.user = user
            self.tenant = tenant
            return TestClient(app, raise_server_exceptions=False)

    return AuthClient()


def test_api_find_slots_requires_create_or_edit(api_client: object) -> None:
    client = api_client.mount(
        permissions={
            "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
        },
    )
    response = client.post(
        "/api/v1/scheduling/find-slots",
        headers={
            **auth_headers(),
            **csrf_headers(api_client.user, api_client.tenant),
            "Accept": "application/json",
        },
        json={
            "service_id": str(uuid4()),
            "after": datetime(2026, 7, 15, 10, 0, tzinfo=TZ).isoformat(),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_api_find_slots_returns_json(api_client: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.scheduling import AvailableSlot, FindSlotsResponse

    service_id = uuid4()
    start = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)

    async def _fake_find(db: object, tenant_id: object, request: object) -> FindSlotsResponse:
        return FindSlotsResponse(
            slots=[
                AvailableSlot(
                    professional_id=uuid4(),
                    professional_name="Ana",
                    start=start,
                    end=start.replace(hour=10, minute=30),
                    service_id=service_id,
                    service_name="Consulta",
                )
            ],
            same_start_time_warning=False,
        )

    monkeypatch.setattr(
        "app.routes.api.scheduling.appointment_slot_service.find_next_available_slots",
        _fake_find,
    )
    client = api_client.mount(
        permissions={
            "appointments": {"view": True, "create": True, "edit": False, "cancel": False}
        },
    )
    response = client.post(
        "/api/v1/scheduling/find-slots",
        headers={
            **auth_headers(),
            **csrf_headers(api_client.user, api_client.tenant),
            "Accept": "application/json",
        },
        json={
            "service_id": str(service_id),
            "after": start.isoformat(),
            "count": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["same_start_time_warning"] is False
    assert len(body["slots"]) == 1
    assert body["slots"][0]["professional_name"] == "Ana"
    assert body["slots"][0]["service_name"] == "Consulta"
