"""Avisos del presupuesto de IA: 80 % al admin, corte del chat al 90 % y contacto Clerk."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import llm_budget_alert_service as svc
from app.services.llm_budget_alert_service import BudgetUsage


class _FakeRedis:
    """Subconjunto de redis.asyncio usado por el servicio (sin caducidad real)."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        _ = ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def incr(self, key: str) -> int:
        self.values[key] = str(int(self.values.get(key, "0")) + 1)
        return int(self.values[key])

    async def expire(self, key: str, seconds: int) -> bool:
        _ = key, seconds
        return True


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(svc, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr(svc, "_enqueue_alert", mock)
    return mock


def _usage(monkeypatch: pytest.MonkeyPatch, spent: str, budget: str | None = "6") -> None:
    monkeypatch.setattr(
        svc,
        "get_budget_usage",
        AsyncMock(
            return_value=BudgetUsage(
                spent=Decimal(spent), budget=Decimal(budget) if budget else None
            )
        ),
    )


def test_ratio_handles_unlimited_and_zero_budget() -> None:
    assert BudgetUsage(spent=Decimal("5"), budget=None).ratio == 0.0
    assert BudgetUsage(spent=Decimal("0"), budget=Decimal("0")).ratio == 1.0
    assert BudgetUsage(spent=Decimal("5.4"), budget=Decimal("6")).ratio == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("phone", "email", "expected"),
    [
        ("600111222", "hola@negocio.es", "te ayudaremos (600111222 - hola@negocio.es)"),
        ("600111222", None, "te ayudaremos (600111222)"),
        (None, "hola@negocio.es", "te ayudaremos (hola@negocio.es)"),
        (None, None, "te ayudaremos."),
    ],
)
def test_cutoff_message_includes_available_contact(
    phone: str | None, email: str | None, expected: str
) -> None:
    message = svc.build_chat_cutoff_message(phone, email)
    assert message.startswith("En estos momentos no puedo responderte")
    assert message.endswith(expected)


async def test_warning_below_threshold_is_not_sent(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis, enqueued: AsyncMock
) -> None:
    _usage(monkeypatch, "4.70")  # 78 %
    await svc.maybe_warn_budget(AsyncMock(), uuid4())
    enqueued.assert_not_awaited()


async def test_warning_is_sent_once_per_month(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis, enqueued: AsyncMock
) -> None:
    _usage(monkeypatch, "4.90")  # 82 %
    tenant_id = uuid4()

    await svc.maybe_warn_budget(AsyncMock(), tenant_id)
    await svc.maybe_warn_budget(AsyncMock(), tenant_id)

    enqueued.assert_awaited_once_with(tenant_id, "budget_warning")


async def test_warning_never_breaks_the_caller(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis
) -> None:
    _usage(monkeypatch, "5.00")
    monkeypatch.setattr(svc, "_enqueue_alert", AsyncMock(side_effect=ConnectionError("redis")))
    await svc.maybe_warn_budget(AsyncMock(), uuid4())


async def test_chat_cutoff_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _usage(monkeypatch, "5.39")  # 89,8 %
    assert await svc.chat_cutoff_reached(AsyncMock(), uuid4()) is False
    _usage(monkeypatch, "5.40")  # 90 %
    assert await svc.chat_cutoff_reached(AsyncMock(), uuid4()) is True
    _usage(monkeypatch, "100", budget=None)  # sin presupuesto: nunca corta
    assert await svc.chat_cutoff_reached(AsyncMock(), uuid4()) is False


async def test_chat_cutoff_notifies_once_per_day_and_three_per_month(
    redis: _FakeRedis, enqueued: AsyncMock
) -> None:
    tenant_id = uuid4()
    daily_key = svc._period_key("chat_cutoff_daily", tenant_id)

    await svc.notify_chat_cutoff(tenant_id)
    await svc.notify_chat_cutoff(tenant_id)  # mismo día: no
    assert enqueued.await_count == 1

    for _ in range(4):
        redis.values.pop(daily_key, None)  # pasan 24 h
        await svc.notify_chat_cutoff(tenant_id)

    assert enqueued.await_count == 3
    assert all(call.args == (tenant_id, "chat_cutoff") for call in enqueued.await_args_list)


def _db_with_tenant(clerk_org_id: str | None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = SimpleNamespace(clerk_org_id=clerk_org_id, name="Org")
    db.execute = AsyncMock(return_value=result)
    return db


async def test_cutoff_message_reads_clerk_metadata_and_caches_it(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis
) -> None:
    org: dict[str, Any] = {
        "public_metadata": {"contact_phone": " 600111222 ", "contact_email": "hola@negocio.es"}
    }
    get_org = AsyncMock(return_value=org)
    monkeypatch.setattr(svc.clerk_client, "get_organization", get_org)
    db = _db_with_tenant("org_123")

    first = await svc.chat_cutoff_message(db, uuid4())
    second = await svc.chat_cutoff_message(db, uuid4())

    assert first == second
    assert first.endswith("(600111222 - hola@negocio.es)")
    get_org.assert_awaited_once_with("org_123")


async def test_cutoff_message_survives_clerk_failure(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis
) -> None:
    monkeypatch.setattr(
        svc.clerk_client, "get_organization", AsyncMock(side_effect=RuntimeError("clerk down"))
    )
    message = await svc.chat_cutoff_message(_db_with_tenant("org_123"), uuid4())
    assert message == svc.build_chat_cutoff_message(None, None)


async def test_record_llm_cost_checks_warning_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import plan_quota_service

    monkeypatch.setattr(plan_quota_service.usage_meter_service, "add_llm_cost_eur", AsyncMock())
    warn = AsyncMock()
    monkeypatch.setattr(plan_quota_service.llm_budget_alert_service, "maybe_warn_budget", warn)
    db = AsyncMock()
    tenant_id = uuid4()

    await plan_quota_service.record_llm_cost(db, tenant_id=tenant_id, cost_eur=Decimal("0.01"))
    await plan_quota_service.record_llm_cost(db, tenant_id=tenant_id, cost_eur=Decimal("0"))

    warn.assert_awaited_once_with(db, tenant_id)


async def test_alert_job_delegates_and_rejects_unknown_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from app.jobs import budget_alert_jobs

    session = AsyncMock()

    @asynccontextmanager
    async def _session(_tenant_id: object) -> Any:
        yield session

    monkeypatch.setattr(budget_alert_jobs, "session_factory_for_worker", _session)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(budget_alert_jobs.llm_budget_alert_service, "send_admin_alert", send)
    tenant_id = uuid4()

    result = await budget_alert_jobs.send_llm_budget_alert({}, str(tenant_id), "chat_cutoff")
    skipped = await budget_alert_jobs.send_llm_budget_alert({}, str(tenant_id), "otro")

    assert result == {"status": "sent", "kind": "chat_cutoff"}
    assert skipped["status"] == "skipped"
    send.assert_awaited_once_with(session, tenant_id, "chat_cutoff")


async def test_crossing_cutoff_warns_admin_and_sadm_once_per_month(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis, enqueued: AsyncMock
) -> None:
    _usage(monkeypatch, "5.50")  # 92 %: cruza el 80 % y el 90 % a la vez
    tenant_id = uuid4()

    await svc.maybe_warn_budget(AsyncMock(), tenant_id)
    await svc.maybe_warn_budget(AsyncMock(), tenant_id)

    kinds = sorted(call.args[1] for call in enqueued.await_args_list)
    assert kinds == ["budget_warning", "sadm_cutoff"]


async def test_sadm_is_not_warned_below_cutoff(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis, enqueued: AsyncMock
) -> None:
    _usage(monkeypatch, "5.20")  # 86 %
    await svc.maybe_warn_budget(AsyncMock(), uuid4())
    assert [call.args[1] for call in enqueued.await_args_list] == ["budget_warning"]


def test_primary_phone_from_clerk_user() -> None:
    clerk_user = {
        "primary_phone_number_id": "p2",
        "phone_numbers": [
            {"id": "p1", "phone_number": "+34600000001"},
            {"id": "p2", "phone_number": "+34600000002"},
        ],
    }
    assert svc._primary_phone(clerk_user) == "+34600000002"
    assert svc._primary_phone({"phone_numbers": []}) is None
    assert svc._primary_phone({}) is None


async def test_budget_exhausted_is_cached(
    monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis
) -> None:
    from app.core.entitlement_codes import LIMIT_LLM_BUDGET_EUR_MONTH
    from app.schemas.entitlements import Entitlements

    ents = Entitlements(
        plan_code="basic",
        features=frozenset(),
        limits={LIMIT_LLM_BUDGET_EUR_MONTH: Decimal("6")},
        fail_closed=False,
    )
    spent = AsyncMock(return_value=Decimal("6.01"))
    monkeypatch.setattr(svc.usage_meter_service, "get_llm_cost_eur", spent)
    tenant_id = uuid4()

    assert await svc.is_budget_exhausted(AsyncMock(), tenant_id, ents) is True
    assert await svc.is_budget_exhausted(AsyncMock(), tenant_id, ents) is True
    spent.assert_awaited_once()

    unlimited = Entitlements(
        plan_code="premium",
        features=frozenset(),
        limits={LIMIT_LLM_BUDGET_EUR_MONTH: None},
        fail_closed=False,
    )
    assert await svc.is_budget_exhausted(AsyncMock(), uuid4(), unlimited) is False


def test_exhausted_notice_reaches_template_context() -> None:
    from app.config import get_settings
    from app.core.templating import _inject_auth_context

    def _request(exhausted: bool) -> Any:
        state = SimpleNamespace(user=None, tenant=None, llm_budget_exhausted=exhausted)
        return SimpleNamespace(state=state)

    assert (
        _inject_auth_context(_request(True))["llm_budget_exhausted_notice"]
        == get_settings().llm_budget_exhausted_notice
    )
    assert _inject_auth_context(_request(False))["llm_budget_exhausted_notice"] is None
