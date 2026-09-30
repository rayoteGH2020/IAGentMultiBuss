"""Proveedor LLM sin saldo (402): mensaje propio, reintentable y aviso al SADM."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app.core.document_processing_errors import (
    PROVIDER_BILLING_USER_MESSAGE,
    PROVIDER_OVERLOAD_USER_MESSAGE,
    DocumentErrorCode,
    failure_message,
    is_provider_billing_error,
    is_provider_overload_error,
    is_retryable,
    provider_error_code,
    provider_error_user_message,
)
from app.jobs.settings import WorkerSettings
from app.llm import provider_alerts
from app.services import llm_provider_alert_service as svc

# Texto real de google-genai (dev, 2026-09-30), envuelto por Instructor.
_GOOGLE_402 = (
    '<failed_attempts>\n<generation number="1">\n<exception>\n    402 RESOURCE_EXHAUSTED. '
    "{'error': {'code': 402, 'message': 'Your prepayment credits are depleted. Please go to "
    "AI Studio at https://ai.studio/projects to manage your project and billing.', "
    "'status': 'RESOURCE_EXHAUSTED'}}"
)
_ANTHROPIC_CREDITS = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
)
_GOOGLE_429 = "429 RESOURCE_EXHAUSTED. Resource has been exhausted (e.g. check quota)."


@pytest.mark.parametrize("raw", [_GOOGLE_402, _ANTHROPIC_CREDITS])
def test_billing_errors_are_not_overload(raw: str) -> None:
    assert is_provider_billing_error(raw)
    assert not is_provider_overload_error(raw)
    assert provider_error_code(raw) is DocumentErrorCode.provider_billing
    assert provider_error_user_message(raw) == PROVIDER_BILLING_USER_MESSAGE


def test_rate_limit_resource_exhausted_is_still_overload() -> None:
    assert not is_provider_billing_error(_GOOGLE_429)
    assert provider_error_code(_GOOGLE_429) is DocumentErrorCode.provider_overload
    assert provider_error_user_message(_GOOGLE_429) == PROVIDER_OVERLOAD_USER_MESSAGE


@pytest.mark.parametrize("raw", ["used 4020 input tokens", "validation error", None, ""])
def test_unrelated_errors_are_not_provider_errors(raw: str | None) -> None:
    assert not is_provider_billing_error(raw)
    assert provider_error_code(raw) is None
    assert provider_error_user_message(raw) is None


def test_billing_failure_is_retryable_with_user_facing_message() -> None:
    message = failure_message(
        _GOOGLE_402, error_code=DocumentErrorCode.provider_billing, filename="ticket.jpg"
    )

    assert is_retryable(DocumentErrorCode.provider_billing.value)
    assert PROVIDER_BILLING_USER_MESSAGE in message
    assert "prepayment" not in message
    assert "muchas solicitudes" not in message


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        _ = ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())
    mock = AsyncMock()
    monkeypatch.setattr(svc, "_enqueue_alert", mock)
    return mock


async def test_alert_is_enqueued_once_per_provider_and_window(
    monkeypatch: pytest.MonkeyPatch, enqueued: AsyncMock
) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(svc, "get_redis", lambda: fake)  # mismo Redis entre llamadas

    await svc.notify_provider_billing_error("google")
    await svc.notify_provider_billing_error("google")
    await svc.notify_provider_billing_error("anthropic")

    assert [call.args for call in enqueued.await_args_list] == [("google",), ("anthropic",)]


async def test_alert_never_breaks_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(svc, "_enqueue_alert", AsyncMock(side_effect=ConnectionError("redis")))

    await svc.notify_provider_billing_error("google")


async def test_provider_alerts_only_fire_on_billing_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    notify = AsyncMock()
    monkeypatch.setattr(svc, "notify_provider_billing_error", notify)

    await provider_alerts.alert_if_provider_billing_error("google", _GOOGLE_429)
    await provider_alerts.alert_if_provider_billing_error("google", _GOOGLE_402)

    notify.assert_awaited_once_with("google")


@pytest.mark.parametrize(("email_sadm", "sent"), [("sadm@example.com", True), ("", False)])
async def test_sadm_email_without_customer_data(
    monkeypatch: pytest.MonkeyPatch, email_sadm: str, sent: bool
) -> None:
    send = AsyncMock()
    monkeypatch.setattr(svc, "send_email", send)
    monkeypatch.setattr(svc, "get_settings", lambda: SimpleNamespace(email_sadm=email_sadm))

    assert await svc.send_provider_billing_alert("google") is sent

    if sent:
        kwargs = send.await_args.kwargs
        assert kwargs["to"] == "sadm@example.com"
        assert "google" in kwargs["subject"]
        assert "aistudio.google.com" in kwargs["body"]
    else:
        send.assert_not_awaited()


def test_worker_registers_the_alert_job() -> None:
    names = {getattr(f, "__name__", getattr(f, "name", "")) for f in WorkerSettings.functions}
    assert "send_llm_provider_billing_alert" in names
