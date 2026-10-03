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
    ["/sadm/documents", "/sadm/usage", "/sadm/chat-traces", "/sadm/chat-usage", "/sadm/plans"],
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
    ["/sadm/documents", "/sadm/usage", "/sadm/chat-traces", "/sadm/chat-usage", "/sadm/plans"],
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


def test_sadm_provision_routes_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST crear org/user y DELETE miembro ya no existen (D005 / Paso05)."""
    from app.main import app

    methods_by_path: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or not methods:
            continue
        methods_by_path.setdefault(path, set()).update(methods)

    assert "POST" not in methods_by_path.get("/sadm/organizations", set())
    assert "/sadm/users" not in methods_by_path
    assert not any(
        path.startswith("/sadm/users/") and "DELETE" in methods
        for path, methods in methods_by_path.items()
    )


def test_sadm_orgs_page_is_readonly_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
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
    assert "Clerk Dashboard" in r.text
    assert "Nueva organización" not in r.text
    assert 'hx-post="/sadm/organizations"' not in r.text


def test_sadm_open_original_passes_viewer_for_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """El acceso SADM al original llega al servicio con el user_id del superadmin."""
    from unittest.mock import AsyncMock, patch

    admin_org = f"org_admin_{uuid4().hex[:8]}"
    user_sub = f"user_{uuid4().hex[:12]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=user_sub),
    )
    original = AsyncMock(return_value="https://r2.test/original?signed=1")
    document_id = uuid4()

    from app.main import app

    with (
        patch(
            "app.routes.web.admin.documents.document_override_service.original_file_url",
            original,
        ),
        TestClient(app, raise_server_exceptions=True) as client,
    ):
        r = client.get(
            f"/sadm/documents/invoice/{document_id}/file",
            headers={"Authorization": "Bearer fake-jwt", "user-agent": "sadm-ua"},
            follow_redirects=False,
        )

    assert r.status_code == 302
    assert r.headers["location"] == "https://r2.test/original?signed=1"
    kwargs = original.await_args.kwargs
    assert kwargs["document_id"] == document_id
    assert kwargs["viewer_id"] is not None
    assert kwargs["request_ctx"].user_agent == "sadm-ua"


def _mount_superadmin(monkeypatch: pytest.MonkeyPatch) -> None:
    admin_org = f"org_admin_{uuid4().hex[:8]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
    _clear_settings_cache()
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_session_with_org(org_id=admin_org, user_sub=f"user_{uuid4().hex[:12]}"),
    )


def test_sadm_chat_traces_list_is_scoped_to_own_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """El listado usa siempre el tenant del superadmin; ?tenant_id= ajeno se ignora."""
    from unittest.mock import AsyncMock, patch

    _mount_superadmin(monkeypatch)
    list_threads = AsyncMock(return_value=[])
    foreign_tenant = uuid4()

    from app.main import app

    with (
        patch("app.routes.web.admin.chat_traces.chat_trace_service.list_threads", list_threads),
        TestClient(app, raise_server_exceptions=True) as client,
    ):
        r = client.get(
            f"/sadm/chat-traces?tenant_id={foreign_tenant}",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )

    assert r.status_code == 200
    assert "tu organización" in r.text
    tenant_id = list_threads.await_args.kwargs["tenant_id"]
    assert tenant_id is not None
    assert tenant_id != foreign_tenant


def test_sadm_chat_trace_detail_passes_own_tenant_and_viewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El detalle llega al servicio con el tenant propio y el viewer para auditar."""
    from unittest.mock import AsyncMock, patch

    from app.core.errors import NotFoundError

    _mount_superadmin(monkeypatch)
    get_trace = AsyncMock(side_effect=NotFoundError("Chat thread not found"))
    thread_id = uuid4()

    from app.main import app

    with (
        patch("app.routes.web.admin.chat_traces.chat_trace_service.get_thread_trace", get_trace),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        r = client.get(
            f"/sadm/chat-traces/{thread_id}",
            headers={"Authorization": "Bearer fake-jwt", "user-agent": "sadm-ua"},
        )

    assert r.status_code == 404
    kwargs = get_trace.await_args.kwargs
    assert kwargs["thread_id"] == thread_id
    assert kwargs["tenant_id"] is not None
    assert kwargs["viewer_id"] is not None
    assert kwargs["request_ctx"].user_agent == "sadm-ua"


@pytest.mark.parametrize("exhausted", [True, False])
def test_budget_exhausted_banner_on_every_panel_page(
    exhausted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con el presupuesto de IA al 100 % el layout muestra el aviso configurable."""
    from app.config import get_settings

    admin_org = f"org_admin_{uuid4().hex[:8]}"
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", admin_org)
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
    _clear_settings_cache()
    resolve = _fake_session_with_org(org_id=admin_org, user_sub=f"user_{uuid4().hex[:12]}")

    async def _resolve_with_budget(request: Request) -> None:
        await resolve(request)  # type: ignore[operator]
        request.state.llm_budget_exhausted = exhausted

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", _resolve_with_budget)

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/sadm/organizations",
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )

    assert r.status_code == 200
    assert ("llm-budget-exhausted-banner" in r.text) is exhausted
    if exhausted:
        assert get_settings().llm_budget_exhausted_notice.split(".")[0] in r.text
