"""Langfuse recibe metadatos de evaluación, nunca contenido de cliente."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import Settings, get_settings
from app.llm import observability
from app.llm.client import LLMClient
from app.llm.observability import (
    messages_summary,
    result_summary,
    text_summary,
    trace_messages,
    trace_result,
    trace_status_message,
    trace_text,
)
from pydantic import BaseModel, ValidationError

SECRET = "Juan Perez 12345678Z"  # pragma: allowlist secret


class _FakePdf:
    """Imita el objeto multimodal de Instructor: base64 en `.data`."""

    def __init__(self, payload: str) -> None:
        self.data = payload


class _Extraction(BaseModel):
    proveedor: str | None = None
    total: float | None = None
    lineas: list[str] = []
    confidence: float = 0.0


def _dump(payload: Any) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def test_messages_summary_keeps_shape_and_drops_content() -> None:
    messages = [
        {"role": "system", "content": "Eres un extractor"},
        {"role": "user", "content": [SECRET, _FakePdf("QkFTRTY0" * 10)]},
    ]

    summary = messages_summary(messages)

    assert summary["messages"] == 2
    assert summary["roles"] == {"system": 1, "user": 1}
    assert summary["text_chars"] == len("Eres un extractor") + len(SECRET)
    assert summary["media_parts"] == {"_FakePdf": 1}
    assert summary["media_bytes"] == 80
    assert SECRET not in _dump(summary)


def test_messages_summary_handles_dict_parts_and_none() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": SECRET}]},
        {"role": "assistant", "content": None},
        {"role": "tool", "content": json.dumps({"chunks": [SECRET]})},
    ]

    summary = messages_summary(messages)

    assert summary["roles"] == {"user": 1, "assistant": 1, "tool": 1}
    assert summary["text_chars"] > len(SECRET)
    assert SECRET not in _dump(summary)


def test_result_summary_reports_fields_not_values() -> None:
    result = _Extraction(proveedor=SECRET, total=None, lineas=["a", "b"], confidence=0.87)

    summary = result_summary(result)

    assert summary["schema"] == "_Extraction"
    assert summary["fields_present"] == ["confidence", "lineas", "proveedor"]
    assert summary["fields_missing"] == ["total"]
    assert summary["list_sizes"] == {"lineas": 2}
    assert summary["confidence"] == 0.87
    assert SECRET not in _dump(summary)


def test_result_summary_handles_none() -> None:
    assert result_summary(None) == {"result": None}


def test_text_summary_only_length() -> None:
    assert text_summary(SECRET) == {"chars": len(SECRET)}
    assert text_summary(None) == {"chars": 0}


def test_status_message_is_exception_type_only() -> None:
    assert (
        trace_status_message(
            error_type="ValidationError",
            error=f"1 validation error: got {SECRET}",
        )
        == "ValidationError"
    )
    assert trace_status_message(error_type=None, error=None) is None


def test_capture_content_flag_returns_raw_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability, "capture_content_enabled", lambda: True)

    messages = [{"role": "user", "content": SECRET}]
    result = _Extraction(proveedor=SECRET)

    assert trace_messages(messages) == messages
    assert trace_result(result)["proveedor"] == SECRET
    assert trace_text(SECRET) == SECRET


def test_capture_content_disabled_by_default() -> None:
    assert get_settings().langfuse_capture_content is False
    assert observability.capture_content_enabled() is False


def test_capture_content_rejected_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LANGFUSE_CAPTURE_CONTENT", "true")

    monkeypatch.setenv("APP_ENV", "development")
    assert Settings().langfuse_capture_content is True

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValidationError, match="LANGFUSE_CAPTURE_CONTENT"):
        Settings()


class _CapturingLangfuse:
    """Registra todo lo que el código intenta enviar a Langfuse."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def create_trace_id(self) -> Any:
        return uuid4()

    def start_observation(self, **kwargs: Any) -> MagicMock:
        self.payloads.append(kwargs)
        obs = MagicMock()
        obs.update = MagicMock(side_effect=lambda **kw: self.payloads.append(kw))
        obs.end = MagicMock()
        return obs

    def flush(self) -> None:
        pass

    def sent(self) -> str:
        return _dump(self.payloads)


@pytest.mark.asyncio
async def test_complete_sends_no_document_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _CapturingLangfuse()
    client = LLMClient.__new__(LLMClient)
    client._settings = get_settings()
    client._langfuse = fake

    async def fake_invoke(**_kwargs: Any) -> tuple[_Extraction, Any]:
        return _Extraction(proveedor=SECRET, total=12.5, confidence=0.9), MagicMock()

    monkeypatch.setattr(client, "_invoke_sdk", fake_invoke)

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    await client.complete(
        task="extraction",
        messages=[{"role": "user", "content": [SECRET, _FakePdf("QkFTRTY0")]}],
        response_model=_Extraction,
        tenant_id=uuid4(),
        db=db,
        prompt_version="v1",
        source_filename=f"{SECRET}.pdf",
    )

    sent = fake.sent()
    assert SECRET not in sent
    assert "QkFTRTY0" not in sent
    assert '"text_chars"' in sent
    assert '"usage_details"' in sent
    assert '"cost_details"' in sent
    # El nombre del fichero sí se persiste en llm_calls (BD con RLS).
    assert db.add.call_args.args[0].source_filename == f"{SECRET}.pdf"


@pytest.mark.asyncio
async def test_complete_error_sends_exception_type_only(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _CapturingLangfuse()
    client = LLMClient.__new__(LLMClient)
    client._settings = get_settings()
    client._langfuse = fake

    async def failing_invoke(**_kwargs: Any) -> tuple[_Extraction, Any]:
        raise RuntimeError(f"model returned unparseable output: {SECRET}")

    monkeypatch.setattr(client, "_invoke_sdk", failing_invoke)

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    from app.llm.client import LLMCompleteError

    with pytest.raises(LLMCompleteError):
        await client.complete(
            task="extraction",
            messages=[{"role": "user", "content": SECRET}],
            response_model=_Extraction,
            tenant_id=uuid4(),
            db=db,
            prompt_version="v1",
        )

    sent = fake.sent()
    assert SECRET not in sent
    assert "RuntimeError" in sent
    # El mensaje completo sí queda en llm_calls para diagnóstico interno.
    assert SECRET in (db.add.call_args.args[0].error or "")


@pytest.mark.asyncio
async def test_chat_loop_sends_no_conversation_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import chat_loop as cl
    from app.llm.tools.registry import ToolContext, ToolRegistry

    fake = _CapturingLangfuse()
    monkeypatch.setattr(cl, "get_langfuse", lambda: fake)

    async def fake_gemini(**_kwargs: Any) -> cl._TurnOutcome:
        return cl._TurnOutcome(
            assistant_message={"role": "assistant", "content": SECRET},
            final_text=SECRET,
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            raw=None,
        )

    monkeypatch.setattr(cl, "_gemini_turn", fake_gemini)
    monkeypatch.setattr(cl, "LLMCall", lambda **_kwargs: MagicMock(id=uuid4()))

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    ctx = ToolContext(db=db, tenant_id=uuid4())

    await cl.run_tool_loop(
        provider="google",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": SECRET}],
        registry=ToolRegistry(),
        ctx=ctx,
        tenant_id=ctx.tenant_id,
        db=db,
        prompt_version="chat_unified_v1",
        settings=get_settings(),
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )

    sent = fake.sent()
    assert SECRET not in sent
    assert '"text_chars"' in sent


@pytest.mark.asyncio
async def test_knowledge_search_span_has_no_query_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.schemas.knowledge_search import KnowledgeSearchFilters
    from app.services import knowledge_search_service as kss

    fake = _CapturingLangfuse()
    monkeypatch.setattr(kss, "get_langfuse", lambda: fake)
    monkeypatch.setattr(kss, "_check_search_rate", AsyncMock())
    monkeypatch.setattr(kss, "_dense_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(kss, "_sparse_search", AsyncMock(return_value=[]))
    monkeypatch.setattr(kss, "audit_service", MagicMock(log_action=AsyncMock()))

    llm = AsyncMock()
    llm.embed = AsyncMock(return_value=[[1.0] + [0.0] * 511])

    await kss.search(
        AsyncMock(),
        tenant_id=uuid4(),
        query=SECRET,
        filters=KnowledgeSearchFilters(top_k=3),
        llm_client=llm,
        redis=None,
    )

    sent = fake.sent()
    assert SECRET not in sent
    assert '"chars"' in sent
