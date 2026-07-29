"""Tests unitarios de channel_chat_service (Paso 21 E.7)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.llm.chat_loop import ToolLoopResult
from app.llm.tools import build_channel_registry
from app.llm.tools.registry import ToolFamily
from app.models.channel_response_cache import ChannelResponseCache
from app.models.conversation import ChannelMessage, Conversation
from app.models.tenant import Tenant
from app.schemas.chat import ChatCitation
from app.services import channel_chat_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_EMBEDDING = [[0.1] * 512]


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
    cache_hit_row: dict[str, object] | None = None,
    conversation: Conversation | None,
    history: list[ChannelMessage] | None = None,
) -> list[AsyncMock]:
    """Genera la secuencia de resultados que devuelve db.execute() en answer_for_channel.

    Orden de llamadas:
      1. Cache lookup SELECT → mappings().one_or_none()
      2. Conversation SELECT → scalar_one_or_none()
      3a. (cache miss) History SELECT → scalars().all()
      3b. (cache hit)  UPDATE hit_count (resultado ignorado)
      4. Usage meter UPSERT (resultado ignorado)
    """
    # 1. Cache lookup
    cache_result = AsyncMock()
    mappings_mock = MagicMock()
    mappings_mock.one_or_none = MagicMock(return_value=cache_hit_row)
    cache_result.mappings = MagicMock(return_value=mappings_mock)

    # 2. Conversation SELECT
    conv_result = AsyncMock()
    conv_result.scalar_one_or_none = MagicMock(return_value=conversation)

    # 3. History (miss) o UPDATE hit_count (hit)
    third_result = AsyncMock()
    if cache_hit_row is None:
        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=list(history or []))
        third_result.scalars = MagicMock(return_value=scalars_mock)
    # Para cache hit, el UPDATE no necesita ningún atributo especial en el resultado.

    # 4. Usage meter UPSERT
    usage_result = AsyncMock()

    return [cache_result, conv_result, third_result, usage_result]


def _make_db(
    *,
    cache_hit_row: dict[str, object] | None = None,
    conversation: Conversation | None,
    history: list[ChannelMessage] | None = None,
) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=_db_execute_side_effects(
            cache_hit_row=cache_hit_row,
            conversation=conversation,
            history=history,
        )
    )
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _make_mock_llm(loop_result: ToolLoopResult | None = None) -> AsyncMock:
    mock = AsyncMock()
    mock.embed = AsyncMock(return_value=_FAKE_EMBEDDING)
    mock.run_tool_loop = AsyncMock(return_value=loop_result or _fake_loop_result())
    return mock


# ---------------------------------------------------------------------------
# Tests: gestión de conversación
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_conversation_if_not_exists() -> None:
    """Si no hay conversación activa, se crea una nueva y se añade a la sesión."""
    tenant = _make_tenant()
    db = _make_db(conversation=None)
    mock_llm = _make_mock_llm()

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000001",
            message_text="Hola",
        )

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
    mock_llm = _make_mock_llm()

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000002",
            message_text="¿Estáis abiertos?",
        )

    added_types = [type(call.args[0]) for call in db.add.call_args_list]
    assert Conversation not in added_types
    assert ChannelMessage in added_types


@pytest.mark.asyncio
async def test_confidence_zero_when_no_citations() -> None:
    """Cuando el LLM no devuelve citas, la confianza devuelta es 0.0."""
    tenant = _make_tenant()
    db = _make_db(conversation=None)
    mock_llm = _make_mock_llm(_fake_loop_result(citations=()))

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
    mock_llm = _make_mock_llm(_fake_loop_result(citations=(citation,)))

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
# Tests: semantic cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_skips_llm() -> None:
    """Si el cache devuelve una entrada similar, no se llama al LLM."""
    tenant = _make_tenant()
    cache_entry = {
        "id": str(uuid4()),
        "answer_text": "Respuesta del cache.",
        "confidence": 0.85,
    }
    db = _make_db(cache_hit_row=cache_entry, conversation=None)
    mock_llm = _make_mock_llm()

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        response = await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000004",
            message_text="¿A qué hora cerrais?",
        )

    assert response.text == "Respuesta del cache."
    assert response.confidence == pytest.approx(0.85)
    assert response.citations_count == 0
    mock_llm.run_tool_loop.assert_not_called()
    mock_llm.embed.assert_called_once()


@pytest.mark.asyncio
async def test_cache_hit_marks_metadata_flag() -> None:
    """En cache hit, el mensaje assistant tiene cache_hit=True en metadata."""
    tenant = _make_tenant()
    cache_entry = {
        "id": str(uuid4()),
        "answer_text": "Cached.",
        "confidence": 0.90,
    }
    existing_conv = Conversation(
        id=uuid4(), tenant_id=tenant.id, channel="whatsapp", customer_identifier="34600000010"
    )
    db = _make_db(cache_hit_row=cache_entry, conversation=existing_conv)
    mock_llm = _make_mock_llm()

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000010",
            message_text="¿Horario?",
        )

    assistant_msgs = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], ChannelMessage) and call.args[0].role == "assistant"
    ]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0].msg_metadata["cache_hit"] is True


@pytest.mark.asyncio
async def test_cache_miss_stores_high_confidence_response() -> None:
    """En cache miss con confidence alta, se inserta un ChannelResponseCache."""
    tenant = _make_tenant()
    citation = ChatCitation(
        ref=1,
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_name="FAQ",
        kind="faq",
        position=0,
        content_snippet="Cerramos a las 20h.",
        score=0.014,  # normalize_rrf_score(0.014, k=60) ≈ 0.854 > 0.6 min_confidence
    )
    db = _make_db(cache_hit_row=None, conversation=None)
    mock_llm = _make_mock_llm(_fake_loop_result(citations=(citation,)))

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="whatsapp",
            customer_identifier="34600000005",
            message_text="¿Cuándo cerrais?",
        )

    added_types = [type(call.args[0]) for call in db.add.call_args_list]
    assert ChannelResponseCache in added_types


@pytest.mark.asyncio
async def test_low_confidence_does_not_store_in_cache() -> None:
    """Si confidence < channel_cache_min_confidence (0.6), no se guarda en cache."""
    tenant = _make_tenant()
    # Sin citas → confidence = 0.0 < 0.6
    db = _make_db(cache_hit_row=None, conversation=None)
    mock_llm = _make_mock_llm(_fake_loop_result(citations=()))

    with patch("app.services.channel_chat_service.get_llm_client", return_value=mock_llm):
        await channel_chat_service.answer_for_channel(
            db,
            tenant,
            channel="telegram",
            customer_identifier="987654321",
            message_text="¿Algo más?",
        )

    added_types = [type(call.args[0]) for call in db.add.call_args_list]
    assert ChannelResponseCache not in added_types


# ---------------------------------------------------------------------------
# Test: guardrail — solo knowledge tools
# ---------------------------------------------------------------------------


def test_channel_registry_excludes_document_tools() -> None:
    """build_channel_registry() incluye knowledge + calendar, nunca document."""
    registry = build_channel_registry()

    all_tools = registry.list_definitions()
    assert len(all_tools) > 0, "El registry de canal debe tener al menos una tool"

    allowed = {ToolFamily.knowledge, ToolFamily.calendar}
    for tool in all_tools:
        assert tool.family in allowed, (
            f"Tool '{tool.name}' tiene familia '{tool.family}' no permitida en canal"
        )

    # Las tools documentales no deben existir en el registry de canal
    for doc_tool_name in (
        "list_doc_types",
        "search_documents",
        "get_document",
        "aggregate_documents",
    ):
        assert registry.get(doc_tool_name) is None, (
            f"Tool documental '{doc_tool_name}' no debería estar en el channel registry"
        )

    assert registry.allowed_families == frozenset({ToolFamily.knowledge, ToolFamily.calendar})
