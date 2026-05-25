"""Tests del servicio de tickets."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import Tenant, TicketStatus
from app.schemas.ticket import TicketRecibo
from app.services import ticket_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_apply_extraction_result_links_llm_call(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))

    ticket = await ticket_service.create_ticket_stub(
        db_session,
        tenant.id,
        source_file_key="test/ticket.jpg",
        source_filename="ticket.jpg",
        source_mime="image/jpeg",
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))
    ticket = await ticket_service.get_ticket(db_session, tenant.id, ticket.id)

    llm_call_id = uuid4()
    recibo = TicketRecibo(
        fecha=date(2026, 5, 1),
        comercio="Supermercado Test",
        total=Decimal("45.90"),
        confidence=0.88,
    )

    await ticket_service.apply_extraction_result(
        db_session,
        ticket=ticket,
        recibo=recibo,
        llm_call_id=llm_call_id,
    )
    await db_session.commit()
    await set_tenant_context(db_session, str(tenant.id))

    refreshed = await ticket_service.get_ticket(db_session, tenant.id, ticket.id)
    assert refreshed.llm_call_id == llm_call_id
    assert refreshed.status == TicketStatus.ready
    assert refreshed.comercio == "Supermercado Test"


async def test_list_tickets_empty(
    invoices_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory,
) -> None:
    tenant: Tenant = await tenant_factory()
    await set_tenant_context(db_session, str(tenant.id))
    tickets = await ticket_service.list_tickets(db_session, tenant.id)
    assert list(tickets) == []
