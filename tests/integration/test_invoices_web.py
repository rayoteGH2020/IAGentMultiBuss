"""Tests HTTP del listado de facturas."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import Membership, Tenant, User
from fastapi import Request
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _fake_clerk_resolve(request: Request, *, user_sub: str, org_id: str) -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    membership_id = uuid4()
    now = datetime.now(tz=UTC)
    user = User(
        clerk_user_id=user_sub,
        email=f"{user_sub}@test.local",
        name="Test",
        created_at=now,
        updated_at=now,
    )
    user.id = user_id
    tenant = Tenant(
        clerk_org_id=org_id,
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
        role="admin",
        created_at=now,
        updated_at=now,
    )
    membership.id = membership_id
    request.state.user = user
    request.state.tenant = tenant
    request.state.membership = membership


def test_invoices_get_shows_empty_state(
    invoices_migration_applied_sync: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"user_{uuid4().hex[:16]}"
    org_id = f"org_{uuid4().hex[:16]}"

    async def fake_resolve(request: Request) -> None:
        _fake_clerk_resolve(request, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/invoices",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200
    assert "Aún no hay facturas" in r.text


def test_invoices_htmx_returns_fragment_only(
    invoices_migration_applied_sync: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"user_{uuid4().hex[:16]}"
    org_id = f"org_{uuid4().hex[:16]}"

    async def fake_resolve(request: Request) -> None:
        _fake_clerk_resolve(request, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/invoices",
            headers={
                "Authorization": "Bearer fake-jwt",
                "Accept": "text/html",
                "HX-Request": "true",
            },
        )
    assert r.status_code == 200
    assert "Aún no hay facturas" in r.text
    assert "<!DOCTYPE" not in r.text
