# Paso 12 — Schema Pydantic `Factura` y función `extract_invoice()`

## Objetivo

Implementar el corazón del módulo 1: una función pura que recibe los bytes de un fichero (PDF, JPG, PNG, WEBP) más su MIME type y devuelve un objeto Pydantic `Factura` con los datos estructurados. Por debajo usa el cliente LLM del Paso 10 con Instructor para garantizar el formato.

Al final del paso, un test que recibe una factura real de fixtures devuelve una `Factura` válida, con la fila correspondiente en `llm_calls` y traza en Langfuse.

## Pre-requisitos

- Pasos 01-11 completados.
- Carpeta `tests/fixtures/invoices/` con al menos 3 facturas reales (PDFs e imágenes). Anonimízalas si vienen de proveedores reales.

## Contexto relevante

- `arquitectura.md` sección 6.1 (Módulo 1 — Extractor): schema Pydantic, modelo por defecto Gemini Flash, métricas.
- `Agents.md`: prompts versionados, Instructor para output estructurado, multimodal LLM directo (sin OCR previo).

## Tareas

- [ ] Crear `app/schemas/invoice.py` con `Factura` y `LineaFactura`.
- [ ] Exportar desde `app/schemas/__init__.py`.
- [ ] Crear prompt `app/llm/prompts/extraction_v1.txt`.
- [ ] Crear `app/llm/extraction.py` con función `extract_invoice()`.
- [ ] Adaptar `LLMClient.complete()` para aceptar contenido multimodal (lista de bloques `text` / `image` / `document`).
- [ ] Tests unitarios con mock del LLM en `tests/unit/test_extraction.py`.
- [ ] Test de integración con fixture real en `tests/integration/test_extraction_real.py` (gated).
- [ ] Commit: `feat: invoice extraction with instructor and gemini flash`.

## Detalles técnicos

### `app/schemas/invoice.py`

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class LineaFactura(BaseModel):
    descripcion: str = Field(description="Descripción del concepto o producto")
    cantidad: Decimal = Field(gt=0, description="Cantidad o unidades")
    precio_unitario: Decimal = Field(ge=0, description="Precio por unidad sin IVA")
    total: Decimal = Field(ge=0, description="Total de la línea (cantidad x precio)")


class Factura(BaseModel):
    fecha: date = Field(description="Fecha de emisión de la factura")
    proveedor: str = Field(description="Razón social o nombre del emisor")
    cif_nif: str = Field(
        description="CIF, NIF o NIE del emisor en España",
        pattern=r"^[A-Z0-9]{8,10}$",
    )
    numero_factura: str | None = Field(
        default=None, description="Número o serie de factura si aparece"
    )

    base_imponible: Decimal = Field(ge=0, description="Suma sin IVA")
    iva_percent: Decimal = Field(
        ge=0, le=100, description="Porcentaje IVA aplicado (0, 4, 10, 21)"
    )
    iva_amount: Decimal = Field(ge=0, description="Importe del IVA en euros")
    total: Decimal = Field(ge=0, description="Total final con IVA")
    currency: str = Field(default="EUR", description="ISO 4217, normalmente EUR")

    lineas: list[LineaFactura] = Field(
        default_factory=list, description="Líneas de detalle si aparecen"
    )

    confidence: float = Field(
        ge=0, le=1,
        description="Tu confianza global en la extracción (0=incierto, 1=seguro)",
    )

    @model_validator(mode="after")
    def _check_totals_coherent(self) -> "Factura":
        # Tolerancia 1 céntimo por redondeos
        suma = (self.base_imponible + self.iva_amount).quantize(Decimal("0.01"))
        total = self.total.quantize(Decimal("0.01"))
        if abs(suma - total) > Decimal("0.01"):
            # No fallamos: dejamos que el LLM lo intente, pero bajamos confidence
            object.__setattr__(self, "confidence", min(self.confidence, 0.5))
        return self
```

### `app/llm/prompts/extraction_v1.txt`

```
Eres un experto en extracción de datos de facturas españolas.

Recibes una factura como imagen o PDF. Tu tarea es extraer los datos
estructurados con la máxima precisión posible.

Reglas:
- Las fechas en formato ISO (YYYY-MM-DD).
- Los importes como números decimales con punto. Sin separadores de miles.
- El CIF/NIF en mayúsculas, sin espacios ni guiones.
- Si un campo no aparece claramente en el documento, devuelve null cuando
  el schema lo permita. NO inventes datos.
- Para el porcentaje de IVA, devuelve el valor más alto cuando hay varios
  (por ejemplo, si hay líneas al 10% y al 21%, devuelve 21).
- En `confidence` reporta tu certeza real: si el documento es ilegible o
  faltan datos críticos (CIF, total), baja la confidence a 0.3 o menos.
- Si el documento NO es una factura (es un albarán, un recibo no fiscal,
  una nota de gastos), establece confidence a 0.1 y rellena lo que puedas.

Devuelve EXCLUSIVAMENTE el JSON con el formato pedido. Nada más.
```

### `app/llm/extraction.py`

```python
from __future__ import annotations

import base64
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import get_llm_client, load_prompt
from app.schemas.invoice import Factura

logger = structlog.get_logger(__name__)

PROMPT_VERSION = "extraction_v1"


def _build_multimodal_message(file_bytes: bytes, mime_type: str) -> list[dict]:
    """Construye el contenido multimodal en formato Anthropic.

    Para Google Gemini, el cliente LLM debe traducir este formato al suyo.
    """
    system_prompt = load_prompt(PROMPT_VERSION)
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")

    if mime_type == "application/pdf":
        block_type = "document"
        media_type = "application/pdf"
    elif mime_type in {"image/jpeg", "image/png", "image/webp"}:
        block_type = "image"
        media_type = mime_type
    else:
        raise ValueError(f"Unsupported mime type: {mime_type}")

    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": block_type,
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": "Extrae los datos de esta factura.",
                },
            ],
        },
    ]


async def extract_invoice(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> Factura:
    """Extrae datos estructurados de una factura usando el LLM por defecto."""
    if len(file_bytes) > 20 * 1024 * 1024:
        raise ValueError("File too large (>20MB)")

    messages = _build_multimodal_message(file_bytes, mime_type)
    client = get_llm_client()

    factura = await client.complete(
        task="extraction",
        messages=messages,
        response_model=Factura,
        tenant_id=tenant_id,
        db=db,
        prompt_version=PROMPT_VERSION,
        max_retries=2,
    )
    logger.info(
        "extraction.done",
        tenant_id=str(tenant_id),
        proveedor=factura.proveedor,
        total=str(factura.total),
        confidence=factura.confidence,
    )
    return factura
```

### Ajuste del `LLMClient` para multimodal

Si tu `complete()` actual asume `content: str`, ajústalo para que acepte también `content: list[dict]` y lo pase tal cual al SDK. Tanto Anthropic como Google aceptan listas de bloques multimodales; Instructor las respeta.

Si Gemini exige un formato distinto (no `image`/`document` sino `inline_data`), añade un branch en `_resolve_model` que transforme el message antes de enviar. Mantén el contrato externo: la lista de bloques siempre en formato Anthropic-like, y el cliente la traduce internamente si toca Google.

### Test unitario con mock

`tests/unit/test_extraction.py`:

```python
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.extraction import extract_invoice
from app.schemas.invoice import Factura


@pytest.mark.asyncio
async def test_extract_invoice_calls_llm_with_correct_args(db_session, tenant_factory):
    tenant = await tenant_factory()
    fake_factura = Factura(
        fecha="2025-01-15",
        proveedor="Acme S.L.",
        cif_nif="00000000T",  # NIF/CIF ficticio de ejemplo (tests)
        base_imponible=Decimal("100.00"),
        iva_percent=Decimal("21.00"),
        iva_amount=Decimal("21.00"),
        total=Decimal("121.00"),
        confidence=0.95,
    )

    mock_client = AsyncMock()
    mock_client.complete.return_value = fake_factura

    with patch("app.llm.extraction.get_llm_client", return_value=mock_client):
        result = await extract_invoice(
            file_bytes=b"fake-pdf",
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )

    assert result.proveedor == "Acme S.L."
    mock_client.complete.assert_awaited_once()
    kwargs = mock_client.complete.await_args.kwargs
    assert kwargs["task"] == "extraction"
    assert kwargs["response_model"] is Factura
    assert kwargs["prompt_version"] == "extraction_v1"


@pytest.mark.asyncio
async def test_extract_invoice_rejects_huge_files(db_session, tenant_factory):
    tenant = await tenant_factory()
    huge = b"x" * (21 * 1024 * 1024)
    with pytest.raises(ValueError, match="too large"):
        await extract_invoice(
            file_bytes=huge,
            mime_type="application/pdf",
            tenant_id=tenant.id,
            db=db_session,
        )
```

### Test de integración con factura real

`tests/integration/test_extraction_real.py`:

```python
import os
from pathlib import Path

import pytest

from app.llm.extraction import extract_invoice

FIXTURES = Path(__file__).parent.parent / "fixtures" / "invoices"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("RUN_LLM_TESTS"), reason="set RUN_LLM_TESTS=1 to enable"
)
async def test_extract_real_invoice(db_session, tenant_factory):
    tenant = await tenant_factory()
    pdf_path = FIXTURES / "ejemplo_01.pdf"
    file_bytes = pdf_path.read_bytes()

    factura = await extract_invoice(
        file_bytes=file_bytes,
        mime_type="application/pdf",
        tenant_id=tenant.id,
        db=db_session,
    )
    await db_session.commit()

    assert factura.proveedor
    assert factura.cif_nif
    assert factura.total > 0
    assert factura.confidence > 0.5
```

## Criterios de aceptación

- Los tests unitarios pasan sin tocar la red.
- Con `RUN_LLM_TESTS=1`, una factura real se extrae correctamente.
- En Langfuse aparece la traza `llm.extraction` con tokens y coste.
- Hay una fila en `llm_calls` con `task='extraction'` y `prompt_version='extraction_v1'`.
- `mypy --strict` pasa: ningún `Any` colado.
- `ruff` pasa.

## Lo que NO toca este paso

- Endpoint HTTP de upload (Paso 13).
- Worker en background (Paso 14).
- Guardar la `Factura` extraída en la tabla `invoices` (Paso 14).
- Eval set con métricas (Paso 15).

## Posibles problemas

- **Instructor con multimodal en Google**: si la versión de Instructor no soporta limpiamente bloques `image` para Gemini, valora dos opciones: (1) reformatear el mensaje en `LLMClient` cuando `provider == "google"` usando `genai.types.Part.from_bytes(...)`; (2) usar Anthropic Haiku como modelo por defecto de `extraction` temporalmente y dejar Gemini para una siguiente iteración.
- **PDFs grandes (>10MB)**: Anthropic acepta hasta 32MB; Gemini Flash hasta 20MB. El límite de 20MB en `extract_invoice` es deliberadamente conservador.
- **CIF inválido detectado por el `pattern`**: el `pattern` `^[A-Z0-9]{8,10}$` es laxo a propósito (acepta NIE como `X1234567L`). Si necesitas validación rigurosa de letra de control, hazla en `services/invoice_service.py` al persistir, no en el schema (no quieres que el LLM falle el extract por una letra mal).

## Siguiente paso

`Paso13.md` — UI de subida: modal con dropzone HTMX + Alpine.js, endpoint `POST /invoices/upload`, creación de `Invoice` stub, subida a R2 y encolado de job (los jobs no se procesan aún; eso es Paso 14).
