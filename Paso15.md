# Paso 15 — Pipeline end-to-end, evals iniciales, métricas y cierre del MVP del módulo 1

## Objetivo

Validar el módulo 1 completo: subir N facturas reales, comprobar que se extraen correctamente, medir latencia/coste/accuracy, dejar un eval set ejecutable en CI, y montar un smoke test E2E con Playwright. Cerrar el MVP del módulo 1 con un checklist explícito de lo que está hecho y lo que queda pendiente para el siguiente bloque (Paso 16+: pulido, batch, edición inline, exportar CSV).

## Pre-requisitos

- Pasos 01-14 completados.
- Carpeta `tests/fixtures/invoices/` con 10-20 facturas reales (mezcla de proveedores, formatos, calidad).
- Acceso al despliegue local funcional (app + worker + Postgres + Redis + R2/MinIO + Langfuse).

## Contexto relevante

- `arquitectura.md` sección 6.1 (métricas objetivo: field accuracy ≥95%, JSON validity ≥99%, latencia p50<8s, coste p50<0,005€).
- `Agents.md`: evals viven en `app/evals/`, dataset JSON, runners pytest, métricas reportadas.

## Tareas

- [x] Crear `app/evals/datasets/invoices_v1.json` con 10-20 casos (fichero + ground truth). _(stub con 3 ficheros existentes; ground_truth a rellenar)_
- [x] Crear `app/evals/runners/extraction.py` que itera el dataset y mide.
- [x] Crear `app/evals/conftest.py` con fixtures comunes.
- [ ] Ejecutar el eval contra Gemini Flash, registrar métricas en `app/evals/results/`. _(humano: necesita claves LLM en Infisical y ground_truth real)_
- [x] Crear `tests/e2e/test_invoice_upload_flow.py` con Playwright.
- [x] Crear endpoint `GET /metrics/module1` (interno, sin auth de tenant pero con un token) que devuelve agregados de `llm_calls` e `invoices`.
- [ ] Job nightly opcional para snapshot de métricas. _(opcional; no incluido)_
- [x] Workflow GitHub Actions `.github/workflows/evals.yml`.
- [ ] Cierre: ejecutar checklist (sección Final). _(humano)_
- [x] Commit: `feat: invoice extraction evals, e2e test and module1 metrics`.
- [ ] Tag: `git tag mvp-module1` y push. _(humano, al cerrar checklist)_

## Detalles técnicos

### Estructura del dataset

`app/evals/datasets/invoices_v1.json`:

```json
{
  "version": "v1",
  "model_target": "gemini-2.5-flash",
  "cases": [
    {
      "id": "inv_001",
      "file": "ejemplo_01.pdf",
      "ground_truth": {
        "fecha": "2024-11-15",
        "proveedor": "Suministros Industriales SL",
        "cif_nif": "00000000T",
        "base_imponible": "1250.00",
        "iva_percent": "21.00",
        "iva_amount": "262.50",
        "total": "1512.50"
      }
    }
  ]
}
```

Los ficheros referenciados están en `tests/fixtures/invoices/`. Pon ground truth real, copiando a mano de la propia factura.

### `app/evals/runners/extraction.py`

```python
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import median

from app.core.db import session_factory_for_worker
from app.llm.extraction import extract_invoice
from app.schemas.invoice import Factura

DATASET = Path(__file__).parent.parent / "datasets" / "invoices_v1.json"
FIXTURES = Path(__file__).parent.parent.parent.parent / "tests" / "fixtures" / "invoices"
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

    @property
    def field_accuracy(self) -> float:
        if not self.field_results:
            return 0.0
        return sum(1 for f in self.field_results if f.match) / len(self.field_results)


def _decimal_eq(a: str, b: str, tolerance: Decimal = Decimal("0.01")) -> bool:
    try:
        return abs(Decimal(a) - Decimal(b)) <= tolerance
    except Exception:
        return False


def _compare(factura: Factura, gt: dict) -> list[FieldResult]:
    out: list[FieldResult] = []
    out.append(FieldResult("fecha", gt["fecha"], str(factura.fecha),
                           gt["fecha"] == str(factura.fecha)))
    out.append(FieldResult("cif_nif", gt["cif_nif"], factura.cif_nif,
                           gt["cif_nif"].upper() == factura.cif_nif.upper()))
    out.append(FieldResult("proveedor", gt["proveedor"], factura.proveedor,
                           gt["proveedor"].lower() in factura.proveedor.lower()
                           or factura.proveedor.lower() in gt["proveedor"].lower()))
    out.append(FieldResult("total", gt["total"], str(factura.total),
                           _decimal_eq(gt["total"], str(factura.total))))
    out.append(FieldResult("base_imponible", gt["base_imponible"],
                           str(factura.base_imponible),
                           _decimal_eq(gt["base_imponible"], str(factura.base_imponible))))
    out.append(FieldResult("iva_amount", gt["iva_amount"], str(factura.iva_amount),
                           _decimal_eq(gt["iva_amount"], str(factura.iva_amount))))
    return out


async def run_evals(tenant_id) -> dict:
    dataset = json.loads(DATASET.read_text())
    results: list[CaseResult] = []

    for case in dataset["cases"]:
        path = FIXTURES / case["file"]
        file_bytes = path.read_bytes()
        mime = "application/pdf" if path.suffix == ".pdf" else f"image/{path.suffix[1:]}"

        async with session_factory_for_worker(tenant_id) as db:
            t0 = time.perf_counter()
            try:
                factura = await extract_invoice(
                    file_bytes=file_bytes, mime_type=mime,
                    tenant_id=tenant_id, db=db,
                )
                latency = int((time.perf_counter() - t0) * 1000)
                field_results = _compare(factura, case["ground_truth"])
                results.append(CaseResult(
                    case_id=case["id"], success=True,
                    latency_ms=latency, confidence=factura.confidence,
                    field_results=field_results,
                ))
                await db.commit()
            except Exception as exc:
                results.append(CaseResult(
                    case_id=case["id"], success=False,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    confidence=0.0, error=str(exc)[:300],
                ))
                await db.rollback()

    latencies = [r.latency_ms for r in results if r.success]
    accuracies = [r.field_accuracy for r in results if r.success]
    summary = {
        "total_cases": len(results),
        "json_validity_rate": sum(1 for r in results if r.success) / max(len(results), 1),
        "field_accuracy_avg": sum(accuracies) / max(len(accuracies), 1),
        "latency_p50_ms": median(latencies) if latencies else None,
        "latency_p95_ms": sorted(latencies)[int(len(latencies) * 0.95) - 1]
                          if len(latencies) >= 20 else max(latencies, default=None),
        "cases": [
            {
                "id": r.case_id, "success": r.success,
                "latency_ms": r.latency_ms, "confidence": r.confidence,
                "field_accuracy": r.field_accuracy, "error": r.error,
                "fields": [
                    {"field": f.field, "match": f.match,
                     "expected": f.expected, "actual": f.actual}
                    for f in r.field_results
                ],
            }
            for r in results
        ],
    }
    return summary


if __name__ == "__main__":
    import sys
    import uuid

    RESULTS_DIR.mkdir(exist_ok=True)
    tenant = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else uuid.uuid4()
    summary = asyncio.run(run_evals(tenant))
    out = RESULTS_DIR / f"invoices_v1_{int(time.time())}.json"
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2))
    print(f"\nDetalle: {out}")
```

Ejecuta con: `uv run python -m app.evals.runners.extraction <tenant_uuid>`.

### Test E2E con Playwright

`tests/e2e/test_invoice_upload_flow.py`:

```python
import os
from pathlib import Path
import pytest
from playwright.async_api import async_playwright, expect

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "invoices" / "ejemplo_01.pdf"


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.skipif(not os.getenv("RUN_E2E"), reason="set RUN_E2E=1")
async def test_upload_invoice_and_see_result():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(storage_state="tests/e2e/auth_state.json")
        page = await ctx.new_page()

        await page.goto("http://localhost:8000/invoices")
        await expect(page.locator("h1")).to_contain_text("Facturas")

        await page.click("text=Subir facturas")
        async with page.expect_file_chooser() as fc:
            await page.click("label.border-dashed")
        await (await fc.value).set_files(str(FIXTURE_PDF))

        await page.click("button[type=submit]")

        # Esperar a que la fila pase de "Procesando" a "listo"
        row = page.locator("tr").filter(has_text="ejemplo_01.pdf").first
        await expect(row).to_contain_text("listo", timeout=30_000)

        await browser.close()
```

Pre-genera `tests/e2e/auth_state.json` autenticando manualmente una vez con Clerk en modo dev (Playwright `storage_state`).

### Endpoint de métricas

`app/routes/api/metrics.py`:

```python
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.models import Invoice, InvoiceStatus, LLMCall

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/module1")
async def module1_metrics(
    x_metrics_token: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    if x_metrics_token != settings.metrics_token:
        raise HTTPException(403)

    since = datetime.now(timezone.utc) - timedelta(days=7)
    inv_stats = (await db.execute(
        select(Invoice.status, func.count()).where(Invoice.created_at >= since)
        .group_by(Invoice.status)
    )).all()

    llm_stats = (await db.execute(
        select(
            func.count(),
            func.avg(LLMCall.latency_ms),
            func.percentile_disc(0.5).within_group(LLMCall.latency_ms),
            func.percentile_disc(0.95).within_group(LLMCall.latency_ms),
            func.sum(LLMCall.cost_eur),
        ).where(LLMCall.task == "extraction", LLMCall.created_at >= since)
    )).one()

    return {
        "invoices_by_status": {str(s.value): c for s, c in inv_stats},
        "extraction_calls": llm_stats[0],
        "latency_avg_ms": float(llm_stats[1] or 0),
        "latency_p50_ms": llm_stats[2],
        "latency_p95_ms": llm_stats[3],
        "total_cost_eur": float(llm_stats[4] or 0),
    }
```

Añade `metrics_token: str` a `Settings`.

### Workflow GitHub Actions para evals

`.github/workflows/evals.yml`:

```yaml
name: Evals
on:
  pull_request:
    paths:
      - 'app/llm/**'
      - 'app/schemas/invoice.py'
      - 'app/services/invoice_service.py'
      - 'app/evals/**'
  workflow_dispatch:

jobs:
  evals:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_PASSWORD: postgres
        ports: ["5432:5432"]
      redis:
        image: redis:7
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync --frozen
      - run: uv run alembic upgrade head
        env:
          DATABASE_URL: postgresql+asyncpg://postgres@localhost:5432/postgres
      - name: Run extraction evals
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
          LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
          LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
          R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
          R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
          R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
        run: uv run python -m app.evals.runners.extraction
      - uses: actions/upload-artifact@v4
        with:
          name: eval-results
          path: app/evals/results/
```

Más adelante puedes añadir gating: comparar `field_accuracy_avg` con la rama `main` y fallar si baja >5%.

## Verificación manual

1. `docker compose up -d` (Postgres, Redis, Langfuse, MinIO).
2. `uv run uvicorn app.main:app --reload`.
3. `uv run arq app.jobs.settings.WorkerSettings`.
4. Abrir `http://localhost:8000/invoices` autenticado.
5. Subir 5 facturas distintas a la vez.
6. Observar la tabla: filas pasan de "Procesando" a "listo" en 5-15s.
7. Abrir Langfuse en `http://localhost:3000` y ver las 5 trazas con tokens y coste.
8. `psql` y comprobar `SELECT status, COUNT(*) FROM invoices GROUP BY status;`.
9. `curl -H "X-Metrics-Token: $METRICS_TOKEN" http://localhost:8000/metrics/module1`.

## Métricas objetivo para considerar el MVP cerrado

| Métrica | Objetivo | Cómo medir |
|---|---|---|
| JSON validity rate | ≥ 99% | Eval runner |
| Field accuracy (total, CIF, fecha) | ≥ 95% | Eval runner |
| Latencia p50 | < 8s | `/metrics/module1` |
| Latencia p95 | < 20s | `/metrics/module1` |
| Coste medio por factura | < 0,005 € | `/metrics/module1` |
| Tasa de fallos del worker | < 1% | `SELECT * FROM invoices WHERE status='failed'` |

Si una métrica no llega, primero intenta mejorar el prompt (`extraction_v2.txt`) antes de cambiar de modelo. Si tras dos iteraciones de prompt sigue por debajo, prueba `gemini-2.5-pro` o `claude-sonnet-4-6` para los casos difíciles (puede ser un fallback condicional cuando `confidence < 0.5`).

## Checklist de cierre MVP módulo 1

- [ ] Subir factura desde la UI funciona end-to-end.
- [ ] Aparecen filas con extracción correcta para >90% de un set de 10 facturas reales.
- [ ] Langfuse muestra trazas con tokens y coste.
- [ ] `llm_calls` populado con cada llamada.
- [ ] Métricas accesibles vía `/metrics/module1`.
- [ ] Test E2E pasa con `RUN_E2E=1`.
- [ ] Workflow de evals corre en CI.
- [ ] README documenta cómo levantar app + worker.
- [ ] No hay archivos guardados en disco del servidor (todo en R2).
- [ ] RLS activo en `invoices`, `invoice_lines`, `llm_calls`.
- [ ] `mypy --strict` y `ruff check` pasan.
- [ ] Tag `mvp-module1` creado.

## Lo que NO toca este paso (queda para Paso 16+)

- Procesado por lotes con semáforo paralelo por tenant.
- Edición inline de campos (HTMX cell-by-cell).
- Exportar CSV.
- Búsqueda y filtros en la lista de facturas.
- Paginación / infinite scroll.
- Reintento manual desde la UI.
- Auditoría completa en `audit_log`.
- Despliegue en Hetzner con Coolify.
- Onboarding de primer cliente piloto real.
- Módulo 2 (RAG) y módulo 3 (Analytics).

## Posibles problemas

- **Field accuracy baja en `proveedor`**: el matching por substring laxo del comparador puede sobreestimar; conviene aceptar también razones sociales abreviadas. Si pasa lo contrario (acierta de menos), añade normalización (`S.L.` ↔ `SL`).
- **Latencia alta esporádica**: APIs LLM tienen colas variables. Mide con N≥20 facturas para que p50/p95 sean significativos.
- **Coste mayor del esperado**: si tu prompt actual mete demasiados ejemplos few-shot, recórtalo. Usa `gemini-2.5-flash` (no `pro`) para extracción salvo casos difíciles.
- **Playwright auth state expira**: Clerk emite sesiones largas en dev pero no infinitas. Si el E2E empieza a fallar con redirect a `/sign-in`, regenera `auth_state.json`.

## Siguiente bloque del proyecto

Cerrado el MVP del módulo 1, los siguientes pasos lógicos son:

- **Paso 16-19**: pulido módulo 1 (batch paralelo, edición inline, CSV, búsqueda, audit, despliegue Hetzner, primer piloto).
- **Paso 20-28**: módulo 2 — RAG conversacional (modelos `Document`/`Chunk`, ingesta, embeddings, hybrid search, chat SSE, integración WhatsApp).
- **Paso 29-36**: módulo 3 — Analytics conversacional (data sources, introspección de schema, SQL agent con tool use, validación, charts).
- **Paso 37+**: billing con Stripe, panel de admin interna, página pública de marketing.

Cuando quieras seguir, abrimos `Paso16.md` con la primera tanda de pulido del módulo 1.
