"""Tests de confirmación de tipo documental (keep / suggested)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.document_processing_errors import DocumentErrorCode
from app.models import DocTypeCode, InvoiceStatus
from app.services import document_type_confirm_service
from app.services.document_classification import TypeVerificationResult
from app.services.document_type_confirm_service import (
    TYPE_CONFIRM_META_KEY,
    build_type_confirm_meta,
)


@pytest.mark.asyncio
async def test_confirm_keep_enqueues_invoice() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    invoice_id = uuid4()
    verification = TypeVerificationResult(
        user_choice=DocTypeCode.factura,
        detected=DocTypeCode.ticket,
        confidence=0.9,
        needs_confirmation=True,
        method="heuristic_mismatch",
    )
    invoice = MagicMock()
    invoice.id = invoice_id
    invoice.error_code = DocumentErrorCode.type_confirmation_required.value
    invoice.raw_extraction = build_type_confirm_meta(verification)
    invoice.source_file_key = "t/key.pdf"
    invoice.source_filename = "x.pdf"
    invoice.source_mime = "application/pdf"

    with (
        patch(
            "app.services.document_type_confirm_service.invoice_service.get_invoice",
            new=AsyncMock(return_value=invoice),
        ),
        patch(
            "app.services.document_type_confirm_service.enqueue_invoice_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        result = await document_type_confirm_service.confirm_document_type(
            db,
            tenant_id=tenant_id,
            kind="invoice",
            document_id=invoice_id,
            choice="keep",
        )

    assert result.kind == "invoice"
    assert result.document_id == invoice_id
    assert invoice.status == InvoiceStatus.processing
    assert invoice.error_code is None
    enqueue_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_suggested_switches_invoice_to_ticket() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    invoice_id = uuid4()
    ticket_id = uuid4()
    verification = TypeVerificationResult(
        user_choice=DocTypeCode.factura,
        detected=DocTypeCode.ticket,
        confidence=0.9,
        needs_confirmation=True,
        method="heuristic_mismatch",
    )
    invoice = MagicMock()
    invoice.id = invoice_id
    invoice.error_code = DocumentErrorCode.type_confirmation_required.value
    invoice.raw_extraction = build_type_confirm_meta(verification)
    invoice.source_file_key = "t/key.pdf"
    invoice.source_filename = "x.pdf"
    invoice.source_mime = "application/pdf"

    ticket = MagicMock()
    ticket.id = ticket_id

    with (
        patch(
            "app.services.document_type_confirm_service.invoice_service.get_invoice",
            new=AsyncMock(return_value=invoice),
        ),
        patch(
            "app.services.document_type_confirm_service.ticket_service."
            "create_ticket_from_existing_storage",
            new=AsyncMock(return_value=ticket),
        ) as create_mock,
        patch(
            "app.services.document_processing_service.dismiss_from_panel",
            new=AsyncMock(),
        ) as dismiss_mock,
        patch(
            "app.services.document_type_confirm_service.enqueue_ticket_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
        patch(
            "app.services.document_type_confirm_service.enqueue_invoice_processing",
            new=AsyncMock(),
        ) as invoice_enqueue,
    ):
        result = await document_type_confirm_service.confirm_document_type(
            db,
            tenant_id=tenant_id,
            kind="invoice",
            document_id=invoice_id,
            choice="suggested",
        )

    assert result.kind == "ticket"
    assert result.document_id == ticket_id
    create_mock.assert_awaited_once()
    dismiss_mock.assert_awaited_once()
    enqueue_mock.assert_awaited_once()
    invoice_enqueue.assert_not_awaited()


def test_build_type_confirm_meta_shape() -> None:
    verification = TypeVerificationResult(
        user_choice=DocTypeCode.factura,
        detected=DocTypeCode.ticket,
        confidence=0.88,
        needs_confirmation=True,
        method="heuristic_mismatch",
    )
    meta = build_type_confirm_meta(verification)
    assert TYPE_CONFIRM_META_KEY in meta
    assert meta[TYPE_CONFIRM_META_KEY]["suggested"] == "ticket"
