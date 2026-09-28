"""Mi cuenta: % consumido por límite y límite de canales conectados."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.entitlement_codes import PLAN_FEATURES, PLAN_LIMITS
from app.core.errors import ValidationError
from app.core.rate_limiter import (
    chat_messages_tenant_key,
    document_retries_key,
    documents_upload_key,
    knowledge_upload_key,
)
from app.core.templating import templates
from app.schemas.entitlements import Entitlements, PlanLimitItem, PlanSummary, QuotaUsage
from app.services import channel_integration_service, plan_quota_service
from app.services.entitlement_service import build_plan_summary


def _ents(plan_code: str, **overrides: Decimal | None) -> Entitlements:
    limits = dict(PLAN_LIMITS[plan_code])
    limits.update(overrides)
    return Entitlements(plan_code=plan_code, features=PLAN_FEATURES[plan_code], limits=limits)


def _no_platform_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plan_quota_service,
        "_settings",
        lambda: SimpleNamespace(
            knowledge_max_uploads_per_day=None,
            chat_daily_message_limit=None,
            channel_rate_limit_msg_per_hour=None,
            voice_rate_limit_per_hour=None,
            chat_user_daily_message_limit=0,
        ),
    )


# ── Uso por límite ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_limit_usage_reads_same_keys_and_counts_as_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_platform_caps(monkeypatch)
    tenant_id = uuid4()
    counters = {
        documents_upload_key(tenant_id): b"12",
        document_retries_key(tenant_id): b"3",
        knowledge_upload_key(tenant_id): None,  # sin actividad hoy
        chat_messages_tenant_key(tenant_id): "95",
    }
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=lambda key: counters.get(key))
    monkeypatch.setattr(plan_quota_service, "_count_knowledge_docs", AsyncMock(return_value=40))
    monkeypatch.setattr(plan_quota_service, "_count_active_members", AsyncMock(return_value=2))
    monkeypatch.setattr(plan_quota_service, "_count_active_channels", AsyncMock(return_value=0))

    usage = await plan_quota_service.get_limit_usage(AsyncMock(), redis, _ents("basic"), tenant_id)

    assert usage["documents_per_day"] == QuotaUsage(used=12, cap=50)
    assert usage["document_retries_per_day"] == QuotaUsage(used=3, cap=20)
    assert usage["knowledge_uploads_per_day"] == QuotaUsage(used=0, cap=25)
    assert usage["chat_messages_per_day"] == QuotaUsage(used=95, cap=100)
    assert usage["knowledge_docs_max"] == QuotaUsage(used=40, cap=100)
    assert usage["members_max"] == QuotaUsage(used=2, cap=5)
    assert usage["channel_external_slots"] == QuotaUsage(used=0, cap=0)
    assert "channel_messages_per_hour" not in usage  # por cliente final: sin total del tenant


@pytest.mark.asyncio
async def test_get_limit_usage_uses_platform_cap_when_lower(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        plan_quota_service,
        "_settings",
        lambda: SimpleNamespace(
            knowledge_max_uploads_per_day=10,  # más bajo que los 25 del plan
            chat_daily_message_limit=None,
            channel_rate_limit_msg_per_hour=None,
            voice_rate_limit_per_hour=None,
        ),
    )
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"4")
    for name in ("_count_knowledge_docs", "_count_active_members", "_count_active_channels"):
        monkeypatch.setattr(plan_quota_service, name, AsyncMock(return_value=0))

    usage = await plan_quota_service.get_limit_usage(AsyncMock(), redis, _ents("basic"), uuid4())

    assert usage["knowledge_uploads_per_day"].cap == 10


@pytest.mark.asyncio
async def test_get_limit_usage_skips_redis_counters_if_redis_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_platform_caps(monkeypatch)
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    monkeypatch.setattr(plan_quota_service, "_count_knowledge_docs", AsyncMock(return_value=1))
    monkeypatch.setattr(plan_quota_service, "_count_active_members", AsyncMock(return_value=1))
    monkeypatch.setattr(plan_quota_service, "_count_active_channels", AsyncMock(return_value=0))

    usage = await plan_quota_service.get_limit_usage(AsyncMock(), redis, _ents("basic"), uuid4())

    assert set(usage) == {"knowledge_docs_max", "members_max", "channel_external_slots"}


def test_plan_summary_with_usage_shows_effective_cap_and_percent() -> None:
    usage = {
        "documents_per_day": QuotaUsage(used=12, cap=50),
        "members_max": QuotaUsage(used=5, cap=5),
        "knowledge_uploads_per_day": QuotaUsage(used=4, cap=10),  # tope de plataforma
        "channel_external_slots": QuotaUsage(used=0, cap=0),
    }
    summary = build_plan_summary(_ents("basic"), usage)
    items = {item.label: item for item in summary.limits}

    assert items["Documentos procesados al día"].percent == 24
    assert items["Documentos procesados al día"].used == 12
    assert items["Miembros del equipo"].percent == 100
    assert items["Subidas a la base de conocimiento al día"].value == "10"
    assert "Canales de mensajería conectados" not in items  # tope 0: no incluido
    # Sin uso medible: se muestra el tope sin barra.
    assert items["Documentos en la base de conocimiento"].percent is None


def test_plan_summary_percent_is_capped_and_none_when_unlimited() -> None:
    usage = {
        "documents_per_day": QuotaUsage(used=80, cap=50),  # override a la baja
        "members_max": QuotaUsage(used=7, cap=None),
    }
    summary = build_plan_summary(_ents("basic", members_max=None), usage)
    items = {item.label: item for item in summary.limits}

    assert items["Documentos procesados al día"].percent == 100
    assert items["Miembros del equipo"].percent is None
    assert items["Miembros del equipo"].value == "Ilimitado"


# ── Plantilla ──────────────────────────────────────────────────────────────


def _render(limits: list[PlanLimitItem]) -> str:
    template = templates.env.get_template("pages/settings/profile.html")
    ctx = template.new_context(
        {
            "user": SimpleNamespace(name="Ana", email="ana@example.com", created_at=None),
            "tenant": SimpleNamespace(name="Acme"),
            "plan": PlanSummary(code="basic", name="Básico", features=[], limits=limits),
            "usage": SimpleNamespace(
                period=date(2026, 9, 1),
                llm_cost_eur=Decimal("0"),
                llm_budget_eur=None,
                rag_messages_count=0,
            ),
        }
    )
    return re.sub(r"\s+", " ", "".join(template.blocks["settings_content"](ctx)))


@pytest.mark.parametrize(
    ("percent", "color"),
    [(24, "bg-emerald-500"), (70, "bg-amber-500"), (89, "bg-amber-500"), (90, "bg-red-500")],
)
def test_limit_bar_width_and_color(percent: int, color: str) -> None:
    html = _render([PlanLimitItem(label="Docs", value="50", used=1, percent=percent)])

    assert f'aria-valuenow="{percent}"' in html
    assert f'style="width: {percent}%"' in html
    assert color in html
    assert f"{percent} %" in html


def test_limit_without_usage_has_no_bar_and_used_is_formatted() -> None:
    html = _render(
        [
            PlanLimitItem(label="Mensajes por cliente y hora en canales", value="80"),
            PlanLimitItem(label="Docs", value="1.500", used=1234, percent=82),
        ]
    )
    assert html.count('role="progressbar"') == 1
    assert "1.234 <span" in html and "/ 1.500" in html
    assert "se reinician cada día a las 00:00" in html


# ── Límite de canales ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("slots", "active", "allowed"),
    [(Decimal("2"), 1, True), (Decimal("2"), 2, False), (Decimal("0"), 0, False), (None, 9, True)],
)
async def test_ensure_channel_slot(
    monkeypatch: pytest.MonkeyPatch, slots: Decimal | None, active: int, allowed: bool
) -> None:
    _no_platform_caps(monkeypatch)
    monkeypatch.setattr(
        plan_quota_service, "_count_active_channels", AsyncMock(return_value=active)
    )
    ents = _ents("advanced", channel_external_slots=slots)

    if allowed:
        await plan_quota_service.ensure_channel_slot(AsyncMock(), ents, uuid4())
    else:
        with pytest.raises(ValidationError) as exc_info:
            await plan_quota_service.ensure_channel_slot(AsyncMock(), ents, uuid4())
        assert exc_info.value.details.get("code") == "channel_slots_max"


def _patch_save(monkeypatch: pytest.MonkeyPatch, existing: Any) -> AsyncMock:
    monkeypatch.setattr(
        channel_integration_service,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development", whatsapp_app_secret=SimpleNamespace(get_secret_value=str)
        ),
    )
    monkeypatch.setattr(channel_integration_service, "_require_encryption_key", lambda: "k")
    monkeypatch.setattr(channel_integration_service, "encrypt_token", lambda v, k: b"enc")
    monkeypatch.setattr(
        channel_integration_service, "get_integration", AsyncMock(return_value=existing)
    )
    ensure = AsyncMock(side_effect=ValidationError("full", details={"code": "channel_slots_max"}))
    monkeypatch.setattr(plan_quota_service, "ensure_channel_slot", ensure)
    from app.services import entitlement_service

    monkeypatch.setattr(entitlement_service, "resolve_tenant", AsyncMock(return_value=None))
    return ensure


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_status", [None, "revoked"])
async def test_connecting_new_or_revoked_channel_checks_slots(
    monkeypatch: pytest.MonkeyPatch, existing_status: str | None
) -> None:
    existing = None if existing_status is None else SimpleNamespace(status=existing_status)
    ensure = _patch_save(monkeypatch, existing)
    db = AsyncMock()
    db.add = MagicMock()

    with pytest.raises(ValidationError):
        await channel_integration_service.save_integration(
            db, tenant_id=uuid4(), channel="telegram", api_token="t"
        )
    ensure.assert_awaited_once()
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_editing_active_channel_does_not_consume_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(id=uuid4(), status="active", webhook_secret_enc=None)
    ensure = _patch_save(monkeypatch, existing)
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncMock())

    await channel_integration_service.save_integration(
        db, tenant_id=uuid4(), channel="telegram", api_token="t"
    )

    ensure.assert_not_awaited()
    assert existing.status == "active"
