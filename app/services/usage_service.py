"""Consumo agregado por tenant: documentos, tokens y coste LLM.

Base para los límites por plan que vendrán después: primero hay que poder
responder "cuánto consume cada organización" con una definición única, en vez
de que cada pantalla sume por su cuenta.

Todas las lecturas son cross-tenant (consola SADM) y dependen de la política
RLS permisiva `superadmin_select`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import func, select

from app.models import Invoice, InvoiceStatus, LLMCall, Tenant, Ticket, TicketStatus
from app.services.document_override_service import enable_superadmin_lookup

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# Estados que cuentan como documento efectivamente procesado: los fallidos no
# consumen cuota de negocio aunque sí puedan haber costado tokens.
_PROCESSED_INVOICE_STATUSES = (InvoiceStatus.ready, InvoiceStatus.reviewed)
_PROCESSED_TICKET_STATUSES = (TicketStatus.ready, TicketStatus.reviewed)


@dataclass(frozen=True, slots=True)
class TenantUsage:
    """Consumo de un tenant en un periodo mensual."""

    tenant_id: UUID
    tenant_name: str
    documents: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cost_eur: Decimal

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def current_period() -> date:
    """Primer día del mes en curso (UTC), clave de agregación."""
    now = datetime.now(tz=UTC)
    return date(now.year, now.month, 1)


def next_period(period: date) -> date:
    """Primer día del mes siguiente, para acotar el rango con `<`."""
    if period.month == 12:
        return date(period.year + 1, 1, 1)
    return date(period.year, period.month + 1, 1)


async def get_tenant_usage(
    db: AsyncSession,
    *,
    period: date | None = None,
) -> list[TenantUsage]:
    """Consumo de todos los tenants en el periodo, de mayor a menor coste."""
    await enable_superadmin_lookup(db)
    start = period or current_period()
    end = next_period(start)

    llm_stmt = (
        select(
            LLMCall.tenant_id,
            func.count(LLMCall.id).label("calls"),
            func.coalesce(func.sum(LLMCall.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(LLMCall.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(LLMCall.cost_eur), 0).label("cost_eur"),
        )
        .where(LLMCall.created_at >= start, LLMCall.created_at < end)
        .group_by(LLMCall.tenant_id)
    )
    llm_rows = {row.tenant_id: row for row in (await db.execute(llm_stmt)).all()}

    documents = await _document_counts(db, start=start, end=end)

    tenants = (await db.execute(select(Tenant).order_by(Tenant.name))).scalars().all()
    usage = [
        TenantUsage(
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            documents=documents.get(tenant.id, 0),
            llm_calls=int(llm_rows[tenant.id].calls) if tenant.id in llm_rows else 0,
            input_tokens=int(llm_rows[tenant.id].input_tokens) if tenant.id in llm_rows else 0,
            output_tokens=int(llm_rows[tenant.id].output_tokens) if tenant.id in llm_rows else 0,
            cost_eur=(
                Decimal(llm_rows[tenant.id].cost_eur) if tenant.id in llm_rows else Decimal("0")
            ),
        )
        for tenant in tenants
    ]
    usage.sort(key=lambda row: (row.cost_eur, row.documents), reverse=True)
    return usage


async def _document_counts(
    db: AsyncSession,
    *,
    start: date,
    end: date,
) -> dict[UUID, int]:
    """Documentos procesados con éxito por tenant en el rango."""
    invoice_stmt = (
        select(Invoice.tenant_id, func.count(Invoice.id))
        .where(
            Invoice.created_at >= start,
            Invoice.created_at < end,
            Invoice.status.in_(_PROCESSED_INVOICE_STATUSES),
        )
        .group_by(Invoice.tenant_id)
    )
    ticket_stmt = (
        select(Ticket.tenant_id, func.count(Ticket.id))
        .where(
            Ticket.created_at >= start,
            Ticket.created_at < end,
            Ticket.status.in_(_PROCESSED_TICKET_STATUSES),
        )
        .group_by(Ticket.tenant_id)
    )

    counts: dict[UUID, int] = {}
    for tenant_id, count in (await db.execute(invoice_stmt)).all():
        counts[tenant_id] = counts.get(tenant_id, 0) + int(count)
    for tenant_id, count in (await db.execute(ticket_stmt)).all():
        counts[tenant_id] = counts.get(tenant_id, 0) + int(count)
    return counts
