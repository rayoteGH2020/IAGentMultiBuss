"""Clasificación automática factura vs ticket (heurística barata + LLM)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

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
_FACTURA_TICKET_TYPES = frozenset({DocTypeCode.factura, DocTypeCode.ticket})

# Confianza mínima del LLM para forzar confirmación ante mismatch.
HIGH_CONFIDENCE_THRESHOLD = 0.75
# Confianza asignada a un mismatch heurístico (reglas de texto estables).
HEURISTIC_CONFIDENCE = 0.85

TypeVerifyMethod = Literal[
    "trusted_type",
    "heuristic_match",
    "heuristic_mismatch",
    "llm_match",
    "llm_mismatch",
    "llm_low_confidence",
    "llm_failed_trust_user",
    "auto_heuristic",
    "auto_llm",
]


@dataclass(frozen=True, slots=True)
class TypeVerificationResult:
    """Resultado de verificar el tipo elegido por el usuario (factura/ticket)."""

    user_choice: DocTypeCode
    detected: DocTypeCode | None
    confidence: float | None
    needs_confirmation: bool
    method: TypeVerifyMethod


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


async def verify_user_doc_type(
    db: AsyncSession,
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    user_choice: DocTypeCode,
    source_filename: str | None = None,
) -> TypeVerificationResult:
    """Verifica factura/ticket antes de extracción cara.

    Contratos/seguros se confían al tipo de usuario (otra vía de prompts).
    Ante mismatch con confianza alta, el llamador debe pedir confirmación HTMX
    y no encolar la extracción.
    """
    if user_choice not in _FACTURA_TICKET_TYPES:
        return TypeVerificationResult(
            user_choice=user_choice,
            detected=None,
            confidence=None,
            needs_confirmation=False,
            method="trusted_type",
        )

    text = extract_document_text(file_bytes, mime_type)
    heuristic = classify_from_text(text)
    if heuristic is not None:
        if heuristic == user_choice:
            logger.info(
                "document_classification.heuristic_match",
                tenant_id=str(tenant_id),
                doc_type=user_choice.value,
                text_chars=len(text),
            )
            return TypeVerificationResult(
                user_choice=user_choice,
                detected=heuristic,
                confidence=HEURISTIC_CONFIDENCE,
                needs_confirmation=False,
                method="heuristic_match",
            )
        logger.info(
            "document_classification.heuristic_mismatch",
            tenant_id=str(tenant_id),
            user_choice=user_choice.value,
            detected=heuristic.value,
            text_chars=len(text),
        )
        return TypeVerificationResult(
            user_choice=user_choice,
            detected=heuristic,
            confidence=HEURISTIC_CONFIDENCE,
            needs_confirmation=True,
            method="heuristic_mismatch",
        )

    try:
        llm_result = await classify_document_with_llm(
            file_bytes=file_bytes,
            mime_type=mime_type,
            tenant_id=tenant_id,
            db=db,
            source_filename=source_filename,
        )
    except Exception:
        logger.warning(
            "document_classification.llm_failed_trust_user",
            tenant_id=str(tenant_id),
            user_choice=user_choice.value,
            exc_info=True,
        )
        return TypeVerificationResult(
            user_choice=user_choice,
            detected=None,
            confidence=None,
            needs_confirmation=False,
            method="llm_failed_trust_user",
        )

    detected = DocTypeCode(llm_result.doc_type)
    confidence = float(llm_result.confidence)
    if detected == user_choice:
        method: TypeVerifyMethod = "llm_match"
        needs = False
    elif confidence < HIGH_CONFIDENCE_THRESHOLD:
        method = "llm_low_confidence"
        needs = False
    else:
        method = "llm_mismatch"
        needs = True

    logger.info(
        "document_classification.llm_verify",
        tenant_id=str(tenant_id),
        user_choice=user_choice.value,
        detected=detected.value,
        confidence=confidence,
        needs_confirmation=needs,
        method=method,
    )
    return TypeVerificationResult(
        user_choice=user_choice,
        detected=detected,
        confidence=confidence,
        needs_confirmation=needs,
        method=method,
    )


async def resolve_doc_type(
    db: AsyncSession,
    *,
    file_bytes: bytes,
    mime_type: str,
    tenant_id: UUID,
    user_choice: DocTypeCode | None,
    source_filename: str | None = None,
) -> DocTypeCode:
    """Resuelve el tipo documental cuando no hay flujo de confirmación.

    Con elección de usuario en factura/ticket, delega en `verify_user_doc_type`
    y mantiene el tipo del usuario (la confirmación la gestiona el upload).
    Sin elección, heurística y luego LLM.
    """
    if user_choice is not None:
        verification = await verify_user_doc_type(
            db,
            file_bytes=file_bytes,
            mime_type=mime_type,
            tenant_id=tenant_id,
            user_choice=user_choice,
            source_filename=source_filename,
        )
        return verification.user_choice

    text = extract_document_text(file_bytes, mime_type)
    heuristic = classify_from_text(text)
    if heuristic is not None:
        logger.info(
            "document_classification.auto_heuristic",
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
        "document_classification.auto_llm",
        tenant_id=str(tenant_id),
        doc_type=llm_result.doc_type,
        confidence=llm_result.confidence,
    )
    return DocTypeCode(llm_result.doc_type)
