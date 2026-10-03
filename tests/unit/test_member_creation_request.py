"""Solicitud de alta de miembro: validación, plantillas y mensajes de error."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from app.core.templating import templates
from app.routes.web.settings_scheduling import _creation_validation_error
from app.schemas.membership import MemberCreationForm, MemberCreationRequest
from pydantic import ValidationError as PydanticValidationError

_MEMBERS_PAGE = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "templates"
    / "pages"
    / "settings"
    / "members.html"
)


def _data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "first_name": "  Ana ",
        "last_name": "García\nLópez",
        "alias": "   ",
        "email": " Ana.Garcia@Example.COM ",
        "role": "member",
        "start_date": "2026-10-01",
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("method", "path", "endpoint"),
    [
        ("GET", "/settings/members/requests/new", "member_creation_form"),
        ("POST", "/settings/members/requests/new", "member_creation_request"),
    ],
)
def test_creation_routes_are_not_shadowed(method: str, path: str, endpoint: str) -> None:
    """``POST /settings/members/{membership_id}`` no debe capturar la ruta de alta."""
    from app.main import create_app
    from starlette.routing import Match

    scope = {"type": "http", "method": method, "path": path}
    for route in create_app().router.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            assert getattr(route, "name", None) == endpoint
            return
    pytest.fail(f"No route matches {method} {path}")


def test_creation_request_normalizes_fields() -> None:
    payload = MemberCreationRequest.model_validate(_data())

    assert payload.first_name == "Ana"
    assert payload.last_name == "García López"
    assert payload.alias is None
    assert payload.email == "ana.garcia@example.com"
    assert payload.start_date == date(2026, 10, 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": "admin"},
        {"role": "viewer"},
        {"email": "no-es-un-email"},
        {"email": "a b@example.com"},
        {"first_name": "   "},
        {"last_name": ""},
        {"start_date": "mañana"},
    ],
)
def test_creation_request_rejects_invalid_input(overrides: dict[str, Any]) -> None:
    with pytest.raises(PydanticValidationError):
        MemberCreationRequest.model_validate(_data(**overrides))


def test_creation_validation_error_lists_fields_in_spanish() -> None:
    with pytest.raises(PydanticValidationError) as exc_info:
        MemberCreationRequest.model_validate(_data(email="x", role="admin"))
    assert _creation_validation_error(exc_info.value) == "Revisa estos campos: email, rol."


def _render(template: str, **ctx: object) -> str:
    return templates.env.get_template(f"components/scheduling/{template}").render(**ctx)


def _form() -> MemberCreationForm:
    return MemberCreationForm(
        actor_name="Jefa",
        actor_email="jefa@example.com",
        actor_role="co_admin",
        min_date=date(2026, 9, 28),
        max_date=date(2027, 9, 28),
    )


def test_members_page_has_new_member_button() -> None:
    html = _MEMBERS_PAGE.read_text(encoding="utf-8")
    assert 'hx-get="/settings/members/requests/new"' in html
    assert ">Nuevo miembro<" in html
    assert 'hx-target="#member-modal-panel"' in html


def test_members_page_headers_match_row_cells() -> None:
    """6 celdas por fila (4 datos + editar + baja); el botón ocupa las 2 de acciones."""
    html = _MEMBERS_PAGE.read_text(encoding="utf-8")
    thead = html[html.index("<thead>") : html.index("</thead>")]
    for label in ("Email", "Nombre", "Rol", "Permisos citas"):
        assert f"<th>{label}</th>" in thead
    assert '<th colspan="2"' in thead
    assert "Nuevo miembro" in thead


def test_creation_form_fields_roles_and_actor() -> None:
    html = _render("member_creation_form.html", form=_form(), values={}, error=None)

    for name in ("first_name", "last_name", "alias", "email", "start_date", "role"):
        assert f'name="{name}"' in html
    assert 'hx-post="/settings/members/requests/new"' in html
    assert 'min="2026-09-28"' in html and 'max="2027-09-28"' in html
    assert '<option value="member"' in html and '<option value="co_admin"' in html
    assert 'value="admin"' not in html
    assert "Jefa · Co-administrador" in html
    assert 'role="alert"' not in html


def test_creation_form_keeps_values_and_shows_error() -> None:
    values = {"first_name": "Ana", "email": "ana@example.com", "role": "co_admin"}
    html = _render(
        "member_creation_form.html",
        form=_form(),
        values=values,
        error="Ese email ya es miembro de la organización.",
    )
    assert 'value="Ana"' in html
    assert 'value="ana@example.com"' in html
    assert '<option value="co_admin" selected>' in html
    assert "Ese email ya es miembro de la organización." in html


def test_creation_done_summarizes_request() -> None:
    payload = MemberCreationRequest.model_validate(_data(role="co_admin"))
    html = _render("member_creation_done.html", payload=payload)
    assert "Ana García López" in html
    assert "ana.garcia@example.com" in html
    assert "Co-administrador" in html
    assert "01/10/2026" in html
