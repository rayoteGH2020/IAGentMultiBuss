"""Tests HTTP de rutas web del chat (Paso 16 Fase E)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _fake_clerk_resolve(request: Request, *, user_sub: str, org_id: str) -> None:
    from app.models import Membership, Tenant, User

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


def test_chat_get_page(
    chat_schema_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /chat devuelve la página con copy de consulta documental."""
    user_sub = f"user_{uuid4().hex[:16]}"
    org_id = f"org_{uuid4().hex[:16]}"

    async def fake_resolve(request: Request) -> None:
        _fake_clerk_resolve(request, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/chat",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200
    assert "Consulta sobre tus documentos" in r.text
    assert "chat-sidebar" in r.text
    assert "Consulta documental" in r.text
    assert "base de conocimiento" in r.text
    assert 'hx-disinherit="*"' in r.text
    assert 'id="chat-app"' in r.text
    assert 'id="app-frame"' in r.text


def test_chat_threads_htmx_fragment(
    chat_schema_ready: None,
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
            "/chat/threads",
            headers={
                "Authorization": "Bearer fake-jwt",
                "Accept": "text/html",
                "HX-Request": "true",
            },
        )
    assert r.status_code == 200
    assert "Conversaciones" in r.text
