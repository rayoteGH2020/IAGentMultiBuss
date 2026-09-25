"""Comprobación de permisos por membership (Paso 30)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from app.models.membership import Membership
    from app.models.tenant import Tenant
    from app.models.user import User

AppointmentAction = Literal["view", "create", "edit", "cancel"]

SUPERADMIN_ORG_ROLE = "admin"

# Navegación principal por rol Clerk (admin / member|viewer).
# Tupla: (href, label, icon_filename_sin_html).
NavItem = tuple[str, str, str]

ADMIN_NAV_ITEMS: tuple[NavItem, ...] = (
    ("/documents", "Documentos", "invoice"),
    ("/knowledge", "Conocimiento", "book"),
    ("/chat", "Chat", "chat"),
    ("/calendar", "Calendario", "calendar"),
    ("/appointments", "Citas", "appointments"),
    ("/settings", "Ajustes", "settings"),
)

MEMBER_NAV_ITEMS: tuple[NavItem, ...] = (
    ("/chat", "Chat", "chat"),
    ("/appointments", "Citas", "appointments"),
)

# Feature requerida para mostrar cada href del sidebar (None = siempre).
# Chat: basta con documents_chat o knowledge_chat.
_NAV_FEATURE_ANY: dict[str, frozenset[str]] = {
    "/documents": frozenset({"documents"}),
    "/knowledge": frozenset({"knowledge"}),
    "/chat": frozenset({"documents_chat", "knowledge_chat"}),
    "/calendar": frozenset({"calendar_google"}),
    "/appointments": frozenset({"appointments"}),
}

# Prefijos de app permitidos a member/viewer (además de rutas exentas globales).
_MEMBER_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/chat",
    "/appointments",
    "/api/v1/scheduling",
)


def nav_items_for_role(role: str) -> list[NavItem]:
    """Ítems de sidebar visibles según rol de organización (sin filtrar plan)."""
    if role == "admin":
        return list(ADMIN_NAV_ITEMS)
    return list(MEMBER_NAV_ITEMS)


def nav_items_for_access(
    role: str,
    entitlements: object | None = None,
    *,
    has_feature: Callable[[str], bool] | None = None,
) -> list[NavItem]:
    """Sidebar: rol ∩ entitlements.

    ``entitlements`` debe exponer ``.has(code) -> bool`` (p. ej. Entitlements).
    ``has_feature`` permite inyectar un callable en tests.
    """
    items = nav_items_for_role(role)
    checker = has_feature
    if checker is None and entitlements is not None:
        has_method = getattr(entitlements, "has", None)
        if callable(has_method):
            checker = has_method
    if checker is None:
        return items
    visible: list[NavItem] = []
    for href, label, icon in items:
        required = _NAV_FEATURE_ANY.get(href)
        if required is None:
            visible.append((href, label, icon))
            continue
        if any(checker(code) for code in required):
            visible.append((href, label, icon))
    return visible


def home_path_for_role(role: str) -> str:
    """Destino tras login / al denegar una URL (admin → inicio; resto → chat)."""
    return "/" if role == "admin" else "/chat"


def role_can_access_path(role: str, path: str) -> bool:
    """True si el rol puede abrir ``path`` (rutas de producto autenticadas).

    Las rutas públicas, logout, onboarding, SADM, etc. se excluyen antes en
    el middleware; aquí solo se decide el acceso a áreas de la app.
    """
    if role == "admin":
        return True
    normalized = path.rstrip("/") or "/"
    if normalized == "/":
        # Inicio con métricas de facturas: solo admin. Member va a /chat.
        return False
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in _MEMBER_ALLOWED_PREFIXES
    )


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
