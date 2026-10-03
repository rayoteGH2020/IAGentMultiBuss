"""Flujo UI profesionales: alta → form con horario; especialidades en DTO."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.routes.web.settings_scheduling import _parse_specialty_service_ids

_ROOT = Path(__file__).resolve().parents[2]
_FORM = _ROOT / "app" / "templates" / "components" / "scheduling" / "professional_form.html"
_RESULT = (
    _ROOT / "app" / "templates" / "components" / "scheduling" / "professional_create_result.html"
)
_ROUTES = _ROOT / "app" / "routes" / "web" / "settings_scheduling.py"


class _FakeForm:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def getlist(self, key: str) -> list[str]:
        if key == "specialty_service_ids":
            return self._values
        return []


def test_parse_specialty_service_ids() -> None:
    a, b = uuid4(), uuid4()
    assert _parse_specialty_service_ids(_FakeForm([str(a), str(b)])) == [a, b]
    assert _parse_specialty_service_ids(_FakeForm(["", "  "])) == []
    assert _parse_specialty_service_ids(object()) == []


def test_create_form_targets_modal_panel() -> None:
    html = _FORM.read_text(encoding="utf-8")
    assert 'hx-post="/settings/professionals"' in html
    assert 'hx-target="#professional-modal-panel"' in html
    assert "specialty_service_ids" in html


def test_edit_form_keeps_name_and_actions_sticky() -> None:
    html = _FORM.read_text(encoding="utf-8")
    assert "Editar datos de:" in html
    assert "shrink-0" in html
    assert "<header" in html
    assert "<footer" in html
    assert 'type="hidden" name="display_name"' not in html
    assert 'name="display_name"' in html
    assert "Nombre visible" in html
    name_pos = html.find("Nombre visible")
    color_pos = html.find("professional_color_palette.html")
    user_pos = html.find("Usuario vinculado")
    assert 0 <= name_pos < color_pos < user_pos
    assert "overflow-y-auto" in html
    assert "professional_color_palette.html" in html
    assert 'type="color"' not in html
    assert "sm:grid-cols-2" in html
    assert "Usuario vinculado" in html


def test_hours_grid_copies_full_day_to_below() -> None:
    grid = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "templates"
        / "components"
        / "scheduling"
        / "professional_hours_grid.html"
    ).read_text(encoding="utf-8")
    assert "copyDayToBelow" in grid
    assert "hasDayBelow" in grid
    assert "Copiar ↓" in grid
    assert "copyPeriodToBelow" not in grid

    js = (
        Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "alpine-components.js"
    ).read_text(encoding="utf-8")
    assert "copyDayToBelow" in js
    assert "dayCheckboxes" in js


def test_color_palette_has_thirty_swatches() -> None:
    from app.schemas.scheduling import PROFESSIONAL_COLOR_PALETTE, resolve_palette_color

    assert len(PROFESSIONAL_COLOR_PALETTE) == 30
    assert resolve_palette_color("#6366f1") == "#6366f1"
    assert resolve_palette_color("#ffffff") == "#6366f1"

    palette = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "templates"
        / "components"
        / "scheduling"
        / "professional_color_palette.html"
    ).read_text(encoding="utf-8")
    assert 'name="color"' in palette
    assert "professionalColorPicker" in palette
    assert "color_swatches" in palette
    assert "menuOpen" in palette
    assert "x-data='professionalColorPicker(" in palette
    assert "positionMenu" in palette or "toggleMenu" in palette
    assert "fixed" in palette
    assert "grid-cols-5" in palette
    assert "h-8 w-8" in palette
    assert "Color del calendario" in palette
    assert 'data-taken="true"' in palette
    assert "swatch.is_taken" in palette
    assert "{% set occupied = hex in taken %}" not in palette
    assert 'x-text="selected"' not in palette
    assert "font-mono" not in palette
    assert '@click.outside="open' not in palette
    assert "x-model" not in palette

    js = (
        Path(__file__).resolve().parents[2] / "app" / "static" / "js" / "alpine-components.js"
    ).read_text(encoding="utf-8")
    picker_js = js.split("function registerProfessionalColorPicker")[1].split(
        "function registerProfessionalHoursGrid"
    )[0]
    assert "new Set" not in picker_js
    assert "this.taken.includes" in picker_js
    assert "professionalColorPicker" in js
    assert "registerProfessionalColorPicker" in js
    assert "menuOpen" in js

    form = _FORM.read_text(encoding="utf-8")
    assert 'name="specialty_service_ids"' in form
    assert 'class="professional-specialties-grid__label"' in form
    assert "professional-specialties-grid" in form
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in (
        Path(__file__).resolve().parents[2] / "app" / "static" / "css" / "input.css"
    ).read_text(encoding="utf-8")
    # El nombre no debe envolver el checkbox en <label> (solo el check conmuta).
    assert 'name="specialty_service_ids"' not in form.split("Especialidades")[0]
    specialty_block = form.split("Especialidades", 1)[1]
    assert "<label" not in specialty_block.split("{% endif %}", 1)[0]


def test_hours_grid_has_no_global_select_all() -> None:
    grid = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "templates"
        / "components"
        / "scheduling"
        / "professional_hours_grid.html"
    ).read_text(encoding="utf-8")
    assert "Marcar todo" not in grid
    assert "Desmarcar todo" not in grid
    assert "selectAll()" not in grid
    assert "clearAll()" not in grid


def test_professional_row_lists_specialty_names() -> None:
    row = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "templates"
        / "components"
        / "scheduling"
        / "professional_row.html"
    ).read_text(encoding="utf-8")
    assert "specialty_names" in row
    assert 'join(", ")' in row or "join(', ')" in row
    assert "/ 3" not in row
    assert "Activo" in row
    assert "Inactivo" in row
    assert "Agendable" in row
    assert "No agendable" in row
    assert "is_active" in row
    assert "is_bookable" in row
    assert "btn-secondary" in row
    assert "icons/pencil.html" in row
    assert "line-clamp-2" in row
    assert "break-words" in row
    # Column order in row cells: name → specialties → status icons
    name_marker = "display_name"
    specialty_marker = "specialty_names"
    status_marker = "is_bookable"
    assert row.find(name_marker) < row.find(specialty_marker) < row.find(status_marker)


def test_professionals_page_column_order() -> None:
    page = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "templates"
        / "pages"
        / "settings"
        / "professionals.html"
    ).read_text(encoding="utf-8")
    name_pos = page.find("<th>Nombre</th>")
    specialty_pos = page.find("<th>Especialidades</th>")
    status_pos = page.find("<th>Estado</th>")
    assert 0 <= name_pos < specialty_pos < status_pos
    assert "table-fixed" in page
    assert "<colgroup>" in page


def test_create_result_includes_form_and_oob_row() -> None:
    html = _RESULT.read_text(encoding="utf-8")
    assert "professional_form.html" in html
    assert "professional_row.html" in html
    assert "beforeend:#professionals-list" in html


def test_create_route_uses_create_result_template() -> None:
    source = _ROUTES.read_text(encoding="utf-8")
    assert "professional_create_result.html" in source
    assert "get_professional_read" in source
    assert "_parse_specialty_service_ids" in source
