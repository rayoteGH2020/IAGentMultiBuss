"""Tests de force password reset (Paso 50 / Paso 24 Fase A)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import Membership, Tenant, User
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select, text

pytestmark = pytest.mark.integration


def _fake_session(
    *,
    org_id: str,
    user_sub: str,
    force_password_reset: bool,
) -> object:
    async def _resolve(request: Request) -> None:
        now = datetime.now(tz=UTC)
        user_id = uuid4()
        tenant_id = uuid4()
        user = User(
            clerk_user_id=user_sub,
            email=f"{user_sub}@test.local",
            name="Test",
            force_password_reset=force_password_reset,
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
            role="member",
            permissions={
                "appointments": {"view": True, "create": False, "edit": False, "cancel": False}
            },
            created_at=now,
            updated_at=now,
        )
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership
        request.state.force_password_reset = force_password_reset

    return _resolve


def test_redirect_on_force_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    org_id = f"org_{uuid4().hex[:12]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session(org_id=org_id, user_sub=user_sub, force_password_reset=True),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert r.headers.get("location") == "/auth/change-password"


def test_no_redirect_on_change_password_path(monkeypatch: pytest.MonkeyPatch) -> None:
    org_id = f"org_{uuid4().hex[:12]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session(org_id=org_id, user_sub=user_sub, force_password_reset=True),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/auth/change-password",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200
    assert "contraseña nueva" in r.text.lower()


def test_no_redirect_when_flag_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    org_id = f"org_{uuid4().hex[:12]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session(org_id=org_id, user_sub=user_sub, force_password_reset=False),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
            follow_redirects=False,
        )
    assert r.status_code in (200, 302)
    if r.status_code == 302:
        assert r.headers.get("location") != "/auth/change-password"


@pytest.mark.asyncio
async def test_complete_reset_clears_flag(db_session) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'users' "
            "AND column_name = 'force_password_reset'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run p53_force_password_reset migration.")

    user = User(
        clerk_user_id=f"user_{uuid4().hex[:12]}",
        email=f"{uuid4().hex}@example.com",
        name="Reset Test",
        force_password_reset=True,
    )
    db_session.add(user)
    await db_session.flush()

    from app.routes.web.auth import complete_password_reset

    response = await complete_password_reset(user, db_session)
    assert response.status_code == 302
    assert response.headers.get("location") == "/"

    result = await db_session.execute(select(User).where(User.id == user.id))
    db_user = result.scalar_one()
    assert db_user.force_password_reset is False
