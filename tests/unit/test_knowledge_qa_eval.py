"""Tests del runner y dataset knowledge_qa_v1 (Paso 20 Fase F)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.evals.runners import knowledge_qa as runner
from app.evals.runners.knowledge_qa import (
    score_answer_grounded,
    score_citation_present,
    score_retrieval_recall_at_5,
    validate_dataset_schema,
)

DATASET_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "evals" / "datasets" / "knowledge_qa_v1.json"
)


def test_dataset_has_at_least_20_cases() -> None:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert len(data["cases"]) >= 20


def test_dataset_schema_valid() -> None:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert validate_dataset_schema(data) == []


def test_validate_only_mode() -> None:
    summary = asyncio.run(runner.run_evals(None, validate_only=True))
    assert summary["valid"] is True
    assert summary["mode"] == "validate_only"
    assert summary["total_cases"] >= 20


def test_score_retrieval_recall_at_5() -> None:
    class _Chunk:
        def __init__(self, content: str, kind: str) -> None:
            self.content = content
            self.kind = kind

    chunks = [
        _Chunk("Horario Sábados 9:00 a 14:00", "schedule"),
        _Chunk("otro", "faq"),
    ]
    assert score_retrieval_recall_at_5(
        chunks,
        substrings=["Sábados"],
        expected_kinds=["schedule"],
    )
    assert not score_retrieval_recall_at_5(
        chunks,
        substrings=["inexistente"],
        expected_kinds=["schedule"],
    )


def test_score_answer_grounded_requires_evidence() -> None:
    assert score_answer_grounded(
        "El horario del sábado es de 9:00 a 14:00",
        ["sábado", "horario"],
        has_citations=False,
        retrieval_ok=True,
    )
    assert not score_answer_grounded(
        "respuesta vacía",
        ["sábado"],
        has_citations=False,
        retrieval_ok=False,
    )


def test_score_citation_present() -> None:
    assert score_citation_present([{"ref": 1}])
    assert not score_citation_present([])
