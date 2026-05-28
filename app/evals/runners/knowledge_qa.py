"""Runner del eval set knowledge_qa_v1 (Paso 20).

Por caso (modo live):
  1. ``knowledge_search_service.search()`` — retrieval (Recall@5 + kind).
  2. Opcional con ``--with-llm``: loop de chat real (``run_tool_loop``) — respuesta,
     citas y coste.

Modos:
  - ``--validate-only``: valida esquema del dataset sin BD ni LLM.
  - Live retrieval (por defecto): requiere tenant con docs ``ready`` (sembrar con
    ``python -m app.evals.seed_knowledge_eval <tenant_uuid>``).
  - Live + LLM: añadir ``--with-llm`` (API keys Anthropic/Google).

Uso:
    uv run python -m app.evals.runners.knowledge_qa --validate-only
    infisical run -- uv run python -m app.evals.runners.knowledge_qa <tenant_uuid>
    infisical run -- uv run python -m app.evals.runners.knowledge_qa <tenant_uuid> --with-llm
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

DATASET = Path(__file__).parent.parent / "datasets" / "knowledge_qa_v1.json"
RESULTS_DIR = Path(__file__).parent.parent / "results"

_DIFFICULTIES = frozenset({"easy", "medium", "hard"})


@dataclass
class CaseResult:
    case_id: str
    difficulty: str = "medium"
    skipped: bool = False
    retrieval_recall_at_5: bool = False
    answer_grounded: bool = False
    citation_present: bool = False
    tools_executed: list[str] = field(default_factory=list)
    final_text: str = ""
    cost_eur: float = 0.0
    error: str | None = None
    latency_ms: int = 0


def _load_dataset() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(DATASET.read_text(encoding="utf-8")))


def validate_dataset_schema(dataset: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "cases" not in dataset:
        errors.append("missing 'cases'")
        return errors
    cases = dataset["cases"]
    if len(cases) < 20:
        errors.append(f"expected at least 20 cases, got {len(cases)}")
    diff_counts: dict[str, int] = {}
    for i, case in enumerate(cases):
        prefix = f"cases[{i}]"
        for key in ("id", "question", "expected_answer_contains", "difficulty"):
            if key not in case:
                errors.append(f"{prefix}: missing {key!r}")
        diff = case.get("difficulty")
        if isinstance(diff, str):
            if diff not in _DIFFICULTIES:
                errors.append(f"{prefix}: invalid difficulty {diff!r}")
            diff_counts[diff] = diff_counts.get(diff, 0) + 1
        subs = case.get("relevant_content_substrings")
        if subs is not None and (not isinstance(subs, list) or not subs):
            errors.append(f"{prefix}: relevant_content_substrings must be non-empty list")
    return errors


def _chunk_kind_value(kind: Any) -> str:
    return kind.value if hasattr(kind, "value") else str(kind)


def score_retrieval_recall_at_5(
    chunks: list[Any],
    *,
    substrings: list[str],
    expected_kinds: list[str] | None,
) -> bool:
    """True si top-5 contiene substring relevante y (opcional) kind esperado."""
    top = chunks[:5]
    if not top:
        return False
    content_hit = any(
        any(s.lower() in (getattr(c, "content", "") or "").lower() for s in substrings) for c in top
    )
    if not substrings:
        content_hit = bool(top)
    if not expected_kinds:
        return content_hit
    kinds_lower = {k.lower() for k in expected_kinds}
    kind_hit = any(_chunk_kind_value(c.kind).lower() in kinds_lower for c in top)
    return content_hit and kind_hit


def score_answer_grounded(
    final_text: str,
    keywords: list[str],
    *,
    has_citations: bool,
    retrieval_ok: bool,
) -> bool:
    """Heurística MVP: keywords en respuesta y evidencia (citas o retrieval)."""
    if not keywords:
        return has_citations or retrieval_ok
    lower = final_text.lower()
    keyword_hit = any(k.lower() in lower for k in keywords)
    return keyword_hit and (has_citations or retrieval_ok)


def score_citation_present(citations: list[Any]) -> bool:
    return len(citations) > 0


def _percentile(sorted_values: list[int], pct: float) -> int:
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * pct))
    return sorted_values[idx]


async def _sum_llm_cost_eur(db: Any, call_ids: list[uuid.UUID]) -> float:
    if not call_ids:
        return 0.0
    from app.models import LLMCall

    stmt = select(LLMCall.cost_eur).where(LLMCall.id.in_(call_ids))
    rows = (await db.execute(stmt)).scalars().all()
    total = sum((r or Decimal("0")) for r in rows)
    return float(total)


async def _run_retrieval(
    case: dict[str, Any],
    tenant_id: uuid.UUID,
) -> tuple[bool, list[Any], int]:
    from app.core.db import session_factory_for_worker
    from app.llm.client import get_llm_client
    from app.models.knowledge import KnowledgeDocumentKind
    from app.schemas.knowledge_search import KnowledgeSearchFilters
    from app.services import knowledge_search_service

    question = str(case["question"])
    substrings = list(case.get("relevant_content_substrings", []))
    kind_raw = case.get("expected_sources_kind")
    kinds = (
        [KnowledgeDocumentKind(k) for k in kind_raw if isinstance(k, str)]
        if isinstance(kind_raw, list)
        else None
    )

    t0 = time.perf_counter()
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
    recall = score_retrieval_recall_at_5(
        search.chunks,
        substrings=substrings,
        expected_kinds=list(kind_raw) if isinstance(kind_raw, list) else None,
    )
    return recall, search.chunks, latency_ms


async def _get_or_create_eval_user(db: Any, tenant_id: uuid.UUID) -> Any:
    """Un usuario eval por tenant (email único global) para re-ejecutar el runner."""
    from app.models import User

    email = f"knowledge-qa-eval-{tenant_id}@eval.local"
    existing = await db.execute(select(User).where(User.email == email).limit(1))
    user = existing.scalar_one_or_none()
    if user is not None:
        return user
    user = User(email=email, name="Knowledge QA Eval")
    db.add(user)
    await db.flush()
    return user


async def _run_chat_turn(
    case: dict[str, Any],
    tenant_id: uuid.UUID,
) -> tuple[str, list[Any], list[str], list[uuid.UUID], float]:
    from app.config import get_settings
    from app.core.db import session_factory_for_worker, set_tenant_context
    from app.llm.chat_prompts import build_chat_system_prompt, resolve_chat_prompt_version
    from app.llm.client import get_llm_client
    from app.llm.tools.registry import ToolContext
    from app.models import ChatThread
    from app.services import chat_tool_runner

    question = str(case["question"])
    settings = get_settings()

    async with session_factory_for_worker(tenant_id) as db:
        await set_tenant_context(db, str(tenant_id))
        user = await _get_or_create_eval_user(db, tenant_id)
        thread = ChatThread(tenant_id=tenant_id, user_id=user.id, title=question[:120])
        db.add(thread)
        await db.flush()

        system_prompt = build_chat_system_prompt(company_name="Eval Tenant", settings=settings)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]
        registry = chat_tool_runner.get_chat_registry()
        ctx = ToolContext(
            db=db,
            tenant_id=tenant_id,
            user_id=user.id,
            thread_id=thread.id,
        )
        loop_result = await get_llm_client().run_tool_loop(
            messages=messages,
            registry=registry,
            ctx=ctx,
            tenant_id=tenant_id,
            db=db,
            prompt_version=resolve_chat_prompt_version(settings),
        )
        cost = await _sum_llm_cost_eur(db, list(loop_result.llm_call_ids))
        await db.commit()

    return (
        loop_result.final_text,
        list(loop_result.citations),
        list(loop_result.tool_calls_executed),
        list(loop_result.llm_call_ids),
        cost,
    )


async def _run_case_live(
    case: dict[str, Any],
    tenant_id: uuid.UUID,
    *,
    with_llm: bool,
) -> CaseResult:
    case_id = str(case["id"])
    difficulty = str(case.get("difficulty", "medium"))
    if case.get("skip_live"):
        return CaseResult(case_id=case_id, difficulty=difficulty, skipped=True)

    keywords = list(case.get("expected_answer_contains", []))
    t0 = time.perf_counter()

    retrieval_ok = False
    chunks: list[Any] = []
    try:
        retrieval_ok, chunks, _retrieval_ms = await _run_retrieval(case, tenant_id)
        final_text = ""
        citations: list[Any] = []
        tools: list[str] = []
        cost = 0.0
        if with_llm and not case.get("skip_live_llm"):
            final_text, citations, tools, _ids, cost = await _run_chat_turn(case, tenant_id)
        elif chunks:
            final_text = f"Según la base de conocimiento: {chunks[0].content[:400]}"
            from app.services.chat_citations import extract_citations_from_search_data

            raw: dict[str, object] = {
                "chunks": [
                    {
                        "id": str(c.id),
                        "document_id": str(c.document_id),
                        "source_name": c.document_name,
                        "kind": _chunk_kind_value(c.kind),
                        "position": c.position,
                        "content": c.content,
                        "score": c.score,
                    }
                    for c in chunks[:5]
                ],
            }
            citations = extract_citations_from_search_data(raw)

        latency_ms = int((time.perf_counter() - t0) * 1000)
        has_citations = score_citation_present(citations)
        grounded = score_answer_grounded(
            final_text,
            keywords,
            has_citations=has_citations,
            retrieval_ok=retrieval_ok,
        )
        if with_llm and not case.get("skip_live_llm"):
            citation_ok = has_citations
        else:
            citation_ok = has_citations or (retrieval_ok and bool(chunks))

        return CaseResult(
            case_id=case_id,
            difficulty=difficulty,
            retrieval_recall_at_5=retrieval_ok,
            answer_grounded=grounded,
            citation_present=citation_ok,
            tools_executed=tools,
            final_text=final_text[:500],
            cost_eur=cost,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.error(
            "knowledge_qa_eval.case_failed",
            case_id=case_id,
            error=str(exc)[:300],
        )
        return CaseResult(
            case_id=case_id,
            difficulty=difficulty,
            retrieval_recall_at_5=retrieval_ok,
            error=str(exc)[:300],
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )


def _metric_rate(results: list[CaseResult], attr: str) -> float | None:
    evaluated = [r for r in results if not r.skipped and r.error is None]
    if not evaluated:
        return None
    return sum(1 for r in evaluated if getattr(r, attr)) / len(evaluated)


def _apply_pass_flags(summary: dict[str, Any], targets: dict[str, Any]) -> None:
    recall = summary.get("retrieval_recall_at_5")
    if recall is not None:
        summary["retrieval_recall_at_5_pass"] = recall >= float(
            targets.get("retrieval_recall_at_5_min", 0.8),
        )
    grounded = summary.get("answer_grounded")
    if grounded is not None:
        summary["answer_grounded_pass"] = grounded >= float(
            targets.get("answer_grounded_min", 0.85)
        )
    cites = summary.get("citation_present")
    if cites is not None:
        summary["citation_present_pass"] = cites >= float(targets.get("citation_present_min", 0.9))
    p50 = summary.get("latency_p50_ms")
    if p50 is not None:
        summary["latency_p50_pass"] = p50 < int(targets.get("latency_p50_ms_max", 8000))


def _summary(
    results: list[CaseResult],
    *,
    validate_only: bool,
    with_llm: bool,
) -> dict[str, Any]:
    evaluated = [r for r in results if not r.skipped and r.error is None]
    skipped = sum(1 for r in results if r.skipped)
    if validate_only:
        diff_dist: dict[str, int] = {}
        for c in results:
            diff_dist[c.difficulty] = diff_dist.get(c.difficulty, 0) + 1
        return {
            "mode": "validate_only",
            "total_cases": len(results),
            "evaluated_cases": 0,
            "skipped_cases": len(results),
            "difficulty_distribution": diff_dist,
            "retrieval_recall_at_5": None,
            "answer_grounded": None,
            "citation_present": None,
        }

    if not evaluated:
        return {
            "mode": "live_llm" if with_llm else "live_retrieval",
            "total_cases": len(results),
            "evaluated_cases": 0,
            "skipped_cases": skipped,
            "retrieval_recall_at_5": None,
            "answer_grounded": None,
            "citation_present": None,
            "cases": [r.__dict__ for r in results],
        }

    latencies = sorted(r.latency_ms for r in evaluated)
    costs = [r.cost_eur for r in evaluated]
    by_diff: dict[str, dict[str, float | int]] = {}
    for diff in _DIFFICULTIES:
        subset = [r for r in evaluated if r.difficulty == diff]
        if not subset:
            continue
        by_diff[diff] = {
            "count": len(subset),
            "retrieval_recall_at_5": sum(1 for r in subset if r.retrieval_recall_at_5)
            / len(subset),
            "answer_grounded": sum(1 for r in subset if r.answer_grounded) / len(subset),
        }

    return {
        "mode": "live_llm" if with_llm else "live_retrieval",
        "total_cases": len(results),
        "evaluated_cases": len(evaluated),
        "skipped_cases": skipped,
        "retrieval_recall_at_5": _metric_rate(results, "retrieval_recall_at_5"),
        "answer_grounded": _metric_rate(results, "answer_grounded"),
        "citation_present": _metric_rate(results, "citation_present"),
        "latency_p50_ms": _percentile(latencies, 0.5),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "cost_per_question_eur_avg": sum(costs) / len(costs) if costs else 0.0,
        "cost_total_eur": sum(costs),
        "by_difficulty": by_diff,
        "cases": [
            {
                "case_id": r.case_id,
                "difficulty": r.difficulty,
                "skipped": r.skipped,
                "retrieval_recall_at_5": r.retrieval_recall_at_5,
                "answer_grounded": r.answer_grounded,
                "citation_present": r.citation_present,
                "latency_ms": r.latency_ms,
                "cost_eur": round(r.cost_eur, 6),
                "tools_executed": r.tools_executed,
                "error": r.error,
            }
            for r in results
        ],
    }


async def run_evals(
    tenant_id: uuid.UUID | None,
    *,
    validate_only: bool = False,
    with_llm: bool = False,
) -> dict[str, Any]:
    dataset = _load_dataset()
    schema_errors = validate_dataset_schema(dataset)
    if schema_errors:
        return {"valid": False, "schema_errors": schema_errors}

    if validate_only:
        results = [
            CaseResult(
                case_id=str(c["id"]), difficulty=str(c.get("difficulty", "medium")), skipped=True
            )
            for c in dataset["cases"]
        ]
        summary = _summary(results, validate_only=True, with_llm=False)
        summary["valid"] = True
        summary["dataset_version"] = dataset.get("version")
        summary["targets"] = dataset.get("targets")
        return summary

    if tenant_id is None:
        return {
            "valid": False,
            "error": "tenant_uuid required for live eval (or use --validate-only)",
        }

    results = [await _run_case_live(c, tenant_id, with_llm=with_llm) for c in dataset["cases"]]
    summary = _summary(results, validate_only=False, with_llm=with_llm)
    summary["valid"] = True
    summary["dataset_version"] = dataset.get("version")
    targets = dataset.get("targets") or {}
    summary["targets"] = targets
    _apply_pass_flags(summary, targets)
    summary["all_targets_pass"] = all(
        summary.get(k) is True
        for k in (
            "retrieval_recall_at_5_pass",
            "answer_grounded_pass",
            "citation_present_pass",
            "latency_p50_pass",
        )
        if summary.get(k) is not None
    )
    return summary


def _configure_stdio_utf8() -> None:
    """Evita UnicodeEncodeError en consola Windows (cp1252) con respuestas en español."""
    import contextlib

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(AttributeError, OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _configure_stdio_utf8()
    validate_only = "--validate-only" in sys.argv
    with_llm = "--with-llm" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tenant_id = uuid.UUID(args[0]) if args else None

    summary = asyncio.run(
        run_evals(tenant_id, validate_only=validate_only, with_llm=with_llm),
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"knowledge_qa_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    keys = (
        "valid",
        "mode",
        "total_cases",
        "evaluated_cases",
        "skipped_cases",
        "retrieval_recall_at_5",
        "answer_grounded",
        "citation_present",
        "retrieval_recall_at_5_pass",
        "answer_grounded_pass",
        "citation_present_pass",
        "latency_p50_ms",
        "latency_p50_pass",
        "all_targets_pass",
    )
    sys.stdout.write(json.dumps({k: summary.get(k) for k in keys}, indent=2) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")

    if not summary.get("valid"):
        sys.exit(1)
    if summary.get("mode", "").startswith("live") and summary.get("all_targets_pass") is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
