"""Nombre de fichero en /documents y /knowledge: máximo 2 líneas + tooltip completo."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_COMPONENTS = _ROOT / "app" / "templates" / "components"


def _read(name: str) -> str:
    return (_COMPONENTS / name).read_text(encoding="utf-8")


def _tags_showing(html: str, expression: str) -> list[str]:
    """Etiquetas de apertura cuyo contenido (o x-text) es ``expression``."""
    pattern = rf"<(?:p|span)\b[^>]*>(?=\s*(?:—\s*)?{{{{\s*{re.escape(expression)})"
    tags = re.findall(pattern, html)
    tags += re.findall(rf'<(?:p|span)\b[^>]*x-text="{re.escape(expression)}"[^>]*>', html)
    return tags


def _assert_two_lines_with_tooltip(tags: list[str], title: str) -> None:
    assert tags, "no se encontró el elemento con el nombre del fichero"
    for tag in tags:
        assert "line-clamp-2" in tag and "break-all" in tag, tag
        assert "truncate" not in tag, tag
        assert title in tag, tag


def test_upload_slots_show_filename_in_two_lines_with_tooltip() -> None:
    html = _read("upload_slots_grid.html")
    _assert_two_lines_with_tooltip(_tags_showing(html, "slot.file.name"), ':title="slot.file.name"')


def test_document_rows_show_filename_in_two_lines_with_tooltip() -> None:
    html = _read("document_row.html")
    tags = _tags_showing(html, "document.source_filename")
    # confirmar tipo, pendiente de cupo, procesando y error.
    assert len(tags) == 4
    _assert_two_lines_with_tooltip(tags, 'title="{{ document.source_filename')


def test_knowledge_row_shows_name_and_filename_in_two_lines_with_tooltip() -> None:
    html = _read("knowledge_row.html")
    _assert_two_lines_with_tooltip(
        _tags_showing(html, "document.original_filename"),
        'title="{{ document.original_filename }}"',
    )
    _assert_two_lines_with_tooltip(
        _tags_showing(html, "document.name"), 'title="{{ document.name }}"'
    )


def test_knowledge_name_cell_css_does_not_force_single_line() -> None:
    css = (_ROOT / "app" / "static" / "css" / "input.css").read_text(encoding="utf-8")
    rule = re.search(r"\.knowledge-td--name p \{([^}]*)\}", css)
    assert rule is not None
    assert "line-clamp-2" in rule.group(1)
    assert "truncate" not in rule.group(1)
