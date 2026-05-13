"""Tests de auth Clerk + middleware (sin llamadas reales a Clerk)."""

from uuid import uuid4

import pytest
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


def test_home_with_bearer_and_clerk_mocks_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    user_sub = f"user_{uuid4().hex[:16]}"
    org_id = f"org_{uuid4().hex[:16]}"
    _make_clerk_mocks(monkeypatch, user_sub, org_id)
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200
