"""Tests de integración para rutas admin de canales externos (D.6 — Paso 21).

Ejercita la cadena HTTP → admin_channel_integrations → channel_integration_service → BD real.
El LLM y los clientes de WhatsApp/Telegram se mockean; el resto es real.

Convención de IDs: se usan UUIDs aleatorios por test para evitar colisiones entre
ejecuciones y garantizar aislamiento incluso si la BD no se limpia entre tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from app.config import get_settings
from app.core.db import set_tenant_context
from app.main import create_app
from app.models import Membership, Tenant, User
from app.models.channel_integration import ChannelIntegration, ChannelIntegrationStatus
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

_ADMIN_ORG_ID = f"org_admin_{uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Genera una clave Fernet e inyecta ENCRYPTION_KEY y ADMIN_CLERK_ORG_ID."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", _ADMIN_ORG_ID)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


@pytest.fixture
async def channel_integrations_schema_ready(db_session: AsyncSession) -> None:
    """Salta el test si la migración de channel_integrations no está aplicada."""
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'channel_integrations'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run Paso21 C migration (`uv run alembic upgrade head`).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_target_tenant(rls_database_url: str) -> tuple[UUID, UUID]:
    """Inserta Tenant + User + Membership (admin) en BD y retorna (tenant_id, user_id).

    Usa commit explícito para que la sesión de la ruta HTTP pueda ver los datos.
    Usamos UUID aleatorio para que múltiples ejecuciones no colisionen.
    """
    suffix = uuid4().hex[:8]
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        tenant = Tenant(
            clerk_org_id=f"org_target_{suffix}",
            name=f"Target Tenant {suffix}",
            plan="free",
            settings={},
        )
        user = User(
            clerk_user_id=f"user_{suffix}",
            email=f"admin_{suffix}@channel.test",
            name="Admin",
        )
        session.add(tenant)
        session.add(user)
        await session.flush()
        await set_tenant_context(session, str(tenant.id))
        session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))
        await session.commit()
        tid, uid = tenant.id, user.id
    await engine.dispose()
    return tid, uid


def _make_superadmin_state() -> tuple[Any, Any, Any]:
    """Crea objetos en memoria que representan al superadmin."""
    uid, tid, mid = uuid4(), uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    user = User(clerk_user_id="superadmin_sub", email="super@admin.test", name="Super")
    user.id = uid
    user.created_at = now
    tenant = Tenant(clerk_org_id=_ADMIN_ORG_ID, name="Admin Org", plan="free", settings={})
    tenant.id = tid
    tenant.created_at = now
    tenant.updated_at = now
    membership = Membership(user_id=uid, tenant_id=tid, role="admin")
    membership.id = mid
    membership.created_at = now
    return tenant, user, membership


def _make_regular_tenant_state() -> tuple[Any, Any, Any]:
    """Crea objetos en memoria que representan a un tenant regular (no superadmin)."""
    uid, tid, mid = uuid4(), uuid4(), uuid4()
    now = datetime.now(tz=UTC)
    user = User(clerk_user_id="regular_sub", email="user@regular.test", name="Regular")
    user.id = uid
    user.created_at = now
    tenant = Tenant(
        clerk_org_id=f"org_regular_{uuid4().hex[:8]}",
        name="Regular Org",
        plan="free",
        settings={},
    )
    tenant.id = tid
    tenant.created_at = now
    tenant.updated_at = now
    membership = Membership(user_id=uid, tenant_id=tid, role="admin")
    membership.id = mid
    membership.created_at = now
    return tenant, user, membership


def _fake_resolve_factory(tenant: Any, user: Any, membership: Any):
    async def _resolve(request: Any) -> None:
        request.state.tenant = tenant
        request.state.user = user
        request.state.membership = membership

    return _resolve


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_save_whatsapp_integration_requires_admin(
    channel_integrations_schema_ready: None,
    encryption_key: str,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El superadmin puede guardar una integración de WhatsApp y recibe la tarjeta HTML."""
    target_tenant_id, _ = await _seed_target_tenant(rls_database_url)
    sa_tenant, sa_user, sa_membership = _make_superadmin_state()

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_resolve_factory(sa_tenant, sa_user, sa_membership),
    )

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/admin/integrations/{target_tenant_id}/whatsapp/save",
            data={
                "phone_number_id": "123456789",
                "api_token": "EAABwzLixnjY",
                "display_name": "+34 600 000 000",
                "confidence_threshold": "0.5",
            },
        )

    assert resp.status_code == 200
    assert "WhatsApp" in resp.text
    assert "integration-whatsapp" in resp.text


async def test_save_whatsapp_stores_token_encrypted(
    channel_integrations_schema_ready: None,
    db_session: AsyncSession,
    encryption_key: str,
) -> None:
    """api_token_enc en BD ≠ bytes del token en claro (está cifrado con Fernet)."""
    from app.services import channel_integration_service

    suffix = uuid4().hex[:8]
    tenant = Tenant(name=f"WA Enc Tenant {suffix}", plan="free", settings={})
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    plain_token = "EAABwzLixnjY_plain_token_for_test"
    await channel_integration_service.save_integration(
        db_session,
        tenant_id=tenant.id,
        channel="whatsapp",
        api_token=plain_token,
        phone_number_id="111222333",
    )
    await db_session.flush()

    result = await db_session.execute(
        select(ChannelIntegration).where(
            ChannelIntegration.tenant_id == tenant.id,
            ChannelIntegration.channel == "whatsapp",
        )
    )
    row = result.scalar_one()
    assert row.api_token_enc is not None
    # El token cifrado no debe ser los bytes del token original
    assert row.api_token_enc != plain_token.encode()
    # Descifrar da el original
    decrypted = channel_integration_service.decrypt_api_token(row)
    assert decrypted == plain_token
    # status activo
    assert row.status == ChannelIntegrationStatus.active.value


async def test_save_telegram_calls_set_webhook(
    channel_integrations_schema_ready: None,
    encryption_key: str,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Al guardar una integración de Telegram, se llama a set_webhook con la URL correcta."""
    target_tenant_id, _ = await _seed_target_tenant(rls_database_url)
    sa_tenant, sa_user, sa_membership = _make_superadmin_state()

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_resolve_factory(sa_tenant, sa_user, sa_membership),
    )

    mock_set_webhook = AsyncMock(return_value=None)
    with patch(
        "app.routes.web.admin_channel_integrations.telegram_client.set_webhook", mock_set_webhook
    ):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/admin/integrations/{target_tenant_id}/telegram/save",
                data={
                    "api_token": "9876543210:AAHdqTcvCH1vGBJ83z4",
                    "display_name": "@TestBot",
                    "confidence_threshold": "0.6",
                },
            )

    assert resp.status_code == 200
    mock_set_webhook.assert_awaited_once()
    # La URL del webhook debe contener la ruta correcta
    call_args = mock_set_webhook.call_args
    webhook_url: str = call_args.args[1]
    assert "/api/webhooks/telegram/" in webhook_url
    # El webhook_secret debe ser un string no vacío
    assert call_args.kwargs.get("webhook_secret")


async def test_disconnect_revokes_and_deletes_webhook(
    channel_integrations_schema_ready: None,
    encryption_key: str,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Desconectar Telegram llama a delete_webhook y revoca la integración en BD."""
    target_tenant_id, _ = await _seed_target_tenant(rls_database_url)
    sa_tenant, sa_user, sa_membership = _make_superadmin_state()

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_resolve_factory(sa_tenant, sa_user, sa_membership),
    )

    # Primero guardar la integración
    mock_set_webhook = AsyncMock(return_value=None)
    mock_delete_webhook = AsyncMock(return_value=None)

    app = create_app()
    with patch(
        "app.routes.web.admin_channel_integrations.telegram_client.set_webhook", mock_set_webhook
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            save_resp = await client.post(
                f"/admin/integrations/{target_tenant_id}/telegram/save",
                data={
                    "api_token": "1234567890:AABotTokenForDisconnect",
                    "confidence_threshold": "0.5",
                },
            )
    assert save_resp.status_code == 200

    # Luego desconectar
    with patch(
        "app.routes.web.admin_channel_integrations.telegram_client.delete_webhook",
        mock_delete_webhook,
    ):
        app2 = create_app()
        async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
            disc_resp = await client.post(
                f"/admin/integrations/{target_tenant_id}/telegram/disconnect",
            )
    assert disc_resp.status_code == 200
    mock_delete_webhook.assert_awaited_once()

    # Verificar en BD que la integración quedó revocada
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await set_tenant_context(session, str(target_tenant_id))
        result = await session.execute(
            select(ChannelIntegration).where(
                ChannelIntegration.tenant_id == target_tenant_id,
                ChannelIntegration.channel == "telegram",
            )
        )
        row = result.scalar_one_or_none()
    await engine.dispose()

    assert row is not None
    assert row.status == ChannelIntegrationStatus.revoked.value
    assert row.api_token_enc is None


async def test_non_admin_cannot_configure_channel(
    channel_integrations_schema_ready: None,
    encryption_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un tenant regular recibe 403 al intentar acceder a las rutas admin."""
    regular_tenant, regular_user, regular_membership = _make_regular_tenant_state()

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_resolve_factory(regular_tenant, regular_user, regular_membership),
    )

    fake_tenant_id = uuid4()
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.post(
            f"/admin/integrations/{fake_tenant_id}/whatsapp/save",
            data={"phone_number_id": "999", "api_token": "token", "confidence_threshold": "0.5"},
        )

    assert resp.status_code == 403
