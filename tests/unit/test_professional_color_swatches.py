"""Flags de paleta de color de profesionales."""

from __future__ import annotations

from app.schemas.scheduling import (
    DEFAULT_PROFESSIONAL_COLOR,
    PROFESSIONAL_COLOR_PALETTE,
    build_professional_color_swatches,
)


def test_build_swatches_marks_only_taken_not_selected() -> None:
    swatches = build_professional_color_swatches(
        selected_color="#6366f1",
        taken_colors={"#ef4444", "#22c55e", "#6366f1"},
    )
    assert len(swatches) == 30
    by_hex = {row["hex"]: row for row in swatches}

    assert by_hex["#6366f1"]["is_selected"] is True
    assert by_hex["#6366f1"]["is_taken"] is False  # el propio no cuenta como ocupado
    assert by_hex["#ef4444"]["is_taken"] is True
    assert by_hex["#ef4444"]["is_selected"] is False
    assert by_hex["#22c55e"]["is_taken"] is True

    free = [row for row in swatches if not row["is_taken"]]
    assert len(free) == 28


def test_build_swatches_with_empty_taken() -> None:
    swatches = build_professional_color_swatches(
        selected_color=DEFAULT_PROFESSIONAL_COLOR,
        taken_colors=set(),
    )
    assert all(not row["is_taken"] for row in swatches)
    assert sum(1 for row in swatches if row["is_selected"]) == 1
    assert swatches[PROFESSIONAL_COLOR_PALETTE.index(DEFAULT_PROFESSIONAL_COLOR)]["is_selected"]
