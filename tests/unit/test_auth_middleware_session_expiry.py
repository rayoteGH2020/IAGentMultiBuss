"""Middleware de auth: JWT expirado debe forzar re-login (no CSRF 403)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.errors import AuthError
from app.core.middleware import AuthMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _request(
    *,
    path: str = "/documents/invoice/x/retry",
    method: str = "POST",
    cookie: str | None = "expired.jwt.token",
    htmx: bool = True,
    csrf: str | None = "dummy",
) -> Request:
    headers: list[tuple[str, str]] = []
    if htmx:
        headers.append((b"hx-request", b"true"))
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    req = Request(scope)
    if cookie is not None:
        req._cookies = {"__session": cookie}
    return req


@pytest.mark.asyncio
async def test_expired_jwt_on_post_redirects_to_login_not_csrf() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request()

    with patch(
        "app.core.middleware.verify_clerk_jwt",
        side_effect=AuthError("Token expired"),
    ):
        resp = await mw.dispatch(req, call_next)

    call_next.assert_not_awaited()
    assert resp.headers.get("HX-Redirect") == "/login"
    assert resp.status_code == 200
    # Set-Cookie de borrado de __session
    set_cookie = resp.headers.getlist("set-cookie") if hasattr(resp.headers, "getlist") else []
    if not set_cookie:
        # Starlette puede exponer una sola cabecera
        raw = resp.headers.get("set-cookie") or ""
        assert "__session" in raw
    else:
        assert any("__session" in c for c in set_cookie)


@pytest.mark.asyncio
async def test_expired_jwt_on_get_documents_redirects_to_login() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request(path="/documents", method="GET", htmx=False, csrf=None)

    with patch(
        "app.core.middleware.verify_clerk_jwt",
        side_effect=AuthError("Token expired"),
    ):
        resp = await mw.dispatch(req, call_next)

    call_next.assert_not_awaited()
    assert resp.status_code == 302
    assert resp.headers.get("location") == "/login"


@pytest.mark.asyncio
async def test_unauthenticated_post_without_cookie_redirects_to_login() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request(cookie=None)

    resp = await mw.dispatch(req, call_next)

    call_next.assert_not_awaited()
    assert resp.headers.get("HX-Redirect") == "/login"


@pytest.mark.asyncio
async def test_member_denied_documents_redirects_to_chat() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request(path="/documents", method="GET", htmx=True, csrf=None, cookie="ok.jwt")

    user = MagicMock(id="user-1")
    tenant = MagicMock(id="tenant-1")
    membership = MagicMock(role="member", is_active=True)

    async def _resolve(request: Request) -> None:
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership
        request.state.auth_token_invalid = False
        request.state.auth_missing_organization = False
        request.state.force_password_reset = False

    with patch("app.core.middleware.try_resolve_clerk_session", new=_resolve):
        resp = await mw.dispatch(req, call_next)

    call_next.assert_not_awaited()
    assert resp.headers.get("HX-Redirect") == "/chat"


@pytest.mark.asyncio
async def test_member_allowed_chat_continues() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request(path="/chat", method="GET", htmx=False, csrf=None, cookie="ok.jwt")

    user = MagicMock(id="user-1")
    tenant = MagicMock(id="tenant-1")
    membership = MagicMock(role="member", is_active=True)

    async def _resolve(request: Request) -> None:
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership
        request.state.auth_token_invalid = False
        request.state.auth_missing_organization = False
        request.state.force_password_reset = False

    with patch("app.core.middleware.try_resolve_clerk_session", new=_resolve):
        resp = await mw.dispatch(req, call_next)

    call_next.assert_awaited()
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_revoked_membership_redirects_to_login_clearing_session() -> None:
    """Tras organizationMembership.deleted, JWT con org no debe dar acceso."""
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request(path="/documents", method="GET", htmx=True, csrf=None, cookie="stale.jwt")

    async def _resolve(request: Request) -> None:
        request.state.auth_membership_revoked = True
        request.state.auth_token_invalid = False
        request.state.auth_missing_organization = False

    with patch("app.core.middleware.try_resolve_clerk_session", new=_resolve):
        resp = await mw.dispatch(req, call_next)

    call_next.assert_not_awaited()
    assert resp.headers.get("HX-Redirect") == "/login"
    set_cookie = resp.headers.getlist("set-cookie") if hasattr(resp.headers, "getlist") else []
    if not set_cookie:
        raw = resp.headers.get("set-cookie") or ""
        assert "__session" in raw
    else:
        assert any("__session" in c for c in set_cookie)


@pytest.mark.asyncio
async def test_authenticated_post_with_bad_csrf_still_returns_403() -> None:
    mw = AuthMiddleware(app=AsyncMock())
    call_next = AsyncMock(return_value=Response(content=b"ok"))
    req = _request(csrf="bad-token")

    user = MagicMock(id="user-1")
    tenant = MagicMock(id="tenant-1")

    async def _resolve(request: Request) -> None:
        request.state.user = user
        request.state.tenant = tenant
        request.state.auth_token_invalid = False

    with (
        patch("app.core.middleware.try_resolve_clerk_session", new=_resolve),
        patch("app.core.middleware.validate_csrf_token", return_value=False),
    ):
        resp = await mw.dispatch(req, call_next)

    call_next.assert_not_awaited()
    assert resp.status_code == 403
    assert resp.headers.get("HX-Redirect") is None
