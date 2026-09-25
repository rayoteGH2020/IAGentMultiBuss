"""Tests del eval de chat documental (runner, dataset v2 y seed)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.evals import seed_documents_eval
from app.evals.runners import chat_documents as runner

_DATE_FIELD = {
    "factura": "fecha",
    "ticket": "fecha",
    "contrato": "fecha_inicio",
    "seguro": "fecha_inicio",
}
_NAME_FIELD = {
    "factura": "proveedor",
    "ticket": "comercio",
    "contrato": "parte_contraria",
    "seguro": "aseguradora",
}
_AMOUNT_FIELD = {"factura": "total", "ticket": "total", "contrato": "importe", "seguro": "prima"}


def _keep(row: dict[str, Any], doc_type: str, filters: dict[str, Any]) -> bool:
    when: date | None = row[_DATE_FIELD[doc_type]]
    amount: Decimal | None = row[_AMOUNT_FIELD[doc_type]]
    end: date | None = row.get("fecha_fin")
    checks = [
        "name_contains" not in filters
        or filters["name_contains"] in str(row[_NAME_FIELD[doc_type]]).lower(),
        "year" not in filters or (when is not None and when.year == filters["year"]),
        "date_from" not in filters
        or (when is not None and when >= date.fromisoformat(filters["date_from"])),
        "date_to" not in filters
        or (when is not None and when <= date.fromisoformat(filters["date_to"])),
        "total_gt" not in filters or (amount is not None and amount > Decimal(filters["total_gt"])),
        "end_month" not in filters
        or (end is not None and end.strftime("%Y-%m") == filters["end_month"]),
    ]
    return all(checks)


def _evaluate_check(check: dict[str, Any], seed: dict[str, list[dict[str, Any]]]) -> Decimal:
    doc_type = check["doc_type"]
    rows = [r for r in seed[doc_type] if _keep(r, doc_type, check.get("filters", {}))]
    if check["metric"] == "count":
        return Decimal(len(rows))
    field = check.get("field", _AMOUNT_FIELD[doc_type])
    return sum((r[field] for r in rows if r[field] is not None), Decimal("0"))


_DATASET = runner._load_dataset()
_CHECKED = [c for c in _DATASET["cases"] if "check" in c]


@pytest.mark.parametrize("case", _CHECKED, ids=[c["id"] for c in _CHECKED])
def test_dataset_checks_match_seeded_documents(case: dict[str, Any]) -> None:
    seed = seed_documents_eval.build_seed_documents()
    check = case["check"]
    assert _evaluate_check(check, seed) == Decimal(check["expect"])
    expectations = case.get("expected_all", []) + case.get("expected_any", [])
    if check["expect"] != "0":
        assert check["expect"] in expectations


def test_dataset_schema_is_valid_and_not_trivial() -> None:
    assert runner.validate_dataset_schema(_DATASET) == []
    difficulties = {c["difficulty"] for c in _DATASET["cases"]}
    assert difficulties == {"easy", "medium", "hard"}


def test_schema_validation_reports_problems() -> None:
    errors = runner.validate_dataset_schema(
        {
            "cases": [
                {"id": "a", "question": "q", "difficulty": "easy"},
                {"id": "a", "question": "", "difficulty": "extreme", "expected_all": ["1"]},
            ],
        },
    )
    assert "a: needs expected_all or expected_any" in errors
    assert "a: duplicated id" in errors
    assert "a: missing question" in errors
    assert any("difficulty" in e for e in errors)


@pytest.mark.parametrize(
    ("expected", "answer"),
    [
        ("1703.95", "El total asciende a **1.703,95 €**."),
        ("873.75", "Gastaste 873,75 € en 2024."),
        ("13800", "La renta anual es de 13.800 €."),
        ("8052.10", "Pagas 8.052,10 EUR al año."),
        ("448.98", "Importe: 448.98 EUR"),
        ("17", "Tienes **17** facturas."),
        ("HG-46-2026-112095", "La póliza es HG 46 2026 112095."),
        ("Alquiler Seguro", "El proveedor es alquiler seguro energia."),
    ],
)
def test_expected_found_accepts_spanish_formats(expected: str, answer: str) -> None:
    assert runner.expected_found(expected, answer)


@pytest.mark.parametrize(
    ("expected", "answer"),
    [
        ("3", "En 2023 tienes 5 facturas."),
        ("469.11", "La suma es 496,11 €."),
        ("PC Componentes", "La factura más cara es de Conforama."),
    ],
)
def test_expected_found_rejects_wrong_values(expected: str, answer: str) -> None:
    assert not runner.expected_found(expected, answer)


def test_score_answer_requires_all_and_one_of_any() -> None:
    ok, missing = runner.score_answer("2024: 873,75 €", ["2024", "873.75"], [])
    assert ok and missing == []
    ok, missing = runner.score_answer("Suman 578,19 €", [], ["578.19", "618.95"])
    assert ok
    ok, missing = runner.score_answer("Suman 500 €", ["2024"], ["578.19"])
    assert not ok
    assert missing == ["2024", "any_of: 578.19"]


def _result(
    case_id: str, *, correct: bool, tools: bool = True, latency: int = 1000
) -> runner.CaseResult:
    return runner.CaseResult(
        case_id=case_id,
        difficulty="hard",
        answer_correct=correct,
        document_tool_used=tools,
        latency_ms=latency,
    )


def test_summarize_flags_targets() -> None:
    results = [_result("a", correct=True), _result("b", correct=False, tools=False)]
    summary = runner.summarize(results, {"answer_correct_min": 0.8, "document_tool_used_min": 0.9})
    assert summary["answer_correct"] == 0.5
    assert summary["by_difficulty"]["hard"] == 0.5
    assert summary["all_targets_pass"] is False
    assert len(summary["target_failures"]) == 2


def test_summarize_counts_errors_as_incorrect() -> None:
    errored = runner.CaseResult(case_id="e", error="RuntimeError: boom")
    summary = runner.summarize([_result("a", correct=True), errored], {})
    assert summary["error_cases"] == 1
    assert summary["answer_correct"] == 0.5
    assert summary["latency_p50_ms"] == 1000


@pytest.mark.asyncio
async def test_run_case_scores_answer_and_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.evals.runners import knowledge_qa

    turn = AsyncMock(
        return_value=("Tienes **17** facturas.", [], ["aggregate_documents"], [], 0.001)
    )
    monkeypatch.setattr(knowledge_qa, "_run_chat_turn", turn)
    case = {"id": "doc_001", "difficulty": "easy", "question": "¿Cuántas?", "expected_all": ["17"]}

    result = await runner._run_case(case, uuid.uuid4())

    assert result.answer_correct is True
    assert result.document_tool_used is True
    assert result.cost_eur == 0.001


@pytest.mark.asyncio
async def test_run_case_answer_without_tools_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.evals.runners import knowledge_qa

    turn = AsyncMock(return_value=("Tienes 17 facturas.", [], ["search_knowledge"], [], 0.0))
    monkeypatch.setattr(knowledge_qa, "_run_chat_turn", turn)
    case = {"id": "doc_001", "difficulty": "easy", "question": "¿Cuántas?", "expected_all": ["17"]}

    result = await runner._run_case(case, uuid.uuid4())

    assert result.answer_correct is True
    assert result.document_tool_used is False


def test_seed_excludes_rejected_and_duplicated_documents() -> None:
    seed = seed_documents_eval.build_seed_documents()
    invoice_ids = {row["case_id"] for row in seed["factura"]}
    assert "inv_022" not in invoice_ids  # rechazo esperado (4 páginas)
    assert "inv_001" not in invoice_ids  # mismo fichero que un ticket
    assert "inv_005" not in invoice_ids  # sin ground truth
    assert {k: len(v) for k, v in seed.items()} == {
        "factura": 17,
        "ticket": 3,
        "contrato": 6,
        "seguro": 8,
    }


def test_seed_uses_first_alternative_including_null() -> None:
    contracts = {r["case_id"]: r for r in seed_documents_eval.build_seed_documents()["contrato"]}
    assert contracts["ctr_nda"]["importe"] is None
    assert contracts["ctr_arrendamiento_garaje"]["importe"] == Decimal("95")
    assert contracts["ctr_consultoria"]["parte_contraria"] == "Nexora Soluciones Digitales"
