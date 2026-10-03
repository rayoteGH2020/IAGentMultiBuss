"""Auditoría sin datos personales en claro, rastreo y retención (Backlog P2c-7)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.core.audit_pseudonym import audit_ref, file_metadata
from app.core.db import set_tenant_context
from app.models import AuditLog, Membership, Tenant, User
from app.services import audit_lookup_service, audit_service
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _member(
    db: AsyncSession, factory: Callable[..., Coroutine[Any, Any, Tenant]], email: str
) -> tuple[Tenant, User, Membership]:
    tenant = await factory()
    user = User(email=email, name="Ana García")
    db.add(user)
    await db.flush()
    await set_tenant_context(db, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="member")
    db.add(membership)
    await db.flush()
    return tenant, user, membership


async def _log(
    db: AsyncSession,
    tenant: Tenant,
    *,
    action: str,
    resource_type: str,
    resource_id: Any = None,
    user_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await set_tenant_context(db, str(tenant.id))
    await audit_service.log_action(
        db,
        tenant_id=tenant.id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
    )


async def test_lookup_by_email_finds_what_was_done_and_what_they_did_even_after_deletion(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    audit_schema_ready: None,
) -> None:
    email = f"ana-{uuid4().hex[:6]}@empresa.com"
    tenant, user, membership = await _member(db_session, tenant_factory, email)
    await _log(
        db_session,
        tenant,
        action="member.created",
        resource_type="membership",
        resource_id=membership.id,
        metadata={"email_ref": audit_ref(email, "email"), "role": "member"},
    )
    await _log(
        db_session,
        tenant,
        action="document.upload",
        resource_type="document",
        resource_id=uuid4(),
        user_id=user.id,
        metadata=file_metadata("Nómina Ana.pdf", sha256="cd" * 32),
    )

    found = await audit_lookup_service.lookup(
        db_session, audit_lookup_service.LookupCriteria(email=email.upper())
    )
    assert {e.action for e in found.entries} == {"member.created", "document.upload"}
    assert found.user_ids == {user.id}

    # Cuenta borrada (D021): sin email ni nombre, pero el seudónimo lleva a ella.
    user.email = f"deleted+{user.id}@deleted.invalid"
    user.name = None
    await db_session.flush()
    after = await audit_lookup_service.lookup(
        db_session, audit_lookup_service.LookupCriteria(email=email, tenant_id=tenant.id)
    )
    assert {e.action for e in after.entries} == {"member.created", "document.upload"}
    assert after.user_ids == {user.id}

    # El rastreo queda auditado en el tenant, sin el dato buscado.
    await set_tenant_context(db_session, str(tenant.id))
    lookups = (
        (
            await db_session.execute(
                select(AuditLog.metadata_).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.action == audit_lookup_service.ACTION_AUDIT_LOOKUP,
                )
            )
        )
        .scalars()
        .all()
    )
    assert lookups and lookups[-1] == {"criteria": ["email"], "entries": 2}
    assert email not in str(lookups)


async def test_lookup_by_filename_and_sha256(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    audit_schema_ready: None,
) -> None:
    tenant = await tenant_factory()
    sha = uuid4().hex * 2
    name = f"Nómina {uuid4().hex[:6]}.pdf"
    await _log(
        db_session,
        tenant,
        action="document.delete",
        resource_type="document",
        resource_id=uuid4(),
        metadata=file_metadata(name, sha256=sha),
    )

    by_name = await audit_lookup_service.lookup(
        db_session, audit_lookup_service.LookupCriteria(filename=name.upper())
    )
    by_hash = await audit_lookup_service.lookup(
        db_session, audit_lookup_service.LookupCriteria(sha256=sha)
    )

    assert [e.action for e in by_name.entries] == ["document.delete"]
    assert [e.action for e in by_hash.entries] == ["document.delete"]
    assert name not in str(by_name.entries[0].metadata)


async def test_lookup_requires_some_criteria(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="al menos un dato"):
        await audit_lookup_service.lookup(db_session, audit_lookup_service.LookupCriteria())


async def test_purge_keeps_the_last_year_and_works_across_tenants(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    audit_schema_ready: None,
) -> None:
    tenants = [await tenant_factory(), await tenant_factory()]
    now = datetime.now(UTC)
    for tenant in tenants:
        await set_tenant_context(db_session, str(tenant.id))
        for age_days in (800, 10):
            db_session.add(
                AuditLog(
                    tenant_id=tenant.id,
                    action="test.purge",
                    resource_type="tenant",
                    created_at=now - timedelta(days=age_days),
                )
            )
        await db_session.flush()  # con el contexto de su tenant (RLS)

    deleted = (await db_session.execute(text("SELECT purge_audit_log(730, 50000)"))).scalar_one()

    assert deleted >= 2
    for tenant in tenants:
        await set_tenant_context(db_session, str(tenant.id))
        remaining = (
            (
                await db_session.execute(
                    select(AuditLog.created_at).where(
                        AuditLog.tenant_id == tenant.id, AuditLog.action == "test.purge"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(remaining) == 1 and remaining[0] > now - timedelta(days=30)


async def test_purge_refuses_less_than_a_year(db_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="retention_days must be >= 365"):
        await db_session.execute(text("SELECT purge_audit_log(364, 100)"))
