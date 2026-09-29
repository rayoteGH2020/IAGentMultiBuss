"""Coste real en llm_calls y presupuesto: reintentos, fallos y chat (P2b-14)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.errors import LLMCompleteError
from app.llm.client import LLMClient, _AttemptUsage
from app.llm.pricing import compute_cost_eur
from pydantic import BaseModel


class _Out(BaseModel):
    value: str


def _gemini_response(prompt: int, output: int) -> SimpleNamespace:
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt, candidates_token_count=output, thoughts_token_count=0
        )
    )


def test_attempt_usage_sums_every_gemini_attempt() -> None:
    usage = _AttemptUsage()
    for _ in range(3):
        usage.hooks.emit_completion_response(_gemini_response(1000, 100))

    assert usage.attempts == 3
    assert (usage.input_tokens, usage.output_tokens) == (3000, 300)


def test_attempt_usage_snapshots_before_instructor_accumulates() -> None:
    """Anthropic: Instructor reescribe el usage del último intento con el total.

    La foto se toma al emitir el hook, así que no se cuenta dos veces.
    """
    usage = _AttemptUsage()
    first = SimpleNamespace(usage=SimpleNamespace(input_tokens=500, output_tokens=50))
    second = SimpleNamespace(usage=SimpleNamespace(input_tokens=500, output_tokens=50))
    usage.hooks.emit_completion_response(first)
    usage.hooks.emit_completion_response(second)
    # Lo que hace update_total_usage tras el hook del segundo intento.
    second.usage.input_tokens, second.usage.output_tokens = 1000, 100

    assert (usage.input_tokens, usage.output_tokens) == (1000, 100)


class _Langfuse:
    def create_trace_id(self) -> Any:
        return uuid4()

    def start_observation(self, **_kwargs: Any) -> MagicMock:
        return MagicMock()

    def flush(self) -> None:
        pass


@pytest.fixture
def quota(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from app.services import entitlement_service, plan_quota_service

    monkeypatch.setattr(entitlement_service, "resolve_tenant", AsyncMock())
    monkeypatch.setattr(plan_quota_service, "ensure_llm_budget", AsyncMock())
    record = AsyncMock()
    monkeypatch.setattr(plan_quota_service, "record_llm_cost", record)
    return record


def _client() -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client._settings = get_settings()
    client._langfuse = _Langfuse()  # type: ignore[assignment]
    return client


def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


async def _complete(client: LLMClient, db: AsyncMock) -> Any:
    return await client.complete(
        task="extraction",
        messages=[{"role": "user", "content": "x"}],
        response_model=_Out,
        tenant_id=uuid4(),
        db=db,
    )


async def test_complete_counts_all_instructor_attempts(
    monkeypatch: pytest.MonkeyPatch, quota: AsyncMock
) -> None:
    client = _client()

    async def invoke(**kwargs: Any) -> tuple[_Out, Any]:
        responses = [_gemini_response(1000, 100) for _ in range(3)]
        for response in responses:
            kwargs["hooks"].emit_completion_response(response)
        return _Out(value="ok"), responses[-1]

    monkeypatch.setattr(client, "_invoke_sdk", invoke)
    db = _db()

    await _complete(client, db)

    llm_call = db.add.call_args.args[0]
    assert (llm_call.input_tokens, llm_call.output_tokens) == (3000, 300)
    expected = compute_cost_eur(llm_call.model, 3000, 300)
    assert llm_call.cost_eur == expected
    assert quota.await_args.kwargs["cost_eur"] == expected


async def test_failed_extraction_is_charged_for_processed_tokens(
    monkeypatch: pytest.MonkeyPatch, quota: AsyncMock
) -> None:
    client = _client()

    async def invoke(**kwargs: Any) -> tuple[_Out, Any]:
        for _ in range(3):
            kwargs["hooks"].emit_completion_response(_gemini_response(1000, 100))
        raise RuntimeError("schema validation failed after retries")

    monkeypatch.setattr(client, "_invoke_sdk", invoke)
    db = _db()

    with pytest.raises(LLMCompleteError):
        await _complete(client, db)

    llm_call = db.add.call_args.args[0]
    assert llm_call.status == "error"
    assert (llm_call.input_tokens, llm_call.output_tokens) == (3000, 300)
    assert llm_call.cost_eur > 0
    quota.assert_awaited_once()


async def test_error_without_tokens_is_not_charged(
    monkeypatch: pytest.MonkeyPatch, quota: AsyncMock
) -> None:
    client = _client()
    monkeypatch.setattr(client, "_invoke_sdk", AsyncMock(side_effect=RuntimeError("503")))
    db = _db()

    with pytest.raises(LLMCompleteError):
        await _complete(client, db)

    assert db.add.call_args.args[0].cost_eur == 0
    quota.assert_not_awaited()


async def test_chat_turn_cost_is_charged_to_budget(
    monkeypatch: pytest.MonkeyPatch, quota: AsyncMock
) -> None:
    from app.llm import chat_loop
    from app.llm.chat_loop import _TurnOutcome, run_tool_loop
    from app.llm.tools.registry import ToolContext, ToolRegistry

    monkeypatch.setattr(chat_loop, "get_langfuse", _Langfuse)
    monkeypatch.setattr(chat_loop, "LLMCall", lambda **_kw: MagicMock(id=uuid4()))
    monkeypatch.setattr(
        chat_loop,
        "_gemini_turn",
        AsyncMock(
            return_value=_TurnOutcome(
                assistant_message={"role": "assistant", "content": "OK"},
                final_text="OK",
                tool_calls=[],
                input_tokens=3000,
                output_tokens=100,
                raw=None,
            )
        ),
    )
    db = _db()
    tenant_id = uuid4()

    await run_tool_loop(
        provider="google",
        model="gemini-3.5-flash-lite",
        messages=[{"role": "user", "content": "hola"}],
        registry=ToolRegistry(),
        ctx=ToolContext(db=db, tenant_id=tenant_id),
        tenant_id=tenant_id,
        db=db,
        prompt_version="chat_unified_v1",
        settings=get_settings(),
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )

    quota.assert_awaited_once()
    assert quota.await_args.kwargs["tenant_id"] == tenant_id
    assert quota.await_args.kwargs["cost_eur"] == compute_cost_eur(
        "gemini-3.5-flash-lite", 3000, 100
    )
