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


def _decimal_eq(a: str, b: str, tolerance: Decimal = Decimal("0.01")) -> bool:
    # Se usa Decimal en lugar de float para evitar errores de representación
    # binaria (p. ej. 21.0 vs 20.999999...) habituales en importes monetarios.
    # Tolerancia de 0.01 EUR: cubre diferencias de redondeo en el LLM sin permitir
    # discrepancias reales en el importe.
    try:
        return abs(Decimal(a) - Decimal(b)) <= tolerance
    except (InvalidOperation, ValueError):
        # Si alguno de los valores no es numérico (el LLM devolvió texto o None
        # serializado) se considera fallo en lugar de propagar la excepción.
        return False


def _compare(factura: Factura, gt: dict[str, str]) -> list[FieldResult]:
    # Compara únicamente los campos críticos definidos en arquitectura.md §6:
    # fecha, CIF/NIF y total son los que tienen objetivo ≥95% de accuracy.
    # base_imponible e iva_amount se incluyen para detectar errores de desglose
    # aunque el total sea correcto.
    out: list[FieldResult] = [
        FieldResult(
            "fecha",
            gt["fecha"],
            str(factura.fecha),
            # Comparación exacta: el LLM debe devolver ISO-8601 (YYYY-MM-DD)
            # gracias al schema Pydantic `date`; cualquier otro formato ya falló
            # en la validación de Instructor antes de llegar aquí.
            gt["fecha"] == str(factura.fecha),
        ),
        FieldResult(
            "cif_nif",
            gt["cif_nif"],
            factura.cif_nif,
            # Case-insensitive: las facturas físicas mezclan mayúsculas/minúsculas
            # en el CIF aunque el schema lo normalice; se tolera para no penalizar
            # variaciones tipográficas irrelevantes para el negocio.
            gt["cif_nif"].upper() == factura.cif_nif.upper(),
        ),
        FieldResult(
            "proveedor",
            gt["proveedor"],
            factura.proveedor,
            # Coincidencia por substring en cualquier dirección: el LLM puede
            # devolver la razón social completa cuando el ground truth tiene el
            # nombre comercial, o viceversa (p. ej. "Telefónica" vs "Telefónica
            # de España S.A.U."). Evita falsos negativos por variantes legales.
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

    # Casos sin ground_truth se marcan como skipped (no como failure) para que
    # no penalicen las métricas; permiten añadir facturas al dataset antes de
    # tener el ground truth anotado.
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
                error=str(exc)[:300],
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
    # El tenant_id se acepta como argumento para poder correr evals contra un
    # tenant real con sus propios límites de RLS, o generar uno aleatorio para
    # entornos de CI donde no existe ningún tenant concreto.
    tenant_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else uuid.uuid4()
    summary = asyncio.run(run_evals(tenant_id))
    # El timestamp en el nombre de fichero evita sobreescribir runs anteriores y
    # permite comparar la evolución de métricas a lo largo del tiempo sin
    # necesidad de un sistema de versionado externo.
    out = RESULTS_DIR / f"invoices_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # Stdout imprime solo el resumen de alto nivel para que CI pueda parsearlo
    # fácilmente; el detalle campo a campo va al fichero JSON.
    sys.stdout.write(_format_summary_for_stdout(summary) + "\n")
    sys.stdout.write(f"Detalle: {out}\n")


if __name__ == "__main__":
    main()
