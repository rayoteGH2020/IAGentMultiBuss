"""Clasificación documental vía LLM multimodal (fallback cuando no hay texto)."""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

import structlog
from instructor.processing.multimodal import PDF, Image

from app.llm.client import get_llm_client
from app.llm.extraction_media import MAX_PAYLOAD_BYTES, prepare_classification_media
from app.llm.prompts_loader import load_prompt
from app.schemas.document_classification import DocumentTypeClassification

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

PROMPT_VERSION = "classification_v1"


def _media_part(file_bytes: bytes, mime_type: str) -> Image | PDF:
    b64 = base64.standard_b64encode(file_bytes).decode("ascii")
    if mime_type == "application/pdf":
        return PDF.from_raw_base64(b64)
    if mime_type in {"image/jpeg", "image/png", "image/webp"}:
        return Image.from_raw_base64(b64)
    msg = f"Unsupported mime type: {mime_type}"
    raise ValueError(msg)


def _build_messages(
    *, system_prompt: str, file_bytes: bytes, mime_type: str
) -> list[dict[str, Any]]:
    media = _media_part(file_bytes, mime_type)
    instruction = (
        "Clasifica este documento como factura o ticket según los criterios del system prompt."
    )
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [media, instruction],
        },
    ]


async def classify_document_with_llm(
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    db: AsyncSession,
    source_filename: str | None = None,
) -> DocumentTypeClassification:
    """Clasifica un documento cuando las heurísticas de texto no aplican."""
    if len(file_bytes) > MAX_PAYLOAD_BYTES:
        raise ValueError("File too large (>20MB)")

    # Sin esto, una foto de 8 MB se enviaba en base64 tal cual y girada: la
    # clasificación pagaba el mismo documento dos veces más caro que la
    # extracción y leía el ticket de lado. Pillow bloquea, de ahí el hilo.
    prepared, prepared_mime = await asyncio.to_thread(
        prepare_classification_media,
        file_bytes,
        mime_type,
    )

    messages = _build_messages(
        system_prompt=load_prompt(PROMPT_VERSION),
        file_bytes=prepared,
        mime_type=prepared_mime,
    )
    client = get_llm_client()
    completion = await client.complete(
        task="classify",
        messages=messages,
        response_model=DocumentTypeClassification,
        tenant_id=tenant_id,
        db=db,
        prompt_version=PROMPT_VERSION,
        source_filename=source_filename,
        max_retries=1,
    )
    result = completion.result
    logger.info(
        "classification_llm.done",
        tenant_id=str(tenant_id),
        doc_type=result.doc_type,
        confidence=result.confidence,
        llm_call_id=str(completion.llm_call_id),
    )
    return result
