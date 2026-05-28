"""Tests de observabilidad chat RAG (Paso 20 H)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import get_settings
from app.llm.chat_loop import _TurnOutcome, run_tool_loop
from app.llm.tools.registry import ToolContext, ToolDefinition, ToolFamily, ToolRegistry, ToolResult
from app.schemas.knowledge_search import KnowledgeSearchFilters
from pydantic import BaseModel

pytestmark = pytest.mark.asyncio


class _CapturingLangfuse:
    def __init__(self) -> None:
        self.observations: list[dict[str, object]] = []

    def create_trace_id(self) -> object:
        return uuid4()

    def start_observation(self, **kwargs: object) -> MagicMock:
        self.observations.append(kwargs)
        obs = MagicMock()
        obs.update = MagicMock()
        obs.end = MagicMock()
        return obs

    def flush(self) -> None:
        pass


@pytest.mark.asyncio
async def test_chat_rag_turn_langfuse_span(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _CapturingLangfuse()
    monkeypatch.setattr("app.llm.chat_loop.get_langfuse", lambda: fake)

    class SearchArgs(BaseModel):
        query: str

    async def search_exec(_ctx: ToolContext, _args: SearchArgs) -> ToolResult:
        assert _ctx.langfuse_trace_id is not None
        return ToolResult(ok=True, data={"chunks": []}, citations=[])

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="search_knowledge",
            family=ToolFamily.knowledge,
            description="search",
            parameters_model=SearchArgs,
            executor=search_exec,
        ),
    )

    turn_final = _TurnOutcome(
        assistant_message={"role": "assistant", "content": "OK"},
        final_text="OK",
        tool_calls=[],
        input_tokens=1,
        output_tokens=1,
        raw=None,
    )
    turn_tool = _TurnOutcome(
        assistant_message={"role": "assistant", "content": None, "tool_calls": []},
        final_text=None,
        tool_calls=[
            {"id": "sk1", "name": "search_knowledge", "arguments": {"query": "política"}},
        ],
        input_tokens=1,
        output_tokens=1,
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
    monkeypatch.setattr(
        "app.llm.chat_loop.LLMCall",
        lambda **kwargs: MagicMock(id=uuid4()),
    )

    thread_id = uuid4()
    ctx = ToolContext(db=db, tenant_id=uuid4(), thread_id=thread_id)
    settings = get_settings()

    await run_tool_loop(
        provider="google",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "política"}],
        registry=registry,
        ctx=ctx,
        tenant_id=ctx.tenant_id,
        db=db,
        prompt_version="chat_unified_v1",
        settings=settings,
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )

    rag_spans = [o for o in fake.observations if o.get("name") == "chat_rag_turn"]
    assert len(rag_spans) == 1
    meta = rag_spans[0].get("metadata")
    assert isinstance(meta, dict)
    assert meta.get("thread_id") == str(thread_id)
    assert meta.get("knowledge_tools_used") is False
    assert meta.get("citations_count") == 0


@pytest.mark.asyncio
async def test_knowledge_search_uses_parent_trace_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import knowledge_search_service as kss

    fake = _CapturingLangfuse()
    monkeypatch.setattr(kss, "get_langfuse", lambda: fake)
    monkeypatch.setattr(kss, "_check_search_rate", AsyncMock())
    monkeypatch.setattr(kss, "_dense_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(kss, "_sparse_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(kss, "audit_service", MagicMock(log_action=AsyncMock()))

    llm = AsyncMock()
    llm.embed = AsyncMock(return_value=[[1.0] + [0.0] * 511])

    db = AsyncMock()
    parent_trace = str(uuid4())

    await kss.search(
        db,
        tenant_id=uuid4(),
        query="horario",
        filters=KnowledgeSearchFilters(top_k=3),
        llm_client=llm,
        redis=None,
        langfuse_parent_trace_id=parent_trace,
    )

    search_spans = [o for o in fake.observations if o.get("name") == "search_knowledge"]
    assert len(search_spans) == 1
    trace_ctx = search_spans[0].get("trace_context")
    assert trace_ctx is not None
    trace_id = (
        trace_ctx.get("trace_id")
        if isinstance(trace_ctx, dict)
        else getattr(trace_ctx, "trace_id", None)
    )
    assert trace_id == parent_trace
