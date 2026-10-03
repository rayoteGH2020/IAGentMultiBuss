"""Documentos anteriores al histórico del plan (D017, punto 9): fecha sin extraer."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.document_dates import find_issue_date
from app.core.document_processing_errors import (
    DocumentErrorCode,
    is_overridable,
    is_retryable,
    rejection_message,
)
from app.models import DocTypeCode
from app.schemas.document_classification import DocumentTypeClassification
from app.services import document_classification


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fecha: 12/03/2024", date(2024, 3, 12)),
        ("FECHA DE FACTURA 03-11-2025", date(2025, 11, 3)),
        ("Fecha de emisión: 5 de marzo de 2024", date(2024, 3, 5)),
        ("Fecha expedición: 2024-03-12", date(2024, 3, 12)),
        ("fecha:12.03.24", date(2024, 3, 12)),
        # Fecha de emisión y de vencimiento: solo cuenta la de emisión.
        ("Fecha de factura: 12/03/2024 Fecha de vencimiento: 12/04/2024", date(2024, 3, 12)),
    ],
)
def test_find_issue_date_reads_labelled_dates(text: str, expected: date) -> None:
    assert find_issue_date(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Total 12/03/2024",  # sin etiqueta: puede ser cualquier fecha
        "Fecha de vencimiento: 12/03/2024",
        "Fecha de pago: 12/03/2024",
        "Fecha: 01/02/2024 Fecha factura: 03/02/2024",  # ambigua
        "Fecha: 31/02/2024",  # imposible
    ],
)
def test_find_issue_date_refuses_unlabelled_or_ambiguous(text: str) -> None:
    assert find_issue_date(text) is None


def test_outside_history_is_final_not_retryable_nor_overridable() -> None:
    code = DocumentErrorCode.outside_history.value
    assert not is_retryable(code)
    assert not is_overridable(code)
    # Los rechazos por límites siguen siendo del SADM.
    assert is_overridable(DocumentErrorCode.too_many_pages.value)
    message = rejection_message(DocumentErrorCode.outside_history, filename="f.pdf")
    assert "anterior al histórico que incluye tu plan" in message
    assert "administrador del sitio" not in message


def _patch_classification(
    monkeypatch: pytest.MonkeyPatch, *, text: str, llm: DocumentTypeClassification | None
) -> AsyncMock:
    monkeypatch.setattr(document_classification, "extract_document_text", lambda *_a: text)
    llm_mock = AsyncMock(return_value=llm)
    monkeypatch.setattr(document_classification, "classify_document_with_llm", llm_mock)
    return llm_mock


async def _verify(
    choice: DocTypeCode = DocTypeCode.factura,
) -> document_classification.TypeVerificationResult:
    return await document_classification.verify_user_doc_type(
        AsyncMock(),
        file_bytes=b"x",
        mime_type="application/pdf",
        tenant_id=uuid4(),
        user_choice=choice,
    )


@pytest.mark.asyncio
async def test_text_pdf_date_needs_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _patch_classification(
        monkeypatch, text="Base imponible 100 Fecha de factura: 12/03/2024", llm=None
    )
    result = await _verify()
    assert result.method == "heuristic_match"
    assert result.issue_date == date(2024, 3, 12)
    llm.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("confidence", "expected"), [(0.9, date(2024, 3, 12)), (0.5, None)])
async def test_llm_date_comes_from_the_same_classification_call(
    monkeypatch: pytest.MonkeyPatch, confidence: float, expected: date | None
) -> None:
    llm = _patch_classification(
        monkeypatch,
        text="",  # foto: sin texto
        llm=DocumentTypeClassification(
            doc_type="ticket",
            confidence=0.95,
            reason="tique",
            fecha_emision=date(2024, 3, 12),
            fecha_confianza=confidence,
        ),
    )
    result = await _verify(DocTypeCode.ticket)
    llm.assert_awaited_once()
    # Con confianza baja no se rechaza en la subida: decide la extracción.
    assert result.issue_date == expected


@pytest.mark.asyncio
async def test_contracts_skip_date_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_classification(monkeypatch, text="Fecha: 12/03/2010", llm=None)
    result = await _verify(DocTypeCode.contrato)
    assert result.method == "trusted_type" and result.issue_date is None


def test_classification_schema_date_defaults_keep_old_answers_valid() -> None:
    parsed = DocumentTypeClassification(doc_type="factura", confidence=0.9, reason="x")
    assert parsed.fecha_emision is None and parsed.fecha_confianza == 0
