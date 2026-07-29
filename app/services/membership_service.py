"""Gestión de miembros del tenant con réplica Clerk (Paso 30 Fase B)."""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clerk_client
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError, ValidationError
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.membership import (
    MembershipPermissions,
    TenantMemberCreate,
    TenantMemberRead,
    TenantMemberUpdate,
)
from app.services import audit_service

log = structlog.get_logger(__name__)

ACTION_MEMBER_CREATED = "membership.created"
ACTION_MEMBER_UPDATED = "membership.updated"
ACTION_MEMBER_REMOVED = "membership.removed"
RESOURCE_MEMBERSHIP = "membership"

APP_ROLE_TO_CLERK_ROLE: dict[str, str] = {
    "admin": "org:admin",
    "member": "org:member",
    "viewer": "org:member",
}

VALID_APP_ROLES = frozenset({"admin", "member", "viewer"})


def app_role_to_clerk_role(role: str) -> str:
    return APP_ROLE_TO_CLERK_ROLE.get(role, "org:member")


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


async def _get_tenant_or_raise(db: AsyncSession, tenant_id: UUID) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError(f"Tenant {tenant_id} not found")
    if tenant.clerk_org_id is None:
        raise ValidationError("Tenant is not linked to Clerk")
    return tenant


async def list_tenant_members(db: AsyncSession, tenant_id: UUID) -> list[TenantMemberRead]:
    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
        .order_by(User.email)
    )
    rows: list[TenantMemberRead] = []
    for user, membership in result.all():
        rows.append(
            TenantMemberRead(
                membership_id=membership.id,
                user_id=user.id,
                email=user.email,
                name=user.name,
                role=membership.role,
                permissions=MembershipPermissions.from_json_dict(membership.permissions),
                clerk_user_id=user.clerk_user_id,
            )
        )
    return rows


async def create_tenant_member(
    db: AsyncSession,
    tenant_id: UUID,
    payload: TenantMemberCreate,
    *,
    actor_user_id: UUID | None = None,
) -> TenantMemberRead:
    if payload.role not in VALID_APP_ROLES:
        raise ValidationError(f"Invalid role: {payload.role}")

    tenant = await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(email=payload.email, name=payload.name)
        db.add(user)
        await db.flush()

    result_ms = await db.execute(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.tenant_id == tenant_id,
        )
    )
    membership = result_ms.scalar_one_or_none()
    if membership is not None and membership.is_active:
        raise ValidationError("User is already a member of this tenant")

    clerk_role = app_role_to_clerk_role(payload.role)
    clerk_user_id = user.clerk_user_id
    org_id = tenant.clerk_org_id
    if org_id is None:
        raise ValidationError("Tenant has no Clerk organization linked")

    try:
        if clerk_user_id:
            await clerk_client.add_org_member(org_id, clerk_user_id, role=clerk_role)
        else:
            clerk_user = await clerk_client.find_user_by_email(payload.email)
            if clerk_user and isinstance(clerk_user.get("id"), str):
                resolved_clerk_user_id = str(clerk_user["id"])
                clerk_user_id = resolved_clerk_user_id
                user.clerk_user_id = resolved_clerk_user_id
                await clerk_client.add_org_member(org_id, resolved_clerk_user_id, role=clerk_role)
            else:
                await clerk_client.create_org_invitation(
                    org_id,
                    payload.email,
                    role=clerk_role,
                )

        if membership is None:
            membership = Membership(user_id=user.id, tenant_id=tenant_id)
            db.add(membership)
        membership.role = payload.role
        membership.permissions = payload.permissions.to_json_dict()
        membership.is_active = True
        await db.flush()

        if clerk_user_id:
            first, last = _split_name(payload.name)
            await clerk_client.update_user(clerk_user_id, first_name=first, last_name=last)

        await audit_service.log_action(
            db,
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action=ACTION_MEMBER_CREATED,
            resource_type=RESOURCE_MEMBERSHIP,
            resource_id=membership.id,
            metadata={"email": payload.email, "role": payload.role},
        )
    except Exception:
        log.error("membership.create_failed", email=payload.email, tenant_id=str(tenant_id))
        raise

    log.info("membership.created", email=payload.email, tenant_id=str(tenant_id))
    return TenantMemberRead(
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=membership.role,
        permissions=MembershipPermissions.from_json_dict(membership.permissions),
        clerk_user_id=user.clerk_user_id,
    )


async def update_tenant_member(
    db: AsyncSession,
    tenant_id: UUID,
    membership_id: UUID,
    payload: TenantMemberUpdate,
    *,
    actor_user_id: UUID | None = None,
) -> TenantMemberRead:
    tenant = await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))

    result = await db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.id == membership_id,
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundError("Membership not found")
    membership, user = row

    if payload.role is not None and payload.role not in VALID_APP_ROLES:
        raise ValidationError(f"Invalid role: {payload.role}")

    if payload.name is not None:
        user.name = payload.name
    if payload.role is not None:
        membership.role = payload.role
    if payload.permissions is not None:
        membership.permissions = payload.permissions.to_json_dict()

    try:
        if user.clerk_user_id and tenant.clerk_org_id:
            clerk_role = app_role_to_clerk_role(membership.role)
            await clerk_client.update_org_member_role(
                tenant.clerk_org_id,
                user.clerk_user_id,
                clerk_role,
            )
            if payload.name is not None:
                first, last = _split_name(payload.name)
                await clerk_client.update_user(user.clerk_user_id, first_name=first, last_name=last)

        await db.flush()
        await audit_service.log_action(
            db,
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action=ACTION_MEMBER_UPDATED,
            resource_type=RESOURCE_MEMBERSHIP,
            resource_id=membership.id,
            metadata={"role": membership.role},
        )
    except Exception:
        log.error("membership.update_failed", membership_id=str(membership_id))
        raise

    return TenantMemberRead(
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=membership.role,
        permissions=MembershipPermissions.from_json_dict(membership.permissions),
        clerk_user_id=user.clerk_user_id,
    )


async def remove_tenant_member(
    db: AsyncSession,
    tenant_id: UUID,
    membership_id: UUID,
    *,
    actor_user_id: UUID | None = None,
) -> None:
    tenant = await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))

    result = await db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.id == membership_id,
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundError("Membership not found")
    membership, user = row

    try:
        if user.clerk_user_id and tenant.clerk_org_id:
            await clerk_client.remove_org_member(tenant.clerk_org_id, user.clerk_user_id)
        membership.is_active = False
        await db.flush()
        await audit_service.log_action(
            db,
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action=ACTION_MEMBER_REMOVED,
            resource_type=RESOURCE_MEMBERSHIP,
            resource_id=membership_id,
            metadata={"email": user.email},
        )
    except Exception:
        log.error("membership.remove_failed", membership_id=str(membership_id))
        raise

    log.info("membership.removed", membership_id=str(membership_id), tenant_id=str(tenant_id))
