"""Integracion: cuota de chat por plan (Paso04)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.entitlement_codes import LIMIT_CHAT_MESSAGES_PER_DAY
from app.core.errors import RateLimitError
from app.models import ChatThread, Tenant, User
from app.schemas.entitlements import Entitlements
from app.services import chat_service, plan_service
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
        pytest.skip("Run migration p64_plans_entitlements_01.")
    await plan_service.seed_plan_catalog(db_session)


@pytest.mark.asyncio
async def test_chat_over_limit_blocks_before_persist(
    db_session: AsyncSession,
    chat_schema_ready: None,
    plans_catalog_ready: None,
) -> None:
    from app.core.db import set_tenant_context
    from app.models import ChatMessage
    from sqlalchemy import func, select

    tenant = Tenant(name=f"Chat quota {uuid4().hex[:8]}", plan_code="basic")
    user = User(email=f"chat-{uuid4().hex[:8]}@test.local")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread = ChatThread(tenant_id=tenant.id, user_id=user.id)
    db_session.add(thread)
    await db_session.flush()

    ents = Entitlements(
        plan_code="basic",
        features=frozenset({"documents_chat"}),
        limits={LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("1")},
    )
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=2)
    redis.expire = AsyncMock()
    redis.decrby = AsyncMock()

    with pytest.raises(RateLimitError, match="mensajes"):
        await chat_service.enforce_rate_limit(
            redis,
            db_session,
            tenant_id=tenant.id,
            user_id=user.id,
            ents=ents,
        )

    count = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.thread_id == thread.id)
            )
        ).scalar_one()
    )
    assert count == 0


@pytest.mark.asyncio
async def test_chat_under_limit_allows_message(
    db_session: AsyncSession,
    chat_schema_ready: None,
    audit_schema_ready: None,
    plans_catalog_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.db import set_tenant_context
    from app.schemas.entitlements import Entitlements as EntitlementsModel

    tenant = Tenant(name=f"Chat ok {uuid4().hex[:8]}", plan_code="basic")
    user = User(email=f"ok-{uuid4().hex[:8]}@test.local")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    thread = ChatThread(tenant_id=tenant.id, user_id=user.id)
    db_session.add(thread)
    await db_session.flush()

    ents = EntitlementsModel(
        plan_code="basic",
        features=frozenset({"documents_chat"}),
        limits={LIMIT_CHAT_MESSAGES_PER_DAY: Decimal("10")},
    )
    monkeypatch.setattr(
        "app.services.chat_service.entitlement_service.resolve_tenant",
        AsyncMock(return_value=ents),
    )

    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=1)
    redis.expire = AsyncMock()

    async def fake_turn(*_a: object, **_k: object):
        if False:
            yield ""

    monkeypatch.setattr(chat_service, "_run_assistant_turn", fake_turn)

    read = await chat_service.post_user_message(
        db_session,
        redis,
        tenant_id=tenant.id,
        user_id=user.id,
        thread_id=thread.id,
        content="Hola",
    )
    assert read.content == "Hola"
