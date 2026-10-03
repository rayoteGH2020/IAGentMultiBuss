"""Modal de miembros: permisos locales; sin rol/perfil ni escritura a Clerk."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MEMBERS_PAGE = _ROOT / "app" / "templates" / "pages" / "settings" / "members.html"
_MEMBER_ROW = _ROOT / "app" / "templates" / "components" / "scheduling" / "member_row.html"
_MEMBER_FORM = _ROOT / "app" / "templates" / "components" / "scheduling" / "member_form.html"
_SETTINGS_ROUTES = _ROOT / "app" / "routes" / "web" / "settings_scheduling.py"


def test_members_modal_alpine_state_not_on_htmx_target() -> None:
    page = _MEMBERS_PAGE.read_text(encoding="utf-8")
    assert 'id="member-modal-panel"' in page
    assert '@open-member-modal.window="open = true"' in page
    assert 'id="member-modal"' not in page
    assert "Invitar miembro" not in page
    open_idx = page.index('x-data="{ open: false }"')
    panel_idx = page.index('id="member-modal-panel"')
    assert open_idx < panel_idx


def test_member_edit_opens_modal_only_on_success() -> None:
    row = _MEMBER_ROW.read_text(encoding="utf-8")
    assert 'hx-target="#member-modal-panel"' in row
    assert "event.detail.successful" in row
    assert "open-member-modal" in row
    assert "Rol" not in row


def test_member_form_is_permissions_only() -> None:
    form = _MEMBER_FORM.read_text(encoding="utf-8")
    assert "Permisos de citas" in form
    assert "Rol en la app" not in form
    assert 'name="role"' not in form
    assert 'name="name"' not in form
    assert 'name="email"' not in form
    assert "close-member-modal" in form
    assert "event.detail.successful" in form


def test_members_routes_have_no_create_invite() -> None:
    source = _SETTINGS_ROUTES.read_text(encoding="utf-8")
    assert '"/members/new"' not in source
    assert "member_create" not in source
    assert "TenantMemberCreate" not in source
