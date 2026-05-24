# MigracionLyC.md — Integración de LLM Local (Ollama) en IAgentMultiBuss

> **Propósito**: añadir soporte para ejecución de extracción con LLM local (Ollama + Qwen 2.5-VL) manteniendo el 100% de la funcionalidad cloud existente. La app operará en tres modos configurables desde Infisical: `cloud_only` (comportamiento actual, defecto), `local_only` (Ollama) y `hybrid` (Ollama primero, cloud como fallback si falla o la confianza no alcanza el umbral).
>
> **Regla de oro**: ningún paso modifica el comportamiento del modo `cloud_only`. La app actual debe funcionar exactamente igual con `PIPELINE_MODE=cloud_only`.
>
> **Correcciones respecto al borrador inicial**:
> - Nombre correcto del dataclass: `ExtractionResult` (no `ExtractionOutput`).
> - Nombre correcto de función de pricing: `compute_cost_eur` (no `calculate_cost_eur`); devuelve `Decimal`.
> - `LLMCall` es append-only: `pipeline_mode` se pasa en el momento de creación, nunca se muta después del flush.
> - `client.complete()` necesita un nuevo parámetro `pipeline_mode` (opcional, backward-compatible) — ver Paso 7.
> - Target de mock en tests: `app.llm.extraction.get_settings` (no `app.config.get_settings`).
> - Langfuse: las llamadas Ollama no generan trazas en Langfuse (se documenta explícitamente).

---

## Visión de la arquitectura final

```
extract_invoice(*, file_bytes, mime_type, tenant_id, db)
        │
        ├─ PIPELINE_MODE = cloud_only  ──► _extract_cloud(pipeline_mode="cloud_only") ──────────────────►
        │                                                                                                  │
        ├─ PIPELINE_MODE = local_only  ──► _extract_local(pipeline_mode="local_only") ──────────────────►│
        │                                                                                                  │
        └─ PIPELINE_MODE = hybrid                                                                          │
               │                                                                                           │
               ├──► _extract_local(pipeline_mode="hybrid_local")                                           │
               │         │                                                                                 │
               │    ┌────┴──────────────────────────────┐                                                  │
               │    │ confidence ≥ threshold             │ error / confidence < threshold                  │
               │    │                                    ▼                                                  │
               │    │                    _extract_cloud(pipeline_mode="hybrid_cloud_fallback") ────────────►│
               │    │                                                                                       │
               │    └──────────────────────────────────────────────────────────────────────────────────────►│
               │                                                                                             │
               └─────────────────────────────────────────────────────────────────────────────────────────► ExtractionResult
                                                                                              (factura + llm_call_id)
```

### Conversión PDF → PNG (solo ruta Ollama)
```
file_bytes (PDF)  ──►  pypdfium2  ──►  PNG bytes  ──►  Ollama vision model
file_bytes (img)  ──────────────────────────────────►  Ollama vision model (directo)
```
El path cloud (Gemini/Anthropic) **no cambia**: sigue recibiendo bytes brutos como hasta ahora.

### Observabilidad por modo
| Modo | `llm_calls` | Langfuse |
|---|---|---|
| `cloud_only` | ✅ con `pipeline_mode='cloud_only'` | ✅ traza completa |
| `local_only` | ✅ con `pipeline_mode='local_only'`, `cost_eur=0` | ❌ sin traza (Ollama bypasa `LLMClient`) |
| `hybrid` local exitoso | ✅ con `pipeline_mode='hybrid_local'` | ❌ sin traza local |
| `hybrid` fallback cloud | ✅ con `pipeline_mode='hybrid_cloud_fallback'` | ✅ traza cloud |

---

## Prerequisitos antes de empezar los pasos

### PR-1: Verificar que la app actual funciona al 100%

Confirmar que la app existente sigue funcionando antes de tocar ningún fichero.

**Pasos manuales:**

- [x] 1. Abre dos terminales PowerShell en la raíz del proyecto.
- [x] 2. En la primera terminal, arranca el worker ARQ:
  ```powershell
  infisical run -- uv run arq app.jobs.settings.WorkerSettings
  ```
- [x] 3. En la segunda terminal, arranca la app web:
  ```powershell
  infisical run -- uv run uvicorn app.main:app --reload
  ```
- [x] 4. Abre el navegador en `http://localhost:8000` e inicia sesión con tu cuenta de Clerk.
- [x] 5. Ve a la sección de facturas y sube un PDF de prueba.
- [x] 6. Espera a que el estado de la factura cambie a `ready` (el polling HTMX lo actualiza automáticamente).
- [x] 7. Comprueba que los campos extraídos (proveedor, CIF, total, fecha) tienen valores correctos.
- [x] 8. Para ambos procesos con `Ctrl+C` en cada terminal.

---

### PR-2: Instalar Ollama en Windows

- [x] 1. Abre PowerShell y ejecuta:
  ```powershell
  ollama --version
  ```
- [x] 2. **Si el comando falla** (Ollama no instalado): abre el navegador y ve a `https://ollama.com/download/windows`.
- [x] 3. Descarga el instalador `.exe` de Ollama para Windows.
- [x] 4. Ejecuta el instalador (doble clic) — acepta los permisos UAC cuando el sistema lo pida. La instalación es estándar, sin opciones que elegir.
- [x] 5. Una vez instalado, Ollama arranca automáticamente como servicio y aparece un icono en la bandeja del sistema (esquina inferior derecha, junto al reloj).
- [x] 6. Abre una **nueva** terminal PowerShell y ejecuta de nuevo:
  ```powershell
  ollama --version
  ```
  Debe mostrar un número de versión (p. ej. `ollama version 0.6.x`). Si sigue fallando, reinicia Windows y repite.

---

### PR-3: Descargar el modelo de visión

- [x] 1. Abre PowerShell y ejecuta:
  ```powershell
  ollama pull qwen2.5vl:7b
  ```
  La descarga es de ~5.5 GB. Puede tardar entre 5 y 30 minutos según la conexión. Verás una barra de progreso en la terminal; espera a que llegue al 100%.
- [x] 2. Verifica que el modelo quedó instalado:
  ```powershell
  ollama list
  ```
  Debe aparecer `qwen2.5vl:7b` en la lista. Si no aparece, repite el paso 1.

---

### PR-4: Optimizar Ollama para CPU (Intel i5-12450H, 16 GB RAM)

Estos ajustes hacen que Ollama use todos los núcleos físicos disponibles y mantenga el modelo cargado en RAM entre llamadas, evitando la penalización de ~20-30 s de carga en cada extracción.

- [x] 1. Abre PowerShell **como Administrador** (clic derecho sobre el icono de PowerShell → "Ejecutar como administrador"). Las variables de entorno del sistema requieren permisos elevados.
- [x] 2. Ejecuta:
  ```powershell
  setx OLLAMA_NUM_THREADS 8
  ```
  Debe responder: `SUCCESS: Specified value was saved.`
- [x] 3. Ejecuta:
  ```powershell
  setx OLLAMA_KEEP_ALIVE 30m
  ```
  Debe responder: `SUCCESS: Specified value was saved.`
- [x] 4. Cierra esa terminal de Administrador.
- [x] 5. Localiza el icono de Ollama en la bandeja del sistema (esquina inferior derecha, junto al reloj). Si no lo ves, haz clic en la flecha `^` para mostrar los iconos ocultos.
- [x] 6. Haz clic derecho sobre el icono de Ollama → selecciona **"Quit Ollama"** (o "Salir").
- [x] 7. Vuelve a abrir Ollama: pulsa `Win`, escribe `Ollama` y ejecútalo. El icono reaparecerá en la bandeja en unos segundos.
- [x] 8. Verifica que el servicio responde abriendo una nueva terminal PowerShell y ejecutando:
  ```powershell
  ollama list
  ```
  Debe listar `qwen2.5vl:7b` sin errores.

> **Nota**: `setx` guarda las variables para sesiones futuras. No afectan a la sesión actual de PowerShell, de ahí el reinicio del servicio.

---

### PR-5: Verificar servicio Ollama

Confirmación final antes de empezar a modificar código.

- [x] 1. Abre PowerShell y ejecuta:
  ```powershell
  curl http://localhost:11434/api/tags
  ```
- [x] 2. La respuesta debe ser un JSON con este aspecto (abreviado):
  ```json
  {"models":[{"name":"qwen2.5vl:7b","model":"qwen2.5vl:7b",...}]}
  ```
  Si ves `{"models":[]}` sin el modelo, vuelve a PR-3. Si ves un error de conexión, vuelve a PR-4 paso 7.
- [x] 3. Haz una inferencia de prueba rápida para confirmar que el modelo responde (tarda ~30-90 s en CPU):
  ```powershell
  ollama run qwen2.5vl:7b "Di solo la palabra HOLA"
  ```
  Debe responder `HOLA` o similar. Esto confirma que el modelo puede hacer inferencia en tu máquina.

---

## Paso 1 — Añadir dependencias Python y actualizar mypy overrides

### 1.1 Añadir dependencias en `pyproject.toml`

Sección `[project.dependencies]`, junto al bloque LLM:

```toml
"ollama>=0.4.0",
"pypdfium2>=4.30.0",
"Pillow>=10.0.0",
```

> `Pillow` es necesario porque `pypdfium2` usa `.to_pil()` para convertir el bitmap renderizado a imagen PNG. Sin Pillow, `pdf_first_page_to_png` falla en runtime con `ImportError`.

Instalar:

```powershell
uv add ollama pypdfium2 Pillow
```

### 1.2 Añadir mypy overrides en `pyproject.toml`

`ollama` y `pypdfium2` no tienen stubs de tipos. Sin este cambio, `mypy --strict` falla con `Cannot find implementation or library stub for module named "ollama"`. Añadir al final del bloque de overrides existente:

```toml
[[tool.mypy.overrides]]
module = ["ollama.*", "pypdfium2.*"]
ignore_missing_imports = true
```

**Verificación**:
```powershell
uv run python -c "import ollama, pypdfium2, PIL; print('OK')"
uv run mypy app   # debe pasar sin errores nuevos
```

---

## Paso 2 — Añadir secretos en Infisical

```powershell
infisical secrets set PIPELINE_MODE=cloud_only
infisical secrets set OLLAMA_HOST=http://localhost:11434
infisical secrets set OLLAMA_VISION_MODEL=qwen2.5vl:7b
infisical secrets set OLLAMA_TIMEOUT_SECONDS=180
infisical secrets set LLM_LOCAL_CONFIDENCE_THRESHOLD=0.75
infisical secrets set PDF_RENDER_DPI=200
```

> `PIPELINE_MODE=cloud_only` es el valor por defecto: la app se comporta exactamente igual que antes hasta que se cambie.

**Verificación**:
```powershell
infisical secrets | findstr PIPELINE_MODE   # debe mostrar cloud_only
```

---

## Paso 3 — Actualizar `app/config.py`

Añadir el enum `PipelineMode` y seis campos nuevos a `Settings`.

**Enum** (añadir antes de `class Settings`):

```python
from enum import Enum

class PipelineMode(str, Enum):
    CLOUD_ONLY = "cloud_only"   # comportamiento actual; defecto
    LOCAL_ONLY = "local_only"   # Ollama exclusivamente
    HYBRID     = "hybrid"       # Ollama primero, cloud como fallback
```

**Campos** (añadir dentro de `class Settings`, después de los LLM overrides existentes):

```python
# ── Modo de pipeline LLM ─────────────────────────────────────────────────────
pipeline_mode: PipelineMode = PipelineMode.CLOUD_ONLY

# ── Ollama (LLM local) ───────────────────────────────────────────────────────
ollama_host: str = "http://localhost:11434"
ollama_vision_model: str = "qwen2.5vl:7b"
ollama_timeout_seconds: int = 180
# Umbral de confianza mínimo en modo hybrid: si la extracción local devuelve
# un confidence < este valor, se activa el fallback al LLM cloud.
llm_local_confidence_threshold: float = 0.75
# DPI para la conversión PDF→PNG que necesita Ollama (Gemini/Anthropic no usan esto).
pdf_render_dpi: int = 200
```

Las variables de entorno correspondientes son `PIPELINE_MODE`, `OLLAMA_HOST`, etc. (pydantic-settings las resuelve en mayúsculas automáticamente).

**Verificación**:
```powershell
infisical run -- uv run python -c "
from app.config import get_settings, PipelineMode
s = get_settings()
assert s.pipeline_mode == PipelineMode.CLOUD_ONLY
print('pipeline_mode =', s.pipeline_mode)
print('ollama_host   =', s.ollama_host)
print('OK')
"
```

---

## Paso 4 — Crear `app/core/pdf_utils.py`

Fichero nuevo. Solo se usa en la ruta Ollama; el path cloud no lo importa.

```python
"""PDF-to-PNG conversion for local vision models (Ollama).

Cloud providers (Gemini, Anthropic) handle PDF bytes natively;
this module is only used in local_only and hybrid pipeline modes.
"""

import asyncio
from io import BytesIO

import structlog

log = structlog.get_logger(__name__)

_MAX_SIDE_PX = 2200  # Qwen 2.5-VL recommended max


def _convert_pdf_page_sync(pdf_bytes: bytes, dpi: int, page_index: int) -> bytes:
    import pypdfium2  # noqa: PLC0415 — deferred import: avoids load cost when module unused

    doc = pypdfium2.PdfDocument(pdf_bytes)
    try:
        if page_index >= len(doc):
            raise ValueError(
                f"PDF has {len(doc)} page(s); requested page {page_index}"
            )
        page = doc[page_index]
        bitmap = page.render(scale=dpi / 72.0)
        pil_image = bitmap.to_pil()

        w, h = pil_image.size
        if max(w, h) > _MAX_SIDE_PX:
            ratio = _MAX_SIDE_PX / max(w, h)
            pil_image = pil_image.resize(
                (int(w * ratio), int(h * ratio)),
                resample=pil_image.Resampling.LANCZOS,
            )

        buf = BytesIO()
        pil_image.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


async def pdf_first_page_to_png(pdf_bytes: bytes, dpi: int = 200) -> bytes:
    """Return the first page of a PDF as PNG bytes (async-safe via thread pool)."""
    loop = asyncio.get_running_loop()   # get_event_loop() deprecated in Python 3.12+
    png_bytes = await loop.run_in_executor(
        None, _convert_pdf_page_sync, pdf_bytes, dpi, 0
    )
    log.debug("pdf_converted_to_png", pdf_size=len(pdf_bytes), png_size=len(png_bytes))
    return png_bytes
```

**Verificación**:
```powershell
uv run python -c "
import asyncio, pathlib
from app.core.pdf_utils import pdf_first_page_to_png
pdf = pathlib.Path('tests/fixtures/sample_invoice.pdf').read_bytes()
png = asyncio.run(pdf_first_page_to_png(pdf, dpi=200))
assert png[:4] == b'\x89PNG', 'no es PNG'
print(f'PNG size: {len(png):,} bytes — OK')
"
```

---

## Paso 5 — Actualizar `app/llm/pricing.py`

Añadir entradas para modelos Ollama con coste cero. La función `compute_cost_eur` ya devuelve `Decimal("0")` para modelos desconocidos (comportamiento de fail-soft documentado en el código), pero añadir las entradas explícitas hace visible en el dashboard que son llamadas locales.

Añadir al diccionario `PRICING`:

```python
# Modelos Ollama — locales, coste de API = 0 EUR
"qwen2.5vl:7b":  {"input": Decimal("0"), "output": Decimal("0")},
"qwen2.5vl:3b":  {"input": Decimal("0"), "output": Decimal("0")},
"qwen2.5vl:72b": {"input": Decimal("0"), "output": Decimal("0")},
```

> `compute_cost_eur` devuelve `Decimal`, no `float`. No modificar la firma ni el tipo de retorno.

---

## Paso 6 — Añadir columna `pipeline_mode` a `llm_calls`

### 6.1 Actualizar `app/models/llm_call.py`

Añadir el campo dentro de la clase `LLMCall`, después de `langfuse_trace_id`:

```python
# pipeline_mode registra qué rama del pipeline generó esta llamada:
# "cloud_only" | "local_only" | "hybrid_local" | "hybrid_cloud_fallback".
# Nullable para compatibilidad con registros anteriores a la migración.
pipeline_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

> `LLMCall` es append-only por diseño. El campo `pipeline_mode` se establece en el momento de creación del registro, nunca se muta después del flush.

### 6.2 Crear migración Alembic

```powershell
infisical run -- uv run alembic revision --autogenerate -m "add pipeline_mode to llm_calls"
```

Revisar el fichero generado en `migrations/versions/`. Debe contener:

```python
def upgrade() -> None:
    op.add_column(
        "llm_calls",
        sa.Column("pipeline_mode", sa.String(length=40), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("llm_calls", "pipeline_mode")
```

Aplicar:
```powershell
infisical run -- uv run alembic upgrade head
```

**Verificación**:
```powershell
infisical run -- uv run python -c "
from sqlalchemy import inspect, create_engine, text
import os, asyncio
from app.config import get_settings
s = get_settings()
engine = create_engine(s.database_url.replace('+asyncpg', ''))
cols = [c['name'] for c in inspect(engine).get_columns('llm_calls')]
assert 'pipeline_mode' in cols, 'columna no encontrada'
print('OK — columna pipeline_mode en llm_calls')
"
```

---

## Paso 7 — Modificar `app/llm/client.py`: añadir `pipeline_mode` a `complete()`

Cambio **backward-compatible** (parámetro opcional con default `None`). Permite que `_extract_cloud` y `_extract_local` estampen el modo en el `LLMCall` en el momento de creación, respetando el diseño append-only.

### 7.1 Actualizar la firma de `complete()`

```python
async def complete(
    self,
    *,
    task: TaskType,
    messages: list[dict[str, Any]],
    response_model: type[T],
    tenant_id: UUID,
    db: AsyncSession,
    prompt_version: str | None = None,
    max_retries: int = 2,
    pipeline_mode: str | None = None,   # NUEVO — opcional, backward-compatible
) -> LLMCompleteResult[T]:
```

### 7.2 Pasar `pipeline_mode` al crear `LLMCall` (dentro del bloque `finally`)

```python
llm_call = LLMCall(
    tenant_id=tenant_id,
    task=task,
    model=model,
    provider=provider,
    prompt_version=prompt_version,
    input_tokens=input_tokens,
    output_tokens=output_tokens,
    cost_eur=cost,
    latency_ms=latency_ms,
    status=status,
    error=error,
    langfuse_trace_id=trace_id_str,
    pipeline_mode=pipeline_mode,   # NUEVO
)
```

### 7.3 Verificación

Los tests existentes no pasan `pipeline_mode`, por lo que el valor quedará `None` — compatible con los registros históricos de la BD.

```powershell
infisical run -- uv run pytest tests/ -x -q   # la suite completa debe seguir verde
```

---

## Paso 8 — Crear `app/llm/local_provider.py`

Fichero nuevo. Encapsula toda la interacción con Ollama. Solo se importa desde `extraction.py`.

> **Langfuse**: las llamadas Ollama pasan directamente por este módulo sin pasar por `LLMClient`, por lo que **no generan trazas en Langfuse**. Quedan registradas en `llm_calls` para auditoría de coste (0 €) y latencia.

```python
"""Local LLM provider using Ollama (Qwen 2.5-VL).

Sole entry point for Ollama calls. Only imported by app/llm/extraction.py.
Langfuse tracing is not available for local calls; audit trail is via llm_calls only.
"""

import base64
import json
import time
from typing import Any

import structlog
from ollama import AsyncClient, ResponseError

from app.config import get_settings

log = structlog.get_logger(__name__)


class OllamaExtractionError(Exception):
    """Raised when the local model fails to produce a usable response."""


async def extract_with_ollama(
    image_bytes: bytes,
    system_prompt: str,
    user_prompt: str,
) -> tuple[dict[str, Any], int, float]:
    """Call Ollama vision model and return (parsed_dict, tokens_approx, latency_s).

    Args:
        image_bytes: PNG or JPEG bytes of the invoice image.
        system_prompt: Extraction system prompt text.
        user_prompt: User turn prompt text.

    Returns:
        Tuple of (raw dict from JSON response, approximate output token count, latency in s).

    Raises:
        OllamaExtractionError: on connection error, timeout, or invalid JSON output.
    """
    settings = get_settings()
    client = AsyncClient(host=settings.ollama_host, timeout=settings.ollama_timeout_seconds)
    image_b64 = base64.b64encode(image_bytes).decode()

    t0 = time.monotonic()
    try:
        response = await client.chat(
            model=settings.ollama_vision_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prompt,
                    "images": [image_b64],
                },
            ],
            format="json",  # constrained decoding → always syntactically valid JSON
        )
    except ResponseError as exc:
        raise OllamaExtractionError(f"Ollama API error: {exc}") from exc
    except Exception as exc:
        raise OllamaExtractionError(f"Ollama connection error: {exc}") from exc

    latency_s = time.monotonic() - t0
    content = response.message.content or ""
    tokens_approx = getattr(response, "eval_count", 0) or 0

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaExtractionError(
            f"Ollama returned non-JSON content: {content[:200]!r}"
        ) from exc

    if not isinstance(parsed, dict):
        raise OllamaExtractionError(
            f"Ollama JSON is not a dict: {type(parsed).__name__}"
        )

    log.debug(
        "ollama_extraction_done",
        model=settings.ollama_vision_model,
        latency_s=round(latency_s, 2),
        tokens=tokens_approx,
    )
    return parsed, tokens_approx, latency_s


async def ping_ollama() -> bool:
    """Return True if Ollama is reachable and the configured vision model is loaded."""
    settings = get_settings()
    try:
        client = AsyncClient(host=settings.ollama_host, timeout=5)
        models = await client.list()
        names = [m.model for m in models.models]
        return settings.ollama_vision_model in names
    except Exception:
        return False
```

**Verificación** (con Ollama corriendo):
```powershell
infisical run -- uv run python -c "
import asyncio
from app.llm.local_provider import ping_ollama
ok = asyncio.run(ping_ollama())
print('Ollama reachable:', ok)
"
```

---

## Paso 9 — Crear prompt `app/llm/prompts/extraction_local_v1.txt`

Mismo esquema de campos que `extraction_v1.txt` (para que `Factura` valide ambas salidas) pero redactado para Qwen con instrucción JSON explícita.

```
Eres un extractor experto de datos de facturas españolas.
Recibes la IMAGEN de una factura y devuelves EXCLUSIVAMENTE un objeto JSON válido
que cumple exactamente este esquema (sin texto adicional, sin markdown, sin comentarios):

{
  "fecha": "YYYY-MM-DD",
  "proveedor": "Razón social del emisor",
  "cif_nif": "CIF o NIF del emisor (formato exacto, sin espacios ni guiones)",
  "numero_factura": "número de factura o null si no aparece",
  "base_imponible": 0.00,
  "iva_percent": 21.0,
  "iva_amount": 0.00,
  "total": 0.00,
  "currency": "EUR",
  "lineas": [
    {
      "descripcion": "descripción del artículo o servicio",
      "cantidad": 1.0,
      "precio_unitario": 0.00,
      "total": 0.00
    }
  ],
  "confidence": 0.95
}

Reglas estrictas:
- Todos los campos numéricos usan punto decimal, nunca coma. "1.234,56" → 1234.56
- Fechas en formato ISO YYYY-MM-DD.
- Si un campo no está claramente visible, usa null. NUNCA inventes valores.
- "total" es el importe FINAL con IVA incluido.
- "confidence" es tu estimación de la calidad de la extracción (0.0 muy inseguro, 1.0 completamente seguro).
- Para CIF/NIF: copia exactamente el texto que aparece en la factura.
- Analiza la posición espacial: totales abajo a la derecha, emisor arriba a la izquierda.
```

---

## Paso 10 — Refactorizar `app/llm/extraction.py`

Este es el paso central. El contrato externo de `extract_invoice` **no cambia** (misma firma, mismo tipo de retorno `ExtractionResult`). Solo se reorganiza internamente.

### 10.1 Estructura objetivo

```
app/llm/extraction.py
│
├── ExtractionResult          ← sin cambios (frozen dataclass)
├── _media_part()             ← sin cambios
├── _build_extraction_messages() ← sin cambios
│
├── PROMPT_VERSION = "extraction_v1"       ← sin cambios
├── LOCAL_PROMPT_VERSION = "extraction_local_v1"   ← nuevo
│
├── _extract_cloud(*, file_bytes, mime_type, tenant_id, db, pipeline_mode) → ExtractionResult
│   └── contiene el cuerpo actual de extract_invoice, sin cambios de comportamiento
│       solo añade pipeline_mode=pipeline_mode en la llamada a client.complete()
│
├── _extract_local(*, file_bytes, mime_type, tenant_id, db, pipeline_mode) → ExtractionResult
│   └── nuevo: pdf_utils + local_provider + Factura.model_validate() + LLMCall directo
│
└── extract_invoice(*, file_bytes, mime_type, tenant_id, db) → ExtractionResult
    └── router de modos; firma pública sin cambios
```

### 10.2 `_extract_cloud` — refactor del cuerpo actual

Extraer el cuerpo actual de `extract_invoice` a esta función privada añadiendo solo el parámetro `pipeline_mode`:

```python
async def _extract_cloud(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    db: AsyncSession,
    pipeline_mode: str = "cloud_only",
) -> ExtractionResult:
    """Extract invoice using cloud LLM (current behavior, unchanged)."""
    if len(file_bytes) > 20 * 1024 * 1024:
        raise ValueError("File too large (>20MB)")

    messages = _build_extraction_messages(
        system_prompt=load_prompt(PROMPT_VERSION),
        file_bytes=file_bytes,
        mime_type=mime_type,
    )
    client = get_llm_client()
    completion = await client.complete(
        task="extraction",
        messages=messages,
        response_model=Factura,
        tenant_id=tenant_id,
        db=db,
        prompt_version=PROMPT_VERSION,
        max_retries=2,
        pipeline_mode=pipeline_mode,   # ← único cambio respecto al código actual
    )
    factura = completion.result
    logger.info(
        "extraction.cloud.done",
        tenant_id=str(tenant_id),
        llm_call_id=str(completion.llm_call_id),
        pipeline_mode=pipeline_mode,
        proveedor=factura.proveedor,
        total=str(factura.total),
        confidence=factura.confidence,
    )
    return ExtractionResult(factura=factura, llm_call_id=completion.llm_call_id)
```

### 10.3 `_extract_local` — nueva función

```python
async def _extract_local(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    db: AsyncSession,
    pipeline_mode: str = "local_only",
) -> ExtractionResult:
    """Extract invoice using local Ollama vision model.

    Creates LLMCall directly (bypasses LLMClient) because Ollama doesn't share
    the Anthropic/Google SDK interface. Cost is 0 EUR. No Langfuse trace.
    """
    from app.core.pdf_utils import pdf_first_page_to_png
    from app.llm.local_provider import OllamaExtractionError, extract_with_ollama
    from app.llm.pricing import compute_cost_eur
    from app.models import LLMCall

    settings = get_settings()

    # Ollama vision models require an image; convert PDF to PNG first.
    if mime_type == "application/pdf":
        image_bytes = await pdf_first_page_to_png(file_bytes, dpi=settings.pdf_render_dpi)
    else:
        image_bytes = file_bytes

    system_prompt = load_prompt(LOCAL_PROMPT_VERSION)
    user_prompt = "Extrae los datos estructurados de esta factura. Devuelve únicamente el JSON."

    # OllamaExtractionError propagates to the caller (extract_invoice).
    raw_dict, tokens_out, latency_s = await extract_with_ollama(
        image_bytes=image_bytes,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    # Validate against the shared Factura schema.
    try:
        factura = Factura.model_validate(raw_dict)
    except Exception as exc:
        raise OllamaExtractionError(f"Factura schema validation failed: {exc}") from exc

    # Persist audit record. pipeline_mode is set at creation (append-only design).
    llm_call = LLMCall(
        tenant_id=tenant_id,
        task="extraction",
        model=settings.ollama_vision_model,
        provider="ollama",
        prompt_version=LOCAL_PROMPT_VERSION,
        input_tokens=0,          # Ollama doesn't report input tokens reliably
        output_tokens=tokens_out,
        cost_eur=compute_cost_eur(settings.ollama_vision_model, 0, tokens_out),
        latency_ms=int(latency_s * 1000),
        status="ok",
        pipeline_mode=pipeline_mode,
    )
    db.add(llm_call)
    await db.flush()

    logger.info(
        "extraction.local.done",
        tenant_id=str(tenant_id),
        llm_call_id=str(llm_call.id),
        pipeline_mode=pipeline_mode,
        proveedor=factura.proveedor,
        total=str(factura.total),
        confidence=factura.confidence,
    )
    return ExtractionResult(factura=factura, llm_call_id=llm_call.id)
```

### 10.4 `extract_invoice` — nuevo router de modos (firma pública sin cambios)

```python
async def extract_invoice(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    db: AsyncSession,
) -> ExtractionResult:
    """Public entry point for invoice extraction.

    Routes to cloud, local, or hybrid mode based on settings.pipeline_mode.
    The signature is identical to the previous version; callers need no changes.
    """
    from app.config import PipelineMode
    from app.llm.local_provider import OllamaExtractionError

    settings = get_settings()
    mode = settings.pipeline_mode

    if mode == PipelineMode.CLOUD_ONLY:
        return await _extract_cloud(
            file_bytes=file_bytes,
            mime_type=mime_type,
            tenant_id=tenant_id,
            db=db,
            pipeline_mode="cloud_only",
        )

    if mode == PipelineMode.LOCAL_ONLY:
        return await _extract_local(
            file_bytes=file_bytes,
            mime_type=mime_type,
            tenant_id=tenant_id,
            db=db,
            pipeline_mode="local_only",
        )

    # ── HYBRID ──────────────────────────────────────────────────────────────
    assert mode == PipelineMode.HYBRID

    fallback_reason: str | None = None

    try:
        local_result = await _extract_local(
            file_bytes=file_bytes,
            mime_type=mime_type,
            tenant_id=tenant_id,
            db=db,
            pipeline_mode="hybrid_local",   # establecido en creación, no mutado después
        )
        if local_result.factura.confidence >= settings.llm_local_confidence_threshold:
            return local_result

        fallback_reason = (
            f"confidence {local_result.factura.confidence:.2f} "
            f"< threshold {settings.llm_local_confidence_threshold:.2f}"
        )
        logger.info(
            "extraction.hybrid.fallback_low_confidence",
            confidence=local_result.factura.confidence,
            threshold=settings.llm_local_confidence_threshold,
        )

    except OllamaExtractionError as exc:
        fallback_reason = f"local_error: {exc}"
        logger.warning("extraction.hybrid.fallback_local_error", error=str(exc))

    # Cloud fallback: pipeline_mode set at LLMCall creation inside _extract_cloud.
    return await _extract_cloud(
        file_bytes=file_bytes,
        mime_type=mime_type,
        tenant_id=tenant_id,
        db=db,
        pipeline_mode="hybrid_cloud_fallback",
    )
```

### 10.5 Imports y constantes a añadir en `extraction.py`

Todos los imports van al **bloque de imports del módulo** (nivel superior del fichero), no dentro de las funciones. Mypy strict y ruff (`TCH`, `ASYNC`) lo exigen.

```python
# Añadir junto a los imports existentes de app.*
from app.config import PipelineMode, get_settings
from app.core.pdf_utils import pdf_first_page_to_png
from app.llm.local_provider import OllamaExtractionError, extract_with_ollama
from app.llm.pricing import compute_cost_eur
from app.models import LLMCall
```

Y las constantes, junto a `PROMPT_VERSION`:
```python
PROMPT_VERSION = "extraction_v1"            # ya existe
LOCAL_PROMPT_VERSION = "extraction_local_v1"  # nuevo
```

> Los imports `from app.core.pdf_utils`, `from app.llm.local_provider` y `from app.llm.pricing` no crean dependencias circulares: ninguno de esos módulos importa de `extraction.py`. Verificar con `uv run python -c "import app.llm.extraction"` antes de continuar.

Con estos imports en módulo, las funciones `_extract_local` y `extract_invoice` **eliminan** sus correspondientes `from ... import ...` inline que aparecen en los pseudocódigos de §10.2–§10.4 (esas líneas son solo para claridad conceptual; en el código real no van dentro del cuerpo de las funciones).

---

## Paso 11 — Añadir health check de Ollama

**Fichero**: `app/routes/api/health.py`

```python
@router.get("/health/ollama")
async def health_ollama() -> dict:
    from app.config import PipelineMode, get_settings
    from app.llm.local_provider import ping_ollama

    settings = get_settings()
    if settings.pipeline_mode == PipelineMode.CLOUD_ONLY:
        return {"status": "skipped", "reason": "pipeline_mode=cloud_only"}

    ok = await ping_ollama()
    if not ok:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "detail": (
                    f"Ollama unreachable or model "
                    f"{settings.ollama_vision_model!r} not loaded"
                ),
            },
        )
    return {
        "status": "ok",
        "model": settings.ollama_vision_model,
        "host": settings.ollama_host,
    }
```

**Verificación**:
```powershell
# Con PIPELINE_MODE=cloud_only (default):
curl http://localhost:8000/health/ollama
# → {"status":"skipped","reason":"pipeline_mode=cloud_only"}

# Con PIPELINE_MODE=local_only y Ollama corriendo:
# → {"status":"ok","model":"qwen2.5vl:7b","host":"http://localhost:11434"}
```

---

## Paso 12 — Tests unitarios nuevos

### 12.1 `tests/unit/test_pdf_utils.py`

```python
import pytest
from pathlib import Path
from app.core.pdf_utils import pdf_first_page_to_png

SAMPLE_PDF = Path("tests/fixtures/sample_invoice.pdf")


@pytest.mark.asyncio
async def test_pdf_to_png_returns_valid_png():
    pdf_bytes = SAMPLE_PDF.read_bytes()
    png = await pdf_first_page_to_png(pdf_bytes, dpi=150)
    assert isinstance(png, bytes)
    assert png[:4] == b"\x89PNG"


@pytest.mark.asyncio
async def test_pdf_to_png_higher_dpi_produces_larger_output():
    pdf_bytes = SAMPLE_PDF.read_bytes()
    png_low  = await pdf_first_page_to_png(pdf_bytes, dpi=72)
    png_high = await pdf_first_page_to_png(pdf_bytes, dpi=200)
    assert len(png_high) > len(png_low)
```

### 12.2 `tests/unit/test_local_provider.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.llm.local_provider import OllamaExtractionError, extract_with_ollama

VALID_JSON = (
    '{"fecha":"2024-01-15","proveedor":"Empresa SL","cif_nif":"B12345678",'
    '"base_imponible":100.0,"iva_percent":21.0,"iva_amount":21.0,'
    '"total":121.0,"currency":"EUR","lineas":[],"confidence":0.95}'
)


def _make_response(content: str, eval_count: int = 150) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    resp = MagicMock()
    resp.message = msg
    resp.eval_count = eval_count
    return resp


@pytest.mark.asyncio
async def test_extract_returns_parsed_dict(monkeypatch):
    async def fake_chat(**kwargs):
        return _make_response(VALID_JSON)

    monkeypatch.setattr("ollama.AsyncClient.chat", fake_chat)
    result, tokens, latency = await extract_with_ollama(b"img", "sys", "user")
    assert result["total"] == 121.0
    assert tokens == 150
    assert latency >= 0


@pytest.mark.asyncio
async def test_extract_raises_on_non_json(monkeypatch):
    async def fake_chat(**kwargs):
        return _make_response("not json")

    monkeypatch.setattr("ollama.AsyncClient.chat", fake_chat)
    with pytest.raises(OllamaExtractionError, match="non-JSON"):
        await extract_with_ollama(b"img", "sys", "user")


@pytest.mark.asyncio
async def test_extract_raises_on_connection_error(monkeypatch):
    async def fake_chat(**kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr("ollama.AsyncClient.chat", fake_chat)
    with pytest.raises(OllamaExtractionError, match="connection error"):
        await extract_with_ollama(b"img", "sys", "user")
```

### 12.3 `tests/unit/test_extraction_modes.py`

> **Importante**: el mock de `get_settings` debe apuntar a `app.llm.extraction.get_settings`
> (nombre local en el módulo después del import), no a `app.config.get_settings`.

```python
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.config import PipelineMode
from app.schemas.invoice import Factura

_HIGH = Factura(
    fecha="2024-01-15", proveedor="Empresa SL", cif_nif="B12345678",
    base_imponible=Decimal("100"), iva_percent=Decimal("21"),
    iva_amount=Decimal("21"), total=Decimal("121"), confidence=0.95,
)
_LOW = _HIGH.model_copy(update={"confidence": 0.40})

_CLOUD_RESULT = MagicMock(factura=_HIGH, llm_call_id=uuid4())
_LOCAL_HIGH   = MagicMock(factura=_HIGH, llm_call_id=uuid4())
_LOCAL_LOW    = MagicMock(factura=_LOW,  llm_call_id=uuid4())


def _settings(mode: PipelineMode, threshold: float = 0.75) -> MagicMock:
    s = MagicMock()
    s.pipeline_mode = mode
    s.llm_local_confidence_threshold = threshold
    return s


@pytest.mark.asyncio
async def test_cloud_only_never_calls_local():
    local_mock = AsyncMock(side_effect=AssertionError("local must not be called"))
    cloud_mock = AsyncMock(return_value=_CLOUD_RESULT)

    with patch("app.llm.extraction.get_settings", return_value=_settings(PipelineMode.CLOUD_ONLY)), \
         patch("app.llm.extraction._extract_local", local_mock), \
         patch("app.llm.extraction._extract_cloud", cloud_mock):
        from app.llm.extraction import extract_invoice
        await extract_invoice(file_bytes=b"f", mime_type="application/pdf",
                              tenant_id=uuid4(), db=AsyncMock())

    cloud_mock.assert_awaited_once()
    local_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_only_never_calls_cloud():
    local_mock = AsyncMock(return_value=_LOCAL_HIGH)
    cloud_mock = AsyncMock(side_effect=AssertionError("cloud must not be called"))

    with patch("app.llm.extraction.get_settings", return_value=_settings(PipelineMode.LOCAL_ONLY)), \
         patch("app.llm.extraction._extract_local", local_mock), \
         patch("app.llm.extraction._extract_cloud", cloud_mock):
        from app.llm.extraction import extract_invoice
        await extract_invoice(file_bytes=b"f", mime_type="application/pdf",
                              tenant_id=uuid4(), db=AsyncMock())

    local_mock.assert_awaited_once()
    cloud_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_hybrid_high_confidence_no_fallback():
    local_mock = AsyncMock(return_value=_LOCAL_HIGH)
    cloud_mock = AsyncMock(side_effect=AssertionError("cloud must not be called"))

    with patch("app.llm.extraction.get_settings", return_value=_settings(PipelineMode.HYBRID)), \
         patch("app.llm.extraction._extract_local", local_mock), \
         patch("app.llm.extraction._extract_cloud", cloud_mock):
        from app.llm.extraction import extract_invoice
        result = await extract_invoice(file_bytes=b"f", mime_type="application/pdf",
                                       tenant_id=uuid4(), db=AsyncMock())

    assert result.factura.confidence == 0.95
    cloud_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_hybrid_low_confidence_triggers_cloud_fallback():
    local_mock = AsyncMock(return_value=_LOCAL_LOW)
    cloud_mock = AsyncMock(return_value=_CLOUD_RESULT)

    with patch("app.llm.extraction.get_settings", return_value=_settings(PipelineMode.HYBRID)), \
         patch("app.llm.extraction._extract_local", local_mock), \
         patch("app.llm.extraction._extract_cloud", cloud_mock):
        from app.llm.extraction import extract_invoice
        result = await extract_invoice(file_bytes=b"f", mime_type="application/pdf",
                                       tenant_id=uuid4(), db=AsyncMock())

    local_mock.assert_awaited_once()
    cloud_mock.assert_awaited_once()
    # Verifica que pipeline_mode pasado al cloud fue el correcto
    _, kwargs = cloud_mock.call_args
    assert kwargs["pipeline_mode"] == "hybrid_cloud_fallback"
    assert result.factura.confidence == 0.95


@pytest.mark.asyncio
async def test_hybrid_local_error_triggers_cloud_fallback():
    from app.llm.local_provider import OllamaExtractionError

    local_mock = AsyncMock(side_effect=OllamaExtractionError("timeout"))
    cloud_mock = AsyncMock(return_value=_CLOUD_RESULT)

    with patch("app.llm.extraction.get_settings", return_value=_settings(PipelineMode.HYBRID)), \
         patch("app.llm.extraction._extract_local", local_mock), \
         patch("app.llm.extraction._extract_cloud", cloud_mock):
        from app.llm.extraction import extract_invoice
        result = await extract_invoice(file_bytes=b"f", mime_type="application/pdf",
                                       tenant_id=uuid4(), db=AsyncMock())

    cloud_mock.assert_awaited_once()
    assert result.factura.confidence == 0.95


@pytest.mark.asyncio
async def test_hybrid_local_called_with_hybrid_local_mode():
    """_extract_local debe recibir pipeline_mode='hybrid_local', no 'local_only'."""
    local_mock = AsyncMock(return_value=_LOCAL_HIGH)
    cloud_mock = AsyncMock()

    with patch("app.llm.extraction.get_settings", return_value=_settings(PipelineMode.HYBRID)), \
         patch("app.llm.extraction._extract_local", local_mock), \
         patch("app.llm.extraction._extract_cloud", cloud_mock):
        from app.llm.extraction import extract_invoice
        await extract_invoice(file_bytes=b"f", mime_type="application/pdf",
                              tenant_id=uuid4(), db=AsyncMock())

    _, kwargs = local_mock.call_args
    assert kwargs["pipeline_mode"] == "hybrid_local"
```

**Ejecutar todos los tests nuevos**:
```powershell
uv run pytest tests/unit/test_pdf_utils.py tests/unit/test_local_provider.py tests/unit/test_extraction_modes.py -v
```

---

## Paso 13 — Actualizar eval runner para soporte multi-modo

**Fichero**: `app/evals/runners/extraction.py`

Añadir parámetro `--mode` al CLI:

```python
# Añadir al bloque argparse:
parser.add_argument(
    "--mode",
    choices=["cloud_only", "local_only", "hybrid"],
    default="cloud_only",
    help="Pipeline mode for this eval run (default: cloud_only).",
)
```

Al inicio del runner, antes de ejecutar las extracciones:

```python
import os
if args.mode != "cloud_only":
    os.environ["PIPELINE_MODE"] = args.mode
    from app.config import get_settings
    get_settings.cache_clear()
```

Permite comparar accuracy sin cambiar Infisical:

```powershell
infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid> --mode cloud_only  > eval_cloud.json
infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid> --mode local_only  > eval_local.json
infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid> --mode hybrid      > eval_hybrid.json
```

---

## Paso 14 — Verificación E2E completa

### 14.1 Suite de tests completa (debe seguir verde)

```powershell
infisical run -- uv run pytest tests/ -v --tb=short
```

### 14.2 Modo `cloud_only` intacto

```powershell
infisical secrets set PIPELINE_MODE=cloud_only
# Reiniciar worker y app; subir factura de prueba
# Resultado esperado:
#   Invoice.status = ready
#   llm_calls.pipeline_mode = 'cloud_only'
#   llm_calls.provider = 'google' (o 'anthropic' según override)
#   llm_calls.cost_eur > 0
```

### 14.3 Modo `local_only`

```powershell
infisical secrets set PIPELINE_MODE=local_only
# Reiniciar worker y app; subir misma factura
# Resultado esperado:
#   Invoice.status = ready
#   llm_calls.model = 'qwen2.5vl:7b'
#   llm_calls.provider = 'ollama'
#   llm_calls.cost_eur = 0.000000
#   llm_calls.pipeline_mode = 'local_only'
```

### 14.4 Modo `hybrid`

```powershell
infisical secrets set PIPELINE_MODE=hybrid
infisical secrets set LLM_LOCAL_CONFIDENCE_THRESHOLD=0.75
# Reiniciar worker y app
# Factura simple (local suficiente):
#   → 1 registro en llm_calls con pipeline_mode='hybrid_local'
# Factura compleja (local con baja confianza):
#   → 2 registros: pipeline_mode='hybrid_local' + 'hybrid_cloud_fallback'
```

### 14.5 Health checks

```powershell
curl http://localhost:8000/health
curl http://localhost:8000/health/db
curl http://localhost:8000/health/redis
curl http://localhost:8000/health/ollama
```

---

## Resumen de ficheros modificados / creados

| Fichero | Acción | Descripción |
|---|---|---|
| `pyproject.toml` | Modificar | `ollama>=0.4.0`, `pypdfium2>=4.30.0` |
| `app/config.py` | Modificar | `PipelineMode` enum + 6 campos nuevos |
| `app/core/pdf_utils.py` | Crear | PDF→PNG async (solo ruta Ollama) |
| `app/llm/pricing.py` | Modificar | Entradas coste 0 para modelos Ollama |
| `app/models/llm_call.py` | Modificar | `pipeline_mode: Mapped[str \| None]` |
| `migrations/versions/xxx.py` | Crear | Alembic: `ADD COLUMN pipeline_mode` |
| `app/llm/client.py` | Modificar | Parámetro `pipeline_mode` en `complete()` y en creación de `LLMCall` |
| `app/llm/local_provider.py` | Crear | Cliente Ollama async + `ping_ollama()` |
| `app/llm/prompts/extraction_local_v1.txt` | Crear | Prompt para Qwen |
| `app/llm/extraction.py` | Modificar | `_extract_cloud` + `_extract_local` + router de modos en `extract_invoice` |
| `app/routes/api/health.py` | Modificar | Endpoint `/health/ollama` |
| `tests/unit/test_pdf_utils.py` | Crear | Tests conversión PDF→PNG |
| `tests/unit/test_local_provider.py` | Crear | Tests cliente Ollama (mockeado) |
| `tests/unit/test_extraction_modes.py` | Crear | Tests 3 modos (ambos providers mockeados) |
| `app/evals/runners/extraction.py` | Modificar | Flag `--mode` |

**Ficheros que NO se tocan**:
- `app/services/invoice_service.py` — `apply_extraction_result` recibe `ExtractionResult`, sin cambios
- `app/jobs/invoice_jobs.py` — llama a `extract_invoice` con la misma firma keyword-only
- `app/routes/web/invoices.py` — ningún cambio de UI ni de lógica HTTP
- `app/schemas/invoice.py` — `Factura` es el schema único compartido por cloud y local
- `app/core/storage.py`, `app/core/db.py`, `app/core/middleware.py` — sin cambios

---

## Guía de cambio de modo en producción (sin desplegar código)

```powershell
# Volver a modo cloud (comportamiento original)
infisical secrets set PIPELINE_MODE=cloud_only

# Modo local puro (desarrollo / coste 0)
infisical secrets set PIPELINE_MODE=local_only

# Modo híbrido (producción con fallback)
infisical secrets set PIPELINE_MODE=hybrid
infisical secrets set LLM_LOCAL_CONFIDENCE_THRESHOLD=0.80   # ajustar según evals

# Reiniciar el worker ARQ para que lea la nueva configuración
# (get_settings() usa lru_cache; el proceso nuevo parte de un cache limpio)
```
