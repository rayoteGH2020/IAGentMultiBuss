"""Tests de enrutado de subida por DocTypeCode."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.models import DocTypeCode
from app.services import document_upload_service
from app.services.document_classification import TypeVerificationResult


@pytest.mark.asyncio
async def test_ingest_contrato_routes_to_contract_pipeline() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    contract = MagicMock()
    contract.id = uuid4()

    with (
        patch(
            "app.services.document_upload_service.asyncio.to_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.document_upload_service.contract_service.create_contract_from_upload",
            new=AsyncMock(return_value=contract),
        ) as create_mock,
        patch(
            "app.services.document_upload_service.enqueue_contract_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
        patch(
            "app.services.document_upload_service.ticket_service.create_ticket_from_upload",
            new=AsyncMock(),
        ) as ticket_mock,
    ):
        result = await document_upload_service.ingest_uploaded_document(
            db,
            tenant_id=tenant_id,
            filename="contrato.pdf",
            file_bytes=b"%PDF",
            mime_type="application/pdf",
            doc_type=DocTypeCode.contrato,
        )

    assert result.kind == "contract"
    assert result.contract is contract
    create_mock.assert_awaited_once()
    enqueue_mock.assert_awaited_once()
    ticket_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_seguro_routes_to_insurance_pipeline() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    insurance = MagicMock()
    insurance.id = uuid4()

    with (
        patch(
            "app.services.document_upload_service.asyncio.to_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.document_upload_service.insurance_service.create_insurance_from_upload",
            new=AsyncMock(return_value=insurance),
        ) as create_mock,
        patch(
            "app.services.document_upload_service.enqueue_insurance_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
        patch(
            "app.services.document_upload_service.ticket_service.create_ticket_from_upload",
            new=AsyncMock(),
        ) as ticket_mock,
    ):
        result = await document_upload_service.ingest_uploaded_document(
            db,
            tenant_id=tenant_id,
            filename="poliza.pdf",
            file_bytes=b"%PDF",
            mime_type="application/pdf",
            doc_type=DocTypeCode.seguro,
        )

    assert result.kind == "insurance"
    assert result.insurance is insurance
    create_mock.assert_awaited_once()
    enqueue_mock.assert_awaited_once()
    ticket_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_factura_mismatch_does_not_enqueue() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    invoice = MagicMock()
    invoice.id = uuid4()
    verification = TypeVerificationResult(
        user_choice=DocTypeCode.factura,
        detected=DocTypeCode.ticket,
        confidence=0.9,
        needs_confirmation=True,
        method="heuristic_mismatch",
    )

    with (
        patch(
            "app.services.document_upload_service.asyncio.to_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.document_upload_service.document_classification.verify_user_doc_type",
            new=AsyncMock(return_value=verification),
        ),
        patch(
            "app.services.document_upload_service.invoice_service.create_invoice_from_upload",
            new=AsyncMock(return_value=invoice),
        ) as create_mock,
        patch(
            "app.services.document_upload_service.document_type_confirm_service."
            "mark_invoice_awaiting_type_confirmation",
            new=AsyncMock(return_value=invoice),
        ) as await_mock,
        patch(
            "app.services.document_upload_service.enqueue_invoice_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        result = await document_upload_service.ingest_uploaded_document(
            db,
            tenant_id=tenant_id,
            filename="ticket-as-factura.pdf",
            file_bytes=b"%PDF",
            mime_type="application/pdf",
            doc_type=DocTypeCode.factura,
        )

    assert result.awaiting_type_confirmation is True
    assert result.invoice is invoice
    create_mock.assert_awaited_once()
    await_mock.assert_awaited_once()
    enqueue_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_factura_match_enqueues() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    invoice = MagicMock()
    invoice.id = uuid4()
    verification = TypeVerificationResult(
        user_choice=DocTypeCode.factura,
        detected=DocTypeCode.factura,
        confidence=0.85,
        needs_confirmation=False,
        method="heuristic_match",
    )

    with (
        patch(
            "app.services.document_upload_service.asyncio.to_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.document_upload_service.document_classification.verify_user_doc_type",
            new=AsyncMock(return_value=verification),
        ),
        patch(
            "app.services.document_upload_service.invoice_service.create_invoice_from_upload",
            new=AsyncMock(return_value=invoice),
        ),
        patch(
            "app.services.document_upload_service.enqueue_invoice_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
    ):
        result = await document_upload_service.ingest_uploaded_document(
            db,
            tenant_id=tenant_id,
            filename="factura.pdf",
            file_bytes=b"%PDF",
            mime_type="application/pdf",
            doc_type=DocTypeCode.factura,
        )

    assert result.awaiting_type_confirmation is False
    enqueue_mock.assert_awaited_once()
