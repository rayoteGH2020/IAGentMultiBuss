"""Gate de regresión de evals frente a la línea base de ``main`` (AGENTS.md §9).

Compara las métricas de los resultados de esta ejecución con ``baselines.json`` y
falla si alguna baja más de ``max_relative_drop`` (5 % relativo). Si una eval se
ejecuta varias veces (chat documental en CI), se compara la media.

Uso::

    uv run python -m app.evals.compare_baseline --since <epoch> [--baseline PATH]
    uv run python -m app.evals.compare_baseline --since <epoch> --update

``--update`` reescribe la línea base con las medias actuales; el cambio debe ir en
el diff del PR para revisarse. Para vigilar una eval o métrica nueva, añadirla a
mano en ``baselines.json`` y ejecutar ``--update``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

BASELINE_PATH = Path(__file__).parent / "baselines.json"
RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_MAX_RELATIVE_DROP = 0.05
_RESULT_NAME = re.compile(r"^(?P<eval>.+)_(?P<ts>\d+)\.json$")
# Evita que 0.95 vs 1.0 (exactamente 5 %) falle por redondeo en coma flotante.
_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class MetricCheck:
    """Resultado de comparar una métrica con su línea base."""

    eval_name: str
    metric: str
    baseline: float
    current: float | None
    relative_drop: float | None
    regressed: bool


@dataclass(frozen=True, slots=True)
class Baseline:
    """Línea base: métricas por eval y bajada relativa máxima permitida."""

    metrics: dict[str, dict[str, float]]
    max_relative_drop: float = DEFAULT_MAX_RELATIVE_DROP

    def tracked(self) -> dict[str, list[str]]:
        return {name: list(metrics) for name, metrics in self.metrics.items()}


def load_baseline(path: Path) -> Baseline:
    """Lee y valida ``baselines.json``.

    Raises:
        ValueError: si el fichero no tiene la estructura esperada.
    """
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    metrics = raw.get("metrics") if isinstance(raw, dict) else None
    if not isinstance(metrics, dict) or not metrics:
        msg = f"{path}: falta el objeto 'metrics'"
        raise ValueError(msg)
    parsed: dict[str, dict[str, float]] = {}
    for eval_name, values in metrics.items():
        if not isinstance(values, dict) or not values:
            msg = f"{path}: 'metrics.{eval_name}' debe ser un objeto con métricas"
            raise ValueError(msg)
        parsed[eval_name] = {}
        for metric, value in values.items():
            if not _is_number(value):
                msg = f"{path}: 'metrics.{eval_name}.{metric}' debe ser numérico"
                raise ValueError(msg)
            parsed[eval_name][metric] = float(value)
    drop = raw.get("max_relative_drop", DEFAULT_MAX_RELATIVE_DROP)
    if not _is_number(drop) or not 0 <= drop < 1:
        msg = f"{path}: 'max_relative_drop' debe estar en [0, 1)"
        raise ValueError(msg)
    return Baseline(metrics=parsed, max_relative_drop=float(drop))


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def collect_current_metrics(
    results_dir: Path,
    *,
    since: int,
    tracked: dict[str, list[str]],
) -> dict[str, dict[str, float]]:
    """Media de cada métrica vigilada en los resultados con timestamp >= ``since``.

    Los runners nombran los ficheros ``<eval>_<epoch>.json``; ``since`` descarta
    ejecuciones anteriores que sigan en el directorio (local o artifacts).
    """
    samples: dict[str, dict[str, list[float]]] = {}
    for path in sorted(results_dir.glob("*.json")):
        match = _RESULT_NAME.match(path.name)
        if match is None or int(match["ts"]) < since or match["eval"] not in tracked:
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))
        for metric in tracked[match["eval"]]:
            value = summary.get(metric)
            if _is_number(value):
                samples.setdefault(match["eval"], {}).setdefault(metric, []).append(float(value))
    return {
        eval_name: {metric: mean(values) for metric, values in metrics.items()}
        for eval_name, metrics in samples.items()
    }


def compare(baseline: Baseline, current: dict[str, dict[str, float]]) -> list[MetricCheck]:
    """Compara cada métrica de la línea base; una métrica ausente cuenta como regresión."""
    checks: list[MetricCheck] = []
    for eval_name, metrics in baseline.metrics.items():
        for metric, base in metrics.items():
            value = current.get(eval_name, {}).get(metric)
            if value is None:
                checks.append(MetricCheck(eval_name, metric, base, None, None, regressed=True))
                continue
            drop = (base - value) / base if base > 0 else 0.0
            regressed = drop > baseline.max_relative_drop + _EPSILON
            checks.append(MetricCheck(eval_name, metric, base, value, drop, regressed))
    return checks


def format_report(checks: list[MetricCheck], max_relative_drop: float) -> str:
    """Tabla Markdown para stdout y el resumen del job de GitHub Actions."""
    lines = [
        f"### Evals vs baseline de main (bajada máxima {max_relative_drop:.0%})",
        "",
        "| Eval | Métrica | Baseline | Actual | Bajada | Estado |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        current = "—" if check.current is None else f"{check.current:.4f}"
        drop = "—" if check.relative_drop is None else f"{check.relative_drop:+.1%}"
        lines.append(
            f"| {check.eval_name} | {check.metric} | {check.baseline:.4f} "
            f"| {current} | {drop} | {_status(check)} |",
        )
    return "\n".join(lines)


def _status(check: MetricCheck) -> str:
    if check.current is None:
        return "FALTA"
    return "REGRESIÓN" if check.regressed else "ok"


def write_baseline(path: Path, baseline: Baseline, current: dict[str, dict[str, float]]) -> None:
    """Sustituye las métricas vigiladas por las actuales; conserva las que no se midieron."""
    metrics = {
        eval_name: {
            metric: round(current.get(eval_name, {}).get(metric, base), 4)
            for metric, base in values.items()
        }
        for eval_name, values in baseline.metrics.items()
    }
    payload = {
        "updated_at": datetime.now(tz=UTC).date().isoformat(),
        "max_relative_drop": baseline.max_relative_drop,
        "metrics": metrics,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--since", type=int, default=0, help="Epoch mínimo de los resultados")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--allow-regression",
        action="store_true",
        help="Informa de las regresiones sin fallar (etiqueta eval-regression-accepted)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Reescribe la línea base con las métricas actuales",
    )
    return parser.parse_args(argv)


def _publish(report: str) -> None:
    sys.stdout.write(report + "\n")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report + "\n")


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI; devuelve el exit code."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Baseline inválida: {exc}\n")
        return 1
    current = collect_current_metrics(
        args.results_dir, since=args.since, tracked=baseline.tracked()
    )
    checks = compare(baseline, current)
    _publish(format_report(checks, baseline.max_relative_drop))

    if args.update:
        write_baseline(args.baseline, baseline, current)
        sys.stdout.write(f"Baseline actualizada: {args.baseline}\n")
        return 0
    regressions = [check for check in checks if check.regressed]
    if not regressions:
        return 0
    for check in regressions:
        reason = "sin resultado" if check.current is None else f"bajada {check.relative_drop:.1%}"
        sys.stderr.write(f"  - {check.eval_name}.{check.metric}: {reason}\n")
    if args.allow_regression:
        sys.stderr.write("Regresiones aceptadas por etiqueta eval-regression-accepted.\n")
        return 0
    sys.stderr.write("Evals con regresión frente a main (AGENTS.md §9).\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
