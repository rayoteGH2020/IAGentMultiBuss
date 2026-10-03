"""Consumo y avisos de cupos en la interfaz (bloque 7; spec planes §7, D027)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.billing_period import renewal_date
from app.core.db import set_tenant_context
from app.core.entitlement_codes import (
    LIMIT_CHAT_QUESTIONS_PER_MONTH,
    LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD,
    LIMIT_CONTRACT_UPLOADS_PER_MONTH,
    LIMIT_CONTRACTS_ACTIVE_MAX,
    LIMIT_DOCUMENT_RETRIES_PER_MONTH,
    LIMIT_INVOICES_PER_MONTH,
    LIMIT_LLM_BUDGET_EUR_MONTH,
    LIMIT_TICKETS_PER_MONTH,
)
from app.models import ContractStatus, Tenant
from app.schemas.entitlements import Entitlements
from app.services import (
    contract_service,
    monthly_quota_service,
    plan_quota_service,
    quota_status_service,
)
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _ents(
    *,
    invoices: int = 6,
    tickets: int = 4,
    chat: int = 10,
    monthly: int = 5,
    first: int = 15,
    active: int = 3,
    budget: Decimal | None = Decimal("6"),
) -> Entitlements:
    return Entitlements(
        plan_code="basic",
        features=frozenset({"documents"}),
        limits={
            LIMIT_INVOICES_PER_MONTH: Decimal(invoices),
            LIMIT_TICKETS_PER_MONTH: Decimal(tickets),
            LIMIT_DOCUMENT_RETRIES_PER_MONTH: Decimal("40"),
            LIMIT_CHAT_QUESTIONS_PER_MONTH: Decimal(chat),
            LIMIT_CONTRACT_UPLOADS_PER_MONTH: Decimal(monthly),
            LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD: Decimal(first),
            LIMIT_CONTRACTS_ACTIVE_MAX: Decimal(active),
            LIMIT_LLM_BUDGET_EUR_MONTH: budget,
        },
    )


@pytest.fixture
def ents_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Entitlements]:
    env = {"ents": _ents()}

    async def _resolve(_db: AsyncSession, _tenant_id: Any) -> Entitlements:
        return env["ents"]

    monkeypatch.setattr("app.services.entitlement_service.resolve_tenant", _resolve)
    return env


async def _tenant(
    db: AsyncSession,
    factory: Callable[..., Coroutine[Any, Any, Tenant]],
    *,
    initial_load: bool = False,
) -> Tenant:
    tenant = await factory()
    if not initial_load:
        tenant.created_at = datetime.now(UTC) - timedelta(days=365)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    return tenant


async def _consume(db: AsyncSession, ents: Entitlements, tenant: Tenant, code: str, n: int) -> None:
    for _ in range(n):
        assert await monthly_quota_service.try_consume(db, ents, tenant.id, code)


async def _active_contracts(db: AsyncSession, tenant: Tenant, n: int) -> None:
    for _ in range(n):
        contract = await contract_service.create_contract_stub(
            db,
            tenant.id,
            source_file_key=f"t/{uuid4()}.pdf",
            source_filename="c.pdf",
            source_mime="application/pdf",
        )
        contract.status = ContractStatus.ready
    await db.flush()


async def test_monthly_statuses_show_bag_breakdown_extras_and_renewal(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
) -> None:
    ents = ents_env["ents"]
    tenant = await _tenant(db_session, tenant_factory)
    await _consume(db_session, ents, tenant, LIMIT_INVOICES_PER_MONTH, 5)
    await _consume(db_session, ents, tenant, LIMIT_TICKETS_PER_MONTH, 3)
    await monthly_quota_service.add_extra(
        db_session, tenant_id=tenant.id, code=LIMIT_TICKETS_PER_MONTH, amount=2, actor_user_id=None
    )
    await _active_contracts(db_session, tenant, 2)

    statuses = {
        s.key: s for s in await quota_status_service.monthly_statuses(db_session, ents, tenant.id)
    }

    documents = statuses["documents"]
    assert (documents.used, documents.cap) == (8, 12)  # 6 + 4 + 2 de ampliación
    assert documents.detail == "facturas 5 · tickets 3"
    assert documents.renewal == renewal_date()
    assert statuses["contract_uploads"].label == "Altas de contratos"
    assert (statuses["contracts_active"].used, statuses["contracts_active"].cap) == (2, 3)
    assert set(statuses) == {"documents", "retries", "chat", "contract_uploads", "contracts_active"}


async def test_initial_load_status_uses_its_bag_and_end_date(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
) -> None:
    tenant = await _tenant(db_session, tenant_factory, initial_load=True)

    statuses = {
        s.key: s
        for s in await quota_status_service.monthly_statuses(
            db_session, ents_env["ents"], tenant.id
        )
    }

    uploads = statuses["contract_uploads"]
    assert uploads.label == "Altas de contratos (carga inicial)"
    assert uploads.cap == 15
    assert uploads.renewal is not None and uploads.renewal > renewal_date() - timedelta(days=1)


async def test_documents_alerts_at_80_and_100_percent(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
) -> None:
    ents = ents_env["ents"]
    tenant = await _tenant(db_session, tenant_factory)
    await _consume(db_session, ents, tenant, LIMIT_INVOICES_PER_MONTH, 7)
    assert await quota_status_service.documents_alerts(db_session, tenant.id) == []

    await _consume(db_session, ents, tenant, LIMIT_INVOICES_PER_MONTH, 1)  # 8 de 10
    [warning] = await quota_status_service.documents_alerts(db_session, tenant.id)
    assert warning.level == "warning"
    assert "Has usado 8 de 10 facturas y tickets" in warning.message

    await _consume(db_session, ents, tenant, LIMIT_TICKETS_PER_MONTH, 2)
    await _consume(db_session, ents, tenant, LIMIT_CONTRACT_UPLOADS_PER_MONTH, 4)  # 4 de 5 = 80 %
    await _active_contracts(db_session, tenant, 3)
    alerts = await quota_status_service.documents_alerts(db_session, tenant.id)

    assert [a.level for a in alerts] == ["exhausted", "warning", "exhausted"]
    assert "Has agotado las 10 facturas y tickets" in alerts[0].message
    assert "4 de 5 altas de contratos" in alerts[1].message
    assert "marca antes el contrato anterior como sustituido" in alerts[2].message


async def test_chat_alert_only_when_exhausted(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
) -> None:
    ents = ents_env["ents"]
    tenant = await _tenant(db_session, tenant_factory)
    await _consume(db_session, ents, tenant, LIMIT_CHAT_QUESTIONS_PER_MONTH, 9)
    # 90 %: el chat es un tope técnico, sin aviso al 80 % (spec §4.4).
    assert await quota_status_service.chat_alert(db_session, ents, tenant.id) is None

    await _consume(db_session, ents, tenant, LIMIT_CHAT_QUESTIONS_PER_MONTH, 1)
    alert = await quota_status_service.chat_alert(db_session, ents, tenant.id)
    assert alert is not None and alert.level == "exhausted"
    assert "Has usado las 10 preguntas de este mes" in alert.message


async def test_alerts_never_break_the_page(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant(db_session, tenant_factory)
    monkeypatch.setattr(
        monthly_quota_service, "bag_usage", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr(
        monthly_quota_service, "get_usage", AsyncMock(side_effect=RuntimeError("boom"))
    )

    assert await quota_status_service.documents_alerts(db_session, tenant.id) == []
    assert await quota_status_service.chat_alert(db_session, ents_env["ents"], tenant.id) is None


async def test_quotas_not_included_are_hidden(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
) -> None:
    ents_env["ents"] = _ents(chat=0, active=0)
    tenant = await _tenant(db_session, tenant_factory)

    keys = {
        s.key
        for s in await quota_status_service.monthly_statuses(
            db_session, ents_env["ents"], tenant.id
        )
    }

    assert "chat" not in keys and "contracts_active" not in keys
    assert await quota_status_service.chat_alert(db_session, ents_env["ents"], tenant.id) is None


async def test_ai_usage_percent_and_sadm_level(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    invoices_schema_ready: None,
    ents_env: dict[str, Entitlements],
) -> None:
    ents = ents_env["ents"]
    tenant = await _tenant(db_session, tenant_factory)
    assert await quota_status_service.tenant_alert_level(db_session, ents, tenant.id) is None

    await plan_quota_service.record_llm_cost(db_session, tenant_id=tenant.id, cost_eur=Decimal("5"))
    assert await quota_status_service.ai_usage_percent(db_session, ents, tenant.id) == 83
    assert await quota_status_service.tenant_alert_level(db_session, ents, tenant.id) == "warning"

    await _consume(db_session, ents, tenant, LIMIT_CHAT_QUESTIONS_PER_MONTH, 10)
    assert await quota_status_service.tenant_alert_level(db_session, ents, tenant.id) == "exhausted"
    assert (
        await quota_status_service.ai_usage_percent(db_session, _ents(budget=None), tenant.id)
        is None
    )
