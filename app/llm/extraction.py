"""Extracción estructurada de facturas (PDF / imagen) vía cliente LLM."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

import structlog
from instructor.processing.multimodal import PDF, Image

from app.llm.client import get_llm_client
from app.llm.prompts_loader import load_prompt
from app.schemas.invoice import Factura

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Constante de versión del prompt en el módulo: punto único de cambio cuando
# se publique una nueva versión. Debe coincidir con el nombre del fichero en
# app/llm/prompts/ (extraction_v1.txt). Cambiar aquí automáticamente actualiza
# el campo prompt_version en llm_calls, permitiendo correlacionar resultados
# con la versión del prompt en el dashboard de métricas.
PROMPT_VERSION = "extraction_v1"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Datos extraídos y referencia a la llamada LLM asociada."""

    factura: Factura
    llm_call_id: UUID


def _media_part(file_bytes: bytes, mime_type: str) -> Image | PDF:
    """Parte multimodal usando tipos Instructor (Anthropic + GenAI).

    Instructor abstrae las diferencias de formato entre Anthropic y Google
    para contenido multimodal. PDF y Image son wrappers que cada proveedor
    serializa según su API interna.
    """
    # standard_b64encode (no urlsafe): tanto Anthropic como Google esperan
    # base64 estándar (con +/ y padding =) para contenido binario embebido.
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if mime_type == "application/pdf":
        # PDF.from_raw_base64: Instructor construye la parte multimodal PDF
        # en el formato correcto para cada proveedor (document_block en Anthropic,
        # inline_data en Google).
        return PDF.from_raw_base64(b64)
    if mime_type in {"image/jpeg", "image/png", "image/webp"}:
        # Los tres formatos de imagen cubren los casos más comunes de facturas
        # fotografiadas. Deben coincidir con los tipos aceptados en core/uploads.py.
        return Image.from_raw_base64(b64)
    msg = f"Unsupported mime type: {mime_type}"
    raise ValueError(msg)


def _build_extraction_messages(
    *, system_prompt: str, file_bytes: bytes, mime_type: str
) -> list[dict[str, Any]]:
    media = _media_part(file_bytes, mime_type)
    # Instrucción breve en el mensaje de usuario: el system prompt ya describe
    # el schema y el comportamiento esperado; el mensaje de usuario solo
    # indica la acción sobre el documento adjunto.
    instruction = "Extrae los datos de esta factura. Devuelve el JSON conforme al schema indicado por el modelo."
    return [
        # Rol "system": define el contexto del asistente (quién es, qué schema
        # debe seguir, cómo manejar la confianza). Viene del prompt versionado.
        {"role": "system", "content": system_prompt},
        {
            # Rol "user": lleva el documento y la instrucción.
            # El media va primero en la lista de contenido porque los modelos
            # multimodales procesan mejor el documento antes de la pregunta.
            "role": "user",
            "content": [
                media,
                instruction,
            ],
        },
    ]


async def extract_invoice(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    db: AsyncSession,
) -> ExtractionResult:
    """Extrae datos estructurados de una factura usando el router LLM (`task='extraction'`)."""
    # Límite de 20 MB: Anthropic rechaza payloads multimodales mayores. Se
    # valida aquí (antes de la llamada HTTP) para dar un error claro y evitar
    # el coste de red de subir un fichero que será rechazado igualmente.
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
        # max_retries=2: Instructor reintenta si el LLM no devuelve JSON
        # válido conforme al schema Factura. 2 reintentos = hasta 3 intentos
        # totales. Más reintentos aumentarían el coste sin mejora significativa.
        max_retries=2,
    )
    factura = completion.result
    # Se loguea solo los campos clave (no el objeto completo) para mantener
    # los logs legibles y evitar loguear datos sensibles de la factura.
    logger.info(
        "extraction.done",
        tenant_id=str(tenant_id),
        llm_call_id=str(completion.llm_call_id),
        proveedor=factura.proveedor,
        total=str(factura.total),
        confidence=factura.confidence,
    )
    return ExtractionResult(factura=factura, llm_call_id=completion.llm_call_id)
