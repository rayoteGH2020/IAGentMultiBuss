"""Runner stub de evals del chat documental (módulo 1.5).

Carga ``chat_documents_v1.json`` y calcula métricas:
- ``tool_selection_accuracy``: fracción de casos donde las tools ejecutadas
  coinciden con ``expected_tools`` (orden ignorado).
- ``answer_grounded_in_data``: fracción donde la respuesta final contiene al
  menos una ``grounded_keywords`` o hay datos en tool results.

Modo stub (por defecto en CI): casos con ``skip_live_llm: true`` se omiten.
Modo live: ``infisical run -- uv run python -m app.evals.runners.chat_documents <tenant_uuid>``
ejecuta el loop real (requiere API keys).

Uso validación sin LLM:
    uv run python -m app.evals.runners.chat_documents --validate-only
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

DATASET = Path(__file__).parent.parent / "datasets" / "chat_documents_v1.json"
RESULTS_DIR = Path(__file__).parent.parent / "results"


@dataclass
class CaseResult:
    case_id: str
    skipped: bool = False
    success: bool = False
    tool_selection_match: bool = False
    answer_grounded: bool = False
    tools_executed: list[str] = field(default_factory=list)
    final_text: str = ""
    error: str | None = None


def score_tool_selection(
    expected: list[str],
    actual: list[str],
) -> bool:
    """True si el conjunto de tools coincide (sin duplicar orden)."""
    return set(expected) == set(actual)


def score_answer_grounded(
    final_text: str,
    grounded_keywords: list[str],
    *,
    tool_results_ok: bool,
) -> bool:
    """Heurística MVP: keywords en respuesta o tools devolvieron ok."""
    if not grounded_keywords:
        return tool_results_ok
    lower = final_text.lower()
    if any(kw.lower() in lower for kw in grounded_keywords):
        return True
    return tool_results_ok and bool(final_text.strip())


def _load_dataset() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(DATASET.read_text(encoding="utf-8")))


def validate_dataset_schema(dataset: dict[str, Any]) -> list[str]:
    """Devuelve lista de errores de esquema (vacía = OK)."""
    errors: list[str] = []
    if "cases" not in dataset:
        errors.append("missing 'cases' array")
        return errors
    for i, case in enumerate(dataset["cases"]):
        if "id" not in case:
            errors.append(f"case[{i}] missing id")
        if "user_message" not in case:
            errors.append(f"case {case.get('id', i)} missing user_message")
        if "expected_tools" not in case:
            errors.append(f"case {case.get('id', i)} missing expected_tools")
    return errors


async def _run_case_live(
    case: dict[str, Any],
    tenant_id: uuid.UUID,
) -> CaseResult:
    """Ejecuta un caso contra el loop real (solo si no está marcado skip)."""
    from app.core.db import session_factory_for_worker, set_tenant_context
    from app.llm.client import get_llm_client
    from app.llm.prompts_loader import load_prompt
    from app.llm.tools.document_chat import PROMPT_VERSION, build_document_chat_registry
    from app.llm.tools.registry import ToolContext
    from app.models import ChatMessage, ChatMessageRole, ChatThread, User

    case_id = str(case["id"])
    if case.get("skip_live_llm"):
        return CaseResult(case_id=case_id, skipped=True)

    async with session_factory_for_worker(tenant_id) as db:
        await set_tenant_context(db, str(tenant_id))
        user = User(email=f"eval-{case_id}@eval.local", name="Eval")
        db.add(user)
        await db.flush()
        thread = ChatThread(tenant_id=tenant_id, user_id=user.id)
        db.add(thread)
        user_msg = ChatMessage(
            tenant_id=tenant_id,
            thread_id=thread.id,
            role=ChatMessageRole.user,
            content=str(case["user_message"]),
        )
        db.add(user_msg)
        await db.flush()

        registry = build_document_chat_registry()
        ctx = ToolContext(db=db, tenant_id=tenant_id, user_id=user.id, thread_id=thread.id)
        system_prompt = load_prompt(PROMPT_VERSION)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": case["user_message"]},
        ]
        try:
            loop_result = await get_llm_client().run_tool_loop(
                messages=messages,
                registry=registry,
                ctx=ctx,
                tenant_id=tenant_id,
                db=db,
                prompt_version=PROMPT_VERSION,
            )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception("chat_eval.case_failed", case_id=case_id)
            return CaseResult(
                case_id=case_id,
                success=False,
                error=str(exc)[:300],
            )

        expected = list(case.get("expected_tools", []))
        tool_match = score_tool_selection(expected, loop_result.tool_calls_executed)
        grounded = score_answer_grounded(
            loop_result.final_text,
            list(case.get("grounded_keywords", [])),
            tool_results_ok=len(loop_result.tool_calls_executed) > 0,
        )
        return CaseResult(
            case_id=case_id,
            success=tool_match and grounded,
            tool_selection_match=tool_match,
            answer_grounded=grounded,
            tools_executed=list(loop_result.tool_calls_executed),
            final_text=loop_result.final_text[:500],
        )


def _summary(results: list[CaseResult], *, validate_only: bool) -> dict[str, Any]:
    evaluated = [r for r in results if not r.skipped]
    skipped = sum(1 for r in results if r.skipped)
    if validate_only or not evaluated:
        return {
            "mode": "validate_only" if validate_only else "stub",
            "total_cases": len(results),
            "evaluated_cases": 0,
            "skipped_cases": skipped if not validate_only else len(results),
            "tool_selection_accuracy": None,
            "answer_grounded_in_data": None,
            "cases": [r.__dict__ for r in results],
        }
    tool_acc = sum(1 for r in evaluated if r.tool_selection_match) / len(evaluated)
    grounded_acc = sum(1 for r in evaluated if r.answer_grounded) / len(evaluated)
    return {
        "mode": "live",
        "total_cases": len(results),
        "evaluated_cases": len(evaluated),
        "skipped_cases": skipped,
        "tool_selection_accuracy": tool_acc,
        "answer_grounded_in_data": grounded_acc,
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
        stub_results = [
            CaseResult(case_id=str(c["id"]), skipped=True) for c in dataset.get("cases", [])
        ]
        summary = _summary(stub_results, validate_only=True)
        summary["valid"] = True
        summary["dataset_version"] = dataset.get("version")
        return summary

    tid = tenant_id or uuid.uuid4()
    live_results: list[CaseResult] = []
    for case in dataset.get("cases", []):
        live_results.append(await _run_case_live(case, tid))
    summary = _summary(live_results, validate_only=False)
    summary["valid"] = True
    summary["dataset_version"] = dataset.get("version")
    return summary


def main() -> None:
    validate_only = "--validate-only" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tenant_id = uuid.UUID(args[0]) if args else None

    summary = asyncio.run(
        run_evals(tenant_id, validate_only=validate_only),
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"chat_documents_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    keys = (
        "valid",
        "mode",
        "total_cases",
        "evaluated_cases",
        "skipped_cases",
        "tool_selection_accuracy",
        "answer_grounded_in_data",
    )
    sys.stdout.write(json.dumps({k: summary.get(k) for k in keys}, indent=2) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")


if __name__ == "__main__":
    main()
