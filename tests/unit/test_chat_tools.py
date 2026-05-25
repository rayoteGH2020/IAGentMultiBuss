"""Tests de tools LLM para chat documental (Paso 16 Fase C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.llm.tools.document_chat import (
    build_document_chat_registry,
    register_knowledge_tool_stubs,
)
from app.llm.tools.registry import ToolContext, ToolRegistry


@pytest.mark.asyncio
async def test_search_knowledge_returns_not_available_when_disabled() -> None:
    registry = ToolRegistry()
    register_knowledge_tool_stubs(registry)
    ctx = ToolContext(
        db=AsyncMock(),
        tenant_id=uuid4(),
        user_id=uuid4(),
    )
    result = await registry.execute(
        "search_knowledge",
        {"query": "política de gastos"},
        ctx,
    )
    assert result.ok is False
    assert result.error == "knowledge_not_available"
    assert result.data.get("error") == "knowledge_not_available"


def test_document_registry_exposes_five_tools() -> None:
    registry = build_document_chat_registry()
    names = {t.name for t in registry.list_for_llm()}
    assert names == {
        "list_doc_types",
        "search_documents",
        "get_document",
        "aggregate_documents",
        "list_document_parties",
    }


def test_registry_gemini_and_anthropic_schemas() -> None:
    registry = build_document_chat_registry()
    anthropic = registry.to_anthropic_tools()
    gemini = registry.to_gemini_tools()
    assert len(anthropic) == 5
    assert len(gemini) == 1
    assert anthropic[0]["name"] == "list_doc_types"
    assert "input_schema" in anthropic[0]


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
