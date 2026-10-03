"""Umbrales de métricas del eval de extracción (arquitectura.md §6.1)."""

from __future__ import annotations

JSON_VALIDITY_MIN = 0.99
FIELD_ACCURACY_MIN = 0.95
# El dataset incluye PDFs de 2-3 páginas (el máximo que se envía al LLM). Con
# thinking bajo (D014) la latencia apenas depende de las páginas: p50 ~2,8 s.
# Si el eval supera el umbral, revisar primero la config de thinking del modelo.
LATENCY_P50_MAX_MS = 8000


def metrics_pass(summary: dict[str, object]) -> tuple[bool, list[str]]:
    """Devuelve (ok, lista de motivos de fallo)."""
    failures: list[str] = []
    json_rate = summary.get("json_validity_rate")
    if isinstance(json_rate, int | float) and json_rate < JSON_VALIDITY_MIN:
        failures.append(
            f"json_validity_rate {json_rate:.1%} < {JSON_VALIDITY_MIN:.0%}",
        )
    accuracy = summary.get("field_accuracy_avg")
    if isinstance(accuracy, int | float) and accuracy < FIELD_ACCURACY_MIN:
        failures.append(
            f"field_accuracy_avg {accuracy:.1%} < {FIELD_ACCURACY_MIN:.0%}",
        )
    latency = summary.get("latency_p50_ms")
    if isinstance(latency, int | float) and latency > LATENCY_P50_MAX_MS:
        failures.append(
            f"latency_p50_ms {latency} > {LATENCY_P50_MAX_MS}",
        )
    rejection_failures = summary.get("rejection_failures")
    if isinstance(rejection_failures, int) and rejection_failures > 0:
        failures.append(
            f"rejection_failures {rejection_failures} > 0 (límite no aplicado)",
        )
    return (not failures, failures)
