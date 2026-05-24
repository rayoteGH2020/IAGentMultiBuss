# CLAUDE.md — Proyecto: Extractor de Facturas (modo configurable: solo_local / hibrido / solo_claude)

> Este fichero es leído automáticamente por **Claude Code** (`CLAUDE.md` en la raíz del proyecto) y puede usarse en **Cursor** indicándolo como contexto (`@CLAUDE.md`) o pegándolo en `.cursorrules`.
>
> Es la **fuente única de verdad** del proyecto: contiene contexto, decisiones técnicas, pasos de instalación y convenciones de código. Si el agente (Cursor/Claude Code) sigue este fichero al pie de la letra, debería poder dejar el proyecto operativo sin más intervención.
>
> **⚠️ ESTE FICHERO REEMPLAZA LAS VERSIONES ANTERIORES** (basadas en Tesseract+Qwen-texto, en `.env`, o en el SDK de Infisical). Si tienes código previo, descarta los módulos `ocr.py`, cualquier carga de `.env`, las dependencias `pytesseract`/`pdf2image`/`python-dotenv`/`infisicalsdk`, y cualquier llamada a `InfisicalSDKClient`.

---

## 1. Contexto del proyecto

Sistema de extracción estructurada de datos de **facturas en español** (PDF e imagen). El objetivo final de producción es **≥95% de accuracy en CIF, total y fecha** mediante una arquitectura **híbrida** (modelo de visión local + Claude API como fallback), pero el sistema es **configurable en 3 modos** para soportar todo el ciclo de desarrollo:

| Modo | Local (Qwen 2.5-VL) | Fallback a Claude | Caso de uso |
|------|---------------------|-------------------|-------------|
| `solo_local`   | ✅ siempre | ❌ nunca | Desarrollo y depuración. Coste API = 0 €. Permite ver qué % de facturas pasa la validación local sin contaminar con fallbacks. **Modo por defecto en esta fase.** |
| `hibrido`      | ✅ primero | ✅ si validación falla | Producción. Equilibrio coste / accuracy / privacidad. |
| `solo_claude`  | ❌ saltado | ✅ siempre | Sanity check, máxima accuracy puntual, o comparativa de modelos. |

```
                                  ┌─────────────────────────────────────────┐
Factura (PDF/JPG/PNG)              │   PIPELINE_MODE (env var) o flag --mode │
   ↓                               └──────────────┬──────────────────────────┘
Cloudflare R2 (entrada)                           │
   ↓                                              ▼
pypdfium2 (PDF → imagen PNG)        ┌──────────────────────────┐
   ↓                                │  ¿modo == solo_claude?   │
   ┌──── NO ─────────────────────── │  ¿modo == solo_local?    │
   │                                │  ¿modo == hibrido?       │
   ▼                                └──────────────────────────┘
Ollama + Qwen 2.5-VL 7B
   ↓
Validador (CIF, base+IVA≈total, fecha, completitud)
   ↓
   ├── ✅ OK                         →  Pydantic  →  R2 (salida)
   │
   └── ❌ Validación falla
          │
          ├── modo solo_local        →  devolver resultado con validation.is_valid=false
          │
          └── modo hibrido           →  Claude API  →  Pydantic  →  R2 (fallback_used=true)
                                                        ↑
modo solo_claude ───────────────────────────────────────┘
```

**Lenguaje**: Python 3.10+
**SO de desarrollo**: Windows 11
**Hardware del usuario**:
- CPU: Intel i5-12450H (8 cores / 12 threads)
- GPU: Intel UHD Graphics (integrada — **no se usará** para inferencia)
- RAM: 16 GB
- Inferencia local: **CPU only** (~30–90 s por factura con Qwen 2.5-VL 7B Q4)

**Decisiones técnicas ya tomadas** (no reabrir el debate):
- ✅ **Modo de funcionamiento configurable** desde el secreto `PIPELINE_MODE` en Infisical (default), sobreescribible puntualmente con CLI flag `--mode {solo_local,hibrido,solo_claude}`.
- ✅ **VLM (Vision-Language Model)** en local: `qwen2.5vl:7b` vía Ollama. Sustituye al pipeline anterior de Tesseract+LLM-texto, que perdía información de layout.
- ✅ **Conversión PDF→imagen** con `pypdfium2` (binding Python puro, sin Poppler ni binarios del sistema).
- ✅ **Cliente de fallback**: SDK oficial de Anthropic (`anthropic`), modelo por defecto `claude-sonnet-4-6` (ajustable en Infisical sin tocar código).
- ✅ **Capa de validación con reglas** antes de aceptar la salida del LLM local: validación de CIF/NIF español (algoritmo de dígito de control), comprobación `base + IVA ≈ total` (tolerancia 0.02 €), formato y rango de fecha.
- ✅ **`format='json'`** en las llamadas a Ollama para forzar salida JSON válida a nivel de decoder.
- ✅ **Pydantic v2** para validar el esquema final.
- ✅ **Configuración con Infisical CLI** (`infisical run --` inyecta secretos como variables de entorno al lanzar el proceso). El usuario ya está autenticado con `infisical login`. NO se usa el SDK de Python.
- ✅ **Almacenamiento**: Cloudflare R2 vía `boto3` (S3-compatible).
- ❌ **Sin `.env`**, sin `python-dotenv`, sin `infisicalsdk`. Sin Tesseract, sin Poppler, sin LangChain. Sin `Instructor`.
- ❌ Sin fine-tuning en esta fase. Si los evals dan <90% de accuracy global tras validación, se planteará en una fase 2.

---

## 2. Prerrequisitos del sistema

El agente debe verificar (y guiar al usuario a instalar/configurar si falta) lo siguiente **antes** de tocar el código Python.

### 2.1. Python 3.10 o superior

```powershell
python --version
```

Si no está o es < 3.10 → instalar desde https://www.python.org/downloads/ marcando "Add Python to PATH".

### 2.2. Git

```powershell
git --version
```

Si no está → https://git-scm.com/download/win

### 2.3. Ollama

```powershell
ollama --version
```

Si no está:

1. Descargar el instalador oficial para Windows: https://ollama.com/download/windows
2. Ejecutar e instalar (instalador estándar).
3. Ollama queda corriendo como servicio en `http://localhost:11434`.
4. Verificar: `curl http://localhost:11434/api/tags` debe responder con un JSON.

### 2.4. Infisical CLI

El agente debe verificar:

```powershell
infisical --version
infisical user                                       # debe mostrar el usuario logueado
```

Si la CLI no está instalada → instalar (en Windows):

```powershell
# Opción A: vía Scoop (recomendado)
scoop bucket add org https://github.com/Infisical/scoop-infisical.git
scoop install infisical

# Opción B: vía npm si ya hay Node instalado
npm install -g @infisical/cli
```

Documentación oficial: https://infisical.com/docs/cli/overview

Si la CLI está instalada pero **no hay usuario logueado** (`infisical user` falla o vacío) → el agente PARA y pide al usuario:

```powershell
infisical login
```

(esto abre el navegador y guarda el token de sesión en `%USERPROFILE%\.infisical\`)

### 2.5. Inicialización del workspace en el directorio del proyecto

Una sola vez, dentro del directorio del proyecto:

```powershell
infisical init
```

Este comando:
- Lista los proyectos a los que el usuario tiene acceso (interactivo).
- Pide elegir el entorno por defecto (`dev`, `staging`, `prod`).
- Crea un fichero `.infisical.json` en la raíz del proyecto con la forma:

```json
{
  "workspaceId": "abc-123-xyz",
  "defaultEnvironment": "dev"
}
```

**Importante**: este fichero `.infisical.json` **SÍ se commitea a git** (no contiene secretos, solo apunta a qué proyecto/entorno usar). Garantiza que toda persona que clone el repositorio usará automáticamente el mismo proyecto al hacer `infisical run`.

### 2.6. Clave API de Anthropic (en Infisical, no en SO)

El usuario debe disponer de una clave de https://console.anthropic.com/. Se guardará como secreto **`ANTHROPIC_API_KEY` dentro de Infisical** (ver sección 6), nunca como variable de entorno del sistema ni en ficheros.

---

## 3. Pasos de instalación del modelo

### 3.1. Descargar el modelo de visión principal

```powershell
ollama pull qwen2.5vl:7b
```

Tamaño: ~5.5 GB. Tarda varios minutos. Este modelo **acepta imágenes directamente** y devuelve texto/JSON; no necesita OCR previo.

Referencia oficial: https://ollama.com/library/qwen2.5vl

### 3.2. (Opcional) Modelo de visión más ligero para iteración rápida

```powershell
ollama pull qwen2.5vl:3b
```

Útil para iterar el prompt sin esperar 60 s por cada prueba. La calidad final se mide siempre con el modelo grande.

### 3.3. Verificar

```powershell
ollama list                                          # debe aparecer qwen2.5vl:7b
```

### 3.4. Optimizar Ollama para CPU (Intel i5-12450H)

```powershell
setx OLLAMA_NUM_THREADS 8
setx OLLAMA_KEEP_ALIVE 30m
```

- `OLLAMA_NUM_THREADS=8`: usa los 8 cores físicos, deja margen al SO.
- `OLLAMA_KEEP_ALIVE=30m`: mantiene el modelo cargado en RAM entre llamadas (evita la penalización de carga, que en CPU son ~20–30 s).

Después: **reiniciar el servicio Ollama** (cerrar y reabrir el icono de la bandeja).

---

## 4. Estructura del proyecto

```
invoice-extractor/
├── CLAUDE.md                 # Este fichero
├── README.md                 # Resumen breve para humanos
├── .gitignore
├── .infisical.json           # Workspace pointer (NO contiene secretos; SÍ se commitea)
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── config.py             # Settings desde os.environ (poblado por `infisical run`)
│   ├── schemas.py            # Modelos Pydantic (Invoice, LineItem, ExtractionResult, PipelineMode)
│   ├── storage.py            # Wrapper de R2 (subir/bajar/listar)
│   ├── pdf_utils.py          # PDF → imagen PNG (pypdfium2)
│   ├── vision_local.py       # Cliente Ollama para Qwen 2.5-VL
│   ├── llm_fallback.py       # Cliente Anthropic para Claude
│   ├── validator.py          # Reglas: CIF, base+IVA=total, fechas, completitud
│   ├── prompts.py            # Plantillas de prompts (sistema + usuario)
│   └── extractor.py          # Orquestador con lógica de modos
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   └── sample_invoice.pdf
│   ├── test_config.py
│   ├── test_validator.py
│   ├── test_pdf_utils.py
│   ├── test_vision_local.py
│   ├── test_llm_fallback.py
│   ├── test_extractor_modes.py   # Tests específicos por modo
│   └── test_extractor.py
├── data/
│   ├── input/                # Facturas de prueba (NO subir reales a git)
│   └── output/               # JSON extraídos
└── scripts/
    ├── process_one.py        # CLI: procesa una factura (acepta --mode)
    └── run_eval.py           # Evalúa accuracy sobre un dataset (acepta --mode)
```

---

## 5. Dependencias Python

Contenido de `requirements.txt`:

```
ollama>=0.4.0
anthropic>=0.40.0
pypdfium2>=4.30.0
Pillow>=10.0.0
boto3>=1.34.0
pydantic>=2.7.0
```

**Cambios respecto a versiones anteriores**:
- ❌ Eliminados: `pytesseract`, `pdf2image`, `python-dotenv`, `pydantic-settings`, `infisicalsdk`.
- ✅ Añadidos: `anthropic` (SDK de Claude), `pypdfium2` (PDF→imagen sin Poppler).
- 📝 Infisical no aparece como dependencia Python: los secretos los inyecta la CLI como variables de entorno antes de que Python arranque. Esto reduce la superficie de dependencias y hace el código portable a cualquier gestor de secretos que inyecte env vars (Doppler, AWS Secrets Manager, K8s Secrets, etc.).

Instalación (con entorno virtual):

```powershell
cd invoice-extractor
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Para dev/testing:

```powershell
pip install pytest pytest-mock
```

---

## 6. Configuración: secretos en Infisical

**Reglas**:

- **NO existe ningún fichero `.env`** en el proyecto. Los secretos viven solo en Infisical y se inyectan en memoria al ejecutar `infisical run --`.
- El único fichero que sabe de Infisical en el repo es `.infisical.json` (workspaceId + entorno por defecto), generado por `infisical init` en la sección 2.5. **NO contiene secretos.**
- Los secretos se cargan ejecutando los scripts así:

  ```powershell
  infisical run -- python scripts/process_one.py data/input/factura.pdf
  ```

  La CLI fetchea todos los secretos del entorno por defecto de `.infisical.json`, los pone como variables de entorno en el proceso hijo, y arranca Python. Cuando el proceso termina, los secretos desaparecen (nunca tocan disco).

Secretos que el usuario debe crear en Infisical (panel: Project → Secrets → entorno `dev` → Add Secret):

| Nombre del secreto | Valor de ejemplo | Notas |
|---|---|---|
| `R2_ACCOUNT_ID` | `abc123…` | ID de cuenta de Cloudflare |
| `R2_ACCESS_KEY_ID` | `...` | |
| `R2_SECRET_ACCESS_KEY` | `...` | |
| `R2_BUCKET_NAME` | `invoices-bucket` | |
| `R2_ENDPOINT_URL` | `https://<account_id>.r2.cloudflarestorage.com` | |
| `OLLAMA_HOST` | `http://localhost:11434` | |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | |
| `OLLAMA_TIMEOUT_SECONDS` | `180` | |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | |
| `ANTHROPIC_TIMEOUT_SECONDS` | `60` | |
| `PIPELINE_MODE` | `solo_local` | **Default actual**. Cambiar a `hibrido` al pasar a producción. |
| `VALIDATION_TOLERANCE_EUR` | `0.02` | |
| `PDF_RENDER_DPI` | `200` | |

Alternativamente, los secretos pueden crearse desde la CLI sin abrir el navegador:

```powershell
infisical secrets set PIPELINE_MODE=solo_local
infisical secrets set ANTHROPIC_MODEL=claude-sonnet-4-6
# ...
```

Verificación de que todos están bien creados:

```powershell
infisical secrets                                    # lista todos los secretos del entorno por defecto
```

Contenido de `.gitignore`:

```
.venv/
__pycache__/
*.pyc
data/input/*
data/output/*
!data/input/.gitkeep
!data/output/.gitkeep
.env                                                 # por seguridad, aunque no debería existir
```

**No incluir** `.infisical.json` en `.gitignore`: ese fichero sí va a git.

---

## 7. `src/config.py` — settings desde variables de entorno + override por CLI

Con la CLI inyectando los secretos como variables de entorno, `config.py` se vuelve trivial: solo lee `os.environ` y los valida con Pydantic. **No hay llamadas a Infisical desde Python**.

Esqueleto que debe implementar el agente:

```python
# src/config.py
import os
import sys
from enum import Enum
from functools import lru_cache
from pydantic import BaseModel, Field, ValidationError

class PipelineMode(str, Enum):
    SOLO_LOCAL = "solo_local"
    HIBRIDO = "hibrido"
    SOLO_CLAUDE = "solo_claude"

class Settings(BaseModel):
    # Cloudflare R2
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket_name: str
    r2_endpoint_url: str
    # Ollama
    ollama_host: str
    ollama_vision_model: str
    ollama_timeout_seconds: int
    # Anthropic
    anthropic_api_key: str
    anthropic_model: str
    anthropic_timeout_seconds: int
    # Pipeline
    pipeline_mode: PipelineMode
    validation_tolerance_eur: float = Field(0.02, ge=0)
    pdf_render_dpi: int = Field(200, ge=72, le=600)

_REQUIRED_ENV_VARS = (
    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME", "R2_ENDPOINT_URL",
    "OLLAMA_HOST", "OLLAMA_VISION_MODEL", "OLLAMA_TIMEOUT_SECONDS",
    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_TIMEOUT_SECONDS",
    "PIPELINE_MODE",
)

def _check_env_present() -> None:
    """Falla con un mensaje útil si parece que el script no se ejecutó con `infisical run`."""
    missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        sys.exit(
            "❌ Faltan variables de entorno: " + ", ".join(missing) + "\n"
            "\n"
            "   Probablemente NO has lanzado el script con la CLI de Infisical.\n"
            "   Ejecuta así (sin .env, sin export manual):\n"
            "\n"
            "       infisical run -- python <script>\n"
            "\n"
            "   Si ya lo hiciste, comprueba que esos secretos existen en Infisical\n"
            "   con:  infisical secrets\n"
        )

@lru_cache(maxsize=1)
def get_settings(mode_override: PipelineMode | None = None) -> Settings:
    _check_env_present()
    try:
        settings = Settings(
            r2_account_id=os.environ["R2_ACCOUNT_ID"],
            r2_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            r2_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            r2_bucket_name=os.environ["R2_BUCKET_NAME"],
            r2_endpoint_url=os.environ["R2_ENDPOINT_URL"],
            ollama_host=os.environ["OLLAMA_HOST"],
            ollama_vision_model=os.environ["OLLAMA_VISION_MODEL"],
            ollama_timeout_seconds=int(os.environ["OLLAMA_TIMEOUT_SECONDS"]),
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            anthropic_model=os.environ["ANTHROPIC_MODEL"],
            anthropic_timeout_seconds=int(os.environ["ANTHROPIC_TIMEOUT_SECONDS"]),
            pipeline_mode=PipelineMode(os.environ["PIPELINE_MODE"]),
            validation_tolerance_eur=float(os.getenv("VALIDATION_TOLERANCE_EUR", "0.02")),
            pdf_render_dpi=int(os.getenv("PDF_RENDER_DPI", "200")),
        )
    except (ValidationError, ValueError) as e:
        sys.exit(f"❌ Error validando configuración: {e}")
    if mode_override is not None:
        settings = settings.model_copy(update={"pipeline_mode": mode_override})
    return settings
```

**Cómo lo usa el resto del código**:

```python
from src.config import get_settings, PipelineMode
settings = get_settings()                            # usa PIPELINE_MODE del entorno
# o, desde scripts/, con override:
settings = get_settings(mode_override=PipelineMode.HIBRIDO)
```

**Test obligatorio en `tests/test_config.py`**: usar `monkeypatch.setenv` para poblar el entorno simulado, llamar `get_settings.cache_clear()` antes de cada test, y verificar:
- Carga correcta con todas las variables presentes.
- Mensaje de error útil cuando falta alguna.
- `mode_override` sobreescribe correctamente.

---

## 8. Convenciones de código

1. **Tipado estricto**: type hints en todas las funciones públicas. Datos estructurados como `pydantic.BaseModel`, nunca `dict` suelto.
2. **Configuración**: solo `src/config.py` lee `os.environ`. El resto del código importa `from src.config import get_settings`. **Nunca** llamar `os.getenv` fuera de `config.py`.
3. **Llamadas a Ollama**: cliente oficial `ollama` (`from ollama import Client`) con `format='json'`. Las imágenes se pasan como `images=[bytes_o_path]`.
4. **Llamadas a Claude**: SDK oficial `anthropic` (`from anthropic import Anthropic`). La imagen se pasa como bloque `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}}`.
5. **Errores**: cada capa define sus excepciones (`ConfigError`, `StorageError`, `VisionError`, `FallbackError`, `ValidationError`). El orquestador `extractor.py` las captura y registra contexto.
6. **Logging**: módulo `logging` estándar con nivel `INFO` por defecto. **Nunca** `print()` en código de producción (sí en `scripts/`).
7. **Tests**: pytest. Cada wrapper tiene su test con mocks. `validator.py` tiene tests unitarios exhaustivos. `test_extractor_modes.py` verifica el comportamiento de cada modo con vision y fallback mockeados.
8. **Manejo de imágenes**: nunca cargar imágenes >5 MB en memoria sin redimensionar. Si la conversión de PDF produce una imagen mayor, reescalar a 2200 px de lado mayor manteniendo proporción.
9. **Caché de settings**: gracias al `@lru_cache(maxsize=1)` en `get_settings()`, la primera llamada construye el objeto y las siguientes son instantáneas. Tests resetean con `get_settings.cache_clear()`.

---

## 9. Esquemas (`src/schemas.py`)

```python
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, Field

class ExtractionSource(str, Enum):
    LOCAL = "local"           # Qwen 2.5-VL
    FALLBACK = "fallback"     # Claude API

class LineItem(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal

class Invoice(BaseModel):
    invoice_number: str
    issue_date: date | None = None
    due_date: date | None = None
    supplier_name: str
    supplier_tax_id: str | None = Field(None, description="CIF/NIF del emisor")
    customer_name: str | None = None
    customer_tax_id: str | None = None
    subtotal: Decimal | None = None
    tax_rate: Decimal | None = Field(None, description="IVA en %, p. ej. 21")
    tax_amount: Decimal | None = None
    total: Decimal
    currency: str = "EUR"
    line_items: list[LineItem] = []

class ValidationReport(BaseModel):
    is_valid: bool
    cif_valid: bool | None = None
    totals_consistent: bool | None = None
    date_in_range: bool | None = None
    required_fields_present: bool
    errors: list[str] = []
    warnings: list[str] = []

class ExtractionResult(BaseModel):
    invoice: Invoice
    source: ExtractionSource          # qué modelo produjo el resultado final
    fallback_used: bool               # solo true en modo hibrido cuando local falló
    fallback_reason: str | None = None
    validation: ValidationReport
    processing_time_seconds: float
    model_used: str                   # "qwen2.5vl:7b" o "claude-sonnet-4-6"
    extracted_at: datetime
    pipeline_mode: str                # qué modo se usó (auditoría)
```

---

## 10. Prompt del LLM (`src/prompts.py`)

**Mismo prompt** para el modelo local y el fallback (cambia solo el cliente que lo envía), para garantizar consistencia.

```python
SYSTEM_PROMPT = """Eres un extractor experto de datos de facturas españolas.
Recibes la IMAGEN de una factura y devuelves EXCLUSIVAMENTE un objeto JSON
que cumple este esquema (sin texto adicional, sin markdown, sin comentarios):

{
  "invoice_number": "string",
  "issue_date": "YYYY-MM-DD o null",
  "due_date": "YYYY-MM-DD o null",
  "supplier_name": "string",
  "supplier_tax_id": "string o null (CIF/NIF español, formato: letra+8 dígitos o 8 dígitos+letra)",
  "customer_name": "string o null",
  "customer_tax_id": "string o null",
  "subtotal": "número decimal o null (base imponible)",
  "tax_rate": "número decimal (21, 10, 4...) o null",
  "tax_amount": "número decimal o null (importe del IVA)",
  "total": "número decimal (TOTAL final con IVA)",
  "currency": "ISO 4217 (EUR por defecto)",
  "line_items": [
    {"description": "string", "quantity": "número", "unit_price": "número", "total": "número"}
  ]
}

Reglas estrictas:
- Si un campo no está claramente visible en la imagen, usa null (NUNCA inventes).
- Los decimales con punto, no coma. Convierte "1.234,56" → 1234.56.
- Las fechas siempre en formato ISO YYYY-MM-DD.
- Si hay varios totales (subtotal, IVA, total), devuelve el TOTAL FINAL en el campo "total".
- Para CIF/NIF: respeta el formato exacto que aparece en la factura, sin espacios ni guiones.
- Analiza la POSICIÓN espacial de los elementos: los totales suelen estar abajo a la derecha,
  el emisor arriba a la izquierda, el receptor en bloque separado, las líneas en una tabla central.
"""

USER_PROMPT = "Extrae los datos estructurados de esta factura. Devuelve únicamente el JSON."
```

---

## 11. Capa de validación (`src/validator.py`)

Reglas mínimas a implementar:

1. **Validación de CIF/NIF español** (algoritmo oficial):
   - DNI/NIE: 8 dígitos + letra (módulo 23, tabla `TRWAGMYFPDXBNJZSQVHLCKE`).
   - CIF: letra inicial + 7 dígitos + dígito/letra de control.
   - Si `supplier_tax_id` está presente pero no pasa → `cif_valid = False`.
2. **Coherencia de totales**: si `subtotal` y `tax_amount` están presentes, comprobar `abs((subtotal + tax_amount) - total) <= settings.validation_tolerance_eur`. Si no cuadra → `totals_consistent = False`.
3. **Fecha razonable**: `issue_date` no puede ser futura (>hoy) ni anterior a 2000-01-01.
4. **Campos requeridos**: `invoice_number`, `supplier_name`, `total` siempre obligatorios y no vacíos.
5. **Decisión final**: `is_valid = True` solo si todos los checks aplicables pasan.

Tests obligatorios en `tests/test_validator.py` (mínimo): 5 CIFs válidos + 5 inválidos, 5 NIFs válidos + 5 inválidos, casos límite de tolerancia, fechas futuras/antiguas/válidas.

---

## 12. Orquestador (`src/extractor.py`) — lógica por modo

Pseudocódigo (el agente lo implementa con tipos y manejo de errores):

```python
def extract_invoice(file_path: Path, mode_override: PipelineMode | None = None) -> ExtractionResult:
    settings = get_settings(mode_override=mode_override)
    mode = settings.pipeline_mode

    image_bytes = pdf_or_image_to_png(file_path, dpi=settings.pdf_render_dpi)

    # --- MODO solo_claude: salta el local ---
    if mode == PipelineMode.SOLO_CLAUDE:
        invoice = call_claude(image_bytes)
        report = validate(invoice)
        return build_result(invoice, ExtractionSource.FALLBACK,
                            fallback_used=False, report=report, mode=mode)

    # --- MODOS solo_local y hibrido: primero local ---
    invoice_local = call_vision_local(image_bytes)
    report_local = validate(invoice_local)

    if report_local.is_valid:
        return build_result(invoice_local, ExtractionSource.LOCAL,
                            fallback_used=False, report=report_local, mode=mode)

    # Local falló validación
    if mode == PipelineMode.SOLO_LOCAL:
        # Devolvemos el resultado tal cual, con is_valid=false para auditoría
        return build_result(invoice_local, ExtractionSource.LOCAL,
                            fallback_used=False, report=report_local, mode=mode)

    # mode == HIBRIDO → fallback a Claude
    invoice_remote = call_claude(image_bytes)
    report_remote = validate(invoice_remote)
    return build_result(invoice_remote, ExtractionSource.FALLBACK,
                        fallback_used=True, report=report_remote, mode=mode,
                        fallback_reason="; ".join(report_local.errors))
```

---

## 13. Scripts CLI (`scripts/process_one.py`)

Esqueleto mínimo:

```python
import argparse
import json
from pathlib import Path
from src.config import PipelineMode
from src.extractor import extract_invoice

def main():
    parser = argparse.ArgumentParser(description="Extrae datos de una factura.")
    parser.add_argument("file", type=Path, help="Ruta al PDF/JPG/PNG de la factura.")
    parser.add_argument(
        "--mode",
        type=PipelineMode,
        choices=list(PipelineMode),
        default=None,
        help="Sobreescribe el modo configurado en Infisical (PIPELINE_MODE).",
    )
    args = parser.parse_args()
    result = extract_invoice(args.file, mode_override=args.mode)
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
```

**Ejemplos de uso** (siempre con `infisical run --`):

```powershell
# Usa el modo definido en Infisical (por defecto solo_local)
infisical run -- python scripts/process_one.py data/input/factura.pdf

# Fuerza modo híbrido solo para esta ejecución
infisical run -- python scripts/process_one.py data/input/factura.pdf --mode hibrido

# Compara la salida de los 3 modos sobre la misma factura
infisical run -- python scripts/process_one.py data/input/factura.pdf --mode solo_local   > out_local.json
infisical run -- python scripts/process_one.py data/input/factura.pdf --mode solo_claude  > out_claude.json
infisical run -- python scripts/process_one.py data/input/factura.pdf --mode hibrido      > out_hibrido.json

# Cambiar de entorno puntualmente (ej. usar secretos de staging en lugar de dev)
infisical run --env=staging -- python scripts/process_one.py data/input/factura.pdf
```

**Regla general**: cualquier comando Python que necesite acceso a R2, a Anthropic o al `PIPELINE_MODE` se ejecuta con `infisical run --`. Si lo lanzas sin eso, `config.py` falla con un mensaje útil indicando qué falta.

---

## 14. Comandos de validación

```powershell
# Fase 1: prerrequisitos
python --version
ollama list                                          # debe aparecer qwen2.5vl:7b
infisical user                                       # debe mostrar el usuario logueado
cat .infisical.json                                  # workspaceId y defaultEnvironment

# Fase 2: dependencias instaladas
.venv\Scripts\activate
python -c "import ollama, anthropic, pypdfium2, boto3, pydantic; print('OK')"

# Fase 3: secretos accesibles desde la CLI
infisical secrets                                    # lista todos los secretos del entorno por defecto

# Fase 4: bootstrap de configuración (Python lee del entorno inyectado por la CLI)
infisical run -- python -c "from src.config import get_settings; s=get_settings(); print('mode=', s.pipeline_mode, 'model=', s.ollama_vision_model)"

# Fase 5: conexión a Ollama
infisical run -- python -c "from src.vision_local import ping; print(ping())"

# Fase 6: conexión a Anthropic (sin gastar tokens)
infisical run -- python -c "from src.llm_fallback import ping; print(ping())"

# Fase 7: conexión a R2
infisical run -- python -c "from src.storage import list_objects; print(list_objects(prefix='', max=1))"

# Fase 8: validador (tests unitarios, NO necesitan infisical run porque no usan secretos)
pytest tests/test_validator.py -v

# Fase 9: lógica de modos (mockeado, sin red ni secretos reales)
pytest tests/test_extractor_modes.py -v

# Fase 10: pipeline E2E con factura de prueba (modo por defecto = solo_local)
infisical run -- python scripts/process_one.py data/input/sample_invoice.pdf

# Fase 11: confirmación de que el override por CLI funciona
infisical run -- python scripts/process_one.py data/input/sample_invoice.pdf --mode hibrido
```

---

## 15. Orden de ejecución para el agente

Cuando el usuario diga "monta el proyecto", seguir **exactamente** este orden:

1. Verificar prerrequisitos (sección 2). **PARAR** y avisar al usuario si falta: Python, Ollama, o la CLI de Infisical / login. Esas configuraciones las hace el humano.
2. Verificar que existe `.infisical.json` en la raíz del proyecto. Si no existe, pedir al usuario que ejecute `infisical init` (interactivo, debe elegir su proyecto y entorno `dev`).
3. Descargar modelo(s) Ollama (sección 3).
4. Crear estructura de directorios y ficheros vacíos (sección 4).
5. Generar `requirements.txt`, `.gitignore`, `README.md`.
6. Crear venv e instalar dependencias.
7. **Pedir al usuario** que cree los 14 secretos en Infisical (sección 6) si aún no existen. Verificar con `infisical secrets`.
8. Implementar `src/config.py` y validar con la Fase 4 de la sección 14. Esto confirma a la vez que la CLI inyecta bien las vars y que `config.py` las parsea correctamente.
9. Implementar `src/schemas.py`, `src/prompts.py`.
10. Implementar `src/validator.py` con su batería completa de tests; ejecutar tests (DEBEN pasar antes de seguir).
11. Implementar `src/pdf_utils.py` con su test (PDF de prueba → imagen PNG válida).
12. Implementar `src/vision_local.py` con su test (mockear cliente ollama).
13. Implementar `src/llm_fallback.py` con su test (mockear cliente anthropic).
14. Implementar `src/storage.py` con su test (mockear boto3).
15. Implementar `src/extractor.py` con la lógica de los 3 modos. Implementar **antes** `tests/test_extractor_modes.py` con un test por modo (TDD ligero):
    - `solo_local` + validación OK → `fallback_used=False`, `source=LOCAL`.
    - `solo_local` + validación falla → `fallback_used=False`, `source=LOCAL`, `validation.is_valid=False` (NO se llama a Claude, verificar con mock).
    - `hibrido` + validación local OK → `fallback_used=False`.
    - `hibrido` + validación local falla → `fallback_used=True`, `source=FALLBACK`, Claude llamado 1 vez.
    - `solo_claude` → Claude llamado 1 vez, vision_local NO llamado.
16. Implementar `scripts/process_one.py` con el flag `--mode`.
17. Pedir al usuario una factura de prueba, colocarla en `data/input/`, y ejecutar:
    - Primero `infisical run -- python scripts/process_one.py data/input/factura.pdf` (debe usar el default `solo_local` del secreto).
    - Luego `... --mode hibrido` para confirmar que el override funciona.
18. Mostrar ambos `ExtractionResult` y comparar el campo `pipeline_mode` para confirmar el override.

**No avanzar a la siguiente fase si la anterior no pasa su comando de validación.**

---

## 16. Recursos y enlaces de referencia

- Ollama: https://ollama.com/
- Modelo Qwen 2.5-VL en Ollama: https://ollama.com/library/qwen2.5vl
- Paper técnico de Qwen 2.5-VL: https://arxiv.org/abs/2502.13923
- SDK Python de Anthropic: https://github.com/anthropics/anthropic-sdk-python
- Documentación de la API de Anthropic: https://docs.claude.com/
- **Infisical CLI — Visión general**: https://infisical.com/docs/cli/overview
- **Infisical CLI — comando `run`**: https://infisical.com/docs/cli/commands/run
- **Infisical CLI — comando `init`**: https://infisical.com/docs/cli/commands/init
- pypdfium2 (PDF→imagen sin Poppler): https://github.com/pypdfium2-team/pypdfium2
- Cloudflare R2 con boto3: https://developers.cloudflare.com/r2/api/s3/tokens/
- Pydantic v2: https://docs.pydantic.dev/latest/
- Algoritmo validación CIF/NIF (referencia): https://es.wikipedia.org/wiki/N%C3%BAmero_de_identificaci%C3%B3n_fiscal

---

## 17. Notas de mantenimiento y evolución

- **Cambiar de modo no requiere desplegar código**. Basta con editar el secreto `PIPELINE_MODE` en Infisical (`solo_local` → `hibrido` cuando pases a producción), o usar el flag `--mode` puntualmente.
- **Cambiar de entorno (dev/staging/prod)**: añadir `--env=<entorno>` a la llamada de `infisical run`. Por ejemplo:
  ```powershell
  infisical run --env=prod -- python scripts/process_one.py factura.pdf
  ```
  Cero cambios en el código Python.
- **Para A/B tests** o evaluaciones por lote, usa el flag `--mode` en `scripts/run_eval.py` para ejecutar el mismo dataset con cada modo y comparar accuracy / coste / tiempo sin tocar Infisical.
- **Migración a producción (CI/CD, Docker, servidores sin login interactivo)**: NO se cambia el código. Solo cambia la forma de autenticarse a Infisical. En el servidor de producción:
  1. Crear una **Machine Identity** en Infisical (Universal Auth) con permisos de lectura sobre el entorno `prod`.
  2. Setear dos variables en el entorno del servidor: `INFISICAL_UNIVERSAL_AUTH_CLIENT_ID` y `INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET`.
  3. Obtener el token y ejecutar el script:
     ```bash
     export INFISICAL_TOKEN=$(infisical login --method=universal-auth \
         --client-id=$INFISICAL_UNIVERSAL_AUTH_CLIENT_ID \
         --client-secret=$INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET \
         --silent --plain)
     infisical run --env=prod -- python scripts/process_one.py factura.pdf
     ```
  El código Python no se entera de nada: sigue leyendo `os.environ`.
- **Si los evals dan <90% de accuracy global tras validación + fallback**, considerar fine-tuning de Qwen 2.5-VL con LoRA sobre 200–500 facturas etiquetadas (fase 2, NO en este proyecto).
- **Si el ratio de fallback es muy alto** (>30% de facturas terminan en Claude API en modo híbrido), revisar primero el prompt y el `PDF_RENDER_DPI` (subir a 250) antes de cambiar de modelo local.
- **Si el usuario actualiza a un equipo con GPU NVIDIA**, solo se cambia el secreto `OLLAMA_VISION_MODEL` en Infisical (p. ej. a `qwen2.5vl:32b` si tiene ≥24 GB VRAM). Cero cambios de código.
- **Para depurar prompts**, cambiar temporalmente `OLLAMA_VISION_MODEL` a `qwen2.5vl:3b` en Infisical (más rápido). Volver a `qwen2.5vl:7b` antes de cualquier eval real.
- **Rotación de claves**: como están en Infisical, basta con cambiar el valor del secreto. En el siguiente arranque de `infisical run` se inyecta el nuevo valor automáticamente.
- **Costes esperados en modo `hibrido`**: con un ratio de fallback del 15%, procesando 1000 facturas/mes, el coste de Claude API debería estar en torno a 1–3 €/mes con Sonnet. La parte local es gratis (solo electricidad).
