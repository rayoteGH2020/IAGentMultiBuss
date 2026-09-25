"""Runner del eval de chat documental (``chat_documents_v2``).

Siembra documentos conocidos en el tenant de evals (``seed_documents_eval``) y
hace cada pregunta por el mismo camino que producción: prompt unificado,
registry completo de tools y ``run_tool_loop``. Puntúa la respuesta final:

- ``answer_correct``: aparecen todos los ``expected_all`` y, si hay, alguno de
  ``expected_any``. Los valores numéricos se comparan como número, así que
  ``1.703,95 €`` casa con ``1703.95``.
- ``document_tool_used``: el modelo consultó al menos una tool documental (no
  respondió de memoria).

Uso:
    uv run python -m app.evals.runners.chat_documents --validate-only
    infisical run -- uv run python -m app.evals.runners.chat_documents
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)

DATASET = Path(__file__).parent.parent / "datasets" / "chat_documents_v2.json"
RESULTS_DIR = Path(__file__).parent.parent / "results"

DOCUMENT_TOOLS = frozenset(
    {
        "list_doc_types",
        "search_documents",
        "get_document",
        "aggregate_documents",
        "list_document_parties",
    },
)
_DIFFICULTIES = frozenset({"easy", "medium", "hard"})
_NUMERIC = re.compile(r"-?\d+(?:\.\d+)?")
_NUMBER_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")
_TOLERANCE = Decimal("0.005")


@dataclass
class CaseResult:
    case_id: str
    difficulty: str = "medium"
    answer_correct: bool = False
    document_tool_used: bool = False
    missing: list[str] = field(default_factory=list)
    tools_executed: list[str] = field(default_factory=list)
    final_text: str = ""
    latency_ms: int = 0
    cost_eur: float = 0.0
    error: str | None = None


def _normalize_text(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


def _alnum(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", _normalize_text(value))


def _number_candidates(token: str) -> set[Decimal]:
    """Interpretaciones posibles de un número escrito en español o inglés."""
    raw: set[str] = set()
    if "." in token and "," in token:
        if token.rfind(",") > token.rfind("."):
            raw.add(token.replace(".", "").replace(",", "."))
        else:
            raw.add(token.replace(",", ""))
    elif "," in token or "." in token:
        sep = "," if "," in token else "."
        parts = token.split(sep)
        if len(parts) > 2:
            raw.add("".join(parts))
        elif len(parts[1]) == 3:
            # "1.150" o "1,150": ambiguo entre miles y decimales.
            raw.update({"".join(parts), f"{parts[0]}.{parts[1]}"})
        else:
            raw.add(f"{parts[0]}.{parts[1]}")
    else:
        raw.add(token)
    out: set[Decimal] = set()
    for value in raw:
        try:
            out.add(Decimal(value))
        except InvalidOperation:
            continue
    return out


def _numbers_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER_TOKEN.findall(text):
        found |= _number_candidates(token)
    return found


def expected_found(expected: str, answer: str) -> bool:
    """True si ``expected`` aparece en ``answer`` (como número o como texto)."""
    if _NUMERIC.fullmatch(expected):
        target = Decimal(expected)
        return any(abs(n - target) <= _TOLERANCE for n in _numbers_in(answer))
    if _normalize_text(expected) in _normalize_text(answer):
        return True
    code = _alnum(expected)
    return bool(code) and code in _alnum(answer)


def score_answer(
    answer: str,
    expected_all: list[str],
    expected_any: list[str],
) -> tuple[bool, list[str]]:
    """Devuelve (correcta, valores esperados que faltan)."""
    missing = [exp for exp in expected_all if not expected_found(exp, answer)]
    if expected_any and not any(expected_found(exp, answer) for exp in expected_any):
        missing.append("any_of: " + " | ".join(expected_any))
    return (not missing, missing)


def _load_dataset() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(DATASET.read_text(encoding="utf-8")))


def validate_dataset_schema(dataset: dict[str, Any]) -> list[str]:
    """Devuelve lista de errores de esquema (vacía = OK)."""
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["missing or empty 'cases' array"]
    errors: list[str] = []
    seen: set[str] = set()
    for i, case in enumerate(cases):
        case_id = str(case.get("id", f"#{i}"))
        if case_id in seen:
            errors.append(f"{case_id}: duplicated id")
        seen.add(case_id)
        if not str(case.get("question", "")).strip():
            errors.append(f"{case_id}: missing question")
        if case.get("difficulty") not in _DIFFICULTIES:
            errors.append(f"{case_id}: difficulty must be one of {sorted(_DIFFICULTIES)}")
        if not case.get("expected_all") and not case.get("expected_any"):
            errors.append(f"{case_id}: needs expected_all or expected_any")
    return errors


async def _run_case(case: dict[str, Any], tenant_id: Any) -> CaseResult:
    from app.evals.runners.knowledge_qa import _run_chat_turn

    result = CaseResult(case_id=str(case["id"]), difficulty=str(case["difficulty"]))
    t0 = time.perf_counter()
    try:
        final_text, _citations, tools, _ids, cost = await _run_chat_turn(case, tenant_id)
    except Exception as exc:
        logger.exception("chat_documents_eval.case_failed", case_id=result.case_id)
        result.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        return result
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    result.answer_correct, result.missing = score_answer(
        final_text,
        list(case.get("expected_all", [])),
        list(case.get("expected_any", [])),
    )
    result.document_tool_used = bool(DOCUMENT_TOOLS.intersection(tools))
    result.tools_executed = tools
    # Solo se guarda en el JSON local de resultados (datos sembrados, no de clientes).
    result.final_text = final_text[:500]
    result.cost_eur = cost
    return result


def _rate(results: list[CaseResult], attr: str) -> float | None:
    if not results:
        return None
    return sum(1 for r in results if getattr(r, attr)) / len(results)


def summarize(results: list[CaseResult], targets: dict[str, Any]) -> dict[str, Any]:
    """Métricas agregadas, desglose por dificultad y flags de objetivos."""
    evaluated = [r for r in results if r.error is None]
    latencies = sorted(r.latency_ms for r in evaluated)
    summary: dict[str, Any] = {
        "total_cases": len(results),
        "evaluated_cases": len(evaluated),
        "error_cases": len(results) - len(evaluated),
        # Los errores cuentan como respuesta incorrecta: un fallo del loop es
        # una mala experiencia para el usuario, no un caso a excluir.
        "answer_correct": _rate(results, "answer_correct"),
        "document_tool_used": _rate(results, "document_tool_used"),
        "latency_p50_ms": median(latencies) if latencies else None,
        "cost_eur_total": round(sum(r.cost_eur for r in results), 6),
        "by_difficulty": {
            level: _rate([r for r in results if r.difficulty == level], "answer_correct")
            for level in sorted(_DIFFICULTIES)
        },
        "cases": [asdict(r) for r in results],
    }
    failures: list[str] = []
    correct = summary["answer_correct"] or 0.0
    if correct < float(targets.get("answer_correct_min", 0.8)):
        failures.append(f"answer_correct {correct:.1%} < {targets.get('answer_correct_min', 0.8)}")
    tools = summary["document_tool_used"] or 0.0
    if tools < float(targets.get("document_tool_used_min", 0.9)):
        failures.append(
            f"document_tool_used {tools:.1%} < {targets.get('document_tool_used_min', 0.9)}",
        )
    p50 = summary["latency_p50_ms"]
    if p50 is not None and p50 > int(targets.get("latency_p50_ms_max", 8000)):
        failures.append(f"latency_p50_ms {p50} > {targets.get('latency_p50_ms_max', 8000)}")
    summary["target_failures"] = failures
    summary["all_targets_pass"] = not failures
    return summary


async def run_evals(*, validate_only: bool = False) -> dict[str, Any]:
    """Valida el dataset y, en modo live, siembra datos y ejecuta los casos."""
    dataset = _load_dataset()
    errors = validate_dataset_schema(dataset)
    if errors or validate_only:
        return {
            "valid": not errors,
            "schema_errors": errors,
            "total_cases": len(dataset.get("cases", [])),
        }

    from app.evals.seed_documents_eval import seed

    counts = await seed()
    from app.evals.eval_tenant import ensure_eval_tenant

    tenant_id = await ensure_eval_tenant()
    results = [await _run_case(case, tenant_id) for case in dataset["cases"]]
    summary = summarize(results, dataset.get("targets") or {})
    summary["valid"] = True
    summary["dataset_version"] = dataset.get("version")
    summary["seeded_documents"] = counts
    return summary


_STDOUT_KEYS = (
    "valid",
    "schema_errors",
    "total_cases",
    "evaluated_cases",
    "error_cases",
    "answer_correct",
    "document_tool_used",
    "latency_p50_ms",
    "cost_eur_total",
    "by_difficulty",
    "target_failures",
)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    validate_only = "--validate-only" in sys.argv
    summary = asyncio.run(run_evals(validate_only=validate_only))
    view = {k: summary[k] for k in _STDOUT_KEYS if k in summary}
    sys.stdout.write(json.dumps(view, indent=2, ensure_ascii=False) + "\n")
    if not summary.get("valid"):
        raise SystemExit(1)
    if validate_only:
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"chat_documents_v2_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(f"Detalle: {out}\n")
    if summary["target_failures"] and not os.getenv("EVAL_SKIP_GATING"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
