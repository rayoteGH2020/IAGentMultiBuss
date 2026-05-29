"""Tests unitarios de channel_chat_service (Paso 21 E.7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.llm.chat_loop import ToolLoopResult
from app.llm.tools import build_channel_registry
from app.llm.tools.registry import ToolFamily
from app.models.conversation import ChannelMessage, Conversation
from app.models.tenant import Tenant
from app.schemas.chat import ChatCitation
from app.services import channel_chat_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(name: str = "Test Biz") -> Tenant:
    t = Tenant(name=name, plan="free", settings={})
    t.id = uuid4()
    return t


def _fake_loop_result(
    *,
    text: str = "Respuesta generada.",
    citations: tuple[ChatCitation, ...] = (),
) -> ToolLoopResult:
    return ToolLoopResult(
        final_text=text,
        llm_call_ids=[],
        tool_calls_executed=[],
        turn_messages=(),
        citations=citations,
        knowledge_tools_used=bool(citations),
    )


def _db_execute_side_effects(
    *,
    conversation: Conversation | None,
    history: list[ChannelMessage] | None = None,
) -> list[AsyncMock]:
    """Genera la secuencia de resultados que devuelve db.execute() en answer_for_channel."""
    # Llamada 1: SELECT conversation → scalar_one_or_none
    conv_result = AsyncMock()
    conv_result.scalar_one_or_none = MagicMock(return_value=conversation)

    # Llamada 2: SELECT channel_messages → scalars().all()
    hist_result = AsyncMock()
    scalars_mock = MagicMock()
    scalars_mock.all = MagicMock(return_value=list(history or []))
    hist_result.scalars = MagicMock(return_value=scalars_mock)

    return [conv_result, hist_result]


def _make_db(
    *, conversation: Conversation | None, history: list[ChannelMessage] | None = None
) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=_db_execute_side_effects(conversation=conversation, history=history)
    )
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tests: gestión de conversación
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_conversation_if_not_exists() -> None:
    """Si no hay conversación activa, se crea una nueva y se añade a la sesión."""
    tenant = _make_tenant()
    db = _make_db(conversation=None)

    mock_llm = AsyncMock()
    mock_llm.run_tool_loop = AsyncMock(return_value=_fake_loop_result())

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000001",
            message_text="Hola",
        )

    # db.add debe haberse llamado con al menos una Conversation
    added_types = [type(call.args[0]) for call in db.add.call_args_list]
    assert Conversation in added_types


@pytest.mark.asyncio
async def test_reuses_existing_conversation() -> None:
    """Si ya existe una conversación abierta, no se crea una nueva."""
    tenant = _make_tenant()
    existing_conv = Conversation(
        id=uuid4(),
        tenant_id=tenant.id,
        channel="whatsapp",
        customer_identifier="34600000002",
    )
    db = _make_db(conversation=existing_conv)

    mock_llm = AsyncMock()
    mock_llm.run_tool_loop = AsyncMock(return_value=_fake_loop_result())

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000002",
            message_text="¿Estáis abiertos?",
        )

    added_types = [type(call.args[0]) for call in db.add.call_args_list]
    # No debe haberse añadido ninguna Conversation nueva
    assert Conversation not in added_types
    # Sí deben haberse añadido los ChannelMessage del turno
    assert ChannelMessage in added_types


@pytest.mark.asyncio
async def test_confidence_zero_when_no_citations() -> None:
    """Cuando el LLM no devuelve citas, la confianza devuelta es 0.0."""
    tenant = _make_tenant()
    db = _make_db(conversation=None)

    mock_llm = AsyncMock()
    mock_llm.run_tool_loop = AsyncMock(return_value=_fake_loop_result(citations=()))

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        response = await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="telegram",
            customer_identifier="123456789",
            message_text="¿Qué servicios ofrecéis?",
        )

    assert response.confidence == pytest.approx(0.0)
    assert response.citations_count == 0


@pytest.mark.asyncio
async def test_confidence_positive_when_citations_present() -> None:
    """Cuando hay citas, la confianza es > 0."""
    tenant = _make_tenant()
    db = _make_db(conversation=None)

    citation = ChatCitation(
        ref=1,
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_name="FAQ",
        kind="faq",
        position=0,
        content_snippet="Abrimos de lunes a viernes.",
        score=0.014,  # score RRF típico (rango ~0.01-0.016)
    )

    mock_llm = AsyncMock()
    mock_llm.run_tool_loop = AsyncMock(return_value=_fake_loop_result(citations=(citation,)))

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        response = await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000003",
            message_text="¿Cuál es vuestro horario?",
        )

    assert response.confidence > 0.0
    assert response.citations_count == 1


# ---------------------------------------------------------------------------
# Test: guardrail — solo knowledge tools
# ---------------------------------------------------------------------------


def test_knowledge_tools_only_no_invoice_tools() -> None:
    """build_channel_registry() solo incluye tools de la familia knowledge."""
    registry = build_channel_registry()

    all_tools = registry.list_definitions()
    assert len(all_tools) > 0, "El registry de canal debe tener al menos una tool"

    for tool in all_tools:
        assert (
            tool.family == ToolFamily.knowledge
        ), f"Tool '{tool.name}' tiene familia '{tool.family}' en lugar de 'knowledge'"

    # Las tools documentales no deben existir en el registry de canal
    for doc_tool_name in (
        "list_doc_types",
        "search_documents",
        "get_document",
        "aggregate_documents",
    ):
        assert (
            registry.get(doc_tool_name) is None
        ), f"Tool documental '{doc_tool_name}' no debería estar en el channel registry"

    # El family guard también rechaza tools de otra familia aunque estén registradas
    assert registry.allowed_families == frozenset({ToolFamily.knowledge})
