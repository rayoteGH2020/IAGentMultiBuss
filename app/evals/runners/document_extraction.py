"""Runner de evals de extracción de tickets, contratos y pólizas (módulo 1).

Complementa a ``extraction.py`` (facturas): mismo criterio de métricas y umbrales
(``app/evals/thresholds.py``), un dataset por tipo y ground truth tolerante a la
ambigüedad del schema (``app/evals/document_compare.py``).

Uso:
    infisical run -- uv run python -m app.evals.runners.document_extraction [ticket|contrato|seguro|all]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from app.core.db import session_factory_for_worker
from app.evals.document_compare import (
    FieldComparison,
    compare_contract,
    compare_insurance,
    compare_ticket,
)
from app.evals.eval_tenant import ensure_eval_tenant
from app.evals.runners.extraction import (
    RESULTS_DIR,
    CaseResult,
    FieldResult,
    _format_summary_for_stdout,
    _mime_for,
    _summary,
)
from app.evals.thresholds import metrics_pass
from app.llm.extraction import extract_contract, extract_insurance, extract_ticket

if TYPE_CHECKING:
    import uuid
    from collections.abc import Awaitable, Callable

    from pydantic import BaseModel

logger = structlog.get_logger(__name__)

DATASETS_DIR = Path(__file__).parent.parent / "datasets"
FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


@dataclass(frozen=True, slots=True)
class DocEvalSpec:
    """Cómo evaluar un tipo documental: dataset, extractor y comparador."""

    dataset: str
    extract: Callable[..., Awaitable[Any]]
    result_attr: str
    compare: Callable[[BaseModel, dict[str, Any]], list[FieldComparison]]


SPECS: dict[str, DocEvalSpec] = {
    "ticket": DocEvalSpec("tickets_v1.json", extract_ticket, "ticket", compare_ticket),
    "contrato": DocEvalSpec("contracts_v1.json", extract_contract, "contract", compare_contract),
    "seguro": DocEvalSpec("insurances_v1.json", extract_insurance, "insurance", compare_insurance),
}


def load_dataset(doc_type: str) -> dict[str, Any]:
    """Carga el dataset del tipo documental indicado."""
    path = DATASETS_DIR / SPECS[doc_type].dataset
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


async def run_case(case: dict[str, Any], spec: DocEvalSpec, tenant_id: uuid.UUID) -> CaseResult:
    """Extrae un documento y compara sus campos con el ground truth."""
    case_id = str(case["id"])
    path = FIXTURES_ROOT / case["file"]
    if not path.exists():
        return CaseResult(
            case_id=case_id,
            success=False,
            latency_ms=0,
            confidence=0.0,
            error=f"fixture not found: {case['file']}",
        )
    file_bytes = await asyncio.to_thread(path.read_bytes)
    # Sesión por caso, como en el runner de facturas: un fallo no contamina el resto
    # y cada llamada persiste su propio llm_calls.
    async with session_factory_for_worker(tenant_id) as db:
        t0 = time.perf_counter()
        try:
            result = await spec.extract(
                file_bytes=file_bytes,
                mime_type=_mime_for(path),
                tenant_id=tenant_id,
                db=db,
            )
            latency = int((time.perf_counter() - t0) * 1000)
            extracted: BaseModel = getattr(result, spec.result_attr)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            return CaseResult(
                case_id=case_id,
                success=False,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                confidence=0.0,
                error=f"{type(exc).__name__}: {str(exc)[:300]}",
            )
    fields = [
        FieldResult(field=name, expected=exp, actual=act, match=ok)
        for name, exp, act, ok in spec.compare(extracted, case["ground_truth"])
    ]
    return CaseResult(
        case_id=case_id,
        success=True,
        latency_ms=latency,
        confidence=float(getattr(extracted, "confidence", 0.0)),
        field_results=fields,
    )


async def run_doc_type(doc_type: str, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Ejecuta todos los casos de un tipo documental y devuelve el resumen."""
    spec = SPECS[doc_type]
    results: list[CaseResult] = []
    for case in load_dataset(doc_type)["cases"]:
        results.append(await run_case(case, spec, tenant_id))
        # Misma pausa que el runner de facturas: evita rate-limit en runs largos.
        await asyncio.sleep(3)
    summary = _summary(results)
    summary["doc_type"] = doc_type
    return summary


def _selected_types(argv: list[str]) -> list[str]:
    requested = argv[0] if argv else "all"
    if requested == "all":
        return list(SPECS)
    if requested not in SPECS:
        raise SystemExit(f"Tipo desconocido {requested!r}. Usa: {', '.join(SPECS)} o all.")
    return [requested]


async def _main_async(doc_types: list[str]) -> dict[str, dict[str, Any]]:
    tenant_id = await ensure_eval_tenant()
    return {doc_type: await run_doc_type(doc_type, tenant_id) for doc_type in doc_types}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summaries = asyncio.run(_main_async(_selected_types(sys.argv[1:])))

    all_failures: list[str] = []
    for doc_type, summary in summaries.items():
        out = RESULTS_DIR / f"{Path(SPECS[doc_type].dataset).stem}_{int(time.time())}.json"
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        sys.stdout.write(f"[{doc_type}]\n{_format_summary_for_stdout(summary)}\nDetalle: {out}\n")
        ok, failures = metrics_pass(summary)
        if not ok:
            all_failures.extend(f"{doc_type}: {reason}" for reason in failures)

    if os.getenv("EVAL_SKIP_GATING") or not all_failures:
        return
    sys.stderr.write("Eval por debajo de objetivos:\n")
    for reason in all_failures:
        sys.stderr.write(f"  - {reason}\n")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
