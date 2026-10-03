"""UI Settings → Servicios: listado read-only, modal editar, observaciones."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _ROOT / "app" / "templates" / "pages" / "settings" / "services.html"
_ROW = _ROOT / "app" / "templates" / "components" / "scheduling" / "service_row.html"
_FORM = _ROOT / "app" / "templates" / "components" / "scheduling" / "service_form.html"
_ROUTES = _ROOT / "app" / "routes" / "web" / "settings_scheduling.py"


def test_services_list_is_read_only_with_edit_and_delete_icons() -> None:
    row = _ROW.read_text(encoding="utf-8")
    assert "<form" not in row
    assert 'name="duration_minutes"' not in row
    assert 'name="is_active"' not in row
    assert 'hx-get="/settings/services/' in row
    assert "hx-delete=" in row
    assert "icons/pencil.html" in row
    assert "icons/trash.html" in row
    assert "service.notes" in row


def test_services_create_and_edit_include_notes() -> None:
    page = _PAGE.read_text(encoding="utf-8")
    form = _FORM.read_text(encoding="utf-8")
    assert 'name="notes"' in page
    assert "Observaciones" in page
    assert 'name="notes"' in form
    assert "Observaciones" in form
    assert 'name="is_active"' in form
    assert 'name="name"' in form
    assert 'name="duration_minutes"' in form


def test_services_modal_alpine_not_on_htmx_target() -> None:
    page = _PAGE.read_text(encoding="utf-8")
    assert 'id="service-modal-panel"' in page
    assert 'x-data="{ open: false }"' in page
    open_idx = page.index('x-data="{ open: false }"')
    panel_idx = page.index('id="service-modal-panel"')
    assert open_idx < panel_idx


def test_services_routes_expose_edit_and_delete() -> None:
    source = _ROUTES.read_text(encoding="utf-8")
    assert '"/services/{service_id}/edit"' in source
    assert "service_delete" in source
    assert "delete_service" in source
