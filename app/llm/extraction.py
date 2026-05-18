"""Extracción estructurada de facturas (PDF / imagen) vía cliente LLM."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import structlog
from instructor.processing.multimodal import PDF, Image

from app.llm.client import get_llm_client
from app.llm.prompts_loader import load_prompt
from app.schemas.invoice import Factura

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

PROMPT_VERSION = "extraction_v1"


def _media_part(file_bytes: bytes, mime_type: str) -> Image | PDF:
    """Parte multimodal usando tipos Instructor (Anthropic + GenAI)."""
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if mime_type == "application/pdf":
        return PDF.from_raw_base64(b64)
    if mime_type in {"image/jpeg", "image/png", "image/webp"}:
        return Image.from_raw_base64(b64)
    msg = f"Unsupported mime type: {mime_type}"
    raise ValueError(msg)


def _build_extraction_messages(
    *, system_prompt: str, file_bytes: bytes, mime_type: str
) -> list[dict[str, Any]]:
    media = _media_part(file_bytes, mime_type)
    instruction = "Extrae los datos de esta factura. Devuelve el JSON conforme al schema indicado por el modelo."
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
) -> Factura:
    """Extrae datos estructurados de una factura usando el router LLM (`task='extraction'`)."""
    if len(file_bytes) > 20 * 1024 * 1024:
        raise ValueError("File too large (>20MB)")

    messages = _build_extraction_messages(
        system_prompt=load_prompt(PROMPT_VERSION),
        file_bytes=file_bytes,
        mime_type=mime_type,
    )
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
