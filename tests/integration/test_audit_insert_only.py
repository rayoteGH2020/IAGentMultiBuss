"""audit_log solo inserción para saas_app (p78, Backlog P2c-1).

Corre con el rol RLS (``saas_app``) de ``db_session``. Las FK (borrar tenant o
usuario) deben seguir funcionando: Postgres las ejecuta como propietario.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import uuid4

import pytest
from app.core.db import set_tenant_context
from app.models import AuditLog, Tenant, User
from app.services import audit_service
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _audited_tenant(
    db: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> tuple[Tenant, User, AuditLog]:
    tenant = await tenant_factory()
    user = User(clerk_user_id=f"user_{uuid4().hex[:12]}", email=f"{uuid4().hex[:8]}@test.local")
    db.add(user)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    row = await audit_service.log_action(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        action="test.audit_insert_only",
        resource_type="test",
    )
    return tenant, user, row


async def test_saas_app_has_no_update_or_delete_privilege(
    audit_schema_ready: None,
    db_session: AsyncSession,
) -> None:
    result = await db_session.execute(
        text(
            "SELECT current_user, "
            "has_table_privilege(current_user, 'audit_log', 'INSERT'), "
            "has_table_privilege(current_user, 'audit_log', 'UPDATE'), "
            "has_table_privilege(current_user, 'audit_log', 'DELETE')"
        )
    )
    role, can_insert, can_update, can_delete = result.one()

    if role != "saas_app":
        pytest.skip(f"db_session no usa saas_app sino {role}")
    assert (can_insert, can_update, can_delete) == (True, False, False)


async def test_update_and_delete_are_rejected(
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant, _, row = await _audited_tenant(db_session, tenant_factory)

    for statement in (
        update(AuditLog).where(AuditLog.id == row.id).values(action="test.tampered"),
        delete(AuditLog).where(AuditLog.tenant_id == tenant.id),
    ):
        with pytest.raises(ProgrammingError, match="permission denied"):
            async with db_session.begin_nested():
                await db_session.execute(statement)

    stored = await db_session.scalar(select(AuditLog.action).where(AuditLog.id == row.id))
    assert stored == "test.audit_insert_only"


async def test_fk_actions_still_work_for_user_and_tenant_deletion(
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant, user, row = await _audited_tenant(db_session, tenant_factory)

    # ON DELETE SET NULL sobre audit_log.user_id.
    await db_session.execute(delete(User).where(User.id == user.id))
    user_id = await db_session.scalar(
        select(AuditLog.user_id)
        .where(AuditLog.id == row.id)
        .execution_options(populate_existing=True)
    )
    assert user_id is None

    # ON DELETE CASCADE desde tenants.
    await db_session.execute(delete(Tenant).where(Tenant.id == tenant.id))
    remaining = await db_session.scalar(select(AuditLog.id).where(AuditLog.tenant_id == tenant.id))
    assert remaining is None
