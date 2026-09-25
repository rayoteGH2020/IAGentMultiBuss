"""Guards: onboarding sin org + aviso al superadmin (P-2.2)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app" / "templates"
STATIC_JS = ROOT / "app" / "static" / "js"


def test_clerk_auth_helper_routes_choose_organization_to_onboarding() -> None:
    js = (STATIC_JS / "clerk-auth.js").read_text(encoding="utf-8")
    assert '"choose-organization"' in js or "'choose-organization'" in js
    assert "/onboarding" in js
    assert "taskUrls" in js
    assert "setActive" in js
    assert "ensureOrgOrRedirect" in js
    assert "CreateOrganization" not in js
    assert "mountCreateOrganization" not in js
    assert "mountTaskChooseOrganization" not in js
    assert "OrganizationSwitcher" not in js


def test_login_and_signup_use_clerk_auth_task_urls() -> None:
    login = (TEMPLATES / "pages" / "auth" / "login.html").read_text(encoding="utf-8")
    signup = (TEMPLATES / "pages" / "auth" / "signup.html").read_text(encoding="utf-8")
    for html in (login, signup):
        assert "/static/js/clerk-auth.js" in html
        assert "MiSaaSClerkAuth.loadOptions" in html
        assert "ensureOrgOrRedirect" in html
        assert "mountCreateOrganization" not in html
        assert "CreateOrganization" not in html


def test_no_org_page_notify_superadmin_flow() -> None:
    page = (TEMPLATES / "pages" / "auth" / "no_org.html").read_text(encoding="utf-8")
    prompt = (TEMPLATES / "components" / "no_org_notify_prompt.html").read_text(encoding="utf-8")
    notified = (TEMPLATES / "components" / "no_org_notified.html").read_text(encoding="utf-8")

    assert "Tu usuario no está asociado a una organización" in page
    assert "ensureOrgOrRedirect" in page
    assert "setActive" in (STATIC_JS / "clerk-auth.js").read_text(encoding="utf-8")
    assert "Contacta" not in page
    assert "Volver al login" not in page
    assert "Cerrar sesión" in page
    assert page.count("Cerrar sesión") == 1
    assert 'id="no-org-logout-btn"' in page
    assert "setNoOrgLogoutBusy" in page
    assert 'include "components/no_org_notify_prompt.html"' in page

    assert 'id="no-org-notify-btn"' in prompt
    assert "/onboarding/notify-superadmin" in prompt
    assert "setNoOrgLogoutBusy" in prompt
    assert ">aquí</button>" in prompt
    assert "text-sky-700" in prompt
    assert "Pulsa" in prompt
    assert "notificar al superadmin" in prompt
    assert "Recibirás un email cuando puedas acceder" in prompt
    assert "CreateOrganization" not in prompt

    assert "Hemos notificado al superadmin" in notified
