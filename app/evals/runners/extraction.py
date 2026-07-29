"""Runner del eval set de extracción de facturas (módulo 1).

Ejecuta el dataset `app/evals/datasets/invoices_v1.json` contra `extract_invoice`
y vuelca un resumen + detalle por caso en `app/evals/results/invoices_v1_<ts>.json`.

El runner no levanta la aplicación FastAPI; crea sesiones de BD directamente via
`session_factory_for_worker` (el mismo mecanismo que usan los workers ARQ) para
poder ejecutarse en CI o de forma puntual desde línea de comandos sin necesidad
de un servidor corriendo.

Uso:
    infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

import structlog

from app.core.db import session_factory_for_worker
from app.evals.eval_tenant import ensure_eval_tenant
from app.evals.field_compare import compare_factura, ground_truth_is_usable
from app.evals.thresholds import metrics_pass
from app.llm.extraction import extract_invoice

logger = structlog.get_logger(__name__)

# Rutas relativas al fichero para que funcionen independientemente del directorio
# desde el que se invoca el script (CI, local, worker).
DATASET = Path(__file__).parent.parent / "datasets" / "invoices_v1.json"
# Los fixtures de invoices se comparten con los tests de integración para no duplicar
# archivos de muestra en el repo.
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


def _compare(factura: Any, gt: dict[str, Any]) -> list[FieldResult]:
    return [
        FieldResult(field=name, expected=exp, actual=act, match=ok)
        for name, exp, act, ok in compare_factura(factura, gt)
    ]


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
    if not ground_truth_is_usable(gt):
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
        # Error de configuración del dataset: el fichero referenciado no existe
        # en fixtures. Se registra como fallo (no skipped) para que sea visible
        # en el resumen y no pase desapercibido.
        return CaseResult(
            case_id=case_id,
            success=False,
            latency_ms=0,
            confidence=0.0,
            error=f"fixture not found: {path.name}",
        )

    file_bytes = path.read_bytes()
    mime = _mime_for(path)

    # Se crea una sesión de BD por caso (no una global para todo el run) para
    # que un fallo o rollback en un caso no afecte a los demás y para que cada
    # llamada a `extract_invoice` registre su propio `llm_calls` de forma
    # atómica, igual que ocurre en producción desde el worker ARQ.
    async with session_factory_for_worker(tenant_id) as db:
        # La medición empieza antes de la llamada al LLM para capturar la
        # latencia total incluyendo la serialización del fichero, igual que
        # lo experimentaría el usuario en producción.
        t0 = time.perf_counter()
        try:
            extraction = await extract_invoice(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=tenant_id,
                db=db,
            )
            factura = extraction.factura
            latency = int((time.perf_counter() - t0) * 1000)
            field_results = _compare(factura, gt)
            # El commit persiste el registro `llm_calls` creado dentro de
            # `extract_invoice`; sin él las métricas de coste por tenant no
            # quedarían grabadas en la BD de evals.
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
                # Truncamos a 300 chars para que el JSON de resultados sea
                # legible; el error completo queda en los logs de structlog.
                error=str(exc).encode("utf-8", errors="replace").decode("utf-8")[:300],
            )


def _summary(results: list[CaseResult]) -> dict[str, Any]:
    # Los casos skipped se excluyen de todas las métricas para no distorsionar
    # los porcentajes; se reportan por separado para visibilidad.
    valid = [r for r in results if not r.skipped]
    # Solo los casos exitosos contribuyen a latencia y accuracy: los fallos
    # (error de red, timeout, schema inválido) tienen latencias atípicas que
    # contaminarían los percentiles.
    latencies = [r.latency_ms for r in valid if r.success]
    accuracies = [r.field_accuracy for r in valid if r.success]

    p95: int | None
    if len(latencies) >= 20:
        # El p95 estadístico solo es significativo con suficientes muestras;
        # con menos de 20 datos usar el máximo es más honesto que un percentil
        # calculado sobre pocos puntos que puede ser engañosamente bajo.
        p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    else:
        p95 = max(latencies, default=None)
    return {
        "total_cases": len(results),
        "evaluated_cases": len(valid),
        "skipped_cases": sum(1 for r in results if r.skipped),
        # json_validity_rate mide si el LLM generó JSON válido que pasa el
        # schema Instructor; es distinto de field_accuracy (JSON válido pero
        # con campos incorrectos). Objetivo: ≥99% según arquitectura.md §6.
        "json_validity_rate": (sum(1 for r in valid if r.success) / max(len(valid), 1)),
        # field_accuracy_avg promedia la precisión campo a campo sobre los casos
        # exitosos. Objetivo: ≥95% en campos críticos según arquitectura.md §6.
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
    # Los casos se ejecutan secuencialmente (no con asyncio.gather) para evitar
    # saturar la cuota de rate-limit de la API del LLM y para que las métricas
    # de latencia reflejen el tiempo real de una extracción individual, no el
    # efecto de peticiones concurrentes compitiendo por recursos.
    cases = dataset.get("cases", [])
    for case in cases:
        results.append(await _run_case(case, tenant_id))
        # Pausa breve entre casos para no disparar rate-limit de la API en runs largos.
        await asyncio.sleep(3)

    # Reintento único para fallos transitorios (p. ej. 429 rate-limit).
    case_by_id = {case["id"]: case for case in cases}
    for idx, result in enumerate(results):
        if result.skipped or result.success or not result.error:
            continue
        if "solicitudes" not in result.error.lower() and "rate" not in result.error.lower():
            continue
        retry_case = case_by_id.get(result.case_id)
        if retry_case is None:
            continue
        logger.info("evals.case.retry", case_id=result.case_id, reason="transient_error")
        await asyncio.sleep(15)
        results[idx] = await _run_case(retry_case, tenant_id)

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


async def _main_async(requested_tenant: uuid.UUID | None) -> dict[str, Any]:
    tenant_id = await ensure_eval_tenant(requested_tenant)
    return await run_evals(tenant_id)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    requested_tenant: uuid.UUID | None = None
    if len(sys.argv) > 1:
        try:
            requested_tenant = uuid.UUID(sys.argv[1])
        except ValueError as exc:
            msg = (
                f"tenant_uuid inválido: {sys.argv[1]!r}. "
                "Usa un UUID de la tabla tenants (sin <>). "
                "Ejemplo: infisical run -- uv run python -m app.evals.runners.extraction "
                "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            )
            raise SystemExit(msg) from exc
    try:
        summary = asyncio.run(_main_async(requested_tenant))
    except json.JSONDecodeError as exc:
        msg = f"Dataset JSON inválido ({DATASET}): {exc}"
        raise SystemExit(msg) from exc
    # El timestamp en el nombre de fichero evita sobreescribir runs anteriores y
    # permite comparar la evolución de métricas a lo largo del tiempo sin
    # necesidad de un sistema de versionado externo.
    out = RESULTS_DIR / f"invoices_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # Stdout imprime solo el resumen de alto nivel para que CI pueda parsearlo
    # fácilmente; el detalle campo a campo va al fichero JSON.
    sys.stdout.write(_format_summary_for_stdout(summary) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")

    if os.getenv("EVAL_SKIP_GATING"):
        return

    ok, failures = metrics_pass(summary)
    if not ok:
        sys.stderr.write("Eval por debajo de objetivos:\n")
        for reason in failures:
            sys.stderr.write(f"  - {reason}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
