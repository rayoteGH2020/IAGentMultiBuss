"""Tests del gate de regresión de evals frente a la línea base de main."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.evals.compare_baseline import (
    BASELINE_PATH,
    Baseline,
    collect_current_metrics,
    compare,
    load_baseline,
    main,
)

_TRACKED = {
    "invoices_v1": ["field_accuracy_avg"],
    "chat_documents_v2": ["answer_correct"],
}


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_baseline(tmp_path: Path, metrics: dict[str, dict[str, float]]) -> Path:
    return _write_json(
        tmp_path / "baselines.json",
        {"max_relative_drop": 0.05, "metrics": metrics},
    )


def _results_dir(tmp_path: Path, files: dict[str, dict[str, object]]) -> Path:
    results = tmp_path / "results"
    results.mkdir()
    for name, payload in files.items():
        _write_json(results / name, payload)
    return results


def test_collect_averages_repeated_runs_and_ignores_old_or_untracked(tmp_path: Path) -> None:
    results = _results_dir(
        tmp_path,
        {
            "chat_documents_v2_2000.json": {"answer_correct": 1.0},
            "chat_documents_v2_2001.json": {"answer_correct": 0.9375},
            "chat_documents_v2_1000.json": {"answer_correct": 0.1},
            "invoices_v1_2000.json": {"field_accuracy_avg": 0.98, "extra": 5},
            "knowledge_qa_v1_2000.json": {"answer_correct": 0.2},
            "notes.json": {"answer_correct": 0.0},
        },
    )

    current = collect_current_metrics(results, since=2000, tracked=_TRACKED)

    assert current == {
        "chat_documents_v2": {"answer_correct": pytest.approx(0.96875)},
        "invoices_v1": {"field_accuracy_avg": 0.98},
    }


def test_collect_skips_non_numeric_values(tmp_path: Path) -> None:
    results = _results_dir(
        tmp_path,
        {"invoices_v1_2000.json": {"field_accuracy_avg": True}},
    )

    assert collect_current_metrics(results, since=0, tracked=_TRACKED) == {}


def test_compare_flags_drop_above_five_percent_only() -> None:
    baseline = Baseline(
        metrics={
            "invoices_v1": {"field_accuracy_avg": 1.0},
            "chat_documents_v2": {"answer_correct": 1.0},
        },
    )
    current = {
        "invoices_v1": {"field_accuracy_avg": 0.95},
        "chat_documents_v2": {"answer_correct": 0.9375},
    }

    checks = {c.eval_name: c for c in compare(baseline, current)}

    assert checks["invoices_v1"].regressed is False
    assert checks["chat_documents_v2"].regressed is True
    assert checks["chat_documents_v2"].relative_drop == pytest.approx(0.0625)


def test_compare_accepts_improvement_and_zero_baseline() -> None:
    baseline = Baseline(metrics={"invoices_v1": {"field_accuracy_avg": 0.9, "rate": 0.0}})
    current = {"invoices_v1": {"field_accuracy_avg": 0.99, "rate": 0.0}}

    assert not any(check.regressed for check in compare(baseline, current))


def test_compare_treats_missing_metric_as_regression() -> None:
    baseline = Baseline(metrics={"contracts_v1": {"field_accuracy_avg": 1.0}})

    [check] = compare(baseline, {})

    assert check.regressed is True
    assert check.current is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"metrics": {}},
        {"metrics": {"invoices_v1": {"field_accuracy_avg": "high"}}},
        {"metrics": {"invoices_v1": {"field_accuracy_avg": 1.0}}, "max_relative_drop": 1.5},
    ],
)
def test_load_baseline_rejects_invalid_files(tmp_path: Path, payload: object) -> None:
    path = _write_json(tmp_path / "baselines.json", payload)

    with pytest.raises(ValueError):
        load_baseline(path)


def test_repo_baseline_covers_evals_run_in_ci() -> None:
    baseline = load_baseline(BASELINE_PATH)

    assert baseline.max_relative_drop == pytest.approx(0.05)
    assert set(baseline.metrics) == {
        "invoices_v1",
        "tickets_v1",
        "contracts_v2",
        "insurances_v1",
        "chat_documents_v2",
    }


def test_main_fails_on_regression_and_writes_step_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _write_baseline(tmp_path, {"chat_documents_v2": {"answer_correct": 1.0}})
    results = _results_dir(tmp_path, {"chat_documents_v2_10.json": {"answer_correct": 0.9}})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    code = main(["--baseline", str(baseline), "--results-dir", str(results)])

    assert code == 1
    assert "REGRESIÓN" in summary.read_text(encoding="utf-8")


def test_main_allow_regression_does_not_fail(tmp_path: Path) -> None:
    baseline = _write_baseline(tmp_path, {"chat_documents_v2": {"answer_correct": 1.0}})
    results = _results_dir(tmp_path, {"chat_documents_v2_10.json": {"answer_correct": 0.9}})

    code = main(
        ["--baseline", str(baseline), "--results-dir", str(results), "--allow-regression"],
    )

    assert code == 0


def test_main_passes_within_tolerance(tmp_path: Path) -> None:
    baseline = _write_baseline(tmp_path, {"invoices_v1": {"field_accuracy_avg": 0.98}})
    results = _results_dir(tmp_path, {"invoices_v1_10.json": {"field_accuracy_avg": 0.97}})

    assert main(["--baseline", str(baseline), "--results-dir", str(results)]) == 0


def test_main_update_rewrites_measured_metrics_only(tmp_path: Path) -> None:
    baseline = _write_baseline(
        tmp_path,
        {
            "invoices_v1": {"field_accuracy_avg": 0.9},
            "contracts_v1": {"field_accuracy_avg": 1.0},
        },
    )
    results = _results_dir(
        tmp_path,
        {"invoices_v1_10.json": {"field_accuracy_avg": 0.987654}},
    )

    code = main(["--baseline", str(baseline), "--results-dir", str(results), "--update"])

    assert code == 0
    written = json.loads(baseline.read_text(encoding="utf-8"))
    assert written["max_relative_drop"] == 0.05
    assert written["metrics"] == {
        "invoices_v1": {"field_accuracy_avg": 0.9877},
        "contracts_v1": {"field_accuracy_avg": 1.0},
    }


def test_main_reports_invalid_baseline(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    assert main(["--baseline", str(missing), "--results-dir", str(tmp_path)]) == 1
