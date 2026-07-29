"""Comprobación de permisos por membership (Paso 30)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Collection

    from app.models.membership import Membership
    from app.models.tenant import Tenant
    from app.models.user import User

AppointmentAction = Literal["view", "create", "edit", "cancel"]

SUPERADMIN_ORG_ROLE = "admin"


def membership_can(
    membership: Membership,
    module: str,
    action: str,
) -> bool:
    """True si la membership puede ejecutar `action` en `module`.

    Los admins tienen bypass implícito (decisión 13).
    """
    if membership.role == "admin":
        return True
    raw_permissions = membership.permissions
    if not isinstance(raw_permissions, dict):
        return False
    module_perms = raw_permissions.get(module, {})
    if not isinstance(module_perms, dict):
        return False
    return bool(module_perms.get(action, False))


def membership_can_appointment(membership: Membership, action: AppointmentAction) -> bool:
    """Atajo para permisos de citas."""
    return membership_can(membership, "appointments", action)


def is_platform_superadmin(
    *,
    tenant: Tenant | None,
    membership: Membership | None,
    user: User | None = None,
    admin_clerk_org_id: str,
    allowed_clerk_user_ids: Collection[str] = (),
) -> bool:
    """True solo si el usuario es admin activo de la organización SADM.

    Pertenecer a la org de `ADMIN_CLERK_ORG_ID` no basta: un `member` o
    `viewer` de esa org no puede acceder a datos cross-tenant. Si
    `allowed_clerk_user_ids` no está vacío actúa como allowlist adicional
    (defensa en profundidad ante altas indebidas en Clerk).

    Args:
        tenant: Tenant resuelto del request (None si no hay sesión).
        membership: Membership del usuario en ese tenant.
        user: Usuario local; obligatorio si se configura allowlist.
        admin_clerk_org_id: Valor de `ADMIN_CLERK_ORG_ID`.
        allowed_clerk_user_ids: Allowlist opcional de `clerk_user_id`.

    Returns:
        True si la sesión puede operar como superadmin de plataforma.
    """
    admin_org = admin_clerk_org_id.strip()
    if not admin_org or tenant is None or membership is None:
        return False
    if tenant.clerk_org_id != admin_org:
        return False
    if not membership.is_active or membership.role != SUPERADMIN_ORG_ROLE:
        return False
    if allowed_clerk_user_ids:
        return user is not None and user.clerk_user_id in allowed_clerk_user_ids
    return True
