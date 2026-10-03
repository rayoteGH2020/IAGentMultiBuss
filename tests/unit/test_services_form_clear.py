"""Tras Añadir servicio, nombre y observaciones deben limpiarse."""

from __future__ import annotations

from pathlib import Path

_SERVICES = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "templates"
    / "pages"
    / "settings"
    / "services.html"
)


def test_services_add_form_clears_name_after_success() -> None:
    html = _SERVICES.read_text(encoding="utf-8")
    assert 'hx-post="/settings/services"' in html
    assert "event.detail.successful" in html
    assert "n.value = ''" in html or 'n.value = ""' in html
    assert "o.value = ''" in html or 'o.value = ""' in html
    assert 'name="name"' in html
    assert 'name="notes"' in html


def test_services_add_button_has_stable_control_id() -> None:
    html = _SERVICES.read_text(encoding="utf-8")
    assert 'id="settings-services-btn-add"' in html
    assert 'data-control="settings.services.add"' in html
