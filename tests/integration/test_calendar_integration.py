"""Integración calendar_service con Postgres real (Paso 17)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.config import get_settings
from app.core.crypto import decrypt_token
from app.core.db import set_tenant_context
from app.models import AuditLog, Membership, Tenant, User
from app.models.calendar_integration import CalendarIntegration, CalendarIntegrationStatus
from app.schemas.calendar import TokenResponse
from app.services import calendar_service
from app.services.audit_service import (
    ACTION_CALENDAR_INTEGRATION_LINKED,
    ACTION_CALENDAR_INTEGRATION_UNLINKED,
    RESOURCE_CALENDAR_INTEGRATION,
)
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


@pytest.fixture
async def calendar_schema_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'calendar_integrations'"
        ),
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run Paso17 migration (`uv run alembic upgrade head`).")


async def _seed_tenant_user(db_session: AsyncSession) -> tuple[Tenant, User]:
    suffix = uuid4().hex[:8]
    tenant = Tenant(name=f"Calendar Tenant {suffix}")
    user = User(email=f"calendar_{suffix}@test.local", name="Calendar User")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))
    await db_session.flush()
    return tenant, user


async def test_save_get_integration_decrypts_tokens(
    calendar_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    encryption_key: str,
) -> None:
    """save → get → tokens descifrados coinciden; en BD están cifrados."""
    tenant, user = await _seed_tenant_user(db_session)
    token_response = TokenResponse(
        access_token="access-plain-token",
        refresh_token="refresh-plain-token",
        expires_in=3600,
        scope="https://www.googleapis.com/auth/calendar.readonly",
    )

    saved = await calendar_service.save_integration(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        token_response=token_response,
        google_email="user@gmail.com",
    )
    await db_session.flush()

    loaded = await calendar_service.get_integration(db_session, tenant.id, user.id)
    assert loaded is not None
    assert loaded.id == saved.id
    assert loaded.status == CalendarIntegrationStatus.active.value
    assert loaded.google_email == "user@gmail.com"
    assert loaded.access_token_enc is not None
    assert loaded.refresh_token_enc is not None
    assert loaded.access_token_enc != b"access-plain-token"
    assert loaded.refresh_token_enc != b"refresh-plain-token"

    access, refresh = await calendar_service.get_decrypted_tokens(db_session, loaded)
    assert access == "access-plain-token"
    assert refresh == "refresh-plain-token"
    assert decrypt_token(loaded.access_token_enc, encryption_key) == access
    assert decrypt_token(loaded.refresh_token_enc, encryption_key) == refresh

    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == ACTION_CALENDAR_INTEGRATION_LINKED,
            AuditLog.resource_id == saved.id,
        )
    )
    audit_row = audit_result.scalar_one()
    assert audit_row.resource_type == RESOURCE_CALENDAR_INTEGRATION
    assert audit_row.user_id == user.id
    assert audit_row.metadata_ is not None
    assert audit_row.metadata_["google_email"] == "user@gmail.com"
    assert audit_row.metadata_["provider"] == "google"
    assert "access_token" not in audit_row.metadata_
    assert "refresh_token" not in audit_row.metadata_


async def test_save_integration_upserts_existing_row(
    calendar_schema_ready: None,
    db_session: AsyncSession,
    encryption_key: str,
) -> None:
    """Un segundo save actualiza la misma fila (constraint único por usuario)."""
    tenant, user = await _seed_tenant_user(db_session)
    first = await calendar_service.save_integration(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        token_response=TokenResponse(
            access_token="first-access",
            refresh_token="first-refresh",
            expires_in=3600,
        ),
        google_email="first@gmail.com",
    )
    await db_session.flush()

    updated = await calendar_service.save_integration(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        token_response=TokenResponse(
            access_token="second-access",
            refresh_token="second-refresh",
            expires_in=7200,
        ),
        google_email="second@gmail.com",
    )
    await db_session.flush()

    assert updated.id == first.id
    assert updated.google_email == "second@gmail.com"
    access, refresh = await calendar_service.get_decrypted_tokens(db_session, updated)
    assert access == "second-access"
    assert refresh == "second-refresh"

    result = await db_session.execute(
        select(CalendarIntegration).where(
            CalendarIntegration.tenant_id == tenant.id,
            CalendarIntegration.user_id == user.id,
        )
    )
    assert len(result.scalars().all()) == 1


async def test_revoke_integration_marks_revoked_and_clears_tokens(
    calendar_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    encryption_key: str,
) -> None:
    """revoke → status=revoked y tokens eliminados de la fila."""
    tenant, user = await _seed_tenant_user(db_session)
    integration = await calendar_service.save_integration(
        db_session,
        tenant_id=tenant.id,
        user_id=user.id,
        token_response=TokenResponse(
            access_token="access-to-revoke",
            refresh_token="refresh-to-revoke",
            expires_in=3600,
        ),
        google_email="revoke@gmail.com",
    )
    await db_session.flush()
    integration_id = integration.id

    mock_client = AsyncMock()
    mock_client.revoke_token = AsyncMock(return_value=None)

    await calendar_service.revoke_integration(
        db_session,
        tenant.id,
        user.id,
        client=mock_client,
    )
    await db_session.flush()

    mock_client.revoke_token.assert_awaited()
    reloaded = await calendar_service.get_integration(db_session, tenant.id, user.id)
    assert reloaded is not None
    assert reloaded.id == integration_id
    assert reloaded.status == CalendarIntegrationStatus.revoked.value
    assert reloaded.access_token_enc is None
    assert reloaded.refresh_token_enc is None
    assert reloaded.token_expires_at is None

    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == ACTION_CALENDAR_INTEGRATION_UNLINKED,
            AuditLog.resource_id == integration_id,
        )
    )
    audit_row = audit_result.scalar_one()
    assert audit_row.resource_type == RESOURCE_CALENDAR_INTEGRATION
    assert audit_row.user_id == user.id
    assert audit_row.metadata_ is not None
    assert audit_row.metadata_["google_email"] == "revoke@gmail.com"
