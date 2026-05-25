"""Tools de consulta documental (familia ``document``)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.llm.tools.registry import (
    ToolCitation,
    ToolContext,
    ToolDefinition,
    ToolFamily,
    ToolRegistry,
    ToolResult,
)
from app.schemas.chat import DocTypeRead
from app.schemas.document_query import (
    AggregateGroupBy,
    AggregateMetric,
    DocumentRead,
    DocumentSearchFilters,
    InvoiceRead,
    TicketRead,
)
from app.schemas.pagination import Page
from app.services import doc_type_service, document_query_service

PROMPT_VERSION = "chat_documents_v1"


class ListDocTypesArgs(BaseModel):
    """Sin parámetros: lista tipos activos desde ``doc_types``."""


class SearchDocumentsArgs(BaseModel):
    doc_type_code: str = Field(description="Código del catálogo doc_types (p. ej. factura, ticket)")
    fecha_from: date | None = None
    fecha_to: date | None = None
    total_min: Decimal | None = Field(default=None, ge=0)
    total_max: Decimal | None = Field(default=None, ge=0)
    status: list[str] | None = None
    text_query: str | None = None
    proveedor_query: str | None = None
    cif_nif: str | None = None
    comercio_query: str | None = None
    numero_ticket: str | None = None
    forma_pago: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class GetDocumentArgs(BaseModel):
    doc_type_code: str
    document_id: UUID


class AggregateDocumentsArgs(BaseModel):
    doc_type_code: str
    metric: str = Field(description="count o sum_total")
    group_by: str = Field(
        default="none", description="none, proveedor, comercio, month, year, status"
    )
    fecha_from: date | None = None
    fecha_to: date | None = None
    total_min: Decimal | None = Field(default=None, ge=0)
    total_max: Decimal | None = Field(default=None, ge=0)
    status: list[str] | None = None
    text_query: str | None = None
    proveedor_query: str | None = None
    cif_nif: str | None = None
    comercio_query: str | None = None
    numero_ticket: str | None = None
    forma_pago: str | None = None


class ListDocumentPartiesArgs(BaseModel):
    doc_type_code: str
    query: str | None = Field(default=None, description="Filtro opcional por nombre")


def _filters_from_search_args(args: SearchDocumentsArgs) -> DocumentSearchFilters:
    return DocumentSearchFilters.model_validate(args.model_dump())


def _filters_from_aggregate_args(args: AggregateDocumentsArgs) -> DocumentSearchFilters:
    data = args.model_dump(exclude={"metric", "group_by", "doc_type_code"})
    return DocumentSearchFilters.model_validate(data)


def _parse_metric(raw: str) -> AggregateMetric:
    normalized = raw.strip().lower()
    if normalized in {"count", "metric_count"}:
        return AggregateMetric.metric_count
    if normalized in {"sum_total", "sum", "total"}:
        return AggregateMetric.sum_total
    msg = f"Unsupported metric: {raw!r}"
    raise ValueError(msg)


def _parse_group_by(raw: str) -> AggregateGroupBy:
    try:
        return AggregateGroupBy(raw.strip().lower())
    except ValueError as exc:
        msg = f"Unsupported group_by: {raw!r}"
        raise ValueError(msg) from exc


def _citation_from_document(doc: DocumentRead) -> ToolCitation:
    if isinstance(doc, InvoiceRead):
        label = doc.proveedor or doc.source_filename or str(doc.id)
        snippet = f"factura total={doc.total} fecha={doc.fecha}"
    elif isinstance(doc, TicketRead):
        label = doc.comercio or doc.source_filename or str(doc.id)
        snippet = f"ticket total={doc.total} fecha={doc.fecha}"
    else:
        label = str(doc.id)
        snippet = None
    return ToolCitation(
        source="document",
        id=str(doc.id),
        label=label,
        snippet=snippet,
    )


def _citations_from_page(page: Page[DocumentRead]) -> list[ToolCitation]:
    return [_citation_from_document(item) for item in page.items]


async def execute_list_doc_types(ctx: ToolContext, _args: ListDocTypesArgs) -> ToolResult:
    rows = await doc_type_service.list_active_doc_types(ctx.db)
    items = [DocTypeRead.model_validate(row).model_dump(mode="json") for row in rows]
    return ToolResult(ok=True, data={"doc_types": items}, citations=[])


async def execute_search_documents(ctx: ToolContext, args: SearchDocumentsArgs) -> ToolResult:
    page = await document_query_service.search_documents(
        ctx.db,
        ctx.tenant_id,
        doc_type_code=args.doc_type_code,
        filters=_filters_from_search_args(args),
    )
    return ToolResult(
        ok=True,
        data=page.model_dump(mode="json"),
        citations=_citations_from_page(page),
    )


async def execute_get_document(ctx: ToolContext, args: GetDocumentArgs) -> ToolResult:
    doc = await document_query_service.get_document(
        ctx.db,
        ctx.tenant_id,
        doc_type_code=args.doc_type_code,
        document_id=args.document_id,
    )
    return ToolResult(
        ok=True,
        data={"document": doc.model_dump(mode="json")},
        citations=[_citation_from_document(doc)],
    )


async def execute_aggregate_documents(ctx: ToolContext, args: AggregateDocumentsArgs) -> ToolResult:
    result = await document_query_service.aggregate_documents(
        ctx.db,
        ctx.tenant_id,
        doc_type_code=args.doc_type_code,
        filters=_filters_from_aggregate_args(args),
        metric=_parse_metric(args.metric),
        group_by=_parse_group_by(args.group_by),
    )
    return ToolResult(ok=True, data=result.model_dump(mode="json"), citations=[])


async def execute_list_document_parties(
    ctx: ToolContext,
    args: ListDocumentPartiesArgs,
) -> ToolResult:
    parties = await document_query_service.list_document_parties(
        ctx.db,
        ctx.tenant_id,
        doc_type_code=args.doc_type_code,
        query=args.query,
    )
    return ToolResult(ok=True, data={"parties": parties}, citations=[])


def build_document_chat_registry() -> ToolRegistry:
    """Registry con las cinco tools MVP de consulta documental."""
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="list_doc_types",
            family=ToolFamily.document,
            description=(
                "Lista los tipos de documento activos que el usuario puede consultar. "
                "Llámala si preguntan qué documentos están disponibles."
            ),
            parameters_model=ListDocTypesArgs,
            executor=execute_list_doc_types,
        ),
    )
    registry.register(
        ToolDefinition(
            name="search_documents",
            family=ToolFamily.document,
            description=(
                "Busca documentos del tenant con filtros (fechas, importes, proveedor, comercio, etc.). "
                "Requiere doc_type_code válido del catálogo."
            ),
            parameters_model=SearchDocumentsArgs,
            executor=execute_search_documents,
        ),
    )
    registry.register(
        ToolDefinition(
            name="get_document",
            family=ToolFamily.document,
            description="Obtiene el detalle de un documento por id y doc_type_code.",
            parameters_model=GetDocumentArgs,
            executor=execute_get_document,
        ),
    )
    registry.register(
        ToolDefinition(
            name="aggregate_documents",
            family=ToolFamily.document,
            description=(
                "Agrega documentos: metric count o sum_total, group_by opcional "
                "(none, proveedor, comercio, month, year, status)."
            ),
            parameters_model=AggregateDocumentsArgs,
            executor=execute_aggregate_documents,
        ),
    )
    registry.register(
        ToolDefinition(
            name="list_document_parties",
            family=ToolFamily.document,
            description=(
                "Lista proveedores (factura) o comercios (ticket) distintos del tenant, "
                "con filtro opcional por nombre."
            ),
            parameters_model=ListDocumentPartiesArgs,
            executor=execute_list_document_parties,
        ),
    )
    return registry


def register_knowledge_tool_stubs(registry: ToolRegistry) -> None:
    """Stubs deshabilitados para tests y futuro módulo RAG (Paso 20+)."""

    class SearchKnowledgeArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        query: str

    async def _stub_knowledge(_ctx: ToolContext, _args: SearchKnowledgeArgs) -> ToolResult:
        return ToolResult(ok=False, data={}, citations=[], error="not_implemented")

    registry.register(
        ToolDefinition(
            name="search_knowledge",
            family=ToolFamily.knowledge,
            description="Búsqueda semántica en base de conocimiento (no disponible en MVP).",
            parameters_model=SearchKnowledgeArgs,
            executor=_stub_knowledge,
            enabled=False,
        ),
    )
