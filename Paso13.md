# Paso 13 — UI de subida: dropzone HTMX, endpoint `/invoices/upload` y encolado

## Objetivo

Cerrar el lado web de la subida: modal con drag&drop, validación cliente, validación servidor, subida real a R2, creación de filas `Invoice` en estado `pending` y encolado del job de extracción. Los jobs no se procesarán todavía (eso es el siguiente paso); en esta fase solo verificamos que el flujo de entrada funciona y la tabla muestra las filas nuevas en estado `processing` con un spinner.

## Pre-requisitos

- Pasos 01-12 completados.
- Bucket R2 (o MinIO local) operativo.
- ARQ y Redis funcionando (aunque el worker aún esté inactivo).

## Contexto relevante

- `arquitectura.md` sección 6.1 (Módulo 1 — flujo de usuario, pasos 1-7).
- `Agents.md`: patrón página/fragmento, Alpine solo para estado puramente cliente, archivos siempre a R2, capa `routes/ → services/`.

## Tareas

- [ ] Añadir dependencia: `python-magic` para validar magic bytes.
- [ ] Configurar ARQ: `app/jobs/settings.py` con `WorkerSettings` (aunque el worker no se levante todavía).
- [ ] Crear `app/jobs/queue.py` con helper `enqueue_invoice_processing()`.
- [ ] Ampliar `app/services/invoice_service.py` con `create_invoice_from_upload()`.
- [ ] Crear endpoint `POST /invoices/upload` en `app/routes/web/invoices.py`.
- [ ] Habilitar el botón "Subir facturas" en `pages/invoices/index.html`.
- [ ] Crear `app/templates/components/upload_modal.html`.
- [ ] Crear `app/templates/components/invoice_row.html` con estados visuales.
- [ ] Endpoint `GET /invoices/rows` que devuelve solo las filas recientes (para refrescar la tabla tras subir).
- [ ] Test de integración: subir un PDF, recibir 200, verificar fila en BD con estado `pending`.
- [ ] Commit: `feat: invoice upload UI with htmx dropzone`.

## Detalles técnicos

### `app/jobs/settings.py`

```python
from arq.connections import RedisSettings

from app.config import settings


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300
    keep_result = 3600
    functions: list = []  # se rellena en Paso 14
```

### `app/jobs/queue.py`

```python
from __future__ import annotations

import uuid
from functools import lru_cache

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


@lru_cache(maxsize=1)
def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def enqueue_invoice_processing(invoice_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "process_invoice",
        str(invoice_id),
        str(tenant_id),
        _job_id=f"invoice:{invoice_id}",
    )
    assert job is not None
    return job.job_id
```

### Validación de fichero subido

`app/core/uploads.py`:

```python
from __future__ import annotations

import magic

MAX_FILE_SIZE = 20 * 1024 * 1024
ALLOWED_MIMES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class UploadValidationError(Exception):
    pass


def validate_invoice_upload(filename: str, data: bytes) -> str:
    if len(data) == 0:
        raise UploadValidationError("Empty file")
    if len(data) > MAX_FILE_SIZE:
        raise UploadValidationError(
            f"File too large: {len(data)} bytes (max {MAX_FILE_SIZE})"
        )
    detected = magic.from_buffer(data[:4096], mime=True)
    if detected not in ALLOWED_MIMES:
        raise UploadValidationError(f"Unsupported file type: {detected}")
    return detected
```

### Ampliación de `invoice_service.py`

```python
from app.core.keys import invoice_key
from app.core.storage import get_storage


async def create_invoice_from_upload(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
) -> Invoice:
    storage = get_storage()
    key = invoice_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)

    invoice = await create_invoice_stub(
        db,
        tenant_id,
        source_file_key=key,
        source_filename=filename[:300],
        source_mime=mime_type,
    )
    invoice.status = InvoiceStatus.processing
    return invoice
```

### Endpoint `POST /invoices/upload`

En `app/routes/web/invoices.py`:

```python
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.core.errors import ValidationError
from app.core.templating import render
from app.core.uploads import UploadValidationError, validate_invoice_upload
from app.deps import current_tenant, get_db
from app.jobs.queue import enqueue_invoice_processing
from app.models import Tenant
from app.services import invoice_service

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/upload")
async def upload_invoices(
    request: Request,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
):
    if not files:
        raise ValidationError("No files provided")
    if len(files) > 20:
        raise ValidationError("Max 20 files per upload")

    created = []
    errors = []
    for f in files:
        try:
            data = await f.read()
            mime = validate_invoice_upload(f.filename or "file", data)
            invoice = await invoice_service.create_invoice_from_upload(
                db,
                tenant_id=tenant.id,
                filename=f.filename or "file",
                file_bytes=data,
                mime_type=mime,
            )
            await db.flush()
            await enqueue_invoice_processing(invoice.id, tenant.id)
            created.append(invoice)
        except UploadValidationError as exc:
            errors.append({"filename": f.filename, "error": str(exc)})
            logger.warning(
                "upload.rejected", filename=f.filename, error=str(exc)
            )

    await db.commit()

    invoices = await invoice_service.list_invoices(db, tenant.id, limit=50)
    return render(
        request,
        full="pages/invoices/index.html",
        partial="components/invoices_table.html",
        ctx={
            "invoices": invoices,
            "tenant": tenant,
            "upload_errors": errors,
            "just_uploaded_ids": {str(i.id) for i in created},
        },
    )
```

Asegúrate de que el `render()` o el template usa `just_uploaded_ids` para resaltar las filas recién creadas (clase `bg-blue-50` por ejemplo).

### `app/templates/components/upload_modal.html`

```html
<div
  x-data="{ open: false, files: [], dragging: false }"
  @keydown.escape.window="open = false"
  class="inline-block">

  <button @click="open = true"
          class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm">
    Subir facturas
  </button>

  <div x-show="open" x-cloak
       class="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center"
       @click.self="open = false">
    <div class="bg-white rounded-lg shadow-xl w-full max-w-lg p-6">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold">Subir facturas</h2>
        <button @click="open = false"
                class="text-slate-400 hover:text-slate-600">✕</button>
      </div>

      <form
        hx-post="/invoices/upload"
        hx-encoding="multipart/form-data"
        hx-target="#invoices-table-container"
        hx-swap="outerHTML"
        hx-indicator="#upload-spinner"
        @htmx:after-request="open = false; files = []">

        <label
          class="block border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition"
          :class="dragging ? 'border-blue-500 bg-blue-50' : 'border-slate-300'"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="
            dragging = false;
            files = Array.from($event.dataTransfer.files);
            $refs.input.files = $event.dataTransfer.files;
          ">

          <input
            type="file"
            name="files"
            x-ref="input"
            class="hidden"
            multiple
            accept=".pdf,.jpg,.jpeg,.png,.webp"
            @change="files = Array.from($event.target.files)">

          <p class="text-sm text-slate-600">
            Arrastra aquí PDFs o fotos de facturas, o haz click para elegir.
          </p>
          <p class="text-xs text-slate-400 mt-1">
            Máximo 20 ficheros, 20 MB cada uno.
          </p>
        </label>

        <ul x-show="files.length" class="mt-4 space-y-1 max-h-40 overflow-y-auto">
          <template x-for="f in files" :key="f.name">
            <li class="flex items-center justify-between text-sm text-slate-700">
              <span x-text="f.name" class="truncate"></span>
              <span class="text-xs text-slate-400"
                    x-text="(f.size / 1024).toFixed(0) + ' KB'"></span>
            </li>
          </template>
        </ul>

        <div class="mt-6 flex items-center justify-end gap-2">
          <span id="upload-spinner" class="htmx-indicator text-sm text-slate-500">
            Subiendo…
          </span>
          <button type="button" @click="open = false"
                  class="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900">
            Cancelar
          </button>
          <button type="submit"
                  :disabled="files.length === 0"
                  class="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 text-white text-sm rounded-md">
            Subir <span x-show="files.length" x-text="'(' + files.length + ')'"></span>
          </button>
        </div>
      </form>
    </div>
  </div>
</div>
```

### `pages/invoices/index.html` (actualización)

Sustituye el botón placeholder por `{% include "components/upload_modal.html" %}`. Envuelve la tabla en un contenedor con id estable para que HTMX la reemplace:

```html
<div id="invoices-table-container">
  {% include "components/invoices_table.html" %}
</div>
```

### Estado visual `processing` con polling

En `components/invoice_row.html` (extrae la fila de la tabla a un componente), cuando `inv.status == "processing"` o `pending`, añade atributos HTMX que la propia fila se refresque:

```html
{% if inv.status.value in ("pending", "processing") %}
  <tr
    id="invoice-{{ inv.id }}"
    hx-get="/jobs/invoice/{{ inv.id }}/status"
    hx-trigger="load delay:1500ms, every 2s"
    hx-swap="outerHTML">
    <td colspan="5" class="px-4 py-3 text-sm text-slate-500">
      <span class="inline-flex items-center gap-2">
        <svg class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="currentColor"
                  stroke-width="3" class="opacity-25"></circle>
          <path d="M4 12a8 8 0 018-8" stroke="currentColor"
                stroke-width="3" class="opacity-75"></path>
        </svg>
        Procesando <span class="text-slate-400">— {{ inv.source_filename }}</span>
      </span>
    </td>
  </tr>
{% else %}
  {# fila normal con datos extraídos #}
  ...
{% endif %}
```

El endpoint `/jobs/invoice/{id}/status` lo implementa Paso 14, pero la fila ya queda lista.

### Test de integración

`tests/integration/test_invoice_upload.py`:

```python
from io import BytesIO

import pytest
from sqlalchemy import select

from app.models import Invoice, InvoiceStatus


@pytest.mark.asyncio
async def test_upload_invoice_creates_row(authed_client, db_session, fake_pdf_bytes):
    files = {"files": ("ejemplo.pdf", BytesIO(fake_pdf_bytes), "application/pdf")}
    r = await authed_client.post("/invoices/upload", files=files)
    assert r.status_code == 200

    rows = (await db_session.execute(select(Invoice))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == InvoiceStatus.processing
    assert rows[0].source_file_key.startswith("invoices/")
```

`fake_pdf_bytes` es una fixture que devuelve los bytes mínimos de un PDF válido (`%PDF-1.4\n%%EOF`).

## Criterios de aceptación

- Al hacer click en "Subir facturas" se abre el modal.
- Drag&drop de PDFs muestra la lista de ficheros.
- Submit dispara `POST /invoices/upload` con `multipart/form-data`.
- Tras el submit, la tabla se reemplaza, aparecen filas con spinner.
- En R2 (o MinIO) están los objetos bajo `invoices/{tenant_id}/...`.
- En BD hay filas con `status='processing'` y `source_file_key` no nulo.
- En Redis hay jobs encolados (`redis-cli LRANGE arq:queue 0 -1`).
- Si subes un fichero no permitido (txt, zip), el upload reporta el error sin crear `Invoice`.

## Lo que NO toca este paso

- Ejecutar realmente la extracción (Paso 14).
- Endpoint `/jobs/invoice/{id}/status` (Paso 14).
- Edición inline de campos extraídos (lo abordamos después del Paso 15).

## Posibles problemas

- **`python-magic` requiere libmagic**: en Debian/Ubuntu, `apt install libmagic1`. En el Dockerfile, añadirlo a la lista de paquetes.
- **`UploadFile.read()` carga todo en RAM**: para 20MB × 20 ficheros = 400MB pico. Aceptable en MVP; si pasa a ser problema, stream a R2 en chunks.
- **Tamaño máximo de request**: configura `client_max_body_size` en el reverse proxy y `--limit-request-size` en uvicorn si haces despliegue. En dev no hace falta.
- **CORS si subes desde dominio distinto**: en MVP todo va al mismo dominio; no es problema.

## Siguiente paso

`Paso14.md` — Worker ARQ `process_invoice`: descarga de R2, llama a `extract_invoice`, actualiza la fila `Invoice` con los campos extraídos y líneas. Endpoint `/jobs/invoice/{id}/status` para que el polling HTMX cierre el ciclo.
