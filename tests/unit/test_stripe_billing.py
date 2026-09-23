"""Tests del webhook Stripe y efectos de plan (Paso09)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import stripe
from app.core.errors import AuthError
from app.routes.api import webhooks_stripe
from app.services import plan_service, stripe_billing_service
from pydantic import SecretStr
from starlette.requests import Request


def _request(body: bytes = b"{}") -> Request:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/"}, receive)


@asynccontextmanager
async def _session_scope() -> AsyncIterator[object]:
    yield object()


def _patch_stripe_route(monkeypatch: pytest.MonkeyPatch, *, claim: bool = True) -> None:
    monkeypatch.setattr(webhooks_stripe, "session_scope", _session_scope)
    monkeypatch.setattr(
        webhooks_stripe,
        "claim_webhook_event",
        AsyncMock(return_value=claim),
    )
    monkeypatch.setattr(
        webhooks_stripe,
        "read_request_body_limited",
        AsyncMock(return_value=b'{"id":"evt_1"}'),
    )
    monkeypatch.setattr(
        webhooks_stripe,
        "get_settings",
        lambda: SimpleNamespace(
            stripe_webhook_secret=SecretStr("whsec_test"),
            webhook_max_body_bytes=262_144,
        ),
    )


@pytest.mark.asyncio
async def test_stripe_invalid_signature_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stripe_route(monkeypatch)

    def _boom(_payload: bytes, _sig: str) -> dict[str, Any]:
        raise stripe.SignatureVerificationError("bad", "sig")

    monkeypatch.setattr(webhooks_stripe.stripe_billing_service, "construct_stripe_event", _boom)

    with pytest.raises(AuthError, match="Invalid webhook signature"):
        await webhooks_stripe.stripe_webhook(_request(), "t=1,v1=x")


@pytest.mark.asyncio
async def test_stripe_replay_skips_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stripe_route(monkeypatch, claim=False)
    handle = AsyncMock()
    monkeypatch.setattr(
        webhooks_stripe.stripe_billing_service,
        "construct_stripe_event",
        lambda _p, _s: {"id": "evt_dup", "type": "checkout.session.completed"},
    )
    monkeypatch.setattr(webhooks_stripe.stripe_billing_service, "handle_stripe_event", handle)

    result = await webhooks_stripe.stripe_webhook(_request(), "t=1,v1=x")

    assert result == {"received": True}
    handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkout_completed_assigns_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    tenant = MagicMock()
    tenant.id = tenant_id
    tenant.plan_code = "basic"
    tenant.billing_status = "none"
    tenant.stripe_customer_id = None
    tenant.stripe_subscription_id = None

    db = AsyncMock()
    assign = AsyncMock(return_value=tenant)
    monkeypatch.setattr(stripe_billing_service, "_require_tenant", AsyncMock(return_value=tenant))
    monkeypatch.setattr(stripe_billing_service.plan_service, "assign_tenant_plan", assign)
    monkeypatch.setattr(stripe_billing_service.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(stripe_billing_service, "set_tenant_context", AsyncMock())

    await stripe_billing_service.handle_stripe_event(
        db,
        {
            "id": "evt_ok",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "mode": "subscription",
                    "customer": "cus_1",
                    "subscription": "sub_1",
                    "client_reference_id": str(tenant_id),
                    "metadata": {"tenant_id": str(tenant_id), "plan_code": "medium"},
                }
            },
        },
    )

    assert tenant.billing_status == "active"
    assert tenant.stripe_customer_id == "cus_1"
    assert tenant.stripe_subscription_id == "sub_1"
    assign.assert_awaited_once()
    assert assign.await_args.kwargs["plan_code"] == "medium"
    assert assign.await_args.kwargs["source"] == plan_service.SOURCE_STRIPE


@pytest.mark.asyncio
async def test_subscription_deleted_downgrades_to_basic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = MagicMock()
    tenant.id = uuid4()
    tenant.billing_status = "active"
    tenant.stripe_customer_id = "cus_1"
    tenant.stripe_subscription_id = "sub_1"

    assign = AsyncMock(return_value=tenant)
    monkeypatch.setattr(
        stripe_billing_service,
        "_tenant_by_stripe_customer",
        AsyncMock(return_value=tenant),
    )
    monkeypatch.setattr(stripe_billing_service.plan_service, "assign_tenant_plan", assign)
    monkeypatch.setattr(stripe_billing_service.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(stripe_billing_service, "set_tenant_context", AsyncMock())

    await stripe_billing_service.handle_stripe_event(
        AsyncMock(),
        {
            "id": "evt_del",
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_1", "id": "sub_1"}},
        },
    )

    assert tenant.billing_status == "canceled"
    assert tenant.stripe_subscription_id is None
    assert assign.await_args.kwargs["plan_code"] == "basic"


@pytest.mark.asyncio
async def test_payment_failed_marks_past_due(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = MagicMock()
    tenant.id = uuid4()
    tenant.billing_status = "active"
    tenant.plan_code = "high"

    monkeypatch.setattr(
        stripe_billing_service,
        "_tenant_by_stripe_customer",
        AsyncMock(return_value=tenant),
    )
    monkeypatch.setattr(stripe_billing_service.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(stripe_billing_service, "set_tenant_context", AsyncMock())
    assign = AsyncMock()
    monkeypatch.setattr(stripe_billing_service.plan_service, "assign_tenant_plan", assign)

    await stripe_billing_service.handle_stripe_event(
        AsyncMock(),
        {
            "id": "evt_fail",
            "type": "invoice.payment_failed",
            "data": {"object": {"customer": "cus_1"}},
        },
    )

    assert tenant.billing_status == "past_due"
    assign.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmapped_price_does_not_assign(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = MagicMock()
    tenant.id = uuid4()
    tenant.billing_status = "none"
    tenant.stripe_customer_id = "cus_1"

    monkeypatch.setattr(
        stripe_billing_service,
        "_tenant_by_stripe_customer",
        AsyncMock(return_value=tenant),
    )
    monkeypatch.setattr(
        stripe_billing_service.plan_service,
        "get_plan_by_stripe_price_id",
        AsyncMock(return_value=None),
    )
    assign = AsyncMock()
    monkeypatch.setattr(stripe_billing_service.plan_service, "assign_tenant_plan", assign)
    monkeypatch.setattr(stripe_billing_service.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(stripe_billing_service, "set_tenant_context", AsyncMock())

    await stripe_billing_service.handle_stripe_event(
        AsyncMock(),
        {
            "id": "evt_unk",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_1",
                    "customer": "cus_1",
                    "status": "active",
                    "items": {"data": [{"price": {"id": "price_unknown"}}]},
                }
            },
        },
    )

    assert tenant.billing_status == "active"
    assign.assert_not_awaited()


def test_is_stripe_configured_false_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        stripe_billing_service,
        "get_settings",
        lambda: SimpleNamespace(stripe_secret_key=SecretStr("")),
    )
    assert stripe_billing_service.is_stripe_configured() is False
