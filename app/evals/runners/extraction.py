"""Runner del eval set de extracción de facturas (módulo 1).

Ejecuta el dataset `app/evals/datasets/invoices_v1.json` contra `extract_invoice`
y vuelca un resumen + detalle por caso en `app/evals/results/invoices_v1_<ts>.json`.

Uso:
    infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid>
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import TYPE_CHECKING, Any

import structlog

from app.core.db import session_factory_for_worker
from app.llm.extraction import extract_invoice

if TYPE_CHECKING:
    from app.schemas.invoice import Factura

logger = structlog.get_logger(__name__)

DATASET = Path(__file__).parent.parent / "datasets" / "invoices_v1.json"
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "invoices"
RESULTS_DIR = Path(__file__).parent.parent / "results"


@dataclass
class FieldResult:
    field: str
    expected: str
    actual: str
    match: bool


@dataclass
class CaseResult:
    case_id: str
    success: bool
    latency_ms: int
    confidence: float
    field_results: list[FieldResult] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False

    @property
    def field_accuracy(self) -> float:
        if not self.field_results:
            return 0.0
        return sum(1 for f in self.field_results if f.match) / len(self.field_results)


def _decimal_eq(a: str, b: str, tolerance: Decimal = Decimal("0.01")) -> bool:
    try:
        return abs(Decimal(a) - Decimal(b)) <= tolerance
    except (InvalidOperation, ValueError):
        return False


def _compare(factura: Factura, gt: dict[str, str]) -> list[FieldResult]:
    out: list[FieldResult] = [
        FieldResult(
            "fecha",
            gt["fecha"],
            str(factura.fecha),
            gt["fecha"] == str(factura.fecha),
        ),
        FieldResult(
            "cif_nif",
            gt["cif_nif"],
            factura.cif_nif,
            gt["cif_nif"].upper() == factura.cif_nif.upper(),
        ),
        FieldResult(
            "proveedor",
            gt["proveedor"],
            factura.proveedor,
            gt["proveedor"].lower() in factura.proveedor.lower()
            or factura.proveedor.lower() in gt["proveedor"].lower(),
        ),
        FieldResult(
            "total",
            gt["total"],
            str(factura.total),
            _decimal_eq(gt["total"], str(factura.total)),
        ),
        FieldResult(
            "base_imponible",
            gt["base_imponible"],
            str(factura.base_imponible),
            _decimal_eq(gt["base_imponible"], str(factura.base_imponible)),
        ),
        FieldResult(
            "iva_amount",
            gt["iva_amount"],
            str(factura.iva_amount),
            _decimal_eq(gt["iva_amount"], str(factura.iva_amount)),
        ),
    ]
    return out


def _mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    msg = f"Mime no soportado para fixture: {path.name}"
    raise ValueError(msg)


async def _run_case(case: dict[str, Any], tenant_id: uuid.UUID) -> CaseResult:
    case_id = case["id"]
    gt = case.get("ground_truth")
    if not gt:
        logger.info("evals.case.skip", case_id=case_id, reason="missing ground_truth")
        return CaseResult(
            case_id=case_id,
            success=False,
            latency_ms=0,
            confidence=0.0,
            skipped=True,
        )

    path = FIXTURES / case["file"]
    if not path.exists():
        return CaseResult(
            case_id=case_id,
            success=False,
            latency_ms=0,
            confidence=0.0,
            error=f"fixture not found: {path.name}",
        )

    file_bytes = path.read_bytes()
    mime = _mime_for(path)

    async with session_factory_for_worker(tenant_id) as db:
        t0 = time.perf_counter()
        try:
            factura = await extract_invoice(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=tenant_id,
                db=db,
            )
            latency = int((time.perf_counter() - t0) * 1000)
            field_results = _compare(factura, gt)
            await db.commit()
            return CaseResult(
                case_id=case_id,
                success=True,
                latency_ms=latency,
                confidence=float(factura.confidence),
                field_results=field_results,
            )
        except Exception as exc:
            await db.rollback()
            return CaseResult(
                case_id=case_id,
                success=False,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                confidence=0.0,
                error=str(exc)[:300],
            )


def _summary(results: list[CaseResult]) -> dict[str, Any]:
    valid = [r for r in results if not r.skipped]
    latencies = [r.latency_ms for r in valid if r.success]
    accuracies = [r.field_accuracy for r in valid if r.success]
    p95: int | None
    if len(latencies) >= 20:
        p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    else:
        p95 = max(latencies, default=None)
    return {
        "total_cases": len(results),
        "evaluated_cases": len(valid),
        "skipped_cases": sum(1 for r in results if r.skipped),
        "json_validity_rate": (sum(1 for r in valid if r.success) / max(len(valid), 1)),
        "field_accuracy_avg": (sum(accuracies) / max(len(accuracies), 1)) if accuracies else 0.0,
        "latency_p50_ms": median(latencies) if latencies else None,
        "latency_p95_ms": p95,
        "cases": [
            {
                "id": r.case_id,
                "success": r.success,
                "skipped": r.skipped,
                "latency_ms": r.latency_ms,
                "confidence": r.confidence,
                "field_accuracy": r.field_accuracy,
                "error": r.error,
                "fields": [
                    {
                        "field": f.field,
                        "match": f.match,
                        "expected": f.expected,
                        "actual": f.actual,
                    }
                    for f in r.field_results
                ],
            }
            for r in results
        ],
    }


async def run_evals(tenant_id: uuid.UUID) -> dict[str, Any]:
    """Ejecuta todos los casos del dataset y devuelve el resumen."""
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    results: list[CaseResult] = []
    for case in dataset.get("cases", []):
        results.append(await _run_case(case, tenant_id))
    return _summary(results)


def _format_summary_for_stdout(summary: dict[str, Any]) -> str:
    keys = (
        "total_cases",
        "evaluated_cases",
        "skipped_cases",
        "json_validity_rate",
        "field_accuracy_avg",
        "latency_p50_ms",
        "latency_p95_ms",
    )
    return json.dumps({k: summary.get(k) for k in keys}, indent=2)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tenant_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else uuid.uuid4()
    summary = asyncio.run(run_evals(tenant_id))
    out = RESULTS_DIR / f"invoices_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    sys.stdout.write(_format_summary_for_stdout(summary) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")


if __name__ == "__main__":
    main()
