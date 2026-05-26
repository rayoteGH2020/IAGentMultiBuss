"""Extracción estructurada de facturas y tickets (PDF / imagen) vía cliente LLM."""

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
from app.schemas.ticket import TicketRecibo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Constante de versión del prompt en el módulo: punto único de cambio cuando
# se publique una nueva versión. Debe coincidir con el nombre del fichero en
# app/llm/prompts/ (extraction_v1.txt). Cambiar aquí automáticamente actualiza
# el campo prompt_version en llm_calls, permitiendo correlacionar resultados
# con la versión del prompt en el dashboard de métricas.
PROMPT_VERSION = "extraction_v1"
TICKET_PROMPT_VERSION = "ticket_extraction_v1"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Datos extraídos de factura y referencia a la llamada LLM asociada."""

    factura: Factura
    llm_call_id: UUID


@dataclass(frozen=True, slots=True)
class TicketExtractionResult:
    """Datos extraídos de ticket y referencia a la llamada LLM asociada."""

    ticket: TicketRecibo
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
    *,
    system_prompt: str,
    file_bytes: bytes,
    mime_type: str,
    instruction: str,
) -> list[dict[str, Any]]:
    media = _media_part(file_bytes, mime_type)
    return [
        {"role": "system", "content": system_prompt},
        {
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
    source_filename: str | None = None,
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
        instruction=(
            "Extrae los datos de esta factura. Devuelve el JSON conforme al schema indicado."
        ),
    )
    client = get_llm_client()

    completion = await client.complete(
        task="extraction",
        messages=messages,
        response_model=Factura,
        tenant_id=tenant_id,
        db=db,
        prompt_version=PROMPT_VERSION,
        source_filename=source_filename,
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


async def extract_ticket(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    db: AsyncSession,
    source_filename: str | None = None,
) -> TicketExtractionResult:
    """Extrae datos estructurados de un ticket usando el router LLM (`task='extraction'`)."""
    if len(file_bytes) > 20 * 1024 * 1024:
        raise ValueError("File too large (>20MB)")

    messages = _build_extraction_messages(
        system_prompt=load_prompt(TICKET_PROMPT_VERSION),
        file_bytes=file_bytes,
        mime_type=mime_type,
        instruction=(
            "Extrae los datos de este ticket o recibo. Devuelve el JSON conforme al schema."
        ),
    )
    client = get_llm_client()
    completion = await client.complete(
        task="extraction",
        messages=messages,
        response_model=TicketRecibo,
        tenant_id=tenant_id,
        db=db,
        prompt_version=TICKET_PROMPT_VERSION,
        source_filename=source_filename,
        max_retries=2,
    )
    ticket = completion.result
    logger.info(
        "ticket_extraction.done",
        tenant_id=str(tenant_id),
        llm_call_id=str(completion.llm_call_id),
        comercio=ticket.comercio,
        total=str(ticket.total),
        confidence=ticket.confidence,
    )
    return TicketExtractionResult(ticket=ticket, llm_call_id=completion.llm_call_id)
