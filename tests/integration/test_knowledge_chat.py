"""Tests de integración del chat unificado con RAG (Paso 20)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.llm.chat_loop import TurnMessageRecord
from app.llm.chat_prompts import PROMPT_DOCUMENTS, PROMPT_UNIFIED, resolve_chat_prompt_version
from app.llm.tools.document_chat import build_document_chat_registry
from app.llm.tools.registry import ToolFamily
from app.models import Tenant, User
from app.services import chat_service
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_chat_uses_knowledge_tools_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = build_document_chat_registry()
    names = {t.name for t in registry.list_for_llm()}
    assert "search_knowledge" in names
    assert get_settings().knowledge_tools_enabled is True


@pytest.mark.asyncio
async def test_chat_skips_knowledge_tools_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.tools.registry.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=False),
    )
    registry = build_document_chat_registry()
    names = {t.name for t in registry.list_for_llm()}
    assert "search_knowledge" not in names


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
    user = User(email=f"rag-{uuid4().hex[:8]}@test.local")
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

    citations_json = [
        {
            "ref": 1,
            "chunk_id": str(uuid4()),
            "document_name": "FAQ",
            "kind": "faq",
            "position": 0,
            "content_snippet": "Horario martes viernes",
            "score": 0.8,
        },
    ]
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


def test_knowledge_tool_family_registered() -> None:
    registry = build_document_chat_registry()
    knowledge = [t for t in registry.list_definitions() if t.family == ToolFamily.knowledge]
    assert {t.name for t in knowledge} == {
        "list_knowledge_sources",
        "search_knowledge",
        "get_knowledge_chunk",
    }
