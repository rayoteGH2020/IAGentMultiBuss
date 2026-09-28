"""Nombre de organización en Settings: solo lectura (gestión vía Clerk / SADM)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PROFILE = _ROOT / "app" / "templates" / "pages" / "settings" / "profile.html"
_SETTINGS_ROUTES = _ROOT / "app" / "routes" / "web" / "settings.py"
_TENANT_SERVICE = _ROOT / "app" / "services" / "tenant_service.py"
_BILLING = _ROOT / "app" / "templates" / "pages" / "settings" / "billing.html"


def test_tenant_info_is_read_only() -> None:
    html = _PROFILE.read_text(encoding="utf-8")
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


def test_tenant_service_module_removed() -> None:
    assert not _TENANT_SERVICE.exists()


def test_tenant_cannot_change_plan_from_settings() -> None:
    """El plan lo asigna solo el SADM: sin checkout ni portal de Stripe en Settings."""
    source = _SETTINGS_ROUTES.read_text(encoding="utf-8")
    assert "/billing/checkout" not in source
    assert "/billing/portal" not in source
    assert "stripe_billing_service" not in source
    # Facturación unificada en «Mi cuenta»: la antigua página ya no existe.
    assert not _BILLING.exists()
    html = _PROFILE.read_text(encoding="utf-8")
    assert "hx-post" not in html
    assert "<form" not in html
