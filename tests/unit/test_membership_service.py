"""Tests de membership_service con Clerk mockeado (Paso 30 §E.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.errors import (
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.models.audit_log import AuditLog
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.membership import (
    AppointmentPermissions,
    MemberCreationRequest,
    MembershipPermissions,
    TenantMemberCreate,
    TenantMemberRead,
    TenantMemberUpdate,
)
from app.services import membership_service
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _tenant_with_org(db_session: AsyncSession) -> Tenant:
    tenant = Tenant(clerk_org_id=f"org_{uuid4().hex[:8]}", name="T", plan="free", settings={})
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest.mark.asyncio
async def test_create_tenant_member_persists_permissions(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)

    async def fake_invitation(org_id: str, email: str, role: str = "org:member") -> dict[str, str]:
        return {"id": "inv_1"}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.create_org_invitation",
        fake_invitation,
    )

    async def fake_find_user(email: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.find_user_by_email",
        fake_find_user,
    )

    await set_tenant_context(db_session, str(tenant.id))
    payload = TenantMemberCreate(
        email="new.member@example.com",
        name="New Member",
        role="viewer",
    )
    result = await membership_service.create_tenant_member(db_session, tenant.id, payload)

    assert result.role == "viewer"
    assert result.permissions.appointments.view is True
    assert result.permissions.appointments.create is False

    ms = await db_session.execute(select(Membership).where(Membership.id == result.membership_id))
    membership = ms.scalar_one()
    assert membership.permissions["appointments"]["view"] is True


@pytest.mark.asyncio
async def test_create_tenant_member_uses_add_org_member_for_existing_clerk_user(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    clerk_id = f"user_{uuid4().hex[:8]}"
    user = User(clerk_user_id=clerk_id, email="linked@example.com", name="Linked")
    db_session.add(user)
    await db_session.flush()

    calls: list[tuple[str, str, str]] = []

    async def fake_add(org_id: str, clerk_user_id: str, role: str = "org:member") -> dict[str, str]:
        calls.append((org_id, clerk_user_id, role))
        return {"id": clerk_user_id}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.add_org_member",
        fake_add,
    )

    async def fake_update_user(*args: object, **kwargs: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_user",
        fake_update_user,
    )

    await set_tenant_context(db_session, str(tenant.id))
    result = await membership_service.create_tenant_member(
        db_session,
        tenant.id,
        TenantMemberCreate(email="linked@example.com", name="Linked User", role="member"),
    )

    assert result.clerk_user_id == clerk_id
    assert calls == [(tenant.clerk_org_id, clerk_id, "org:member")]


@pytest.mark.asyncio
async def test_create_tenant_member_does_not_persist_membership_when_clerk_fails(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)

    async def fake_find_user(email: str) -> None:
        return None

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Clerk invitation failed")

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.find_user_by_email",
        fake_find_user,
    )
    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.create_org_invitation",
        boom,
    )

    await set_tenant_context(db_session, str(tenant.id))
    with pytest.raises(RuntimeError, match="Clerk invitation failed"):
        await membership_service.create_tenant_member(
            db_session,
            tenant.id,
            TenantMemberCreate(email="fail@example.com", name="Fail", role="member"),
        )

    count = await db_session.scalar(
        select(func.count()).select_from(Membership).where(Membership.tenant_id == tenant.id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_create_tenant_member_rejects_duplicate(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    user = User(email="dup@example.com", name="Dup")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="member"))
    await db_session.flush()

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.create_org_invitation",
        lambda *a, **k: {"id": "inv"},
    )

    with pytest.raises(ValidationError, match="already a member"):
        await membership_service.create_tenant_member(
            db_session,
            tenant.id,
            TenantMemberCreate(email="dup@example.com", name="Dup", role="viewer"),
        )


@pytest.mark.asyncio
async def test_create_tenant_member_reactivates_revoked_membership(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    clerk_id = f"user_{uuid4().hex[:8]}"
    user = User(clerk_user_id=clerk_id, email="return@example.com", name="Return")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(
        user_id=user.id,
        tenant_id=tenant.id,
        role="member",
        is_active=False,
    )
    db_session.add(membership)
    await db_session.flush()

    async def fake_add(*args: object, **kwargs: object) -> dict[str, str]:
        return {"id": clerk_id}

    async def fake_update(*args: object, **kwargs: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.add_org_member",
        fake_add,
    )
    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_user",
        fake_update,
    )

    result = await membership_service.create_tenant_member(
        db_session,
        tenant.id,
        TenantMemberCreate(email=user.email, name="Return", role="viewer"),
    )

    assert result.membership_id == membership.id
    assert membership.is_active is True
    assert membership.role == "viewer"


@pytest.mark.asyncio
async def test_update_tenant_member_permissions_local_only_no_clerk(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    user = User(clerk_user_id=f"user_{uuid4().hex[:8]}", email="perm@example.com", name="U")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="member")
    db_session.add(membership)
    await db_session.flush()
    original_role = membership.role

    async def boom_clerk(*args: object, **kwargs: object) -> None:
        raise AssertionError("Clerk must not be called on member update")

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_org_member_role",
        boom_clerk,
    )
    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.update_user",
        boom_clerk,
    )

    await set_tenant_context(db_session, str(tenant.id))
    perms = MembershipPermissions(
        appointments=AppointmentPermissions(view=True, create=True, edit=False, cancel=False),
    )
    updated = await membership_service.update_tenant_member(
        db_session,
        tenant.id,
        membership.id,
        TenantMemberUpdate(permissions=perms),
    )
    assert updated.permissions.appointments.create is True
    assert updated.role == original_role


async def _member_for_phone(db_session: AsyncSession) -> tuple[Tenant, User, Membership]:
    tenant = await _tenant_with_org(db_session)
    user = User(clerk_user_id=f"user_{uuid4().hex[:8]}", email=f"tel.{uuid4().hex[:6]}@example.com")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role="member")
    db_session.add(membership)
    await db_session.flush()
    return tenant, user, membership


@pytest.mark.asyncio
async def test_admin_sets_and_clears_member_phone_in_users(db_session: AsyncSession) -> None:
    """El teléfono se guarda en users (no en Clerk) y vacío lo borra."""
    tenant, user, membership = await _member_for_phone(db_session)
    perms = MembershipPermissions()

    updated = await membership_service.update_tenant_member(
        db_session,
        tenant.id,
        membership.id,
        TenantMemberUpdate(permissions=perms, phone="+34  600 111 222"),
        actor_role="admin",
    )
    assert updated.phone == "+34 600 111 222"
    assert user.phone == "+34 600 111 222"

    cleared = await membership_service.update_tenant_member(
        db_session,
        tenant.id,
        membership.id,
        TenantMemberUpdate(permissions=perms, phone=""),
        actor_role="admin",
    )
    assert cleared.phone is None
    assert user.phone is None


@pytest.mark.asyncio
async def test_co_admin_cannot_change_member_phone(db_session: AsyncSession) -> None:
    tenant, user, membership = await _member_for_phone(db_session)
    user.phone = "600111222"
    await db_session.flush()

    with pytest.raises(ForbiddenError):
        await membership_service.update_tenant_member(
            db_session,
            tenant.id,
            membership.id,
            TenantMemberUpdate(permissions=MembershipPermissions(), phone="699000000"),
            actor_role="co_admin",
        )
    # Sin el campo, el co_admin sí guarda permisos y el teléfono no cambia.
    updated = await membership_service.update_tenant_member(
        db_session,
        tenant.id,
        membership.id,
        TenantMemberUpdate(permissions=MembershipPermissions()),
        actor_role="co_admin",
    )
    assert updated.phone == "600111222"


def test_member_phone_validation() -> None:
    from pydantic import ValidationError as PydanticValidationError

    for bad in ("600-111-222", "abc", "12345"):
        with pytest.raises(PydanticValidationError):
            TenantMemberUpdate(permissions=MembershipPermissions(), phone=bad)
    assert TenantMemberUpdate(permissions=MembershipPermissions(), phone="  ").phone is None


async def _add_member(
    db_session: AsyncSession, tenant: Tenant, role: str, name: str
) -> tuple[User, Membership]:
    user = User(
        clerk_user_id=f"user_{uuid4().hex[:8]}",
        email=f"{role}.{uuid4().hex[:6]}@example.com",
        name=name,
    )
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    membership = Membership(user_id=user.id, tenant_id=tenant.id, role=role)
    db_session.add(membership)
    await db_session.flush()
    return user, membership


async def _member_to_remove(
    db_session: AsyncSession, *, actor_role: str = "admin"
) -> tuple[Tenant, Membership, User]:
    """Tenant con un actor (admin por defecto) y un member a dar de baja."""
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, actor_role, "Boss")
    _, membership = await _add_member(db_session, tenant, "member", "Remove Me")
    return tenant, membership, actor


def _patch_removal_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    email_sadm: str = "sadm@example.com",
    smtp_host: str = "smtp.example.com",
) -> AsyncMock:
    monkeypatch.setattr(
        "app.services.membership_service.get_settings",
        lambda: MagicMock(email_sadm=email_sadm, smtp_host=smtp_host),
    )
    send = AsyncMock()
    monkeypatch.setattr("app.services.membership_service.send_email", send)
    return send


async def _request_removal(
    db_session: AsyncSession,
    tenant: Tenant,
    membership_id: UUID,
    redis: AsyncMock | None,
    *,
    actor: User,
    effective_date: date | None = None,
) -> None:
    await membership_service.request_member_removal(
        db_session,
        tenant.id,
        membership_id,
        actor_user_id=actor.id,
        effective_date=effective_date or membership_service.request_date_bounds()[0],
        redis=redis,
    )


def _redis(*, set_result: bool | None = True) -> AsyncMock:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=set_result)
    redis.delete = AsyncMock()
    return redis


@pytest.mark.asyncio
async def test_request_member_removal_emails_sadm_and_keeps_membership_active(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session, actor_role="co_admin")
    send = _patch_removal_settings(monkeypatch)

    async def boom_clerk(*args: object, **kwargs: object) -> None:
        raise AssertionError("Clerk must not be called on removal request")

    monkeypatch.setattr(
        "app.services.membership_service.clerk_client.remove_org_member",
        boom_clerk,
    )
    effective = membership_service.request_date_bounds()[0] + timedelta(days=7)

    await _request_removal(
        db_session, tenant, membership.id, _redis(), actor=actor, effective_date=effective
    )

    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["to"] == "sadm@example.com"
    assert kwargs["subject"] == f"Solicitud de baja de usuario: {tenant.name}"
    body = kwargs["body"]
    assert str(tenant.id) in body
    assert str(tenant.clerk_org_id) in body
    assert "Remove Me" in body
    assert str(membership.id) in body
    assert actor.email in body
    assert "Rol: Co-administrador" in body
    assert f"Fecha baja efectiva: {effective:%d/%m/%Y}" in body
    stored = await db_session.get(Membership, membership.id)
    assert stored is not None
    assert stored.is_active is True
    assert stored.removal_effective_date == effective
    assert stored.removal_requested_at is not None

    members = await membership_service.list_tenant_members(db_session, tenant.id)
    listed = next(m for m in members if m.membership_id == membership.id)
    assert listed.removal_pending is True
    assert listed.removal_effective_date == effective

    # Pendiente: una segunda solicitud se rechaza aunque Redis no la frene.
    with pytest.raises(ValidationError) as exc_info:
        await _request_removal(db_session, tenant, membership.id, _redis(), actor=actor)
    assert exc_info.value.details.get("code") == "removal_already_requested"
    with pytest.raises(ValidationError):
        await membership_service.get_removal_form(
            db_session, tenant.id, membership.id, actor_user_id=actor.id
        )
    assert send.await_count == 1

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == "membership.removal_requested",
        )
    )
    assert audit is not None
    assert audit.metadata_ is not None
    assert audit.metadata_["actor_role"] == "co_admin"
    assert audit.metadata_["effective_date"] == effective.isoformat()


@pytest.mark.asyncio
async def test_co_admin_cannot_request_admin_removal(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    _, owner_ms = await _add_member(db_session, tenant, "admin", "Owner")
    co_admin, _ = await _add_member(db_session, tenant, "co_admin", "Co")
    send = _patch_removal_settings(monkeypatch)

    with pytest.raises(ForbiddenError):
        await _request_removal(db_session, tenant, owner_ms.id, _redis(), actor=co_admin)
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "co_admin"])
async def test_manager_cannot_request_own_removal(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, own_ms = await _add_member(db_session, tenant, role, "Self")
    send = _patch_removal_settings(monkeypatch)

    with pytest.raises(ForbiddenError):
        await _request_removal(db_session, tenant, own_ms.id, _redis(), actor=actor)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_cannot_request_removal(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session, actor_role="member")
    _patch_removal_settings(monkeypatch)

    with pytest.raises(ForbiddenError):
        await _request_removal(db_session, tenant, membership.id, _redis(), actor=actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_days", [-1, 366])
async def test_request_member_removal_rejects_out_of_range_date(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    offset_days: int,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session)
    send = _patch_removal_settings(monkeypatch)
    effective = membership_service.request_date_bounds()[0] + timedelta(days=offset_days)

    with pytest.raises(ValidationError) as exc_info:
        await _request_removal(
            db_session, tenant, membership.id, _redis(), actor=actor, effective_date=effective
        )
    assert exc_info.value.details.get("code") == "removal_date_invalid"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_removal_form_includes_actor_role_and_date_bounds(
    db_session: AsyncSession,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session, actor_role="co_admin")

    form = await membership_service.get_removal_form(
        db_session, tenant.id, membership.id, actor_user_id=actor.id
    )

    assert form.member.membership_id == membership.id
    assert form.actor_role == "co_admin"
    assert form.actor_email == actor.email
    assert (form.min_date, form.max_date) == membership_service.request_date_bounds()


@pytest.mark.asyncio
async def test_request_member_removal_rate_limited(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session)
    send = _patch_removal_settings(monkeypatch)

    with pytest.raises(RateLimitError):
        await _request_removal(
            db_session, tenant, membership.id, _redis(set_result=None), actor=actor
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_member_removal_requires_email_sadm(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session)
    _patch_removal_settings(monkeypatch, email_sadm="  ")

    with pytest.raises(ValidationError) as exc_info:
        await _request_removal(db_session, tenant, membership.id, _redis(), actor=actor)
    assert exc_info.value.details.get("code") == "email_sadm_missing"


@pytest.mark.asyncio
async def test_request_member_removal_fails_without_smtp(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session)
    send = _patch_removal_settings(monkeypatch, smtp_host="")
    redis = _redis()

    with pytest.raises(ExternalServiceError) as exc_info:
        await _request_removal(db_session, tenant, membership.id, redis, actor=actor)
    assert exc_info.value.details.get("code") == "smtp_not_configured"
    send.assert_not_awaited()
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_member_removal_send_failure_releases_rate_limit(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, actor = await _member_to_remove(db_session)
    send = _patch_removal_settings(monkeypatch)
    send.side_effect = RuntimeError("smtp down")
    redis = _redis()

    with pytest.raises(ExternalServiceError) as exc_info:
        await _request_removal(db_session, tenant, membership.id, redis, actor=actor)
    assert exc_info.value.details.get("code") == "removal_request_send_failed"
    redis.delete.assert_awaited_once()
    # Sin email enviado no debe quedar una baja "pendiente".
    assert membership.removal_pending is False


@pytest.mark.asyncio
async def test_create_tenant_member_reactivation_clears_pending_removal(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, membership, _ = await _member_to_remove(db_session)
    membership.removal_requested_at = datetime.now(UTC)
    membership.removal_effective_date = date(2026, 10, 9)
    membership.is_active = False
    await db_session.flush()
    user = await db_session.get(User, membership.user_id)
    assert user is not None

    async def ok(*args: object, **kwargs: object) -> dict[str, str]:
        return {"id": "x"}

    for fn in ("add_org_member", "update_user"):
        monkeypatch.setattr(f"app.services.membership_service.clerk_client.{fn}", ok)
    _patch_capacity(monkeypatch)

    result = await membership_service.create_tenant_member(
        db_session,
        tenant.id,
        TenantMemberCreate(email=user.email, name="Vuelve", role="member"),
    )

    assert result.removal_pending is False
    assert membership.removal_effective_date is None
    assert membership.removal_requested_at is None


@pytest.mark.asyncio
async def test_member_with_pending_removal_cannot_be_edited(
    db_session: AsyncSession,
) -> None:
    tenant, membership, _ = await _member_to_remove(db_session)
    editable = await membership_service.get_editable_member(db_session, tenant.id, membership.id)
    assert editable.membership_id == membership.id

    membership.removal_requested_at = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
    membership.removal_effective_date = date(2026, 10, 9)
    original = dict(membership.permissions)
    await db_session.flush()

    with pytest.raises(ValidationError) as form_exc:
        await membership_service.get_editable_member(db_session, tenant.id, membership.id)
    assert form_exc.value.details.get("code") == "member_locked_by_removal"

    perms = MembershipPermissions(
        appointments=AppointmentPermissions(view=True, create=True, edit=True, cancel=True),
    )
    with pytest.raises(ValidationError) as update_exc:
        await membership_service.update_tenant_member(
            db_session, tenant.id, membership.id, TenantMemberUpdate(permissions=perms)
        )
    assert update_exc.value.details.get("code") == "member_locked_by_removal"
    assert membership.permissions == original


def test_removal_is_due_from_effective_date_midnight() -> None:
    membership = Membership(role="member")
    today = date(2026, 10, 9)
    assert membership_service.removal_is_due(membership, today) is False
    membership.removal_effective_date = date(2026, 10, 10)
    assert membership_service.removal_is_due(membership, today) is False
    membership.removal_effective_date = today
    assert membership_service.removal_is_due(membership, today) is True
    membership.removal_effective_date = date(2026, 10, 1)
    assert membership_service.removal_is_due(membership, today) is True


@pytest.mark.asyncio
async def test_apply_due_removal_deactivates_and_audits_once(
    db_session: AsyncSession,
) -> None:
    tenant, membership, _ = await _member_to_remove(db_session)
    membership.removal_requested_at = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
    membership.removal_effective_date = date(2026, 10, 9)
    await db_session.flush()

    before = await membership_service.apply_due_removal(
        db_session, membership, source="request", today=date(2026, 10, 8)
    )
    assert before is False and membership.is_active is True

    done = await membership_service.apply_due_removal(
        db_session, membership, source="request", today=date(2026, 10, 9)
    )
    again = await membership_service.apply_due_removal(
        db_session, membership, source="request", today=date(2026, 10, 9)
    )
    assert done is True and again is False
    assert membership.is_active is False
    # Las fechas se conservan como rastro hasta una reactivación.
    assert membership.removal_effective_date == date(2026, 10, 9)

    audits = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id,
                    AuditLog.action == "membership.removal_executed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].resource_id == membership.id
    assert audits[0].metadata_ == {"effective_date": "2026-10-09", "source": "request"}


@pytest.mark.asyncio
async def test_execute_due_removals_sweeps_all_tenants(
    db_session: AsyncSession,
) -> None:
    today = date(2026, 10, 9)
    tenant_a, due_a, _ = await _member_to_remove(db_session)
    tenant_b, due_b, _ = await _member_to_remove(db_session)
    tenant_c, future, _ = await _member_to_remove(db_session)
    for tenant, m, effective in (
        (tenant_a, due_a, today),
        (tenant_b, due_b, date(2026, 10, 1)),
        (tenant_c, future, date(2026, 10, 10)),
    ):
        # FORCE RLS: sin el contexto de su tenant el UPDATE no afecta a ninguna fila.
        await set_tenant_context(db_session, str(tenant.id))
        m.removal_requested_at = datetime(2026, 9, 28, tzinfo=UTC)
        m.removal_effective_date = effective
        await db_session.flush()

    executed = await membership_service.execute_due_removals(db_session, today=today)

    # >= 2: saas_test puede tener otras bajas vencidas de ejecuciones previas.
    assert executed >= 2
    assert due_a.is_active is False
    assert due_b.is_active is False
    assert future.is_active is True
    assert tenant_a.id != tenant_b.id


def test_build_removal_request_email_strips_newlines_from_subject() -> None:
    tenant = Tenant(id=uuid4(), clerk_org_id="org_x", name="Acme\r\nBcc: x@evil.test")
    member = User(email="m@example.com", name=None)
    membership = Membership(id=uuid4(), role="viewer")
    actor = User(email="boss@example.com", name=None)

    subject, body = membership_service.build_removal_request_email(
        tenant=tenant,
        member=member,
        membership=membership,
        actor=actor,
        actor_role="admin",
        effective_date=date(2026, 10, 15),
        requested_at=datetime(2026, 9, 28, 10, 30, tzinfo=UTC),
    )

    assert "\n" not in subject and "\r" not in subject
    assert "Nombre: —" in body
    assert "Rol: Lector" in body
    assert "Rol: Administrador" in body
    assert "Fecha baja efectiva: 15/10/2026" in body
    assert "Fecha solicitud (UTC): 2026-09-28 10:30" in body


def test_removable_membership_ids_hides_self_and_admin_for_co_admin() -> None:
    def read(role: str) -> TenantMemberRead:
        return TenantMemberRead(
            membership_id=uuid4(),
            user_id=uuid4(),
            email=f"{role}@example.com",
            name=None,
            role=role,
            permissions=MembershipPermissions(),
        )

    owner, co_self, co_other, member = (
        read("admin"),
        read("co_admin"),
        read("co_admin"),
        read("member"),
    )
    ids = membership_service.removable_membership_ids(
        [owner, co_self, co_other, member],
        actor_membership_id=co_self.membership_id,
        actor_role="co_admin",
    )
    assert ids == {co_other.membership_id, member.membership_id}

    pending = member.model_copy(update={"removal_effective_date": date(2026, 10, 9)})
    ids = membership_service.removable_membership_ids(
        [pending], actor_membership_id=co_self.membership_id, actor_role="co_admin"
    )
    assert ids == set()


@pytest.mark.asyncio
async def test_list_tenant_members_returns_joined_rows(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant_with_org(db_session)
    user = User(email="listed@example.com", name="Listed")
    db_session.add_all([tenant, user])
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="viewer"))
    await db_session.flush()

    await set_tenant_context(db_session, str(tenant.id))
    members = await membership_service.list_tenant_members(db_session, tenant.id)

    assert len(members) == 1
    assert members[0].email == "listed@example.com"
    assert members[0].role == "viewer"


@pytest.mark.asyncio
async def test_request_member_removal_not_found(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "admin", "Boss")
    send = _patch_removal_settings(monkeypatch)
    with pytest.raises(NotFoundError, match="Membership not found"):
        await _request_removal(db_session, tenant, uuid4(), _redis(), actor=actor)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_tenant_member_rejects_second_admin(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant_with_org(db_session)
    await _add_member(db_session, tenant, "admin", "Owner")

    with pytest.raises(ValidationError) as exc_info:
        await membership_service.create_tenant_member(
            db_session,
            tenant.id,
            TenantMemberCreate(email="owner2@example.com", name="Owner 2", role="admin"),
        )
    assert exc_info.value.details.get("code") == "admin_already_exists"


def _creation_payload(**overrides: object) -> MemberCreationRequest:
    data: dict[str, object] = {
        "first_name": "Lucía",
        "last_name": "Pérez Gómez",
        "alias": "Lu",
        "email": "Lucia.Perez@Example.com",
        "role": "co_admin",
        "start_date": membership_service.request_date_bounds()[0] + timedelta(days=3),
    }
    data.update(overrides)
    return MemberCreationRequest.model_validate(data)


def _patch_capacity(monkeypatch: pytest.MonkeyPatch, *, full: bool = False) -> None:
    async def fake_capacity(*args: object, **kwargs: object) -> None:
        if full:
            raise ValidationError("members max")

    async def fake_resolve(*args: object, **kwargs: object) -> object:
        return object()

    monkeypatch.setattr("app.services.plan_quota_service.ensure_member_capacity", fake_capacity)
    monkeypatch.setattr("app.services.entitlement_service.resolve_tenant", fake_resolve)


async def _request_creation(
    db_session: AsyncSession,
    tenant: Tenant,
    payload: MemberCreationRequest,
    *,
    actor: User,
    redis: AsyncMock | None = None,
) -> None:
    await membership_service.request_member_creation(
        db_session, tenant.id, payload, actor_user_id=actor.id, redis=redis or _redis()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_role", ["admin", "co_admin"])
async def test_request_member_creation_emails_sadm_without_creating_member(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    actor_role: str,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, actor_role, "Jefa")
    send = _patch_removal_settings(monkeypatch)
    _patch_capacity(monkeypatch)
    payload = _creation_payload()

    async def boom_clerk(*args: object, **kwargs: object) -> None:
        raise AssertionError("Clerk must not be called on creation request")

    for fn in ("add_org_member", "create_org_invitation"):
        monkeypatch.setattr(f"app.services.membership_service.clerk_client.{fn}", boom_clerk)

    await _request_creation(db_session, tenant, payload, actor=actor)

    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["subject"] == f"Solicitud de alta de usuario: {tenant.name}"
    body = kwargs["body"]
    assert f"Fecha alta: {payload.start_date:%d/%m/%Y}" in body
    assert "Nombre: Lucía" in body
    assert "Apellidos: Pérez Gómez" in body
    assert "Alias: Lu" in body
    assert "Email: lucia.perez@example.com" in body
    assert "Rol: Co-administrador (org:co_admin en Clerk)" in body
    assert str(tenant.clerk_org_id) in body
    assert actor.email in body

    members = await membership_service.list_tenant_members(db_session, tenant.id)
    assert [m.email for m in members] == [actor.email]

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == "membership.creation_requested",
        )
    )
    assert audit is not None and audit.metadata_ is not None
    assert audit.metadata_["role"] == "co_admin"
    assert audit.metadata_["actor_role"] == actor_role


@pytest.mark.asyncio
async def test_member_cannot_request_creation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "member", "Nope")
    send = _patch_removal_settings(monkeypatch)
    _patch_capacity(monkeypatch)

    with pytest.raises(ForbiddenError):
        await _request_creation(db_session, tenant, _creation_payload(), actor=actor)
    with pytest.raises(ForbiddenError):
        await membership_service.get_creation_form(db_session, tenant.id, actor_user_id=actor.id)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_member_creation_rejects_existing_member(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "admin", "Jefa")
    existing, _ = await _add_member(db_session, tenant, "member", "Ya")
    send = _patch_removal_settings(monkeypatch)
    _patch_capacity(monkeypatch)

    with pytest.raises(ValidationError) as exc_info:
        await _request_creation(
            db_session, tenant, _creation_payload(email=existing.email.upper()), actor=actor
        )
    assert exc_info.value.details.get("code") == "member_already_exists"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_member_creation_rejects_when_plan_is_full(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "admin", "Jefa")
    send = _patch_removal_settings(monkeypatch)
    _patch_capacity(monkeypatch, full=True)

    with pytest.raises(ValidationError) as exc_info:
        await _request_creation(db_session, tenant, _creation_payload(), actor=actor)
    assert exc_info.value.details.get("code") == "members_max_reached"
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_days", [-1, 366])
async def test_request_member_creation_rejects_out_of_range_date(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    offset_days: int,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "admin", "Jefa")
    send = _patch_removal_settings(monkeypatch)
    _patch_capacity(monkeypatch)
    start = membership_service.request_date_bounds()[0] + timedelta(days=offset_days)

    with pytest.raises(ValidationError) as exc_info:
        await _request_creation(
            db_session, tenant, _creation_payload(start_date=start), actor=actor
        )
    assert exc_info.value.details.get("code") == "creation_date_invalid"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_member_creation_rate_limited_per_email(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "admin", "Jefa")
    send = _patch_removal_settings(monkeypatch)
    _patch_capacity(monkeypatch)
    redis = _redis(set_result=None)

    with pytest.raises(RateLimitError) as exc_info:
        await _request_creation(db_session, tenant, _creation_payload(), actor=actor, redis=redis)
    assert exc_info.value.details.get("code") == "creation_request_rate_limited"
    assert redis.set.await_args.args[0] == (
        f"membership:creation_request:{tenant.id}:lucia.perez@example.com"
    )
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_creation_form_returns_actor_and_bounds(
    db_session: AsyncSession,
) -> None:
    tenant = await _tenant_with_org(db_session)
    actor, _ = await _add_member(db_session, tenant, "co_admin", "Jefa")

    form = await membership_service.get_creation_form(db_session, tenant.id, actor_user_id=actor.id)

    assert form.actor_role == "co_admin"
    assert form.actor_email == actor.email
    assert (form.min_date, form.max_date) == membership_service.request_date_bounds()


def test_app_role_to_clerk_role_mapping() -> None:
    assert membership_service.app_role_to_clerk_role("admin") == "org:admin"
    assert membership_service.app_role_to_clerk_role("co_admin") == "org:co_admin"
    assert membership_service.app_role_to_clerk_role("viewer") == "org:member"
    assert membership_service.app_role_to_clerk_role("unknown") == "org:member"
