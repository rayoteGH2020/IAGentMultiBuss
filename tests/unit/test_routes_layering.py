"""Guardrail: app/routes must not import app.models directly."""

from __future__ import annotations

from pathlib import Path


def test_routes_do_not_import_models() -> None:
    routes_dir = Path(__file__).resolve().parents[2] / "app" / "routes"
    offenders: list[str] = []
    for path in sorted(routes_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "from app.models" in text or "import app.models" in text:
            offenders.append(str(path.relative_to(routes_dir.parents[1])))
    assert offenders == [], f"routes import models directly: {offenders}"
