"""Tests de confirmación de tipo documental (keep / suggested)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.document_processing_errors import DocumentErrorCode
from app.models import DocTypeCode, InvoiceStatus, Ticket
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
        _quota_ok() as reserve_mock,
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
    reserve_mock.assert_awaited_once()
    enqueue_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_keep_without_quota_leaves_document_pending() -> None:
    invoice = MagicMock()
    invoice.id = uuid4()
    invoice.error_code = DocumentErrorCode.type_confirmation_required.value
    invoice.raw_extraction = build_type_confirm_meta(
        TypeVerificationResult(
            user_choice=DocTypeCode.factura,
            detected=DocTypeCode.ticket,
            confidence=0.9,
            needs_confirmation=True,
            method="heuristic_mismatch",
        )
    )

    with (
        patch(
            "app.services.document_type_confirm_service.invoice_service.get_invoice",
            new=AsyncMock(return_value=invoice),
        ),
        patch(
            "app.services.document_type_confirm_service.enqueue_invoice_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
        _quota_ok(reserved=False),
    ):
        await document_type_confirm_service.confirm_document_type(
            AsyncMock(),
            tenant_id=uuid4(),
            kind="invoice",
            document_id=invoice.id,
            choice="keep",
        )

    enqueue_mock.assert_not_awaited()


@contextmanager
def _quota_ok(*, reserved: bool = True) -> Iterator[AsyncMock]:
    """Cupo de documentos: True = hay hueco y se encola; False = queda pendiente."""
    reserve = AsyncMock(return_value=reserved)
    with (
        patch(
            "app.services.document_type_confirm_service.document_quota_service.reserve_or_hold",
            new=reserve,
        ),
        patch(
            "app.services.document_type_confirm_service.entitlement_service.resolve_tenant",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        yield reserve


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
    invoice.dismissed_at = None

    ticket = MagicMock(spec=Ticket)
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
            "app.services.document_type_confirm_service.enqueue_ticket_processing",
            new=AsyncMock(),
        ) as enqueue_mock,
        patch(
            "app.services.document_type_confirm_service.enqueue_invoice_processing",
            new=AsyncMock(),
        ) as invoice_enqueue,
        _quota_ok() as reserve_mock,
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
    # El original (pendiente de confirmar) se oculta sin pasar por dismiss_from_panel,
    # que solo admite documentos fallidos; el hash pasa al documento nuevo.
    assert invoice.dismissed_at is not None
    assert reserve_mock.await_args is not None
    assert reserve_mock.await_args.args[2] is ticket
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
