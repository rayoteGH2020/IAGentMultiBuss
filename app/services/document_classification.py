"""Clasificación automática factura vs ticket."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.core.document_text import extract_document_text
from app.llm.classification import classify_document_with_llm
from app.models import DocTypeCode

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

_TICKET_MARKERS = ("tique", "ticket", "factura simplificada")
_FACTURA_MARKER = "base imponible"


def classify_from_text(text: str) -> DocTypeCode | None:
    """Reglas heurísticas actuales (sustituibles por LLM puro en el futuro)."""
    normalized = " ".join(text.lower().split())
    if not normalized:
        return None
    if any(marker in normalized for marker in _TICKET_MARKERS):
        return DocTypeCode.ticket
    if _FACTURA_MARKER in normalized:
        return DocTypeCode.factura
    return None


async def resolve_doc_type(
    db: AsyncSession,
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    user_choice: DocTypeCode | None,
    source_filename: str | None = None,
) -> DocTypeCode:
    """Resuelve el tipo documental: elección del usuario o detección automática."""
    if user_choice is not None:
        logger.info(
            "document_classification.user_choice",
            tenant_id=str(tenant_id),
            doc_type=user_choice.value,
        )
        return user_choice

    text = extract_document_text(file_bytes, mime_type)
    heuristic = classify_from_text(text)
    if heuristic is not None:
        logger.info(
            "document_classification.heuristic",
            tenant_id=str(tenant_id),
            doc_type=heuristic.value,
            text_chars=len(text),
        )
        return heuristic

    llm_result = await classify_document_with_llm(
        file_bytes=file_bytes,
        mime_type=mime_type,
        tenant_id=tenant_id,
        db=db,
        source_filename=source_filename,
    )
    logger.info(
        "document_classification.llm",
        tenant_id=str(tenant_id),
        doc_type=llm_result.doc_type,
        confidence=llm_result.confidence,
    )
    return DocTypeCode(llm_result.doc_type)
