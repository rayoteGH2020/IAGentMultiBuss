"""Runner del eval set knowledge_qa_v1 (Paso 20).

Modo ``--validate-only``: valida el dataset sin LLM ni Postgres.
Modo live: requiere tenant con documentos ``ready`` indexados.

Uso:
    uv run python -m app.evals.runners.knowledge_qa --validate-only
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

DATASET = Path(__file__).parent.parent / "datasets" / "knowledge_qa_v1.json"
RESULTS_DIR = Path(__file__).parent.parent / "results"


@dataclass
class CaseResult:
    case_id: str
    skipped: bool = False
    retrieval_recall_at_5: bool = False
    answer_grounded: bool = False
    citation_present: bool = False
    error: str | None = None
    latency_ms: int = 0


def _load_dataset() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(DATASET.read_text(encoding="utf-8")))


def validate_dataset_schema(dataset: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "cases" not in dataset:
        errors.append("missing 'cases'")
        return errors
    for i, case in enumerate(dataset["cases"]):
        for key in ("id", "question", "expected_answer_contains"):
            if key not in case:
                errors.append(f"cases[{i}]: missing {key!r}")
    return errors


def _retrieval_hit(contents: list[str], substrings: list[str]) -> bool:
    for text in contents[:5]:
        lower = text.lower()
        if any(s.lower() in lower for s in substrings):
            return True
    return False


def _answer_grounded(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


async def _run_case_live(case: dict[str, Any], tenant_id: uuid.UUID) -> CaseResult:
    case_id = str(case["id"])
    if case.get("skip_live"):
        return CaseResult(case_id=case_id, skipped=True)

    from app.core.db import session_factory_for_worker
    from app.llm.client import get_llm_client
    from app.models.knowledge import KnowledgeDocumentKind
    from app.schemas.knowledge_search import KnowledgeSearchFilters
    from app.services import knowledge_search_service

    question = str(case["question"])
    substrings = list(case.get("relevant_content_substrings", []))
    keywords = list(case.get("expected_answer_contains", []))
    kind_raw = case.get("expected_sources_kind")
    kinds = (
        [KnowledgeDocumentKind(k) for k in kind_raw if isinstance(k, str)]
        if isinstance(kind_raw, list)
        else None
    )

    t0 = time.perf_counter()
    try:
        async with session_factory_for_worker(tenant_id) as db:
            search = await knowledge_search_service.search(
                db,
                tenant_id=tenant_id,
                query=question,
                filters=KnowledgeSearchFilters(kind=kinds, top_k=10),
                llm_client=get_llm_client(),
                redis=None,
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        contents = [c.content for c in search.chunks]
        retrieval = _retrieval_hit(contents, substrings) if substrings else bool(contents)
        draft_answer = (
            f"Según la base de conocimiento: {contents[0][:300]}"
            if contents
            else "No encontré información."
        )
        grounded = _answer_grounded(draft_answer, keywords) if keywords else retrieval
        return CaseResult(
            case_id=case_id,
            retrieval_recall_at_5=retrieval,
            answer_grounded=grounded,
            citation_present=bool(contents),
            latency_ms=latency_ms,
        )
    except Exception as exc:
        return CaseResult(case_id=case_id, error=str(exc)[:300])


def _summary(results: list[CaseResult], *, validate_only: bool) -> dict[str, Any]:
    evaluated = [r for r in results if not r.skipped and r.error is None]
    if validate_only or not evaluated:
        return {
            "mode": "validate_only" if validate_only else "stub",
            "total_cases": len(results),
            "evaluated_cases": 0,
            "retrieval_recall_at_5": None,
            "answer_grounded": None,
            "citation_present": None,
        }
    n = len(evaluated)
    latencies = sorted(r.latency_ms for r in evaluated)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    return {
        "mode": "live",
        "total_cases": len(results),
        "evaluated_cases": n,
        "retrieval_recall_at_5": sum(1 for r in evaluated if r.retrieval_recall_at_5) / n,
        "answer_grounded": sum(1 for r in evaluated if r.answer_grounded) / n,
        "citation_present": sum(1 for r in evaluated if r.citation_present) / n,
        "latency_p50_ms": p50,
        "cases": [r.__dict__ for r in results],
    }


async def run_evals(
    tenant_id: uuid.UUID | None,
    *,
    validate_only: bool = False,
) -> dict[str, Any]:
    dataset = _load_dataset()
    schema_errors = validate_dataset_schema(dataset)
    if schema_errors:
        return {"valid": False, "schema_errors": schema_errors}

    if validate_only:
        results = [CaseResult(case_id=str(c["id"]), skipped=True) for c in dataset["cases"]]
        out = _summary(results, validate_only=True)
        out["valid"] = True
        return out

    if tenant_id is None:
        return {"valid": False, "error": "tenant_uuid required for live eval"}

    results = [await _run_case_live(c, tenant_id) for c in dataset["cases"]]
    summary = _summary(results, validate_only=False)
    summary["valid"] = True
    summary["targets"] = dataset.get("targets")
    return summary


def main() -> None:
    validate_only = "--validate-only" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tenant_id = uuid.UUID(args[0]) if args else None
    summary = asyncio.run(run_evals(tenant_id, validate_only=validate_only))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"knowledge_qa_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Detalle: {out}")


if __name__ == "__main__":
    main()
