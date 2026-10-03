"""Tests de umbrales de métricas de evals."""

from __future__ import annotations

from app.evals.thresholds import metrics_pass


def test_metrics_pass_when_all_targets_met() -> None:
    ok, failures = metrics_pass(
        {
            "json_validity_rate": 1.0,
            "field_accuracy_avg": 0.96,
            "latency_p50_ms": 7500,
        },
    )
    assert ok is True
    assert failures == []


def test_metrics_fail_when_accuracy_low() -> None:
    ok, failures = metrics_pass(
        {
            "json_validity_rate": 1.0,
            "field_accuracy_avg": 0.80,
            "latency_p50_ms": 7500,
        },
    )
    assert ok is False
    assert any("field_accuracy" in item for item in failures)
