"""Tests del runner stub de evals chat documental."""

from __future__ import annotations

import json
from pathlib import Path

from app.evals.runners.chat_documents import (
    DATASET,
    score_answer_grounded,
    score_tool_selection,
    validate_dataset_schema,
)


def test_score_tool_selection_exact_match() -> None:
    assert score_tool_selection(["list_doc_types"], ["list_doc_types"])
    assert score_tool_selection(
        ["search_documents", "aggregate_documents"],
        ["aggregate_documents", "search_documents"],
    )
    assert not score_tool_selection(["list_doc_types"], ["search_documents"])


def test_score_answer_grounded_keywords() -> None:
    assert score_answer_grounded(
        "Según tus facturas, el total es 100 €",
        ["factura"],
        tool_results_ok=True,
    )
    assert not score_answer_grounded("Sin datos", ["factura"], tool_results_ok=False)
    assert score_answer_grounded("Respuesta", [], tool_results_ok=True)


def test_chat_documents_dataset_schema_valid() -> None:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    assert validate_dataset_schema(dataset) == []


def test_chat_documents_dataset_file_exists() -> None:
    assert Path(DATASET).is_file()
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 3
