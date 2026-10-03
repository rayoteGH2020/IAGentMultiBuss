"""Tests de clasificación heurística y verificación de tipo."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.models import DocTypeCode
from app.schemas.document_classification import DocumentTypeClassification
from app.services.document_classification import (
    HIGH_CONFIDENCE_THRESHOLD,
    classify_from_text,
    verify_user_doc_type,
)


def test_classify_ticket_from_simplificada() -> None:
    assert classify_from_text("FACTURA SIMPLIFICADA Nº 123") == DocTypeCode.ticket


def test_classify_ticket_from_ticket_word() -> None:
    assert classify_from_text("Ticket de compra 15/05/2026") == DocTypeCode.ticket


def test_classify_ticket_from_tique() -> None:
    assert classify_from_text("Tique regalo incluido") == DocTypeCode.ticket


def test_classify_factura_from_base_imponible() -> None:
    assert classify_from_text("Base imponible: 100,00 €") == DocTypeCode.factura


def test_ticket_markers_take_priority_over_base_imponible() -> None:
    text = "Factura simplificada. Base imponible: 10 €"
    assert classify_from_text(text) == DocTypeCode.ticket


def test_classify_empty_text_returns_none() -> None:
    assert classify_from_text("") is None
    assert classify_from_text("   ") is None


@pytest.mark.asyncio
async def test_verify_contrato_trusted_without_classification() -> None:
    db = AsyncMock()
    result = await verify_user_doc_type(
        db,
        file_bytes=b"%PDF",
        mime_type="application/pdf",
        tenant_id=uuid4(),
        user_choice=DocTypeCode.contrato,
    )
    assert result.needs_confirmation is False
    assert result.method == "trusted_type"


@pytest.mark.asyncio
async def test_verify_heuristic_mismatch_requires_confirmation() -> None:
    db = AsyncMock()
    with patch(
        "app.services.document_classification.extract_document_text",
        return_value="Ticket de compra total 12 EUR",
    ):
        result = await verify_user_doc_type(
            db,
            file_bytes=b"img",
            mime_type="image/jpeg",
            tenant_id=uuid4(),
            user_choice=DocTypeCode.factura,
        )
    assert result.needs_confirmation is True
    assert result.detected == DocTypeCode.ticket
    assert result.method == "heuristic_mismatch"


@pytest.mark.asyncio
async def test_verify_heuristic_match_no_confirmation() -> None:
    db = AsyncMock()
    with patch(
        "app.services.document_classification.extract_document_text",
        return_value="Base imponible: 100 EUR",
    ):
        result = await verify_user_doc_type(
            db,
            file_bytes=b"img",
            mime_type="image/jpeg",
            tenant_id=uuid4(),
            user_choice=DocTypeCode.factura,
        )
    assert result.needs_confirmation is False
    assert result.method == "heuristic_match"


@pytest.mark.asyncio
async def test_verify_llm_mismatch_high_confidence_requires_confirmation() -> None:
    db = AsyncMock()
    llm = DocumentTypeClassification(
        doc_type="ticket",
        confidence=max(HIGH_CONFIDENCE_THRESHOLD, 0.9),
        reason="parece tique",
    )
    with (
        patch(
            "app.services.document_classification.extract_document_text",
            return_value="",
        ),
        patch(
            "app.services.document_classification.classify_document_with_llm",
            new=AsyncMock(return_value=llm),
        ),
    ):
        result = await verify_user_doc_type(
            db,
            file_bytes=b"img",
            mime_type="image/jpeg",
            tenant_id=uuid4(),
            user_choice=DocTypeCode.factura,
        )
    assert result.needs_confirmation is True
    assert result.detected == DocTypeCode.ticket
    assert result.method == "llm_mismatch"


@pytest.mark.asyncio
async def test_verify_llm_low_confidence_trusts_user() -> None:
    db = AsyncMock()
    llm = DocumentTypeClassification(
        doc_type="ticket",
        confidence=0.4,
        reason="dudoso",
    )
    with (
        patch(
            "app.services.document_classification.extract_document_text",
            return_value="",
        ),
        patch(
            "app.services.document_classification.classify_document_with_llm",
            new=AsyncMock(return_value=llm),
        ),
    ):
        result = await verify_user_doc_type(
            db,
            file_bytes=b"img",
            mime_type="image/jpeg",
            tenant_id=uuid4(),
            user_choice=DocTypeCode.factura,
        )
    assert result.needs_confirmation is False
    assert result.method == "llm_low_confidence"
