"""Tests de carga y render de prompts versionados."""

from __future__ import annotations

from app.llm.prompts_loader import load_prompt, render_prompt


def test_load_prompt_ping_v1() -> None:
    text = load_prompt("ping_v1")
    assert "{name}" in text


def test_render_prompt_name_placeholder_does_not_conflict_with_prompt_id() -> None:
    """El fichero se identifica con prompt_name; {name} es variable del template."""
    text = render_prompt("ping_v1", name="Ana")
    assert "Ana" in text
    assert "{name}" not in text
