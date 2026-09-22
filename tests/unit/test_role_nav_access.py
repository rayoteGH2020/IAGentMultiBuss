"""Matriz de navegación y acceso por rol de organización."""

from __future__ import annotations

from app.core.permissions import (
    ADMIN_NAV_ITEMS,
    MEMBER_NAV_ITEMS,
    home_path_for_role,
    nav_items_for_role,
    role_can_access_path,
)


def test_admin_nav_includes_documents_settings_calendar() -> None:
    hrefs = {item[0] for item in nav_items_for_role("admin")}
    assert hrefs == {item[0] for item in ADMIN_NAV_ITEMS}
    assert "/documents" in hrefs
    assert "/settings" in hrefs
    assert "/calendar" in hrefs
    assert "/chat" in hrefs
    assert "/appointments" in hrefs


def test_member_nav_only_chat_and_appointments() -> None:
    assert nav_items_for_role("member") == list(MEMBER_NAV_ITEMS)
    assert nav_items_for_role("viewer") == list(MEMBER_NAV_ITEMS)
    hrefs = {item[0] for item in nav_items_for_role("member")}
    assert hrefs == {"/chat", "/appointments"}


def test_home_path_for_role() -> None:
    assert home_path_for_role("admin") == "/"
    assert home_path_for_role("member") == "/chat"
    assert home_path_for_role("viewer") == "/chat"


def test_admin_can_access_all_app_paths() -> None:
    for path in (
        "/",
        "/documents",
        "/documents/x",
        "/knowledge",
        "/chat",
        "/calendar",
        "/calendar/voice",
        "/appointments",
        "/settings",
        "/settings/members",
        "/jobs/x",
    ):
        assert role_can_access_path("admin", path) is True


def test_member_path_matrix() -> None:
    assert role_can_access_path("member", "/") is False
    assert role_can_access_path("member", "/documents") is False
    assert role_can_access_path("member", "/knowledge") is False
    assert role_can_access_path("member", "/calendar") is False
    assert role_can_access_path("member", "/settings") is False
    assert role_can_access_path("member", "/settings/profile") is False
    assert role_can_access_path("member", "/jobs/1") is False

    assert role_can_access_path("member", "/chat") is True
    assert role_can_access_path("member", "/chat/threads/x") is True
    assert role_can_access_path("member", "/appointments") is True
    assert role_can_access_path("member", "/appointments/new") is True
    assert role_can_access_path("member", "/api/v1/scheduling/find-slots") is True


def test_sidebar_uses_role_nav_helper() -> None:
    from pathlib import Path

    html = (
        Path(__file__).resolve().parents[2] / "app" / "templates" / "components" / "sidebar.html"
    ).read_text(encoding="utf-8")
    assert "nav_items_for_access" in html or "nav_items_for_role" in html
    assert "home_path_for_role" in html
    assert '("/documents", "Documentos"' not in html
