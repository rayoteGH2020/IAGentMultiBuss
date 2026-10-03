"""Sesión Clerk caducada: volver a la página de origen tras login (sin open redirect)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.errors import AuthError
from app.core.middleware import AuthMiddleware
from app.core.safe_redirect import path_from_url, safe_internal_path
from app.core.templating import _inject_auth_context
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/documents", "/documents"),
        (
            "/documents?doc_type_code=invoice&sort=date",
            "/documents?doc_type_code=invoice&sort=date",
        ),
        ("/", "/"),
        (None, None),
        ("", None),
        ("documents", None),
        ("//evil.com", None),
        ("//evil.com/documents", None),
        ("/\\evil.com", None),
        ("https://evil.com/documents", None),
        ("/documents\r\nSet-Cookie: x=1", None),
        ("/login", None),
        ("/login?redirect_url=/x", None),
        ("/logout", None),
        ("/api/webhooks/clerk", None),
        ("/auth/change-password", None),
        ("/" + "a" * 3000, None),
    ],
)
def test_safe_internal_path(value: str | None, expected: str | None) -> None:
    assert safe_internal_path(value) == expected


def test_path_from_url_keeps_only_path_and_query() -> None:
    assert path_from_url("http://localhost:8000/documents?sort=date") == "/documents?sort=date"
    assert path_from_url("https://evil.com/documents") == "/documents"  # el host se descarta
    assert path_from_url("http://localhost:8000/login") is None
    assert path_from_url(None) is None


def _request(*, path: str, method: str, headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    req = Request(scope)
    req._cookies = {"__session": "expired.jwt"}
    return req


async def _dispatch_expired(req: Request) -> Response:
    mw = AuthMiddleware(app=AsyncMock())
    with patch("app.core.middleware.verify_clerk_jwt", side_effect=AuthError("Token expired")):
        return await mw.dispatch(req, AsyncMock(return_value=Response(content=b"ok")))


@pytest.mark.asyncio
async def test_expired_htmx_upload_returns_to_current_page() -> None:
    req = _request(
        path="/documents/upload",
        method="POST",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost:8000/documents",
            "X-CSRF-Token": "x",
        },
    )
    resp = await _dispatch_expired(req)
    assert resp.headers.get("HX-Redirect") == "/login?redirect_url=%2Fdocuments"


@pytest.mark.asyncio
async def test_expired_page_get_returns_to_same_page() -> None:
    req = _request(path="/documents", method="GET", headers={})
    resp = await _dispatch_expired(req)
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/login?redirect_url=%2Fdocuments"


@pytest.mark.asyncio
async def test_expired_non_htmx_post_goes_to_plain_login() -> None:
    req = _request(path="/documents/upload", method="POST", headers={"X-CSRF-Token": "x"})
    resp = await _dispatch_expired(req)
    assert resp.headers.get("location") == "/login"


@pytest.mark.asyncio
async def test_revoked_membership_does_not_keep_return_path() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    req = _request(
        path="/documents",
        method="GET",
        headers={"HX-Request": "true", "HX-Current-URL": "http://localhost:8000/documents"},
    )

    async def _resolve(request: Request) -> None:
        request.state.auth_membership_revoked = True
        request.state.auth_token_invalid = False
        request.state.auth_missing_organization = False

    with patch("app.core.middleware.try_resolve_clerk_session", new=_resolve):
        resp = await mw.dispatch(req, AsyncMock(return_value=Response(content=b"ok")))
    assert resp.headers.get("HX-Redirect") == "/login"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?redirect_url=%2Fdocuments", '"/documents"'),
        ("?redirect_url=%2F%2Fevil.com", '"/"'),
        ("?redirect_url=https%3A%2F%2Fevil.com", '"/"'),
        ("", '"/"'),
    ],
)
def test_login_page_redirects_back_only_to_internal_paths(query: str, expected: str) -> None:
    from app.main import create_app

    with TestClient(create_app(), base_url="http://localhost") as client:
        html = client.get(f"/login{query}").text
    assert f"const afterLoginUrl = {expected};" in html
    assert "forceRedirectUrl: afterLoginUrl" in html


def test_dashboard_loads_clerk_session_keepalive_for_authenticated_users() -> None:
    state = SimpleNamespace(user=SimpleNamespace(id=uuid4()), tenant=None)
    request = SimpleNamespace(state=state)
    ctx = _inject_auth_context(request)  # type: ignore[arg-type]
    assert ctx["clerk_js_script_url"].endswith("/dist/clerk.browser.js")

    anonymous = _inject_auth_context(SimpleNamespace(state=SimpleNamespace()))  # type: ignore[arg-type]
    assert "clerk_js_script_url" not in anonymous

    layout = (_ROOT / "app" / "templates" / "layouts" / "dashboard.html").read_text("utf-8")
    assert "components/clerk_browser_script.html" in layout
    assert "/static/js/clerk-session.js" in layout


def test_keepalive_refreshes_token_before_htmx_requests_and_keeps_hx_confirm() -> None:
    js = (_ROOT / "app" / "static" / "js" / "clerk-session.js").read_text("utf-8")
    assert 'addEventListener("htmx:confirm"' in js
    assert "session.getToken()" in js
    # issueRequest(true) salta el confirm nativo de htmx: debe replicarse.
    assert "global.confirm(detail.question)" in js
    assert "detail.issueRequest(true)" in js
