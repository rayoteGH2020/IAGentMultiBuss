# Paso 14 — Worker ARQ `process_invoice` y endpoint de polling

## Objetivo

Cerrar el ciclo asíncrono: el worker ARQ procesa cada factura encolada, descarga el fichero de R2, llama a `extract_invoice()`, guarda los datos en la fila `Invoice` y sus `InvoiceLine`. En paralelo, el endpoint `/jobs/invoice/{id}/status` devuelve el fragmento HTML de la fila actualizada, que el polling HTMX (configurado en Paso 13) consumirá hasta que la factura esté `ready` o `failed`.

Al final del paso, subir un PDF en la UI culmina con la fila mostrando proveedor, CIF, total y estado verde.

## Pre-requisitos

- Pasos 01-13 completados.
- Redis corriendo (Docker Compose, Paso 02).
- R2 / MinIO operativo.

## Contexto relevante

- `arquitectura.md` sección 6.1 (Módulo 1, pasos 5-9).
- `Agents.md`: capas `services/` orquestan lógica, `jobs/` invocan servicios, RLS siempre, todas las llamadas LLM en `llm_calls` + Langfuse.

## Tareas

- [ ] Crear `app/jobs/invoice_jobs.py` con función `process_invoice`.
- [ ] Registrar la función en `app/jobs/settings.py` (`functions = [process_invoice]`).
- [ ] Implementar setup/teardown del contexto del worker: pool de sesión Postgres con `app.current_tenant`.
- [ ] Ampliar `app/services/invoice_service.py` con `apply_extraction_result()`.
- [ ] Crear endpoint `GET /jobs/invoice/{invoice_id}/status` que devuelve `components/invoice_row.html`.
- [ ] Asegurar que el worker procesa con concurrencia limitada por tenant (semáforo en Redis).
- [ ] Test de integración: encolar job manualmente, ejecutar worker en línea, verificar fila `ready`.
- [ ] Documentar cómo levantar el worker en `README.md`.
- [ ] Commit: `feat: arq worker for invoice extraction with polling endpoint`.

## Detalles técnicos

### `app/jobs/invoice_jobs.py`

```python
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import structlog

from app.core.db import session_factory_for_worker
from app.core.storage import get_storage
from app.llm.extraction import extract_invoice
from app.services import invoice_service

logger = structlog.get_logger(__name__)


async def process_invoice(ctx: dict[str, Any], invoice_id: str, tenant_id: str) -> dict:
    """Job ARQ: descarga la factura, la extrae y guarda los resultados."""
    inv_uuid = uuid.UUID(invoice_id)
    t_uuid = uuid.UUID(tenant_id)
    logger.info("worker.invoice.start", invoice_id=invoice_id, tenant_id=tenant_id)

    async with session_factory_for_worker(t_uuid) as db:
        invoice = await invoice_service.get_invoice(db, t_uuid, inv_uuid)

        try:
            storage = get_storage()
            file_bytes = await storage.download_bytes(invoice.source_file_key)
            mime = invoice.source_mime or "application/pdf"

            factura = await extract_invoice(
                file_bytes=file_bytes,
                mime_type=mime,
                tenant_id=t_uuid,
                db=db,
            )

            await invoice_service.apply_extraction_result(
                db, invoice=invoice, factura=factura
            )
            await db.commit()
            logger.info(
                "worker.invoice.done",
                invoice_id=invoice_id,
                proveedor=factura.proveedor,
                total=str(factura.total),
            )
            return {"status": "ok", "invoice_id": invoice_id}

        except Exception as exc:
            await db.rollback()
            async with session_factory_for_worker(t_uuid) as db2:
                await invoice_service.mark_failed(
                    db2, invoice_id=inv_uuid, tenant_id=t_uuid, error=str(exc)[:500]
                )
                await db2.commit()
            logger.exception("worker.invoice.failed", invoice_id=invoice_id)
            raise
```

### `app/core/db.py` (añadir helper para worker)

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def session_factory_for_worker(tenant_id: uuid.UUID):
    """Sesión con app.current_tenant ya seteado, sin pasar por middleware HTTP."""
    async with async_session_maker() as session:
        await session.execute(
            text("SET LOCAL app.current_tenant = :tid"),
            {"tid": str(tenant_id)},
        )
        yield session
```

### `app/services/invoice_service.py` (añadir)

```python
from datetime import datetime, timezone

from app.models import InvoiceLine, InvoiceStatus
from app.schemas.invoice import Factura


async def apply_extraction_result(
    db: AsyncSession, *, invoice: Invoice, factura: Factura
) -> Invoice:
    invoice.fecha = factura.fecha
    invoice.proveedor = factura.proveedor[:300]
    invoice.cif_nif = factura.cif_nif
    invoice.base_imponible = factura.base_imponible
    invoice.iva_percent = factura.iva_percent
    invoice.iva_amount = factura.iva_amount
    invoice.total = factura.total
    invoice.currency = factura.currency
    invoice.confidence = factura.confidence
    invoice.raw_extraction = factura.model_dump(mode="json")
    invoice.status = InvoiceStatus.ready
    invoice.updated_at = datetime.now(timezone.utc)
    invoice.error_message = None

    # Reemplazar líneas: las orphans las borra cascade
    invoice.lines = []
    await db.flush()
    for idx, linea in enumerate(factura.lineas):
        invoice.lines.append(
            InvoiceLine(
                tenant_id=invoice.tenant_id,
                descripcion=linea.descripcion[:1000],
                cantidad=linea.cantidad,
                precio_unitario=linea.precio_unitario,
                total=linea.total,
                position=idx,
            )
        )
    await db.flush()
    return invoice


async def mark_failed(
    db: AsyncSession, *, invoice_id: uuid.UUID, tenant_id: uuid.UUID, error: str
) -> None:
    invoice = await get_invoice(db, tenant_id, invoice_id)
    invoice.status = InvoiceStatus.failed
    invoice.error_message = error
    invoice.updated_at = datetime.now(timezone.utc)
```

### `app/jobs/settings.py`

```python
from arq.connections import RedisSettings

from app.config import settings
from app.jobs.invoice_jobs import process_invoice


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [process_invoice]
    max_jobs = 5
    job_timeout = 180
    keep_result = 3600
    max_tries = 2
    on_startup = None
    on_shutdown = None
```

### Endpoint de polling

`app/routes/web/jobs.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import current_tenant, get_db
from app.models import Tenant
from app.services import invoice_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/invoice/{invoice_id}/status")
async def invoice_status(
    request: Request,
    invoice_id: UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
):
    invoice = await invoice_service.get_invoice(db, tenant.id, invoice_id)
    # Siempre devuelve el fragmento de fila; HTMX deja de hacer polling
    # cuando la fila ya no tiene los atributos hx-trigger (estado final).
    return render(
        request,
        full="components/invoice_row.html",
        partial="components/invoice_row.html",
        ctx={"inv": invoice},
    )
```

Registra el router en `main.py`.

### `components/invoice_row.html` actualizado

```html
{% if inv.status.value in ("pending", "processing") %}
  <tr
    id="invoice-{{ inv.id }}"
    hx-get="/jobs/invoice/{{ inv.id }}/status"
    hx-trigger="every 2s"
    hx-swap="outerHTML"
    hx-target="this">
    <td colspan="5" class="px-4 py-3 text-sm text-slate-500">
      <span class="inline-flex items-center gap-2">
        <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor"
                  stroke-width="3" class="opacity-25"></circle>
          <path d="M4 12a8 8 0 018-8" stroke="currentColor"
                stroke-width="3" class="opacity-75"></path>
        </svg>
        Procesando — <span class="text-slate-400">{{ inv.source_filename }}</span>
      </span>
    </td>
  </tr>
{% elif inv.status.value == "failed" %}
  <tr id="invoice-{{ inv.id }}" class="bg-red-50">
    <td class="px-4 py-3 text-sm text-red-700" colspan="5">
      Error: {{ inv.error_message or 'desconocido' }}
      — <a href="#" class="underline">reintentar</a>
    </td>
  </tr>
{% else %}
  <tr id="invoice-{{ inv.id }}" class="hover:bg-slate-50">
    <td class="px-4 py-3">{{ inv.fecha or '—' }}</td>
    <td class="px-4 py-3">{{ inv.proveedor or '—' }}</td>
    <td class="px-4 py-3 font-mono text-xs">{{ inv.cif_nif or '—' }}</td>
    <td class="px-4 py-3 text-right">
      {% if inv.total %}{{ "%.2f"|format(inv.total) }} €{% else %}—{% endif %}
    </td>
    <td class="px-4 py-3">
      <span class="inline-block px-2 py-0.5 text-xs rounded-full bg-green-100 text-green-700">
        listo
      </span>
    </td>
  </tr>
{% endif %}
```

Y en `components/invoices_table.html`, dentro del `{% for %}`, llama a `{% include "components/invoice_row.html" %}`.

### Test de integración

`tests/integration/test_invoice_worker.py`:

```python
import pytest

from app.jobs.invoice_jobs import process_invoice
from app.models import InvoiceStatus


@pytest.mark.asyncio
@pytest.mark.integration
async def test_process_invoice_end_to_end(
    monkeypatch, db_session, tenant_factory, upload_pdf_fixture
):
    """Invoca el job directamente (sin ARQ) usando un mock de extracción."""
    tenant = await tenant_factory()
    invoice = await upload_pdf_fixture(tenant)  # crea Invoice + sube a R2

    async def fake_extract(**kwargs):
        from decimal import Decimal
        from app.schemas.invoice import Factura
        return Factura(
            fecha="2025-01-15",
            proveedor="Test S.L.",
            cif_nif="00000000T",  # NIF/CIF ficticio de ejemplo (tests)
            base_imponible=Decimal("100.00"),
            iva_percent=Decimal("21.00"),
            iva_amount=Decimal("21.00"),
            total=Decimal("121.00"),
            confidence=0.95,
        )

    monkeypatch.setattr("app.jobs.invoice_jobs.extract_invoice", fake_extract)

    await process_invoice({}, str(invoice.id), str(tenant.id))

    await db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.ready
    assert invoice.proveedor == "Test S.L."
    assert invoice.total == 121
```

### Levantar el worker

Añade a `README.md`:

```bash
# Terminal 1: app
uv run uvicorn app.main:app --reload

# Terminal 2: worker
uv run arq app.jobs.settings.WorkerSettings

# Terminal 3 (opcional): monitoreo cola
redis-cli LLEN arq:queue
redis-cli KEYS "arq:job:*"
```

## Criterios de aceptación

- Subir una factura desde la UI → 5-15s después la fila muestra los datos extraídos en verde.
- `psql` muestra la `Invoice` con `status='ready'`, `proveedor`, `cif_nif`, `total`, `raw_extraction` y filas en `invoice_lines`.
- Langfuse muestra una traza `llm.extraction` correlacionada con la subida.
- Si forzamos un error (por ejemplo, borrar el objeto de R2 manualmente antes de que se procese), la fila acaba en `failed` con `error_message` poblado.
- Test de integración pasa.
- `mypy` y `ruff` pasan.

## Comandos útiles

```bash
# Ver jobs en cola
redis-cli LRANGE arq:queue 0 -1

# Ver resultados recientes
redis-cli KEYS "arq:result:*"

# Drenar la cola (cuidado, dev only)
redis-cli FLUSHDB

# Logs del worker con structlog en formato JSON
uv run arq app.jobs.settings.WorkerSettings | jq .
```

## Lo que NO toca este paso

- Procesado paralelo con semáforo por tenant (refinamiento del Paso 15+).
- Edición inline de los campos extraídos.
- Exportar CSV.
- Eval set con métricas (parte de Paso 15).
- Reintento manual desde la UI ("reintentar" del template aún no funciona).

## Posibles problemas

- **El worker no ve `app.current_tenant`**: cada nueva conexión Postgres parte sin sesión-level vars. Usa `SET LOCAL` dentro de la transacción, no `SET`. El helper `session_factory_for_worker` ya lo hace.
- **Polling sigue infinitamente si el status devuelve `processing`**: HTMX deja de hacer polling cuando el fragmento devuelto ya no contiene `hx-trigger="every 2s"`. La rama `else` del template no debe tener atributos `hx-*`.
- **`SET LOCAL` requiere transacción**: con `AsyncSession` SQLAlchemy abre la transacción al primer execute. Si `SET LOCAL` se ejecuta sin transacción explícita, Postgres lo silenciosamente ignora. Verifica con `SHOW app.current_tenant;` desde el worker.
- **Worker no recoge cambios al recargar código**: ARQ no tiene hot-reload. Reinícialo a mano tras cada cambio.

## Siguiente paso

`Paso15.md` — Verificación end-to-end con facturas reales, eval set inicial de 10-20 facturas con ground truth, métricas (latencia p50/p95, coste medio, field accuracy), smoke test E2E con Playwright y checklist de cierre del MVP del módulo 1.
