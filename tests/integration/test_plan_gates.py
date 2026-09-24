"""Integracion de gates por plan (Paso03)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.entitlement_codes import OVERRIDE_SETTINGS_KEY
from app.models import Tenant
from app.services import entitlement_service, plan_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
async def plans_catalog_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'plans'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run migration p64_plans_entitlements_01 (`alembic upgrade head`).")
    await plan_service.seed_plan_catalog(db_session)


@pytest.mark.asyncio
async def test_basic_entitlements_allow_knowledge_deny_appointments(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Basic gate {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()

    ents = await entitlement_service.resolve_entitlements(db_session, tenant)
    assert ents.has("documents") is True
    assert ents.has("knowledge") is True
    assert ents.has("appointments") is False


@pytest.mark.asyncio
async def test_advanced_entitlements_allow_appointments(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Advanced gate {uuid4().hex[:8]}",
        plan="advanced",
        plan_code="advanced",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()

    ents = await entitlement_service.resolve_entitlements(db_session, tenant)
    assert ents.has("knowledge") is True
    assert ents.has("appointments") is True
    assert ents.has("channel_whatsapp") is True


@pytest.mark.asyncio
async def test_override_can_enable_appointments_on_basic(
    db_session: AsyncSession,
    plans_catalog_ready: None,
) -> None:
    tenant = Tenant(
        name=f"Override gate {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
        settings={OVERRIDE_SETTINGS_KEY: {"features": {"appointments": True}}},
    )
    db_session.add(tenant)
    await db_session.flush()

    ents = await entitlement_service.resolve_entitlements(db_session, tenant)
    assert ents.has("appointments") is True


@pytest.mark.asyncio
async def test_whatsapp_webhook_does_not_enqueue_without_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firma OK + integración conocida + feature off → 200 sin encolar."""
    import hashlib
    import hmac
    import json

    from app.config import get_settings
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    app_secret = "gate_test_secret"  # pragma: allowlist secret
    monkeypatch.setenv("WHATSAPP_APP_SECRET", app_secret)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "gate_verify")
    get_settings.cache_clear()

    integration = MagicMock()
    integration.tenant_id = uuid4()
    integration.id = uuid4()

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pnid_gate"},
                            "messages": [
                                {
                                    "id": "wamid.gate",
                                    "from": "34600000000",
                                    "type": "text",
                                    "text": {"body": "hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()

    ents = MagicMock()
    ents.has = MagicMock(return_value=False)
    enqueue = AsyncMock()

    with (
        patch(
            "app.routes.api.webhooks_whatsapp.claim_webhook_event",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.routes.api.webhooks_whatsapp.channel_integration_service."
            "get_integration_by_phone_number_id",
            AsyncMock(return_value=integration),
        ),
        patch(
            "app.routes.api.webhooks_whatsapp.entitlement_service.resolve_tenant",
            AsyncMock(return_value=ents),
        ),
        patch(
            "app.routes.api.webhooks_whatsapp.enqueue_channel_message",
            enqueue,
        ),
    ):
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.post(
                "/api/webhooks/whatsapp",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": signature,
                },
            )

    assert response.status_code == 200
    enqueue.assert_not_awaited()
    ents.has.assert_called_with("channel_whatsapp")
