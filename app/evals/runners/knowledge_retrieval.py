"""Runner del eval set de retrieval de conocimiento (Paso 19).

Métricas:
  - ``recall_at_5``: fracción de casos con al menos un substring relevante en top-5.
  - ``mrr_at_10``: Mean Reciprocal Rank en top-10 (primer hit relevante).

Modos:
  - ``--validate-only``: valida esquema del dataset sin BD ni LLM.
  - Live: ``infisical run -- uv run python -m app.evals.runners.knowledge_retrieval <tenant_uuid>``
    requiere Postgres con documentos ``ready`` indexados.

Uso:
    uv run python -m app.evals.runners.knowledge_retrieval --validate-only
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)

DATASET = Path(__file__).parent.parent / "datasets" / "knowledge_retrieval_v1.json"
RESULTS_DIR = Path(__file__).parent.parent / "results"


@dataclass
class CaseResult:
    case_id: str
    skipped: bool = False
    recall_at_5: bool = False
    reciprocal_rank: float = 0.0
    top_chunk_snippets: list[str] = field(default_factory=list)
    error: str | None = None


def _load_dataset() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(DATASET.read_text(encoding="utf-8")))


def validate_dataset_schema(dataset: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "cases" not in dataset:
        errors.append("missing 'cases' array")
        return errors
    for i, case in enumerate(dataset["cases"]):
        prefix = f"cases[{i}]"
        for key in ("id", "query", "relevant_content_substrings"):
            if key not in case:
                errors.append(f"{prefix}: missing '{key}'")
        subs = case.get("relevant_content_substrings")
        if subs is not None and (not isinstance(subs, list) or not subs):
            errors.append(f"{prefix}: relevant_content_substrings must be non-empty list")
    return errors


def _case_relevant(chunk_content: str, substrings: list[str]) -> bool:
    lower = chunk_content.lower()
    return any(s.lower() in lower for s in substrings)


def score_recall_at_5(
    ranked_contents: list[str],
    substrings: list[str],
) -> bool:
    return any(_case_relevant(content, substrings) for content in ranked_contents[:5])


def score_reciprocal_rank(
    ranked_contents: list[str],
    substrings: list[str],
    *,
    k: int = 10,
) -> float:
    for rank, content in enumerate(ranked_contents[:k], start=1):
        if _case_relevant(content, substrings):
            return 1.0 / rank
    return 0.0


async def _run_case_live(
    case: dict[str, Any],
    tenant_id: uuid.UUID,
) -> CaseResult:
    case_id = str(case["id"])
    if case.get("skip_live"):
        return CaseResult(case_id=case_id, skipped=True)

    substrings = list(case.get("relevant_content_substrings", []))
    query = str(case["query"])

    from app.core.db import session_factory_for_worker
    from app.llm.client import get_llm_client
    from app.models.knowledge import KnowledgeDocumentKind
    from app.schemas.knowledge_search import KnowledgeSearchFilters
    from app.services import knowledge_search_service

    kind_raw = case.get("document_kind")
    kind_filter = [KnowledgeDocumentKind(kind_raw)] if isinstance(kind_raw, str) else None

    try:
        async with session_factory_for_worker(tenant_id) as db:
            result = await knowledge_search_service.search(
                db,
                tenant_id=tenant_id,
                query=query,
                filters=KnowledgeSearchFilters(kind=kind_filter, top_k=10),
                llm_client=get_llm_client(),
                redis=None,
            )

        contents = [c.content for c in result.chunks]
        recall = score_recall_at_5(contents, substrings)
        rr = score_reciprocal_rank(contents, substrings, k=10)
        return CaseResult(
            case_id=case_id,
            recall_at_5=recall,
            reciprocal_rank=rr,
            top_chunk_snippets=[c[:120] for c in contents[:3]],
        )
    except Exception as exc:
        logger.exception("knowledge_retrieval_eval.case_failed", case_id=case_id)
        return CaseResult(case_id=case_id, error=str(exc)[:300])


def _summary(results: list[CaseResult], *, validate_only: bool) -> dict[str, Any]:
    evaluated = [r for r in results if not r.skipped and r.error is None]
    skipped = sum(1 for r in results if r.skipped)
    if validate_only or not evaluated:
        return {
            "mode": "validate_only" if validate_only else "stub",
            "total_cases": len(results),
            "evaluated_cases": 0,
            "skipped_cases": skipped if not validate_only else len(results),
            "recall_at_5": None,
            "mrr_at_10": None,
            "cases": [
                {
                    "case_id": r.case_id,
                    "skipped": r.skipped,
                    "recall_at_5": r.recall_at_5,
                    "reciprocal_rank": r.reciprocal_rank,
                    "error": r.error,
                }
                for r in results
            ],
        }

    recall_at_5 = sum(1 for r in evaluated if r.recall_at_5) / len(evaluated)
    mrr_at_10 = sum(r.reciprocal_rank for r in evaluated) / len(evaluated)
    return {
        "mode": "live",
        "total_cases": len(results),
        "evaluated_cases": len(evaluated),
        "skipped_cases": skipped,
        "recall_at_5": recall_at_5,
        "mrr_at_10": mrr_at_10,
        "cases": [
            {
                "case_id": r.case_id,
                "skipped": r.skipped,
                "recall_at_5": r.recall_at_5,
                "reciprocal_rank": round(r.reciprocal_rank, 4),
                "error": r.error,
            }
            for r in results
        ],
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
        stub_results = [
            CaseResult(case_id=str(c["id"]), skipped=True) for c in dataset.get("cases", [])
        ]
        summary = _summary(stub_results, validate_only=True)
        summary["valid"] = True
        summary["dataset_version"] = dataset.get("version")
        summary["targets"] = dataset.get("targets")
        return summary

    if tenant_id is None:
        return {
            "valid": False,
            "error": "tenant_uuid required for live eval (or use --validate-only)",
        }

    live_results: list[CaseResult] = []
    for case in dataset.get("cases", []):
        live_results.append(await _run_case_live(case, tenant_id))
    summary = _summary(live_results, validate_only=False)
    summary["valid"] = True
    summary["dataset_version"] = dataset.get("version")
    summary["targets"] = dataset.get("targets")
    targets = dataset.get("targets") or {}
    if summary.get("recall_at_5") is not None:
        summary["recall_at_5_pass"] = summary["recall_at_5"] >= float(
            targets.get("recall_at_5_min", 0.75)
        )
    if summary.get("mrr_at_10") is not None:
        summary["mrr_at_10_pass"] = summary["mrr_at_10"] >= float(targets.get("mrr_at_10_min", 0.6))
    return summary


def main() -> None:
    validate_only = "--validate-only" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tenant_id = uuid.UUID(args[0]) if args else None

    summary = asyncio.run(
        run_evals(tenant_id, validate_only=validate_only),
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"knowledge_retrieval_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    keys = (
        "valid",
        "mode",
        "total_cases",
        "evaluated_cases",
        "skipped_cases",
        "recall_at_5",
        "mrr_at_10",
        "recall_at_5_pass",
        "mrr_at_10_pass",
    )
    sys.stdout.write(json.dumps({k: summary.get(k) for k in keys}, indent=2) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")


if __name__ == "__main__":
    main()
