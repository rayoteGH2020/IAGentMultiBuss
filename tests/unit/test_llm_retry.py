"""Reintentos ante errores transitorios del proveedor LLM (extracción y chat)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.document_processing_errors import PROVIDER_OVERLOAD_USER_MESSAGE
from app.llm import chat_loop
from app.llm import retry as llm_retry
from tenacity import wait_none

_GEMINI_503 = (
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently "
    "experiencing high demand. Spikes in demand are usually temporary. Please try "
    "again later.', 'status': 'UNAVAILABLE'}}"
)


class _ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin esperas reales entre reintentos: el test solo comprueba la política."""
    monkeypatch.setattr(llm_retry, "wait_exponential_jitter", lambda **_kw: wait_none())


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_ApiError(503, _GEMINI_503), True),
        (_ApiError(429, "RESOURCE_EXHAUSTED"), True),
        (_ApiError(400, "INVALID_ARGUMENT"), False),
        (_ApiError(401, "unauthorized"), False),
        (Exception(_GEMINI_503), True),  # sin .code: markers del mensaje
        (Exception("boom"), False),
    ],
)
def test_is_retryable_provider_error(exc: BaseException, expected: bool) -> None:
    assert llm_retry.is_retryable_provider_error(exc) is expected


@pytest.mark.asyncio
async def test_call_with_transient_retry_recovers_after_503() -> None:
    call = AsyncMock(side_effect=[_ApiError(503, _GEMINI_503), _ApiError(503, _GEMINI_503), "ok"])
    result = await llm_retry.call_with_transient_retry(
        call, max_attempts=3, max_wait_seconds=4.0, provider="google", model="m"
    )
    assert result == "ok"
    assert call.await_count == 3


@pytest.mark.asyncio
async def test_call_with_transient_retry_reraises_original_after_max_attempts() -> None:
    call = AsyncMock(side_effect=_ApiError(503, _GEMINI_503))
    with pytest.raises(_ApiError):
        await llm_retry.call_with_transient_retry(
            call, max_attempts=3, max_wait_seconds=4.0, provider="google", model="m"
        )
    assert call.await_count == 3


@pytest.mark.asyncio
async def test_call_with_transient_retry_does_not_retry_client_errors() -> None:
    call = AsyncMock(side_effect=_ApiError(400, "INVALID_ARGUMENT"))
    with pytest.raises(_ApiError):
        await llm_retry.call_with_transient_retry(
            call, max_attempts=3, max_wait_seconds=4.0, provider="google", model="m"
        )
    assert call.await_count == 1


def _turn(text: str) -> chat_loop._TurnOutcome:
    return chat_loop._TurnOutcome(
        assistant_message={"role": "assistant", "content": text},
        final_text=text,
        tool_calls=[],
        input_tokens=1,
        output_tokens=1,
        raw=None,
    )


def _settings(*, retry: bool, max_attempts: int = 4, max_wait: float = 15.0) -> MagicMock:
    return MagicMock(
        llm_retry_transient_errors=retry,
        llm_retry_max_attempts=max_attempts,
        llm_retry_max_wait_seconds=max_wait,
        knowledge_tools_enabled=False,
    )


async def _run_turn(settings: MagicMock) -> chat_loop._TurnOutcome:
    return await chat_loop._run_turn(
        provider="google",
        model="gemini-3.5-flash-lite",
        conversation=[{"role": "user", "content": "hola"}],
        registry=MagicMock(),
        settings=settings,
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )


@pytest.mark.asyncio
async def test_chat_turn_retries_503_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini = AsyncMock(side_effect=[_ApiError(503, _GEMINI_503), _turn("Listo.")])
    monkeypatch.setattr(chat_loop, "_gemini_turn", gemini)

    turn = await _run_turn(_settings(retry=True))

    assert turn.final_text == "Listo."
    assert gemini.await_count == 2


@pytest.mark.asyncio
async def test_chat_turn_caps_attempts_below_extraction_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gemini = AsyncMock(side_effect=_ApiError(503, _GEMINI_503))
    monkeypatch.setattr(chat_loop, "_gemini_turn", gemini)

    with pytest.raises(_ApiError):
        await _run_turn(_settings(retry=True, max_attempts=10))

    assert gemini.await_count == chat_loop._CHAT_RETRY_MAX_ATTEMPTS == 3


@pytest.mark.asyncio
async def test_chat_turn_no_retry_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini = AsyncMock(side_effect=_ApiError(503, _GEMINI_503))
    monkeypatch.setattr(chat_loop, "_gemini_turn", gemini)

    with pytest.raises(_ApiError):
        await _run_turn(_settings(retry=False))

    assert gemini.await_count == 1


class _FakeObs:
    def update(self, **_kwargs: object) -> None:
        pass

    def end(self) -> None:
        pass


class _FakeLangfuse:
    def create_trace_id(self) -> object:
        return uuid4()

    def start_observation(self, **_kwargs: object) -> _FakeObs:
        return _FakeObs()

    def flush(self) -> None:
        pass


async def _run_loop_with_error(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> str:
    from app.llm.tools.registry import ToolContext, ToolRegistry

    monkeypatch.setattr(chat_loop, "_gemini_turn", AsyncMock(side_effect=exc))
    monkeypatch.setattr(chat_loop, "get_langfuse", lambda: _FakeLangfuse())
    monkeypatch.setattr(chat_loop, "LLMCall", lambda **_kw: MagicMock(id=uuid4()))
    monkeypatch.setattr(
        chat_loop, "filter_citations_existing_for_tenant", AsyncMock(return_value=[])
    )
    db = AsyncMock()
    db.add = MagicMock()
    ctx = ToolContext(db=db, tenant_id=uuid4())
    result = await chat_loop.run_tool_loop(
        provider="google",
        model="gemini-3.5-flash-lite",
        messages=[{"role": "user", "content": "hola"}],
        registry=ToolRegistry(),
        ctx=ctx,
        tenant_id=ctx.tenant_id,
        db=db,
        prompt_version="chat_documents_v1",
        settings=_settings(retry=True),
        anthropic_client=AsyncMock(),
        google_client=MagicMock(),
    )
    return result.final_text


@pytest.mark.asyncio
async def test_chat_shows_overload_message_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = await _run_loop_with_error(monkeypatch, _ApiError(503, _GEMINI_503))
    assert text == PROVIDER_OVERLOAD_USER_MESSAGE


@pytest.mark.asyncio
async def test_chat_shows_generic_message_for_other_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = await _run_loop_with_error(monkeypatch, ValueError("unexpected"))
    assert text == chat_loop._GENERIC_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_chat_shows_billing_message_and_alerts_sadm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """402 (créditos agotados) no se reintenta, no dice "muchas solicitudes" y avisa."""
    from app.core.document_processing_errors import PROVIDER_BILLING_USER_MESSAGE

    alert = AsyncMock()
    monkeypatch.setattr(chat_loop, "alert_if_provider_billing_error", alert)
    gemini_402 = (
        "402 RESOURCE_EXHAUSTED. {'error': {'code': 402, 'message': 'Your prepayment "
        "credits are depleted.'}}"
    )

    text = await _run_loop_with_error(monkeypatch, _ApiError(402, gemini_402))

    assert text == PROVIDER_BILLING_USER_MESSAGE
    alert.assert_awaited_once()
    assert alert.await_args.args[0] == "google"
