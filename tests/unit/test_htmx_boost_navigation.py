"""Tests de navegación HTMX boost / render página completa."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.core.middleware import _htmx_aware_redirect
from app.core.templating import render
from fastapi import Request
from starlette.responses import RedirectResponse


def _request(*, htmx: bool = False, boosted: bool = False) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if htmx:
        headers.append((b"hx-request", b"true"))
    if boosted:
        headers.append((b"hx-boosted", b"true"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/knowledge",
        "raw_path": b"/knowledge",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    request = Request(scope)
    request.state.user = None
    request.state.tenant = None
    request.state.membership = None
    return request


def test_render_boosted_returns_full_page_not_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """hx-boost envía HX-Request+HX-Boosted: debe devolver layout completo."""
    captured: dict[str, str] = {}

    def fake_template_response(
        *,
        request: Request,
        name: str,
        context: dict[str, object],
        status_code: int,
    ) -> MagicMock:
        captured["name"] = name
        return MagicMock()

    monkeypatch.setattr(
        "app.core.templating.templates.TemplateResponse",
        fake_template_response,
    )
    render(
        _request(htmx=True, boosted=True),
        full="pages/knowledge/index.html",
        partial="components/knowledge_rows.html",
        ctx={},
    )
    assert captured["name"] == "pages/knowledge/index.html"


def test_render_htmx_fragment_without_boost_uses_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_template_response(
        *,
        request: Request,
        name: str,
        context: dict[str, object],
        status_code: int,
    ) -> MagicMock:
        captured["name"] = name
        return MagicMock()

    monkeypatch.setattr(
        "app.core.templating.templates.TemplateResponse",
        fake_template_response,
    )
    render(
        _request(htmx=True, boosted=False),
        full="pages/knowledge/index.html",
        partial="components/knowledge_rows.html",
        ctx={},
    )
    assert captured["name"] == "components/knowledge_rows.html"


def test_htmx_aware_redirect_uses_hx_redirect_header() -> None:
    request = _request(htmx=True)
    response = _htmx_aware_redirect(request, "/documents")
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/documents"


def test_htmx_aware_redirect_uses_302_for_normal_browser() -> None:
    request = _request(htmx=False)
    response = _htmx_aware_redirect(request, "/documents")
    assert isinstance(response, RedirectResponse)
    assert response.status_code == 302
    assert response.headers["location"] == "/documents"
