"""Tests de auth Clerk + middleware (sin llamadas reales a Clerk)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.csrf import CSRF_HEADER_NAME, generate_csrf_token
from app.models import Membership, Tenant, User
from fastapi import Request, Response
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _make_clerk_mocks(monkeypatch: pytest.MonkeyPatch, user_sub: str, org_id: str) -> None:
    def verify_ok(_token: str) -> dict[str, object]:
        return {"sub": user_sub, "org_id": org_id}

    async def fetch_user(clerk_user_id: str) -> dict[str, object]:
        return {
            "id": clerk_user_id,
            "email_addresses": [
                {"id": "e1", "email_address": f"{clerk_user_id}@test.local"},
            ],
            "primary_email_address_id": "e1",
            "first_name": "Test",
            "last_name": "",
        }

    async def fetch_org(clerk_org_id: str) -> dict[str, object]:
        return {"id": clerk_org_id, "name": "Test Org"}

    monkeypatch.setattr("app.core.middleware.verify_clerk_jwt", verify_ok)
    monkeypatch.setattr("app.services.auth_service.fetch_clerk_user", fetch_user)
    monkeypatch.setattr("app.services.auth_service.fetch_clerk_org", fetch_org)


def test_home_without_session_redirects_to_login() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/login"


def test_home_with_bearer_valid_user_but_no_org_redirects_to_org_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"user_{uuid4().hex[:16]}"

    def verify_no_org(_token: str) -> dict[str, object]:
        return {"sub": user_sub, "v": 2}

    monkeypatch.setattr("app.core.middleware.verify_clerk_jwt", verify_no_org)
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert r.headers.get("location") == "/onboarding"


def test_home_with_bearer_and_clerk_mocks_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    user_sub = f"user_{uuid4().hex[:16]}"
    org_id = f"org_{uuid4().hex[:16]}"
    _make_clerk_mocks(monkeypatch, user_sub, org_id)

    user_id = uuid4()
    tenant_id = uuid4()
    membership_id = uuid4()
    now = datetime.now(tz=UTC)

    async def fake_resolve(request: Request) -> None:
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

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200


def test_mutating_web_route_without_csrf_token_returns_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime.now(tz=UTC)

    async def fake_resolve(request: Request) -> None:
        user = User(
            clerk_user_id="user_csrf_missing",
            email="csrf-missing@test.local",
            name="CSRF Missing",
            created_at=now,
            updated_at=now,
        )
        user.id = user_id
        tenant = Tenant(
            clerk_org_id="org_csrf_missing",
            name="CSRF Missing Org",
            plan="free",
            settings={},
            created_at=now,
            updated_at=now,
        )
        tenant.id = tenant_id
        request.state.user = user
        request.state.tenant = tenant

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import create_app

    app = create_app()

    @app.post("/csrf-protected-test")
    async def csrf_protected_test() -> Response:
        return Response(status_code=204)

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(
            "/csrf-protected-test",
            headers={"Authorization": "Bearer fake-jwt", "HX-Request": "true"},
        )

    assert r.status_code == 403


def test_mutating_web_route_with_valid_csrf_token_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    tenant_id = uuid4()
    now = datetime.now(tz=UTC)

    async def fake_resolve(request: Request) -> None:
        user = User(
            clerk_user_id="user_csrf_ok",
            email="csrf-ok@test.local",
            name="CSRF OK",
            created_at=now,
            updated_at=now,
        )
        user.id = user_id
        tenant = Tenant(
            clerk_org_id="org_csrf_ok",
            name="CSRF OK Org",
            plan="free",
            settings={},
            created_at=now,
            updated_at=now,
        )
        tenant.id = tenant_id
        request.state.user = user
        request.state.tenant = tenant

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import create_app

    app = create_app()

    @app.post("/csrf-protected-test")
    async def csrf_protected_test() -> Response:
        return Response(status_code=204)

    token = generate_csrf_token(user_id=user_id, tenant_id=tenant_id)
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(
            "/csrf-protected-test",
            headers={
                "Authorization": "Bearer fake-jwt",
                "HX-Request": "true",
                CSRF_HEADER_NAME: token,
            },
        )

    assert r.status_code == 204


def test_logout_page_renders_sign_out() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/logout", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Cerrando sesión" in r.text
    assert "/logout/done" in r.text


def test_logout_done_redirects_to_login_and_clears_session_cookie() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        client.cookies.set("__session", "fake-session")
        r = client.get("/logout/done", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/login"
    set_cookies = r.headers.get_list("set-cookie")
    assert set_cookies
    assert any("__session=" in header for header in set_cookies)
    assert any("HttpOnly" in header for header in set_cookies)
    assert any("SameSite=lax" in header for header in set_cookies)


def duplicated_logout_page_renders_sign_out() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/logout", headers={"Accept": "text/html"})
    assert r.status_code == 200
    assert "Cerrando sesión" in r.text
    assert "/logout/done" in r.text


def duplicated_logout_done_redirects_to_login_and_clears_session_cookie() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        client.cookies.set("__session", "fake-session")
        r = client.get("/logout/done", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers.get("location") == "/login"
    set_cookie = r.headers.get("set-cookie", "")
    assert "__session=" in set_cookie
