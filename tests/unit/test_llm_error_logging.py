"""Los logs de fallos LLM llevan metadatos, nunca el mensaje crudo del SDK.

``str(exc)`` puede incluir la respuesta del modelo (Instructor al fallar el
schema) y con ella datos del documento; ese texto solo vive en ``llm_calls.error``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.document_processing_errors import PROVIDER_OVERLOAD_USER_MESSAGE
from app.core.errors import LLMCompleteError
from app.llm import chat_loop as cl
from app.llm import client as client_module
from app.llm import retry as retry_module
from app.llm.client import LLMClient
from app.llm.observability import error_log_fields
from app.llm.tools.registry import ToolContext, ToolRegistry
from app.schemas.entitlements import Entitlements
from pydantic import BaseModel

SECRET = "Juan Perez 12345678Z"  # pragma: allowlist secret


class _Extraction(BaseModel):
    proveedor: str | None = None


class _RecordingLogger:
    """Sustituto de structlog: guarda método, evento y kwargs de cada llamada."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, method: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((method, args, kwargs))

        return _record

    def bind(self, **kwargs: Any) -> _BoundRecordingLogger:
        return _BoundRecordingLogger(self.calls, kwargs)


class _BoundRecordingLogger:
    def __init__(
        self, calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]], ctx: dict[str, Any]
    ) -> None:
        self._calls = calls
        self._ctx = ctx

    def __getattr__(self, method: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> None:
            self._calls.append((method, args, {**self._ctx, **kwargs}))

        return _record


def _assert_no_content(recorder: _RecordingLogger) -> None:
    assert recorder.calls, "se esperaba al menos un log de error"
    for method, args, kwargs in recorder.calls:
        assert method != "exception", "logger.exception adjunta el traceback con el mensaje"
        assert "exc_info" not in kwargs
        assert "error" not in kwargs
        assert SECRET not in repr(args) + repr(kwargs)


def _fake_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _premium_entitlements(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.entitlement_service.resolve_tenant",
        AsyncMock(
            return_value=Entitlements(
                plan_code="premium",
                features=frozenset(),
                limits={"llm_budget_eur_month": None},
                fail_closed=False,
            )
        ),
    )


def _client_without_sdk() -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client._settings = get_settings()
    langfuse = MagicMock()
    langfuse.create_trace_id = MagicMock(side_effect=lambda: uuid4().hex)
    client._langfuse = langfuse
    return client


class _ProviderError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class _GenaiError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def test_error_log_fields_has_no_message() -> None:
    fields = error_log_fields(_ProviderError(f"schema failed: {SECRET}", status_code=400))

    assert fields == {
        "error_type": "_ProviderError",
        "status_code": 400,
        "provider_overload": False,
    }
    assert SECRET not in repr(fields)


def test_error_log_fields_reads_genai_code_and_overload() -> None:
    fields = error_log_fields(_GenaiError(f"503 UNAVAILABLE high demand {SECRET}", code=503))

    assert fields["status_code"] == 503
    assert fields["provider_overload"] is True
    assert SECRET not in repr(fields)


def test_error_log_fields_ignores_non_int_codes() -> None:
    exc = _ProviderError("x")
    exc.code = "INVALID_ARGUMENT"  # type: ignore[attr-defined]
    assert error_log_fields(exc)["status_code"] is None

    exc_bool = _ProviderError("x")
    exc_bool.status_code = True  # type: ignore[attr-defined]
    assert error_log_fields(exc_bool)["status_code"] is None


def test_llm_complete_error_keeps_raw_apart_from_message() -> None:
    exc = LLMCompleteError(
        "LLM call failed (ValidationError)", llm_call_id=uuid4(), raw_error=SECRET
    )

    assert SECRET not in str(exc)
    assert exc.persisted_error == SECRET

    no_raw = LLMCompleteError("LLM call failed", llm_call_id=uuid4())
    assert no_raw.persisted_error == "LLM call failed"


@pytest.mark.asyncio
async def test_complete_failure_logs_and_raises_without_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RecordingLogger()
    monkeypatch.setattr(client_module, "logger", recorder)
    _premium_entitlements(monkeypatch)
    client = _client_without_sdk()

    async def failing_invoke(**_kwargs: Any) -> tuple[_Extraction, Any]:
        raise _ProviderError(f"1 validation error for Factura: input_value='{SECRET}'")

    monkeypatch.setattr(client, "_invoke_sdk", failing_invoke)
    db = _fake_db()

    with pytest.raises(LLMCompleteError) as exc_info:
        await client.complete(
            task="extraction",
            messages=[{"role": "user", "content": "doc"}],
            response_model=_Extraction,
            tenant_id=uuid4(),
            db=db,
            prompt_version="v1",
        )

    _assert_no_content(recorder)
    _, _, kwargs = recorder.calls[-1]
    assert kwargs["error_type"] == "_ProviderError"

    exc = exc_info.value
    assert SECRET not in str(exc)
    assert exc.message == "LLM call failed (_ProviderError)"
    # El detalle técnico sigue en BD (llm_calls) y en persisted_error (documento, RLS).
    assert SECRET in (exc.raw_error or "")
    assert SECRET in exc.persisted_error
    assert SECRET in (db.add.call_args.args[0].error or "")


@pytest.mark.asyncio
async def test_complete_overload_keeps_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "logger", _RecordingLogger())
    _premium_entitlements(monkeypatch)
    client = _client_without_sdk()

    async def failing_invoke(**_kwargs: Any) -> tuple[_Extraction, Any]:
        raise _GenaiError(f"503 UNAVAILABLE {SECRET}", code=503)

    monkeypatch.setattr(client, "_invoke_sdk", failing_invoke)

    with pytest.raises(LLMCompleteError) as exc_info:
        await client.complete(
            task="extraction",
            messages=[{"role": "user", "content": "doc"}],
            response_model=_Extraction,
            tenant_id=uuid4(),
            db=_fake_db(),
            prompt_version="v1",
        )

    assert exc_info.value.message == PROVIDER_OVERLOAD_USER_MESSAGE


def test_anthropic_failure_log_has_no_content(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLogger()
    monkeypatch.setattr(client_module, "logger", recorder)

    client_module._log_anthropic_failure(
        event="anthropic_llm_call_failed",
        task="classify",
        model="claude-x",
        tenant_id=str(uuid4()),
        exc=_ProviderError(f"bad output {SECRET}", status_code=400),
    )

    _assert_no_content(recorder)
    _, _, kwargs = recorder.calls[0]
    assert kwargs["status_code"] == 400
    assert kwargs["provider"] == "anthropic"


def test_retry_log_has_no_content(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLogger()
    monkeypatch.setattr(retry_module, "logger", recorder)
    retry_state = MagicMock()
    retry_state.outcome.exception.return_value = _GenaiError(f"503 {SECRET}", code=503)
    retry_state.attempt_number = 1
    retry_state.next_action.sleep = 0.5

    retry_module.log_transient_retry(retry_state, provider="google", model="gemini-x")

    _assert_no_content(recorder)
    _, _, kwargs = recorder.calls[0]
    assert kwargs["status_code"] == 503


@pytest.mark.asyncio
async def test_chat_loop_failure_log_has_no_content(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RecordingLogger()
    monkeypatch.setattr(cl, "logger", recorder)
    monkeypatch.setattr(cl, "get_langfuse", lambda: MagicMock())
    monkeypatch.setattr(cl, "LLMCall", lambda **_kwargs: MagicMock(id=uuid4()))

    async def failing_gemini(**_kwargs: Any) -> cl._TurnOutcome:
        raise _ProviderError(f"model said {SECRET}", status_code=400)

    monkeypatch.setattr(cl, "_gemini_turn", failing_gemini)
    db = _fake_db()
    ctx = ToolContext(db=db, tenant_id=uuid4())

    await cl.run_tool_loop(
        provider="google",
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "hola"}],
        registry=ToolRegistry(),
        ctx=ctx,
        tenant_id=ctx.tenant_id,
        db=db,
        prompt_version="chat_unified_v1",
        settings=get_settings(),
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )

    failures = [c for c in recorder.calls if c[1] and c[1][0] == "chat_loop.turn_failed"]
    assert failures
    _assert_no_content(_only(failures))


def _only(
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]],
) -> _RecordingLogger:
    recorder = _RecordingLogger()
    recorder.calls = calls
    return recorder
