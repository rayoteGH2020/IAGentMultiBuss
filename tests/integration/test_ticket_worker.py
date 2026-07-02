"""Tests del worker process_ticket contra Postgres real con mocks."""

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from app.core.db import set_tenant_context
from app.core.storage import reset_storage_for_tests
from app.jobs import ticket_jobs
from app.llm.extraction import TicketExtractionResult
from app.models import Tenant
from app.models.ticket import TicketStatus
from app.schemas.ticket import TicketRecibo
from app.services import ticket_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class _FakeStorage:
    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        _ = len(data), content_type
        return key

    async def download_bytes(self, key: str) -> bytes:
        _ = key
        return self._blob


_PDF_BYTES = b"%PDF-1.4 ticket test content"


@pytest.mark.asyncio
async def test_process_ticket_persists_extraction_mock(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    monkeypatch.setattr(
        ticket_jobs,
        "get_storage",
        lambda: _FakeStorage(_PDF_BYTES),
    )

    @asynccontextmanager
    async def _noop_slot(*_a: object, **_kw: object) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(
        ticket_jobs,
        "tenant_invoice_extraction_slot",
        _noop_slot,
    )

    async def fake_extract(
        *,
        file_bytes: bytes,
        mime_type: str,
        tenant_id: UUID,
        db: AsyncSession,
        source_filename: str | None = None,
    ) -> TicketExtractionResult:
        _ = file_bytes, mime_type, tenant_id, db, source_filename
        return TicketExtractionResult(
            ticket=TicketRecibo(
                fecha=date(2025, 3, 10),
                comercio="Bar Test",
                total=Decimal("8.50"),
                confidence=0.92,
            ),
            llm_call_id=UUID("00000000-0000-0000-0000-000000000001"),
        )

    monkeypatch.setattr(ticket_jobs, "extract_ticket", fake_extract)

    ticket = await ticket_service.create_ticket_stub(
        db_session,
        tenant.id,
        source_file_key="test/tickets/mock.jpg",
        source_filename="mock.jpg",
        source_mime="image/jpeg",
    )
    ticket.status = TicketStatus.processing
    await db_session.commit()

    tenant_id = tenant.id
    ticket_id = ticket.id
    await ticket_jobs.process_ticket({}, str(ticket_id), str(tenant_id))

    try:
        db_session.expire(ticket)
        await set_tenant_context(db_session, str(tenant_id))
        refreshed = await ticket_service.get_ticket(db_session, tenant_id, ticket_id)
        assert refreshed.status == TicketStatus.ready
        assert refreshed.comercio == "Bar Test"
        assert refreshed.total == Decimal("8.50")
    finally:
        reset_storage_for_tests()
