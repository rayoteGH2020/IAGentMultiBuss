"""Marca visual: logo estático en sidebar y auth; shell sin cabecera superior."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LOGO = _ROOT / "app" / "static" / "img" / "IAGenMultibuss.png"
_SIDEBAR = _ROOT / "app" / "templates" / "components" / "sidebar.html"
_AUTH = _ROOT / "app" / "templates" / "layouts" / "auth.html"
_DASHBOARD = _ROOT / "app" / "templates" / "layouts" / "dashboard.html"


def test_brand_logo_file_exists() -> None:
    assert _LOGO.is_file()
    assert _LOGO.stat().st_size > 0


def test_sidebar_org_name_tooltip_shows_login_email() -> None:
    html = _SIDEBAR.read_text(encoding="utf-8")
    assert "user.email" in html
    assert 'title="{{ user.email if user and user.email else tenant.name }}"' in html


def test_sidebar_uses_brand_logo_instead_of_text() -> None:
    html = _SIDEBAR.read_text(encoding="utf-8")
    assert "/static/img/IAGenMultibuss.png" in html
    assert ">Mi SaaS<" not in html
    assert '("/", "Inicio", "home")' not in html
    assert "tenant.name" in html
    assert 'href="/logout"' in html


def test_home_nav_icon_removed() -> None:
    assert not (_ROOT / "app" / "templates" / "components" / "icons" / "home.html").exists()


def test_auth_layout_uses_brand_logo() -> None:
    html = _AUTH.read_text(encoding="utf-8")
    assert "/static/img/IAGenMultibuss.png" in html
    assert ">Mi SaaS<" not in html


def test_dashboard_has_no_top_header_chrome() -> None:
    html = _DASHBOARD.read_text(encoding="utf-8")
    assert "Menú de usuario" not in html
    assert "userMenuOpen" not in html
    assert "block page_title" not in html
    assert 'aria-label="Abrir menú"' in html
