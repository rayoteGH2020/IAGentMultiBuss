"""Tests unitarios de tools de conocimiento (Paso 19 Fase C)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.llm.tools.knowledge_tools import (
    MAX_CONTENT_CHARS,
    GetKnowledgeChunkArgs,
    ListKnowledgeSourcesArgs,
    SearchKnowledgeArgs,
    _chunk_to_tool_dict,
    execute_get_knowledge_chunk,
    execute_list_knowledge_sources,
    execute_search_knowledge,
    register_knowledge_tools,
)
from app.llm.tools.registry import ToolContext, ToolRegistry
from app.models.knowledge import KnowledgeDocumentKind
from app.schemas.knowledge_search import (
    KnowledgeChunkRef,
    KnowledgeSearchResult,
    KnowledgeSourceRef,
)


def _sample_chunk(*, content: str = "texto corto") -> KnowledgeChunkRef:
    doc_id = uuid4()
    return KnowledgeChunkRef(
        id=uuid4(),
        document_id=doc_id,
        document_name="Manual interno",
        kind=KnowledgeDocumentKind.policy,
        position=0,
        content=content,
        context="Contexto opcional",
        metadata={"token_estimate": 10},
        score=0.032,
    )


def test_list_knowledge_sources_serialization_excludes_embedding() -> None:
    payload = KnowledgeSourceRef(
        id=uuid4(),
        name="Política de gastos",
        kind=KnowledgeDocumentKind.policy,
        chunk_count=12,
        ingested_at=datetime(2025, 1, 15, tzinfo=UTC).isoformat(),
    ).model_dump(mode="json")
    assert "embedding" not in payload
    assert payload["name"] == "Política de gastos"


def test_search_knowledge_truncates_content() -> None:
    long_content = "x" * (MAX_CONTENT_CHARS + 500)
    chunk = _sample_chunk(content=long_content)
    out = _chunk_to_tool_dict(chunk, truncate=True)
    assert len(out["content"]) == MAX_CONTENT_CHARS
    assert "embedding" not in out


@pytest.mark.asyncio
async def test_get_knowledge_chunk_not_found() -> None:
    ctx = ToolContext(db=AsyncMock(), tenant_id=uuid4())
    with patch(
        "app.llm.tools.knowledge_tools.knowledge_search_service.get_chunk_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await execute_get_knowledge_chunk(
            ctx,
            GetKnowledgeChunkArgs(chunk_id=str(uuid4())),
        )
    assert result.ok is False
    assert result.error == "chunk_not_found"


@pytest.mark.asyncio
async def test_list_knowledge_sources_executor() -> None:
    source_id = uuid4()
    ctx = ToolContext(db=AsyncMock(), tenant_id=uuid4())
    mock_sources = [
        KnowledgeSourceRef(
            id=source_id,
            name="Horarios",
            kind=KnowledgeDocumentKind.schedule,
            chunk_count=3,
            ingested_at=None,
        ),
    ]
    with patch(
        "app.llm.tools.knowledge_tools.knowledge_search_service.list_ready_documents",
        new_callable=AsyncMock,
        return_value=mock_sources,
    ) as list_mock:
        result = await execute_list_knowledge_sources(
            ctx,
            ListKnowledgeSourcesArgs(kind="schedule"),
        )
    list_mock.assert_awaited_once()
    assert result.ok is True
    sources = cast(list[dict[str, object]], result.data["sources"])
    assert sources[0]["id"] == str(source_id)
    assert "embedding" not in sources[0]


@pytest.mark.asyncio
async def test_search_knowledge_executor_truncates_and_cites() -> None:
    chunk = _sample_chunk(content="a" * 2000)
    search_result = KnowledgeSearchResult(
        query="horario",
        total_found=1,
        chunks=[chunk],
        latency_ms=42,
    )
    ctx = ToolContext(db=AsyncMock(), tenant_id=uuid4())
    mock_redis = MagicMock()
    with (
        patch(
            "app.llm.tools.knowledge_tools.knowledge_search_service.search",
            new_callable=AsyncMock,
            return_value=search_result,
        ),
        patch("app.llm.client.get_llm_client", return_value=MagicMock()),
        patch("app.llm.tools.knowledge_tools.get_redis", return_value=mock_redis),
    ):
        result = await execute_search_knowledge(
            ctx,
            SearchKnowledgeArgs(query="horario de atención", top_k=5),
        )
    assert result.ok is True
    chunks = cast(list[dict[str, object]], result.data["chunks"])
    assert len(chunks) == 1
    assert len(cast(str, chunks[0]["content"])) == MAX_CONTENT_CHARS
    assert result.citations[0].source == "knowledge"
    assert result.citations[0].id == str(chunk.id)


def test_registry_registers_three_knowledge_tools() -> None:
    from app.llm.tools.registry import ToolFamily

    registry = ToolRegistry()
    register_knowledge_tools(registry)
    names = {t.name for t in registry.list_definitions(families={ToolFamily.knowledge})}
    assert names == {
        "list_knowledge_sources",
        "search_knowledge",
        "get_knowledge_chunk",
    }


def test_build_document_registry_includes_knowledge_when_listed_with_flag() -> None:
    from unittest.mock import MagicMock, patch

    from app.llm.tools.document_chat import build_document_chat_registry

    registry = build_document_chat_registry()
    # Las tools knowledge siempre se registran en list_definitions,
    # independientemente del flag (el registro es estático).
    all_tools = {t.name for t in registry.list_definitions()}
    assert "search_knowledge" in all_tools
    assert "get_knowledge_chunk" in all_tools

    # Con flag activo (comportamiento Paso 20): list_for_llm incluye knowledge.
    # Se mockea el setting para que el test sea independiente del valor en config.py.
    mock_settings = MagicMock()
    mock_settings.knowledge_tools_enabled = True
    with patch("app.llm.tools.registry.get_settings", return_value=mock_settings):
        llm_tools = {t.name for t in registry.list_for_llm()}
    assert "search_knowledge" in llm_tools
