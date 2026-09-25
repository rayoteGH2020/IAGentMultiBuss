"""Tests del runner de evals de extracción: casos de rechazo esperado."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.evals.runners import extraction as runner
from app.evals.thresholds import metrics_pass

TENANT_ID = uuid.uuid4()


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def _factory(_tenant_id: uuid.UUID) -> AsyncIterator[MagicMock]:
        yield db

    monkeypatch.setattr(runner, "session_factory_for_worker", _factory)
    return db


def _rejection_case(expected: str = "too_many_pages") -> dict[str, Any]:
    return {"id": "inv_022", "file": "ejemplo_22.pdf", "expected_rejection": expected}


@pytest.mark.asyncio
async def test_four_page_pdf_is_rejected_before_llm(fake_session: MagicMock) -> None:
    result = await runner._run_case(_rejection_case(), TENANT_ID)

    assert result.success is True
    assert result.error is None
    assert result.expected_rejection == "too_many_pages"
    fake_session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejection_case_fails_when_extraction_succeeds(
    fake_session: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "extract_invoice", AsyncMock(return_value=MagicMock()))

    result = await runner._run_case(_rejection_case(), TENANT_ID)

    assert result.success is False
    assert result.error == "not rejected, expected too_many_pages"
    fake_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejection_case_fails_with_different_error_code(fake_session: MagicMock) -> None:
    result = await runner._run_case(_rejection_case("file_too_large"), TENANT_ID)

    assert result.success is False
    assert result.error == "rejected with too_many_pages, expected file_too_large"


@pytest.mark.asyncio
async def test_rejection_case_fails_on_unknown_code(fake_session: MagicMock) -> None:
    result = await runner._run_case(_rejection_case("pdf_page_limit"), TENANT_ID)

    assert result.success is False
    assert result.error == "unknown expected_rejection: pdf_page_limit"


def test_summary_excludes_rejections_from_extraction_metrics() -> None:
    results = [
        runner.CaseResult(case_id="ok", success=True, latency_ms=4000, confidence=0.9),
        runner.CaseResult(
            case_id="rej_ok",
            success=True,
            latency_ms=0,
            confidence=0.0,
            expected_rejection="too_many_pages",
        ),
        runner.CaseResult(
            case_id="rej_ko",
            success=False,
            latency_ms=0,
            confidence=0.0,
            expected_rejection="too_many_pages",
            error="not rejected, expected too_many_pages",
        ),
    ]

    summary = runner._summary(results)

    assert summary["evaluated_cases"] == 1
    assert summary["json_validity_rate"] == 1.0
    assert summary["latency_p50_ms"] == 4000
    assert summary["rejection_cases"] == 2
    assert summary["rejection_failures"] == 1


def test_metrics_fail_when_expected_rejection_not_applied() -> None:
    ok, failures = metrics_pass(
        {
            "json_validity_rate": 1.0,
            "field_accuracy_avg": 0.96,
            "latency_p50_ms": 7500,
            "rejection_failures": 1,
        },
    )
    assert ok is False
    assert any("rejection_failures" in item for item in failures)
