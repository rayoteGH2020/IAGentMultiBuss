"""Tests de rutas SADM (Paso 50 / Paso 24 Fase A)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import Membership, Tenant, User
from fastapi import Request
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _clear_settings_cache() -> None:
    from app.config import get_settings

    get_settings.cache_clear()


def _fake_session_with_org(
    *,
    org_id: str,
    user_sub: str,
    force_password_reset: bool = False,
    role: str = "admin",
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
            role=role,
            created_at=now,
            updated_at=now,
        )
        membership.is_active = True
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership
        request.state.force_password_reset = force_password_reset
        admin_org = __import__("os").environ.get("ADMIN_CLERK_ORG_ID", "").strip()
        request.state.is_superadmin = bool(admin_org and org_id == admin_org and role == "admin")

    return _resolve


def test_sadm_requires_auth() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            "/sadm/organizations", headers={"Accept": "text/html"}, follow_redirects=False
        )
    assert r.status_code == 302
    assert r.headers.get("location") == "/login"


def test_sadm_requires_superadmin_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clientes API (JSON) siguen recibiendo 403 explícito."""
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_org = f"org_user_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=user_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "application/json"},
        )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_sadm_requires_superadmin_html_redirects_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Navegador HTML: usuario autenticado sin org SADM → redirect a /."""
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_org = f"org_user_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=user_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
            follow_redirects=False,
        )
    assert r.status_code == 302
    assert r.headers.get("location") == "/"


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_sadm_denies_non_admin_member_of_admin_org(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    """Pertenecer a la org SADM sin rol admin no da acceso cross-tenant."""
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=user_sub, role=role),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "application/json"},
        )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_sadm_denies_admin_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con SUPERADMIN_CLERK_USER_IDS, un admin no listado queda fuera."""
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "user_solo_ruben")
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "application/json"},
        )
    assert r.status_code == 403
    _clear_settings_cache()


def test_sadm_allows_admin_in_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", f"otro_user,{user_sub}")
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200
    _clear_settings_cache()


def test_sadm_list_orgs_ok_for_superadmin(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/sadm/documents", "/sadm/usage", "/sadm/chat-traces", "/sadm/chat-usage"],
)
def test_sadm_document_and_usage_consoles_render_for_superadmin(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    # No heredar allowlist de Infisical: el user_sub es aleatorio.
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            path,
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    if path == "/sadm/chat-traces":
        assert "Trazas" in r.text or "chat" in r.text.lower()
    if path == "/sadm/chat-usage":
        assert "chat" in r.text.lower() or "organización" in r.text.lower()
        assert "tenantSearchSelect" in r.text
        assert "chat-usage-tenant-query" in r.text
        assert "Filtrar por nombre" in r.text


@pytest.mark.parametrize(
    "path",
    ["/sadm/documents", "/sadm/usage", "/sadm/chat-traces", "/sadm/chat-usage"],
)
def test_sadm_document_and_usage_consoles_require_superadmin(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Las consolas cross-tenant nuevas no son accesibles a un tenant normal."""
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_org = f"org_user_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=user_org, user_sub=user_sub),
    )

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get(
            path,
            headers={"Authorization": "Bearer fake-jwt", "Accept": "application/json"},
        )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
