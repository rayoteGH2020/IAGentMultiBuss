"""Tools de retrieval RAG (familia ``knowledge``) — Paso 19 Fase C."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.cache import get_redis
from app.llm.tools.registry import (
    ToolCitation,
    ToolContext,
    ToolDefinition,
    ToolFamily,
    ToolRegistry,
    ToolResult,
)
from app.models.knowledge import KnowledgeDocumentKind
from app.schemas.knowledge_search import KnowledgeChunkRef, KnowledgeSearchFilters
from app.services import knowledge_search_service

# Tope de caracteres por chunk en respuesta de tool (evita saturar contexto LLM).
MAX_CONTENT_CHARS: int = 1500

_KNOWLEDGE_KIND_VALUES: tuple[str, ...] = tuple(k.value for k in KnowledgeDocumentKind)


class ListKnowledgeSourcesArgs(BaseModel):
    """Lista documentos indexados en estado ``ready``."""

    model_config = ConfigDict(extra="forbid")

    kind: str | None = Field(
        default=None,
        description=(
            "Filtrar por categoría de documento (contract, terms, schedule, services, "
            "policy, faq, manual, other). Opcional."
        ),
    )

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str | None) -> str | None:
        if v is None:
            return None
        normalized = v.strip().lower()
        if normalized not in _KNOWLEDGE_KIND_VALUES:
            msg = f"kind inválido: {v!r}. Valores: {', '.join(_KNOWLEDGE_KIND_VALUES)}"
            raise ValueError(msg)
        return normalized


class SearchKnowledgeArgs(BaseModel):
    """Búsqueda híbrida sobre la base de conocimiento."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Pregunta o términos de búsqueda en lenguaje natural",
    )
    kind: list[str] | None = Field(
        default=None,
        description="Filtrar por categorías de documento (opcional)",
    )
    top_k: int = Field(default=10, ge=1, le=25, description="Número de fragmentos a devolver")

    @field_validator("kind")
    @classmethod
    def _validate_kinds(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out: list[str] = []
        for raw in v:
            normalized = raw.strip().lower()
            if normalized not in _KNOWLEDGE_KIND_VALUES:
                msg = f"kind inválido: {raw!r}. Valores: {', '.join(_KNOWLEDGE_KIND_VALUES)}"
                raise ValueError(msg)
            out.append(normalized)
        return out


class GetKnowledgeChunkArgs(BaseModel):
    """Recupera un fragmento concreto por UUID."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(description="UUID del fragmento de conocimiento")


def _kind_filter_from_strings(kinds: list[str] | None) -> list[KnowledgeDocumentKind] | None:
    if kinds is None:
        return None
    return [KnowledgeDocumentKind(k) for k in kinds]


def _chunk_to_tool_dict(chunk: KnowledgeChunkRef, *, truncate: bool) -> dict[str, Any]:
    content = chunk.content
    if truncate and len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS]
    return {
        "id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "source_name": chunk.document_name,
        "kind": chunk.kind.value,
        "position": chunk.position,
        "content": content,
        "context": chunk.context or "",
        "score": round(chunk.score, 4),
    }


def _chunk_detail_to_tool_dict(chunk: KnowledgeChunkRef) -> dict[str, Any]:
    return {
        "id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "source_name": chunk.document_name,
        "kind": chunk.kind.value,
        "position": chunk.position,
        "content": chunk.content,
        "context": chunk.context or "",
        "metadata": chunk.metadata,
    }


def _citation_from_chunk(chunk: KnowledgeChunkRef, *, snippet_len: int = 200) -> ToolCitation:
    snippet_text = chunk.content[:snippet_len] if chunk.content else None
    return ToolCitation(
        source="knowledge",
        id=str(chunk.id),
        label=chunk.document_name,
        snippet=snippet_text,
    )


async def execute_list_knowledge_sources(
    ctx: ToolContext,
    args: ListKnowledgeSourcesArgs,
) -> ToolResult:
    kind_filter = [KnowledgeDocumentKind(args.kind)] if args.kind is not None else None
    sources = await knowledge_search_service.list_ready_documents(
        ctx.db,
        tenant_id=ctx.tenant_id,
        kind_filter=kind_filter,
    )
    items = [s.model_dump(mode="json") for s in sources]
    return ToolResult(ok=True, data={"sources": items}, citations=[])


async def execute_search_knowledge(
    ctx: ToolContext,
    args: SearchKnowledgeArgs,
) -> ToolResult:
    from app.llm.client import get_llm_client

    filters = KnowledgeSearchFilters(
        kind=_kind_filter_from_strings(args.kind),
        top_k=args.top_k,
    )
    result = await knowledge_search_service.search(
        ctx.db,
        tenant_id=ctx.tenant_id,
        query=args.query,
        filters=filters,
        llm_client=get_llm_client(),
        redis=get_redis(),
        langfuse_parent_trace_id=ctx.langfuse_trace_id,
    )
    chunks_payload = [_chunk_to_tool_dict(c, truncate=True) for c in result.chunks]
    citations = [_citation_from_chunk(c) for c in result.chunks]
    return ToolResult(
        ok=True,
        data={
            "query": result.query,
            "total_found": result.total_found,
            "latency_ms": result.latency_ms,
            "chunks": chunks_payload,
        },
        citations=citations,
    )


async def execute_get_knowledge_chunk(
    ctx: ToolContext,
    args: GetKnowledgeChunkArgs,
) -> ToolResult:
    try:
        chunk_uuid = UUID(args.chunk_id.strip())
    except ValueError:
        return ToolResult(
            ok=False,
            data={"error": "invalid_chunk_id", "chunk_id": args.chunk_id},
            citations=[],
            error="invalid_chunk_id",
        )

    chunk = await knowledge_search_service.get_chunk_by_id(
        ctx.db,
        tenant_id=ctx.tenant_id,
        chunk_id=chunk_uuid,
    )
    if chunk is None:
        return ToolResult(
            ok=False,
            data={"error": "chunk_not_found", "chunk_id": str(chunk_uuid)},
            citations=[],
            error="chunk_not_found",
        )

    return ToolResult(
        ok=True,
        data={"chunk": _chunk_detail_to_tool_dict(chunk)},
        citations=[_citation_from_chunk(chunk)],
    )


def build_channel_registry() -> ToolRegistry:
    """Registry exclusivo para canales externos (WhatsApp, Telegram).

    Garantías de seguridad (defensa en profundidad):
      1. Solo registra tools de ``ToolFamily.knowledge`` y ``ToolFamily.calendar`` —
         las tools documentales (facturas, tickets) no existen en este registry;
         ``execute()`` devuelve "Unknown tool" si el LLM intentase llamarlas.
      2. ``allowed_families`` acota la familia permitida: aunque se registrase
         accidentalmente una tool documental, ``execute()`` la rechazaría.
      3. RLS de tenant activo en ``execute()`` vía ``set_tenant_context``: el
         cliente externo solo ve datos del tenant asociado a su número de teléfono.
    """
    from app.llm.tools.calendar_tools import register_calendar_tools

    registry = ToolRegistry(
        allowed_families=frozenset({ToolFamily.knowledge, ToolFamily.calendar}),
    )
    register_knowledge_tools(registry)
    register_calendar_tools(registry)
    return registry


def register_knowledge_tools(registry: ToolRegistry) -> None:
    """Registra las tres tools reales de conocimiento (familia ``knowledge``)."""
    registry.register(
        ToolDefinition(
            name="list_knowledge_sources",
            family=ToolFamily.knowledge,
            description=(
                "Lista los documentos de la base de conocimiento disponibles para consulta "
                "(estado indexado). Úsala para saber qué fuentes existen antes de buscar "
                "o para acotar por categoría (horarios, políticas, contratos, etc.)."
            ),
            parameters_model=ListKnowledgeSourcesArgs,
            executor=execute_list_knowledge_sources,
        ),
    )
    registry.register(
        ToolDefinition(
            name="search_knowledge",
            family=ToolFamily.knowledge,
            description=(
                "Busca en la base de conocimiento de la empresa usando búsqueda híbrida "
                "(semántica + palabras clave). Devuelve los fragmentos más relevantes. "
                "Úsala cuando el usuario pregunte sobre políticas, horarios, servicios, "
                "contratos u otro contenido de la base de conocimiento."
            ),
            parameters_model=SearchKnowledgeArgs,
            executor=execute_search_knowledge,
        ),
    )
    registry.register(
        ToolDefinition(
            name="get_knowledge_chunk",
            family=ToolFamily.knowledge,
            description=(
                "Recupera el texto completo de un fragmento de conocimiento por su ID. "
                "Úsala cuando necesites el contenido íntegro de un resultado de "
                "search_knowledge."
            ),
            parameters_model=GetKnowledgeChunkArgs,
            executor=execute_get_knowledge_chunk,
        ),
    )
