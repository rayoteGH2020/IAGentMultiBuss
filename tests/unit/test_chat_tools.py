"""Tests de tools LLM para chat documental (Paso 16 Fase C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.llm.tools.document_chat import build_document_chat_registry
from app.llm.tools.knowledge_tools import register_knowledge_tools
from app.llm.tools.registry import ToolContext, ToolRegistry, get_tools_for_chat


@pytest.mark.asyncio
async def test_search_knowledge_returns_not_available_when_disabled() -> None:
    registry = ToolRegistry()
    register_knowledge_tools(registry)
    ctx = ToolContext(
        db=AsyncMock(),
        tenant_id=uuid4(),
        user_id=uuid4(),
    )
    with patch(
        "app.llm.tools.registry.get_settings",
        return_value=MagicMock(knowledge_tools_enabled=False),
    ):
        result = await registry.execute(
            "search_knowledge",
            {"query": "política de gastos"},
            ctx,
        )
    assert result.ok is False
    assert result.error == "knowledge_not_available"
    assert result.data.get("error") == "knowledge_not_available"


def test_get_tools_for_chat_respects_knowledge_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.llm.tools.registry.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=False),
    )
    names = {t.name for t in get_tools_for_chat()}
    assert "search_documents" in names
    assert "search_knowledge" not in names

    monkeypatch.setattr(
        "app.llm.tools.registry.get_settings",
        lambda: MagicMock(knowledge_tools_enabled=True),
    )
    names_on = {t.name for t in get_tools_for_chat()}
    assert "search_knowledge" in names_on


def test_document_registry_exposes_unified_tools() -> None:
    registry = build_document_chat_registry()
    names = {t.name for t in registry.list_for_llm()}
    assert names == {
        "list_doc_types",
        "search_documents",
        "get_document",
        "aggregate_documents",
        "list_document_parties",
        "list_knowledge_sources",
        "search_knowledge",
        "get_knowledge_chunk",
    }


def test_registry_gemini_and_anthropic_schemas() -> None:
    from app.llm.tools.registry import _gemini_parameters_schema

    registry = build_document_chat_registry()
    anthropic = registry.to_anthropic_tools()
    gemini = registry.to_gemini_tools()
    assert len(anthropic) == 8
    assert len(gemini) == 1
    assert anthropic[0]["name"] == "list_doc_types"
    assert "input_schema" in anthropic[0]

    for tool in registry.list_for_llm():
        schema = _gemini_parameters_schema(tool.parameters_model)
        assert "additionalProperties" not in schema

    for decl in gemini[0].function_declarations:
        params = decl.parameters
        assert params is not None
        assert getattr(params, "additional_properties", None) is None


@pytest.mark.asyncio
async def test_run_tool_loop_executes_tool_and_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm.chat_loop import ToolLoopResult, _TurnOutcome, run_tool_loop
    from app.llm.tools.registry import ToolContext, ToolRegistry, ToolResult

    registry = ToolRegistry()

    async def fake_executor(_ctx: ToolContext, _args: object) -> ToolResult:
        return ToolResult(ok=True, data={"hello": "world"}, citations=[])

    from pydantic import BaseModel

    class EmptyModel(BaseModel):
        pass

    from app.llm.tools.registry import ToolDefinition, ToolFamily

    registry.register(
        ToolDefinition(
            name="ping",
            family=ToolFamily.document,
            description="ping",
            parameters_model=EmptyModel,
            executor=fake_executor,
        ),
    )

    turn_with_tool = _TurnOutcome(
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "ping", "arguments": {}}],
        },
        final_text=None,
        tool_calls=[{"id": "c1", "name": "ping", "arguments": {}}],
        input_tokens=10,
        output_tokens=5,
        raw=None,
    )
    turn_final = _TurnOutcome(
        assistant_message={"role": "assistant", "content": "Listo."},
        final_text="Listo.",
        tool_calls=[],
        input_tokens=8,
        output_tokens=4,
        raw=None,
    )

    call_count = [0]

    async def fake_gemini(**_kwargs: object) -> _TurnOutcome:
        if call_count[0] == 0:
            call_count[0] += 1
            return turn_with_tool
        return turn_final

    monkeypatch.setattr("app.llm.chat_loop._gemini_turn", fake_gemini)

    db = AsyncMock()
    db.add = MagicMock()
    flush = AsyncMock()
    db.flush = flush

    class FakeObs:
        def update(self, **_kwargs: object) -> None:
            pass

        def end(self) -> None:
            pass

    class FakeLangfuse:
        def create_trace_id(self) -> object:
            return uuid4()

        def start_observation(self, **_kwargs: object) -> FakeObs:
            return FakeObs()

        def flush(self) -> None:
            pass

    monkeypatch.setattr("app.llm.chat_loop.get_langfuse", lambda: FakeLangfuse())

    def _fake_llm_call(**kwargs: object) -> MagicMock:
        row = MagicMock()
        row.id = uuid4()
        return row

    monkeypatch.setattr("app.llm.chat_loop.LLMCall", _fake_llm_call)

    ctx = ToolContext(db=db, tenant_id=uuid4())
    settings = MagicMock(knowledge_tools_enabled=False)

    result = await run_tool_loop(
        provider="google",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "hola"}],
        registry=registry,
        ctx=ctx,
        tenant_id=ctx.tenant_id,
        db=db,
        prompt_version="chat_documents_v1",
        max_iters=6,
        settings=settings,
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )

    assert isinstance(result, ToolLoopResult)
    assert result.final_text == "Listo."
    assert result.tool_calls_executed == ["ping"]
    assert len(result.llm_call_ids) == 2


@pytest.mark.asyncio
async def test_run_tool_loop_attaches_citations_from_search_knowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings
    from app.llm.chat_loop import run_tool_loop
    from app.llm.tools.registry import (
        ToolContext,
        ToolDefinition,
        ToolFamily,
        ToolRegistry,
        ToolResult,
    )
    from pydantic import BaseModel

    chunk_id = uuid4()
    doc_id = uuid4()

    async def search_executor(_ctx: ToolContext, _args: object) -> ToolResult:
        return ToolResult(
            ok=True,
            data={
                "chunks": [
                    {
                        "id": str(chunk_id),
                        "document_id": str(doc_id),
                        "source_name": "FAQ Horarios",
                        "kind": "faq",
                        "position": 0,
                        "content": "Lunes a viernes 9-18h",
                        "score": 0.9,
                    },
                ],
            },
            citations=[],
        )

    class SearchArgs(BaseModel):
        query: str

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search_knowledge",
            family=ToolFamily.knowledge,
            description="search",
            parameters_model=SearchArgs,
            executor=search_executor,
        ),
    )

    from app.llm.chat_loop import _TurnOutcome

    turn_tool = _TurnOutcome(
        assistant_message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "sk1",
                    "name": "search_knowledge",
                    "arguments": {"query": "horario"},
                },
            ],
        },
        final_text=None,
        tool_calls=[
            {"id": "sk1", "name": "search_knowledge", "arguments": {"query": "horario"}},
        ],
        input_tokens=10,
        output_tokens=5,
        raw=None,
    )
    turn_final = _TurnOutcome(
        assistant_message={"role": "assistant", "content": "Abrimos de 9 a 18 [1]."},
        final_text="Abrimos de 9 a 18 [1].",
        tool_calls=[],
        input_tokens=8,
        output_tokens=4,
        raw=None,
    )
    calls = [0]

    async def fake_gemini(**_kwargs: object) -> _TurnOutcome:
        if calls[0] == 0:
            calls[0] += 1
            return turn_tool
        return turn_final

    monkeypatch.setattr("app.llm.chat_loop._gemini_turn", fake_gemini)
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    class FakeObs:
        def update(self, **_kwargs: object) -> None:
            pass

        def end(self) -> None:
            pass

    class FakeLangfuse:
        def create_trace_id(self) -> object:
            return uuid4()

        def start_observation(self, **_kwargs: object) -> FakeObs:
            return FakeObs()

        def flush(self) -> None:
            pass

    monkeypatch.setattr("app.llm.chat_loop.get_langfuse", lambda: FakeLangfuse())
    monkeypatch.setattr(
        "app.llm.chat_loop.LLMCall",
        lambda **kwargs: MagicMock(id=uuid4()),
    )

    ctx = ToolContext(db=db, tenant_id=uuid4())
    settings = get_settings()

    result = await run_tool_loop(
        provider="google",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "horario"}],
        registry=registry,
        ctx=ctx,
        tenant_id=ctx.tenant_id,
        db=db,
        prompt_version="chat_unified_v1",
        settings=settings,
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )

    assert result.knowledge_tools_used is True
    assert len(result.citations) == 1
    assert result.citations[0].document_name == "FAQ Horarios"
    assistant_msgs = [m for m in result.turn_messages if m.role == "assistant" and m.content]
    final_msg = assistant_msgs[-1]
    assert final_msg.citations is not None
    assert final_msg.citations[0]["document_name"] == "FAQ Horarios"
