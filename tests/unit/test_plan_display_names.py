"""El plan se muestra siempre con el mismo nombre (Básico / Avanzado / Premium)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.core.entitlement_codes import PLAN_CODES, PLAN_META, PLAN_UI_NAMES, plan_ui_name
from app.core.templating import templates

_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATES = _ROOT / "app" / "templates"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("basic", "Básico"),
        ("advanced", "Avanzado"),
        ("premium", "Premium"),
        ("BASIC", "Básico"),
        # Alias legacy del campo tenants.plan.
        ("free", "Básico"),
        ("medium", "Básico"),
        ("high", "Avanzado"),
        ("total", "Premium"),
        (None, "—"),
        ("", "—"),
        ("custom_x", "custom_x"),  # desconocido: visible tal cual, sin inventar nombre
    ],
)
def test_plan_ui_name(raw: str | None, expected: str) -> None:
    assert plan_ui_name(raw) == expected


def test_catalog_seed_uses_the_same_names() -> None:
    assert set(PLAN_UI_NAMES) == set(PLAN_CODES)
    for code in PLAN_CODES:
        assert PLAN_META[code][0] == PLAN_UI_NAMES[code]


def test_plan_label_filter_is_registered() -> None:
    assert templates.env.filters["plan_label"]("basic") == "Básico"


def test_templates_never_print_raw_plan_code() -> None:
    """Toda salida de plan en plantillas pasa por el filtro plan_label."""
    offenders: list[str] = []
    # {{ ... }} que no están dentro de un atributo value="..." (valor de formulario).
    expr = re.compile(r'(?<!value=")\{\{([^}]*)\}\}')
    for path in _TEMPLATES.rglob("*.html"):
        for match in expr.finditer(path.read_text(encoding="utf-8")):
            body = match.group(1)
            shows_plan = re.search(r"\b(plan_code|\w+\.plan|plan\.name|plan\.code)\b", body)
            if shows_plan and "plan_label" not in body:
                offenders.append(f"{path.relative_to(_ROOT)}: {{{{{body}}}}}")
    assert offenders == []


def test_python_messages_use_plan_names() -> None:
    sources = [
        _ROOT / "app" / "services" / "membership_service.py",
        _ROOT / "app" / "routes" / "web" / "admin" / "plans.py",
        _ROOT / "app" / "services" / "chat_usage_service.py",
    ]
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert "plan_ui_name(" in text, source.name
    emails = (_ROOT / "app" / "services" / "membership_service.py").read_text(encoding="utf-8")
    assert "Plan: {tenant.plan_code or '—'}" not in emails
