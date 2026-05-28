"""Tests de integración del chat unificado con RAG (Paso 20 Fase G)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.llm.chat_loop import ToolLoopResult, TurnMessageRecord
from app.llm.chat_prompts import PROMPT_DOCUMENTS, PROMPT_UNIFIED, resolve_chat_prompt_version
from app.llm.tools.knowledge_tools import SearchKnowledgeArgs, execute_search_knowledge
from app.llm.tools.registry import ToolContext, get_tools_for_chat
from app.models import AuditLog, ChatMessage, ChatMessageRole, Tenant, User
from app.models.usage_meter import UsageMeter
from app.schemas.knowledge_search import KnowledgeSearchFilters
from app.services import chat_service
from app.services import knowledge_search_service as kss
from app.services.audit_service import ACTION_KNOWLEDGE_CHAT_SEARCH
from app.services.chat_citations import (
    citations_to_json,
    extract_citations_from_search_data,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.knowledge_retrieval_helpers import (
    seed_knowledge_retrieval,
    unit_vector,
)
from tests.integration.test_knowledge_retrieval import (
    _FakeLLMClient,
    _patch_search_deps,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_chat_uses_knowledge_tools_when_enabled(
    chat_schema_ready: None,
    audit_schema_ready: None,
    usage_meter_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mensaje RAG ejecuta ``search_knowledge`` en el loop (mock)."""
    names = {t.name for t in get_tools_for_chat()}
    assert "search_knowledge" in names
    assert get_settings().knowledge_tools_enabled is True

    tenant = await tenant_factory()
    user = User(email=f"rag-tools-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread = await chat_service.create_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        title="RAG tools",
    )
    user_msg = ChatMessage(
        tenant_id=tenant.id,
        thread_id=thread.id,
        role=ChatMessageRole.user,
        content="¿Cuál es la política de vestimenta en sala?",
    )
    db_session.add(user_msg)
    await db_session.flush()

    chunk_id = uuid4()
    doc_id = uuid4()
    search_payload = {
        "chunks": [
            {
                "id": str(chunk_id),
                "document_id": str(doc_id),
                "source_name": "Política vestimenta",
                "kind": "policy",
                "position": 0,
                "content": "uniforme oscuro obligatorio en sala",
                "score": 0.82,
            },
        ],
    }
    citations = extract_citations_from_search_data(search_payload)
    citations_json = citations_to_json(citations)

    async def fake_loop(**_kwargs: object) -> ToolLoopResult:
        return ToolLoopResult(
            final_text="Debes llevar uniforme oscuro en sala [1].",
            llm_call_ids=[uuid4()],
            tool_calls_executed=["search_knowledge"],
            knowledge_tools_used=True,
            citations=tuple(citations),
            turn_messages=(
                TurnMessageRecord(
                    role="assistant",
                    content="",
                    tool_call={
                        "calls": [
                            {
                                "id": "c1",
                                "name": "search_knowledge",
                                "arguments": {"query": "política vestimenta"},
                            },
                        ],
                    },
                ),
                TurnMessageRecord(
                    role="tool",
                    tool_call={"id": "c1", "name": "search_knowledge", "arguments": {}},
                    tool_result={"ok": True, "data": search_payload},
                ),
                TurnMessageRecord(
                    role="assistant",
                    content="Debes llevar uniforme oscuro en sala [1].",
                    citations=citations_json,
                ),
            ),
        )

    mock_client = MagicMock()
    mock_client.run_tool_loop = fake_loop
    monkeypatch.setattr("app.services.chat_service.get_llm_client", lambda: mock_client)

    async for _ in chat_service._run_assistant_turn(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
    ):
        pass

    tool_rows = (
        (
            await db_session.execute(
                select(ChatMessage).where(
                    ChatMessage.thread_id == thread.id,
                    ChatMessage.role == ChatMessageRole.tool,
                ),
            )
        )
        .scalars()
        .all()
    )
    assert any((m.tool_call or {}).get("name") == "search_knowledge" for m in tool_rows)

    cited_rows = (
        (
            await db_session.execute(
                select(ChatMessage).where(
                    ChatMessage.thread_id == thread.id,
                    ChatMessage.role == ChatMessageRole.assistant,
                    ChatMessage.citations.isnot(None),
                ),
            )
        )
        .scalars()
        .all()
    )
    assert cited_rows
    assert any(len(row.citations or []) >= 1 for row in cited_rows)

    assistant_read = await chat_service.get_assistant_message_after_user(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
        user_message_id=user_msg.id,
    )
    assert assistant_read is not None
    assert assistant_read.content == "Debes llevar uniforme oscuro en sala [1]."
    assert assistant_read.citations is not None
    assert len(assistant_read.citations) == 1
    assert assistant_read.citations[0].chunk_id == chunk_id


@pytest.mark.asyncio
async def test_chat_skips_knowledge_tools_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.tools.registry.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=False),
    )
    names = {t.name for t in get_tools_for_chat()}
    assert "search_knowledge" not in names
    assert "list_knowledge_sources" not in names
    assert "get_knowledge_chunk" not in names


def test_chat_unified_prompt_loaded() -> None:
    assert resolve_chat_prompt_version() == PROMPT_UNIFIED


def test_chat_documents_prompt_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.llm.chat_prompts.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=False),
    )
    assert resolve_chat_prompt_version() == PROMPT_DOCUMENTS


@pytest.mark.asyncio
async def test_chat_citations_persisted(
    chat_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    user = User(email=f"rag-cite-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    thread = await chat_service.create_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        title="RAG test",
    )
    user_msg = await chat_service.post_user_message(
        db_session,
        AsyncMock(),
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
        content="¿Horario?",
    )

    doc_id = uuid4()
    chunk_id = uuid4()
    citations_json = citations_to_json(
        extract_citations_from_search_data(
            {
                "chunks": [
                    {
                        "id": str(chunk_id),
                        "document_id": str(doc_id),
                        "source_name": "FAQ",
                        "kind": "faq",
                        "position": 0,
                        "content": "Horario martes viernes",
                        "score": 0.8,
                    },
                ],
            },
        ),
    )
    await chat_service._persist_turn_messages(
        db_session,
        tenant_id=tenant.id,
        thread_id=thread.id,
        records=(
            TurnMessageRecord(
                role="assistant",
                content="Respuesta con fuente [1].",
                citations=citations_json,
            ),
        ),
    )
    await db_session.flush()

    assistant = await chat_service.get_assistant_message_after_user(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
        user_message_id=user_msg.id,
    )
    assert assistant is not None
    assert assistant.citations is not None
    assert len(assistant.citations) == 1
    assert assistant.citations[0].chunk_id == chunk_id
    assert assistant.citations[0].document_id == doc_id


@pytest.mark.asyncio
async def test_chat_citations_below_threshold_excluded(
    chat_schema_ready: None,
    audit_schema_ready: None,
    usage_meter_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunks con score bajo el umbral no aparecen en ``chat_messages.citations``."""
    settings = get_settings()
    threshold = settings.knowledge_chat_min_score_threshold
    high_id = uuid4()
    low_id = uuid4()
    search_payload = {
        "chunks": [
            {
                "id": str(high_id),
                "document_id": str(uuid4()),
                "source_name": "Alta",
                "kind": "faq",
                "position": 0,
                "content": "relevante",
                "score": threshold + 0.2,
            },
            {
                "id": str(low_id),
                "document_id": str(uuid4()),
                "source_name": "Baja",
                "kind": "faq",
                "position": 1,
                "content": "ruido",
                "score": max(0.0, threshold - 0.15),
            },
        ],
    }
    cites = extract_citations_from_search_data(search_payload, settings=settings)
    assert len(cites) == 1
    assert cites[0].chunk_id == high_id

    tenant = await tenant_factory()
    user = User(email=f"rag-thresh-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    thread = await chat_service.create_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        title="Threshold",
    )
    user_msg = ChatMessage(
        tenant_id=tenant.id,
        thread_id=thread.id,
        role=ChatMessageRole.user,
        content="consulta",
    )
    db_session.add(user_msg)
    await db_session.flush()

    citations_json = citations_to_json(cites)

    async def fake_loop(**_kwargs: object) -> ToolLoopResult:
        return ToolLoopResult(
            final_text="Respuesta [1].",
            llm_call_ids=[uuid4()],
            tool_calls_executed=["search_knowledge"],
            knowledge_tools_used=True,
            citations=tuple(cites),
            turn_messages=(
                TurnMessageRecord(
                    role="assistant",
                    content="Respuesta [1].",
                    citations=citations_json,
                ),
            ),
        )

    mock_client = MagicMock()
    mock_client.run_tool_loop = fake_loop
    monkeypatch.setattr("app.services.chat_service.get_llm_client", lambda: mock_client)

    async for _ in chat_service._run_assistant_turn(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
    ):
        pass

    assistant = await chat_service.get_assistant_message_after_user(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
        user_message_id=user_msg.id,
    )
    assert assistant is not None
    assert assistant.citations is not None
    assert len(assistant.citations) == 1
    assert assistant.citations[0].chunk_id == high_id
    assert all(c.chunk_id != low_id for c in assistant.citations)


@pytest.mark.asyncio
async def test_chat_rls_knowledge(
    knowledge_schema_ready: None,
    chat_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant B no recibe chunks indexados bajo tenant A."""
    tenant_a = await tenant_factory(name="Chat RAG Tenant A")
    tenant_b = await tenant_factory(name="Chat RAG Tenant B")
    await set_tenant_context(db_session, str(tenant_a.id))
    seed_a = await seed_knowledge_retrieval(db_session, tenant_id=tenant_a.id)

    await set_tenant_context(db_session, str(tenant_b.id))
    _patch_search_deps(monkeypatch, embed_vector=unit_vector(512, 0))
    llm = _FakeLLMClient(unit_vector(512, 0))
    monkeypatch.setattr("app.llm.client.get_llm_client", lambda: llm)

    ctx_b = ToolContext(db=db_session, tenant_id=tenant_b.id)
    tool_result = await execute_search_knowledge(
        ctx_b,
        SearchKnowledgeArgs(query="horario de atención al cliente", top_k=10),
    )
    assert tool_result.ok is True
    chunks = tool_result.data.get("chunks")
    assert isinstance(chunks, list)
    returned_ids = {item["id"] for item in chunks if isinstance(item, dict)}
    assert str(seed_a.schedule_chunk_id) not in returned_ids
    assert str(seed_a.contract_chunk_id) not in returned_ids

    search = await kss.search(
        db_session,
        tenant_id=tenant_b.id,
        query="CODIGOCONTRATO4242 facturación",
        filters=KnowledgeSearchFilters(top_k=5),
        llm_client=llm,  # type: ignore[arg-type]
        redis=None,
    )
    assert seed_a.contract_chunk_id not in {c.id for c in search.chunks}


@pytest.mark.asyncio
async def test_chat_rag_turn_logs_audit_and_usage_meter(
    chat_schema_ready: None,
    audit_schema_ready: None,
    usage_meter_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turno con knowledge tools: audit ``knowledge.chat_search`` + contador usage_meter."""
    from datetime import UTC, date, datetime

    tenant = await tenant_factory()
    user = User(email=f"rag-meter-{uuid4().hex[:8]}@test.local")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    thread = await chat_service.create_thread(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        title="Meter",
    )
    user_msg = ChatMessage(
        tenant_id=tenant.id,
        thread_id=thread.id,
        role=ChatMessageRole.user,
        content="¿Política de vestimenta?",
    )
    db_session.add(user_msg)
    await db_session.flush()

    search_payload = {
        "chunks": [
            {
                "id": str(uuid4()),
                "document_id": str(uuid4()),
                "source_name": "Política",
                "kind": "policy",
                "position": 0,
                "content": "uniforme",
                "score": 0.7,
            },
        ],
    }
    cite_models = extract_citations_from_search_data(search_payload)
    citations_json = citations_to_json(cite_models)

    async def fake_loop(**_kwargs: object) -> ToolLoopResult:
        return ToolLoopResult(
            final_text="Respuesta [1].",
            llm_call_ids=[uuid4()],
            tool_calls_executed=["search_knowledge"],
            knowledge_tools_used=True,
            citations=tuple(cite_models),
            turn_messages=(
                TurnMessageRecord(
                    role="assistant",
                    content="Respuesta [1].",
                    citations=citations_json,
                ),
            ),
        )

    mock_client = MagicMock()
    mock_client.run_tool_loop = fake_loop
    monkeypatch.setattr("app.services.chat_service.get_llm_client", lambda: mock_client)

    period = date(datetime.now(tz=UTC).year, datetime.now(tz=UTC).month, 1)
    async for _ in chat_service._run_assistant_turn(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
    ):
        pass
    await db_session.flush()

    audit_row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.action == ACTION_KNOWLEDGE_CHAT_SEARCH,
            ),
        )
    ).scalar_one_or_none()
    assert audit_row is not None
    assert audit_row.metadata_ is not None
    assert audit_row.metadata_["thread_id"] == str(thread.id)
    assert audit_row.metadata_["citations_count"] == len(citations_json)
    assert "query_hash" in audit_row.metadata_

    meter_row = (
        await db_session.execute(
            select(UsageMeter).where(
                UsageMeter.tenant_id == tenant.id,
                UsageMeter.period == period,
            ),
        )
    ).scalar_one_or_none()
    assert meter_row is not None
    assert meter_row.rag_messages_count >= 1


def test_knowledge_tool_names_registered() -> None:
    from app.llm.tools.document_chat import build_document_chat_registry
    from app.llm.tools.registry import ToolFamily

    registry = build_document_chat_registry()
    knowledge = [t for t in registry.list_definitions() if t.family == ToolFamily.knowledge]
    assert {t.name for t in knowledge} == {
        "list_knowledge_sources",
        "search_knowledge",
        "get_knowledge_chunk",
    }
