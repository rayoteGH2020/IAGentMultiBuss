"""Tickets — listado, creación y persistencia de extracción LLM."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import ColumnElement, String, cast, func, literal, or_, select
from sqlalchemy.orm import selectinload

from app.core.document_processing_errors import DocumentErrorCode, failure_message
from app.core.errors import NotFoundError, ValidationError
from app.core.keys import ticket_key
from app.core.storage import get_storage
from app.core.text_normalization import ilike_pattern, normalize_search_text
from app.core.uploads import original_upload_filename
from app.models import DocTypeCode, Ticket, TicketStatus
from app.schemas.document_query import (
    AggregateGroupBy,
    AggregateMetric,
    AggregateResult,
    AggregateRow,
    DocumentSearchFilters,
    TicketRead,
)
from app.schemas.pagination import Page
from app.services import doc_type_service

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.ticket import TicketRecibo

logger = structlog.get_logger(__name__)


async def list_tickets(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    status: TicketStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Ticket]:
    stmt = (
        select(Ticket)
        .where(Ticket.tenant_id == tenant_id, Ticket.dismissed_at.is_(None))
        .order_by(Ticket.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Ticket.status == status)
    stmt = stmt.options(selectinload(Ticket.llm_call), selectinload(Ticket.doc_type))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_ticket(
    db: AsyncSession,
    tenant_id: UUID,
    ticket_id: UUID,
) -> Ticket:
    stmt = (
        select(Ticket)
        .where(Ticket.tenant_id == tenant_id, Ticket.id == ticket_id)
        .options(selectinload(Ticket.llm_call))
    )
    result = await db.execute(stmt)
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise NotFoundError(f"Ticket {ticket_id} not found")
    return ticket


async def create_ticket_stub(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    source_file_key: str,
    source_filename: str,
    source_mime: str,
    doc_type: DocTypeCode = DocTypeCode.ticket,
) -> Ticket:
    doc_type_id = await doc_type_service.get_doc_type_id(db, doc_type)
    ticket = Ticket(
        tenant_id=tenant_id,
        doc_type_id=doc_type_id,
        status=TicketStatus.pending,
        source_file_key=source_file_key,
        source_filename=source_filename,
        source_mime=source_mime,
    )
    db.add(ticket)
    await db.flush()
    logger.info("ticket.created", ticket_id=str(ticket.id), tenant_id=str(tenant_id))
    return ticket


async def create_ticket_from_upload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    doc_type: DocTypeCode = DocTypeCode.ticket,
) -> Ticket:
    storage = get_storage()
    key = ticket_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)
    ticket = await create_ticket_stub(
        db,
        tenant_id,
        source_file_key=key,
        source_filename=original_upload_filename(filename),
        source_mime=mime_type,
        doc_type=doc_type,
    )
    ticket.status = TicketStatus.processing
    await db.flush()
    return ticket


async def apply_extraction_result(
    db: AsyncSession,
    *,
    ticket: Ticket,
    recibo: TicketRecibo,
    llm_call_id: UUID,
) -> Ticket:
    ticket.llm_call_id = llm_call_id
    ticket.fecha = recibo.fecha
    ticket.comercio = recibo.comercio[:300]
    ticket.numero_ticket = recibo.numero_ticket[:100] if recibo.numero_ticket else None
    ticket.forma_pago = recibo.forma_pago[:100] if recibo.forma_pago else None
    ticket.base_imponible = recibo.base_imponible
    ticket.iva_percent = recibo.iva_percent
    ticket.iva_amount = recibo.iva_amount
    ticket.total = recibo.total
    ticket.currency = recibo.currency[:3]
    ticket.confidence = Decimal(str(recibo.confidence)).quantize(Decimal("0.01"))
    ticket.raw_extraction = recibo.model_dump(mode="json")
    ticket.status = TicketStatus.ready
    ticket.updated_at = datetime.now(tz=UTC)
    ticket.error_message = None
    ticket.error_code = None
    await db.flush()
    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service

    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=ticket.tenant_id,
        document_kind="ticket",
        document_id=ticket.id,
        status=ProcessingAttemptStatus.ok,
        llm_call_id=llm_call_id,
    )
    return ticket


async def mark_failed(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    tenant_id: UUID,
    error: str,
    llm_call_id: UUID | None = None,
    error_code: DocumentErrorCode = DocumentErrorCode.extraction_failed,
    detail: str | None = None,
) -> None:
    """Marca un ticket como fallido con motivo estructurado (ver invoice_service)."""
    ticket = await get_ticket(db, tenant_id, ticket_id)
    ticket.status = TicketStatus.failed
    if llm_call_id is not None:
        ticket.llm_call_id = llm_call_id
    logger.warning(
        "ticket.processing_failed",
        ticket_id=str(ticket_id),
        tenant_id=str(tenant_id),
        source_filename=ticket.source_filename,
        error_code=error_code.value,
        technical_error=error[:2000],
    )
    ticket.error_code = error_code.value
    ticket.error_message = failure_message(
        error,
        error_code=error_code,
        filename=ticket.source_filename,
        detail=detail,
    )
    ticket.updated_at = datetime.now(tz=UTC)

    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service

    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind="ticket",
        document_id=ticket_id,
        status=ProcessingAttemptStatus.failed,
        llm_call_id=llm_call_id,
        error_message=ticket.error_message,
        error_code=error_code.value,
    )


def _ticket_to_read(ticket: Ticket) -> TicketRead:
    return TicketRead(
        id=ticket.id,
        status=ticket.status.value,
        fecha=ticket.fecha,
        comercio=ticket.comercio,
        numero_ticket=ticket.numero_ticket,
        forma_pago=ticket.forma_pago,
        base_imponible=ticket.base_imponible,
        iva_percent=ticket.iva_percent,
        iva_amount=ticket.iva_amount,
        total=ticket.total,
        currency=ticket.currency,
        confidence=ticket.confidence,
        source_filename=ticket.source_filename,
    )


def _ticket_search_conditions(
    tenant_id: UUID,
    filters: DocumentSearchFilters,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Ticket.tenant_id == tenant_id]

    if filters.fecha_from is not None:
        conditions.append(Ticket.fecha >= filters.fecha_from)
    if filters.fecha_to is not None:
        conditions.append(Ticket.fecha <= filters.fecha_to)
    if filters.total_min is not None:
        conditions.append(Ticket.total >= filters.total_min)
    if filters.total_max is not None:
        conditions.append(Ticket.total <= filters.total_max)
    if filters.status:
        statuses = [TicketStatus(s) for s in filters.status]
        conditions.append(Ticket.status.in_(statuses))
    if filters.numero_ticket:
        pattern = ilike_pattern(filters.numero_ticket)
        conditions.append(
            func.lower(func.coalesce(Ticket.numero_ticket, "")).like(
                pattern,
                escape="\\",
            ),
        )
    if filters.forma_pago:
        pattern = ilike_pattern(filters.forma_pago)
        conditions.append(
            func.lower(func.coalesce(Ticket.forma_pago, "")).like(
                pattern,
                escape="\\",
            ),
        )
    if filters.comercio_query:
        pattern = ilike_pattern(filters.comercio_query)
        normalized = normalize_search_text(filters.comercio_query)
        comercio_col = func.unaccent(func.coalesce(Ticket.comercio, ""))
        conditions.append(
            or_(
                func.lower(comercio_col).like(pattern, escape="\\"),
                func.similarity(comercio_col, literal(normalized)) >= 0.2,
            ),
        )
    if filters.text_query:
        pattern = ilike_pattern(filters.text_query)
        normalized = normalize_search_text(filters.text_query)
        comercio_col = func.unaccent(func.coalesce(Ticket.comercio, ""))
        filename_col = func.lower(func.coalesce(Ticket.source_filename, ""))
        numero_col = func.lower(func.coalesce(Ticket.numero_ticket, ""))
        conditions.append(
            or_(
                func.lower(comercio_col).like(pattern, escape="\\"),
                filename_col.like(pattern, escape="\\"),
                numero_col.like(pattern, escape="\\"),
                func.similarity(comercio_col, literal(normalized)) >= 0.2,
            ),
        )
    return conditions


async def search_tickets(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[TicketRead]:
    """Búsqueda de tickets con filtros tipados y paginación."""
    conditions = _ticket_search_conditions(tenant_id, filters)
    count_stmt = select(func.count()).select_from(Ticket).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = (
        select(Ticket)
        .where(*conditions)
        .order_by(Ticket.fecha.desc().nulls_last(), Ticket.created_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    result = await db.execute(stmt)
    tickets = result.scalars().all()
    return Page(
        items=[_ticket_to_read(t) for t in tickets],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


async def get_ticket_detail(
    db: AsyncSession,
    tenant_id: UUID,
    ticket_id: UUID,
) -> TicketRead:
    """Detalle de ticket (sin raw_extraction)."""
    ticket = await get_ticket(db, tenant_id, ticket_id)
    return _ticket_to_read(ticket)


async def aggregate_tickets(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    """Agregaciones COUNT o SUM(total) sobre tickets."""
    conditions = _ticket_search_conditions(tenant_id, filters)
    metric_expr = (
        func.count()
        if metric == AggregateMetric.metric_count
        else func.coalesce(func.sum(Ticket.total), 0)
    )

    if group_by == AggregateGroupBy.none:
        stmt = select(metric_expr).select_from(Ticket).where(*conditions)
        raw = (await db.execute(stmt)).scalar_one()
        if metric == AggregateMetric.metric_count:
            total_value: Decimal | int = int(raw or 0)
        else:
            total_value = Decimal(str(raw)) if raw is not None else Decimal("0")
        return AggregateResult(
            doc_type_code=DocTypeCode.ticket.value,
            metric=metric,
            group_by=group_by,
            total_value=total_value,
        )

    if group_by == AggregateGroupBy.comercio:
        group_key = func.lower(
            func.unaccent(func.coalesce(Ticket.comercio, "(sin comercio)")),
        ).label("group_key")
    elif group_by == AggregateGroupBy.month:
        group_key = func.to_char(Ticket.fecha, "YYYY-MM").label("group_key")
    elif group_by == AggregateGroupBy.year:
        group_key = func.to_char(Ticket.fecha, "YYYY").label("group_key")
    elif group_by == AggregateGroupBy.status:
        group_key = cast(Ticket.status, String).label("group_key")
    else:
        raise ValidationError(f"Unsupported group_by for tickets: {group_by.value}")

    stmt = (
        select(group_key, metric_expr)
        .select_from(Ticket)
        .where(*conditions)
        .group_by(group_key)
        .order_by(metric_expr.desc())
    )
    rows_result = await db.execute(stmt)
    rows = [
        AggregateRow(
            group_key=str(row.group_key or ""),
            value=int(row[1]) if metric == AggregateMetric.metric_count else row[1],
        )
        for row in rows_result.all()
    ]
    return AggregateResult(
        doc_type_code=DocTypeCode.ticket.value,
        metric=metric,
        group_by=group_by,
        rows=rows,
    )


async def list_comercios(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
    limit: int = 50,
) -> list[str]:
    """Comercios distintos del tenant, opcionalmente filtrados."""
    stmt = (
        select(Ticket.comercio)
        .where(
            Ticket.tenant_id == tenant_id,
            Ticket.comercio.is_not(None),
            Ticket.comercio != "",
        )
        .distinct()
        .order_by(Ticket.comercio)
        .limit(limit)
    )
    if query:
        pattern = ilike_pattern(query)
        comercio_col = func.unaccent(Ticket.comercio)
        stmt = stmt.where(func.lower(comercio_col).like(pattern, escape="\\"))
    result = await db.execute(stmt)
    return [str(row[0]) for row in result.all() if row[0]]
