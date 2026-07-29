"""Tests del borrado completo de documentos administrativos."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.errors import NotFoundError, ValidationError
from app.services import document_delete_service


@pytest.mark.asyncio
async def test_delete_invoice_removes_db_attempts_llm_call_and_r2() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    user_id = uuid4()
    invoice_id = uuid4()
    llm_call_id = uuid4()
    attempt_call_id = uuid4()

    invoice = MagicMock()
    invoice.id = invoice_id
    invoice.source_file_key = "tenants/t/invoices/a.pdf"
    invoice.source_filename = "a.pdf"
    invoice.llm_call_id = llm_call_id

    attempt_ids_result = MagicMock()
    attempt_ids_result.scalars.return_value.all.return_value = [attempt_call_id]
    db.execute = AsyncMock(side_effect=[attempt_ids_result, MagicMock(), MagicMock()])

    storage = MagicMock()
    storage.delete = AsyncMock()

    with (
        patch(
            "app.services.document_delete_service.invoice_service.get_invoice",
            new=AsyncMock(return_value=invoice),
        ),
        patch(
            "app.services.document_delete_service.audit_service.log_action",
            new=AsyncMock(),
        ) as audit_mock,
        patch(
            "app.services.document_delete_service.get_storage",
            return_value=storage,
        ),
    ):
        await document_delete_service.delete_document(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            document_kind="invoice",
            document_id=invoice_id,
        )

    db.delete.assert_awaited_once_with(invoice)
    db.flush.assert_awaited()
    storage.delete.assert_awaited_once_with("tenants/t/invoices/a.pdf")
    audit_mock.assert_awaited_once()
    assert audit_mock.await_args.kwargs["action"] == document_delete_service.ACTION_DOCUMENT_DELETE
    assert audit_mock.await_args.kwargs["metadata"]["llm_calls_deleted"] == 2


@pytest.mark.asyncio
async def test_delete_without_r2_key_skips_storage() -> None:
    db = AsyncMock()
    tenant_id = uuid4()
    ticket_id = uuid4()

    ticket = MagicMock()
    ticket.source_file_key = None
    ticket.source_filename = "t.pdf"
    ticket.llm_call_id = None

    empty_ids = MagicMock()
    empty_ids.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[empty_ids, MagicMock()])

    storage = MagicMock()
    storage.delete = AsyncMock()

    with (
        patch(
            "app.services.document_delete_service.ticket_service.get_ticket",
            new=AsyncMock(return_value=ticket),
        ),
        patch(
            "app.services.document_delete_service.audit_service.log_action",
            new=AsyncMock(),
        ),
        patch(
            "app.services.document_delete_service.get_storage",
            return_value=storage,
        ),
    ):
        await document_delete_service.delete_document(
            db,
            tenant_id=tenant_id,
            user_id=None,
            document_kind="ticket",
            document_id=ticket_id,
        )

    storage.delete.assert_not_awaited()
    db.delete.assert_awaited_once_with(ticket)


@pytest.mark.asyncio
async def test_delete_rejects_invalid_kind() -> None:
    db = AsyncMock()
    with pytest.raises(ValidationError):
        await document_delete_service.delete_document(
            db,
            tenant_id=uuid4(),
            user_id=None,
            document_kind="albaran",  # type: ignore[arg-type]
            document_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_delete_propagates_not_found() -> None:
    db = AsyncMock()
    with (
        patch(
            "app.services.document_delete_service.invoice_service.get_invoice",
            new=AsyncMock(side_effect=NotFoundError("missing")),
        ),
        pytest.raises(NotFoundError),
    ):
        await document_delete_service.delete_document(
            db,
            tenant_id=uuid4(),
            user_id=None,
            document_kind="invoice",
            document_id=uuid4(),
        )
