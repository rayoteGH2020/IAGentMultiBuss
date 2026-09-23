"""Nombre de organización en Settings: solo lectura (gestión vía Clerk / SADM)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TENANT_INFO = _ROOT / "app" / "templates" / "components" / "tenant_info.html"
_SETTINGS_ROUTES = _ROOT / "app" / "routes" / "web" / "settings.py"
_TENANT_SERVICE = _ROOT / "app" / "services" / "tenant_service.py"


def test_tenant_info_is_read_only() -> None:
    html = _TENANT_INFO.read_text(encoding="utf-8")
    assert "{{ tenant.name }}" in html
    assert "<form" not in html
    assert 'hx-post="/settings/organization/name"' not in html
    assert "Guardar" not in html
    assert 'name="name"' not in html
    assert "tenant-name-save-spinner" not in html


def test_settings_has_no_organization_name_endpoint() -> None:
    source = _SETTINGS_ROUTES.read_text(encoding="utf-8")
    assert '"/organization/name"' not in source
    assert "update_organization_name" not in source
    assert "update_tenant_display_name" not in source
    assert "tenant_service" not in source
    # RequireAdmin is used by Stripe billing checkout/portal; org rename remains gone.


def test_tenant_service_module_removed() -> None:
    assert not _TENANT_SERVICE.exists()
