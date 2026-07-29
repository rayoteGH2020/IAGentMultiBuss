"""Enrutado de consultas documentales por doc_type_code (módulo 1.5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models import DocTypeCode
from app.schemas.document_query import (
    AggregateGroupBy,
    AggregateMetric,
    AggregateResult,
    DocumentRead,
    DocumentSearchFilters,
)
from app.schemas.pagination import Page
from app.services import (
    contract_service,
    doc_type_service,
    insurance_service,
    invoice_service,
    ticket_service,
)


@dataclass(frozen=True, slots=True)
class DocumentQueryHandler:
    """Operaciones de consulta soportadas para un código de doc_types."""

    search: Callable[..., Awaitable[Page[DocumentRead]]]
    get: Callable[..., Awaitable[DocumentRead]]
    aggregate: Callable[..., Awaitable[AggregateResult]]
    list_parties: Callable[..., Awaitable[list[str]]]


async def _search_invoices(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[DocumentRead]:
    page = await invoice_service.search_invoices(db, tenant_id, filters=filters)
    return Page(
        items=list(page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


async def _get_invoice(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
) -> DocumentRead:
    return await invoice_service.get_invoice_detail(db, tenant_id, document_id)


async def _aggregate_invoices(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    return await invoice_service.aggregate_invoices(
        db,
        tenant_id,
        filters=filters,
        metric=metric,
        group_by=group_by,
    )


async def _list_invoice_parties(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
) -> list[str]:
    return await invoice_service.list_providers(db, tenant_id, query=query)


async def _search_tickets(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[DocumentRead]:
    page = await ticket_service.search_tickets(db, tenant_id, filters=filters)
    return Page(
        items=list(page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


async def _get_ticket(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
) -> DocumentRead:
    return await ticket_service.get_ticket_detail(db, tenant_id, document_id)


async def _aggregate_tickets(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    return await ticket_service.aggregate_tickets(
        db,
        tenant_id,
        filters=filters,
        metric=metric,
        group_by=group_by,
    )


async def _list_ticket_parties(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
) -> list[str]:
    return await ticket_service.list_comercios(db, tenant_id, query=query)


async def _search_contracts(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[DocumentRead]:
    page = await contract_service.search_contracts(db, tenant_id, filters=filters)
    return Page(
        items=list(page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


async def _get_contract(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
) -> DocumentRead:
    return await contract_service.get_contract_detail(db, tenant_id, document_id)


async def _aggregate_contracts(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    return await contract_service.aggregate_contracts(
        db,
        tenant_id,
        filters=filters,
        metric=metric,
        group_by=group_by,
    )


async def _list_contract_parties(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
) -> list[str]:
    return await contract_service.list_partes_contrarias(db, tenant_id, query=query)


async def _search_insurances(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[DocumentRead]:
    page = await insurance_service.search_insurances(db, tenant_id, filters=filters)
    return Page(
        items=list(page.items),
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


async def _get_insurance(
    db: AsyncSession,
    tenant_id: UUID,
    document_id: UUID,
) -> DocumentRead:
    return await insurance_service.get_insurance_detail(db, tenant_id, document_id)


async def _aggregate_insurances(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    return await insurance_service.aggregate_insurances(
        db,
        tenant_id,
        filters=filters,
        metric=metric,
        group_by=group_by,
    )


async def _list_insurance_parties(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
) -> list[str]:
    return await insurance_service.list_aseguradoras(db, tenant_id, query=query)


DOC_TYPE_HANDLERS: dict[str, DocumentQueryHandler] = {
    DocTypeCode.factura.value: DocumentQueryHandler(
        search=_search_invoices,
        get=_get_invoice,
        aggregate=_aggregate_invoices,
        list_parties=_list_invoice_parties,
    ),
    DocTypeCode.ticket.value: DocumentQueryHandler(
        search=_search_tickets,
        get=_get_ticket,
        aggregate=_aggregate_tickets,
        list_parties=_list_ticket_parties,
    ),
    DocTypeCode.contrato.value: DocumentQueryHandler(
        search=_search_contracts,
        get=_get_contract,
        aggregate=_aggregate_contracts,
        list_parties=_list_contract_parties,
    ),
    DocTypeCode.seguro.value: DocumentQueryHandler(
        search=_search_insurances,
        get=_get_insurance,
        aggregate=_aggregate_insurances,
        list_parties=_list_insurance_parties,
    ),
}


def _require_handler(doc_type_code: str) -> DocumentQueryHandler:
    handler = DOC_TYPE_HANDLERS.get(doc_type_code)
    if handler is None:
        raise ValidationError(
            f"Document type {doc_type_code!r} is not queryable yet",
            details={
                "error": "document_type_not_queryable",
                "code": doc_type_code,
                "hint": "Call list_doc_types for supported types.",
            },
        )
    return handler


async def search_documents(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    doc_type_code: str,
    filters: DocumentSearchFilters,
) -> Page[DocumentRead]:
    doc_type = await doc_type_service.resolve_active_doc_type(db, doc_type_code)
    handler = _require_handler(doc_type.code)
    return await handler.search(db, tenant_id, filters=filters)


async def get_document(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    doc_type_code: str,
    document_id: UUID,
) -> DocumentRead:
    doc_type = await doc_type_service.resolve_active_doc_type(db, doc_type_code)
    handler = _require_handler(doc_type.code)
    return await handler.get(db, tenant_id, document_id)


async def aggregate_documents(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    doc_type_code: str,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    doc_type = await doc_type_service.resolve_active_doc_type(db, doc_type_code)
    handler = _require_handler(doc_type.code)
    return await handler.aggregate(
        db,
        tenant_id,
        filters=filters,
        metric=metric,
        group_by=group_by,
    )


async def list_document_parties(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    doc_type_code: str,
    query: str | None = None,
) -> list[str]:
    doc_type = await doc_type_service.resolve_active_doc_type(db, doc_type_code)
    handler = _require_handler(doc_type.code)
    return await handler.list_parties(db, tenant_id, query=query)
