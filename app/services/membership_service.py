"""Gestión de miembros del tenant con réplica Clerk (Paso 30 Fase B)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import clerk_client
from app.core.datetime_display import display_today
from app.core.db import set_tenant_context
from app.core.email import send_email
from app.core.entitlement_codes import plan_ui_name
from app.core.errors import (
    ExternalServiceError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.core.log_redaction import pseudonymize
from app.core.permissions import (
    ORG_ADMIN_ROLE,
    ORG_CO_ADMIN_ROLE,
    can_request_member_removal,
    is_manager_role,
    role_label,
)
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.membership import (
    MemberCreationForm,
    MemberCreationRequest,
    MemberRemovalForm,
    MembershipPermissions,
    TenantMemberCreate,
    TenantMemberRead,
    TenantMemberUpdate,
)
from app.services import audit_service

if TYPE_CHECKING:
    from redis.asyncio import Redis

log = structlog.get_logger(__name__)

ACTION_MEMBER_CREATED = "membership.created"
ACTION_MEMBER_UPDATED = "membership.updated"
ACTION_MEMBER_REMOVAL_REQUESTED = "membership.removal_requested"
ACTION_MEMBER_CREATION_REQUESTED = "membership.creation_requested"
ACTION_MEMBER_REMOVAL_EXECUTED = "membership.removal_executed"
REMOVAL_SOURCE_REQUEST = "request"
REMOVAL_SOURCE_SCHEDULED = "scheduled"
RESOURCE_MEMBERSHIP = "membership"

REMOVAL_REQUEST_SUBJECT_PREFIX = "Solicitud de baja de usuario: "
CREATION_REQUEST_SUBJECT_PREFIX = "Solicitud de alta de usuario: "
# Evita reenvíos al SADM por doble clic o insistencia; alta/baja real van por Clerk.
SADM_REQUEST_TTL_SECONDS = 24 * 60 * 60
_REMOVAL_REQUEST_KEY_PREFIX = "membership:removal_request:"
_CREATION_REQUEST_KEY_PREFIX = "membership:creation_request:"

APP_ROLE_TO_CLERK_ROLE: dict[str, str] = {
    ORG_ADMIN_ROLE: "org:admin",
    ORG_CO_ADMIN_ROLE: "org:co_admin",
    "member": "org:member",
    "viewer": "org:member",
}

VALID_APP_ROLES = frozenset({ORG_ADMIN_ROLE, ORG_CO_ADMIN_ROLE, "member", "viewer"})

# Margen máximo para programar un alta o baja: evita fechas absurdas por error de tecleo.
REQUEST_MAX_DAYS_AHEAD = 365


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
    return [_member_read(membership, user) for user, membership in result.all()]


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

    if payload.role == ORG_ADMIN_ROLE:
        existing_admin = await db.scalar(
            select(Membership.id)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.role == ORG_ADMIN_ROLE,
                Membership.is_active.is_(True),
            )
            .limit(1)
        )
        if existing_admin is not None:
            raise ValidationError(
                "Tenant already has an admin", details={"code": "admin_already_exists"}
            )

    if membership is None or not membership.is_active:
        from app.services import entitlement_service, plan_quota_service

        ents = await entitlement_service.resolve_tenant(db, tenant_id)
        await plan_quota_service.ensure_member_capacity(db, ents, tenant_id)

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
        membership.clear_removal_request()
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
        log.error(
            "membership.create_failed",
            email_ref=pseudonymize(payload.email),
            tenant_id=str(tenant_id),
        )
        raise

    log.info(
        "membership.created",
        email_ref=pseudonymize(payload.email),
        tenant_id=str(tenant_id),
    )
    return _member_read(membership, user)


def _ensure_not_pending_removal(membership: Membership) -> None:
    """Un miembro con baja solicitada queda congelado: no se edita."""
    if membership.removal_pending:
        raise ValidationError(
            "Member has a pending removal and cannot be edited",
            details={"code": "member_locked_by_removal"},
        )


async def get_editable_member(
    db: AsyncSession, tenant_id: UUID, membership_id: UUID
) -> TenantMemberRead:
    """Miembro activo para el formulario de edición; rechaza si tiene baja pendiente."""
    await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    row = await _active_membership_row(db, tenant_id, membership_id=membership_id)
    if row is None:
        raise NotFoundError("Membership not found")
    _ensure_not_pending_removal(row[0])
    return _member_read(row[0], row[1])


async def update_tenant_member(
    db: AsyncSession,
    tenant_id: UUID,
    membership_id: UUID,
    payload: TenantMemberUpdate,
    *,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
) -> TenantMemberRead:
    """Actualiza permisos de citas y, si viene, el teléfono. No escribe en Clerk.

    El teléfono solo lo cambia el admin del tenant (``actor_role``); el
    co_admin lo ve pero no lo modifica.
    """
    phone_requested = "phone" in payload.model_fields_set
    if phone_requested and actor_role != ORG_ADMIN_ROLE:
        raise ForbiddenError("Only the tenant admin can change a member's phone")
    await _get_tenant_or_raise(db, tenant_id)
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
    _ensure_not_pending_removal(membership)

    membership.permissions = payload.permissions.to_json_dict()
    metadata: dict[str, object] = {"permissions": membership.permissions}
    if phone_requested:
        user.phone = payload.phone
        # Se audita el cambio, no el número (dato personal).
        metadata["phone_set"] = payload.phone is not None
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_MEMBER_UPDATED,
        resource_type=RESOURCE_MEMBERSHIP,
        resource_id=membership.id,
        metadata=metadata,
    )

    log.info("membership.updated", membership_id=str(membership_id), tenant_id=str(tenant_id))
    return _member_read(membership, user)


def _one_line(value: str | None) -> str:
    """Quita saltos de línea: el nombre del tenant va en la cabecera Subject."""
    return " ".join((value or "").split()) or "—"


def build_removal_request_email(
    *,
    tenant: Tenant,
    member: User,
    membership: Membership,
    actor: User,
    actor_role: str,
    effective_date: date,
    requested_at: datetime,
) -> tuple[str, str]:
    """Asunto y cuerpo del aviso de baja al SADM."""
    subject = f"{REMOVAL_REQUEST_SUBJECT_PREFIX}{_one_line(tenant.name)}"
    body = "\n".join(
        [
            "Se solicita dar de baja a un usuario de la organización.",
            "",
            f"Fecha baja efectiva: {effective_date:%d/%m/%Y}",
            "(desde ese día el usuario no debe tener acceso)",
            "",
            "Organización",
            f"  Nombre: {_one_line(tenant.name)}",
            f"  ID tenant: {tenant.id}",
            f"  Clerk org ID: {tenant.clerk_org_id or '—'}",
            f"  Plan: {plan_ui_name(tenant.plan_code or tenant.plan)}",
            "",
            "Usuario a dar de baja",
            f"  Nombre: {_one_line(member.name)}",
            f"  Email: {member.email}",
            f"  Rol: {role_label(membership.role)}",
            f"  Clerk user ID: {member.clerk_user_id or '—'}",
            f"  ID membership: {membership.id}",
            "",
            "Solicitado por",
            f"  Nombre: {_one_line(actor.name)}",
            f"  Email: {actor.email}",
            f"  Rol: {role_label(actor_role)}",
            f"  Fecha solicitud (UTC): {requested_at:%Y-%m-%d %H:%M}",
            "",
            "La aplicación bloquea el acceso automáticamente desde la fecha de baja",
            "efectiva. Para completar la baja, elimina la membresía en Clerk ese día.",
        ]
    )
    return subject, body


def request_date_bounds() -> tuple[date, date]:
    """Rango admitido para fechas de alta/baja solicitadas al SADM (zona de la app)."""
    today = display_today()
    return today, today + timedelta(days=REQUEST_MAX_DAYS_AHEAD)


async def _send_sadm_request(
    *,
    subject: str,
    body: str,
    redis: Redis | None,
    rate_key: str,
    code_prefix: str,
    log_ctx: dict[str, str],
) -> None:
    """Envía una solicitud al SADM con anti-reenvío de 24 h.

    Comprueba configuración antes de reservar la clave Redis; si el envío
    falla, la libera para permitir reintentar.

    Raises:
        ValidationError: falta EMAIL_SADM.
        ExternalServiceError: SMTP sin configurar o fallo de envío.
        RateLimitError: misma solicitud enviada en las últimas 24 h.
    """
    settings = get_settings()
    to = settings.email_sadm.strip()
    if not to:
        raise ValidationError(
            "Superadmin notification email is not configured",
            details={"code": "email_sadm_missing"},
        )
    # send_email() omite el envío sin SMTP: aquí sería un falso "solicitud enviada".
    if not settings.smtp_host.strip():
        raise ExternalServiceError(
            "SMTP is not configured",
            details={"code": "smtp_not_configured"},
        )

    if redis is not None:
        created = await redis.set(rate_key, "1", nx=True, ex=SADM_REQUEST_TTL_SECONDS)
        if not created:
            raise RateLimitError(
                "Request already sent recently",
                details={"code": f"{code_prefix}_rate_limited"},
            )
    try:
        await send_email(to=to, subject=subject, body=body)
    except Exception as exc:
        if redis is not None:
            await redis.delete(rate_key)
        log.exception(f"membership.{code_prefix}_failed", error_type=type(exc).__name__, **log_ctx)
        raise ExternalServiceError(
            "Failed to send request email",
            details={"code": f"{code_prefix}_send_failed"},
        ) from exc


def removable_membership_ids(
    members: list[TenantMemberRead],
    *,
    actor_membership_id: UUID,
    actor_role: str,
) -> set[UUID]:
    """Miembros cuya baja puede solicitar el actor (para pintar el botón Baja).

    Excluye bajas ya solicitadas y pendientes: esas se pintan bloqueadas.
    """
    return {
        m.membership_id
        for m in members
        if not m.removal_pending
        and can_request_member_removal(
            actor_role=actor_role,
            actor_membership_id=actor_membership_id,
            target_role=m.role,
            target_membership_id=m.membership_id,
        )
    }


async def _active_membership_row(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    membership_id: UUID | None = None,
    user_id: UUID | None = None,
) -> tuple[Membership, User] | None:
    stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == tenant_id, Membership.is_active.is_(True))
    )
    if membership_id is not None:
        stmt = stmt.where(Membership.id == membership_id)
    if user_id is not None:
        stmt = stmt.where(Membership.user_id == user_id)
    row = (await db.execute(stmt)).one_or_none()
    return None if row is None else (row[0], row[1])


def _member_read(membership: Membership, user: User) -> TenantMemberRead:
    return TenantMemberRead(
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        name=user.name,
        phone=user.phone,
        role=membership.role,
        permissions=MembershipPermissions.from_json_dict(membership.permissions),
        clerk_user_id=user.clerk_user_id,
        removal_requested_at=membership.removal_requested_at,
        removal_effective_date=membership.removal_effective_date,
    )


async def _authorize_removal(
    db: AsyncSession,
    tenant_id: UUID,
    membership_id: UUID,
    actor_user_id: UUID,
) -> tuple[Membership, User, Membership, User]:
    """Carga objetivo y actor desde BD y aplica las reglas de baja.

    El rol del actor se lee de BD (no del request) para no confiar en estado
    cacheado. Devuelve (membership objetivo, usuario objetivo, membership actor, actor).
    """
    target = await _active_membership_row(db, tenant_id, membership_id=membership_id)
    if target is None:
        raise NotFoundError("Membership not found")
    actor = await _active_membership_row(db, tenant_id, user_id=actor_user_id)
    if actor is None or not can_request_member_removal(
        actor_role=actor[0].role,
        actor_membership_id=actor[0].id,
        target_role=target[0].role,
        target_membership_id=target[0].id,
    ):
        raise ForbiddenError(
            "Not allowed to request removal of this member",
            details={"code": "removal_not_allowed"},
        )
    if target[0].removal_pending:
        raise ValidationError(
            "Removal already requested and pending",
            details={"code": "removal_already_requested"},
        )
    return target[0], target[1], actor[0], actor[1]


async def get_removal_form(
    db: AsyncSession,
    tenant_id: UUID,
    membership_id: UUID,
    *,
    actor_user_id: UUID,
) -> MemberRemovalForm:
    """Datos del formulario de baja; falla si el actor no puede solicitarla."""
    await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    membership, member, actor_membership, actor = await _authorize_removal(
        db, tenant_id, membership_id, actor_user_id
    )
    min_date, max_date = request_date_bounds()
    return MemberRemovalForm(
        member=_member_read(membership, member),
        actor_name=actor.name,
        actor_email=actor.email,
        actor_role=actor_membership.role,
        min_date=min_date,
        max_date=max_date,
    )


async def request_member_removal(
    db: AsyncSession,
    tenant_id: UUID,
    membership_id: UUID,
    *,
    actor_user_id: UUID,
    effective_date: date,
    redis: Redis | None,
) -> None:
    """Envía al SADM la solicitud de baja de un miembro. No modifica la membership.

    Reglas: nadie solicita su propia baja; un co_admin no puede solicitar la
    del admin. La baja real la hace el SADM en Clerk en ``effective_date``.

    Raises:
        NotFoundError: la membership no existe o no está activa en el tenant.
        ForbiddenError: el actor no puede solicitar la baja de ese miembro.
        ValidationError: fecha fuera de rango o falta EMAIL_SADM.
        ExternalServiceError: SMTP sin configurar o fallo de envío.
        RateLimitError: ya se solicitó la baja de este miembro recientemente.
    """
    tenant = await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    membership, member, actor_membership, actor = await _authorize_removal(
        db, tenant_id, membership_id, actor_user_id
    )

    min_date, max_date = request_date_bounds()
    if not min_date <= effective_date <= max_date:
        raise ValidationError(
            "Effective removal date out of range",
            details={"code": "removal_date_invalid"},
        )

    requested_at = datetime.now(UTC)
    subject, body = build_removal_request_email(
        tenant=tenant,
        member=member,
        membership=membership,
        actor=actor,
        actor_role=actor_membership.role,
        effective_date=effective_date,
        requested_at=requested_at,
    )
    await _send_sadm_request(
        subject=subject,
        body=body,
        redis=redis,
        rate_key=f"{_REMOVAL_REQUEST_KEY_PREFIX}{membership_id}",
        code_prefix="removal_request",
        log_ctx={"membership_id": str(membership_id), "tenant_id": str(tenant_id)},
    )
    # Solo tras enviar: si el email falla no queda una baja "pendiente" fantasma.
    membership.removal_requested_at = requested_at
    membership.removal_effective_date = effective_date
    await db.flush()

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_MEMBER_REMOVAL_REQUESTED,
        resource_type=RESOURCE_MEMBERSHIP,
        resource_id=membership_id,
        metadata={
            "email": member.email,
            "actor_role": actor_membership.role,
            "effective_date": effective_date.isoformat(),
        },
    )
    log.info(
        "membership.removal_requested",
        membership_id=str(membership_id),
        tenant_id=str(tenant_id),
    )


def removal_is_due(membership: Membership, today: date | None = None) -> bool:
    """True si la baja solicitada ya debe aplicarse (desde las 00:00 de la fecha efectiva)."""
    effective = membership.removal_effective_date
    return effective is not None and effective <= (today or display_today())


async def apply_due_removal(
    db: AsyncSession,
    membership: Membership,
    *,
    source: str,
    today: date | None = None,
) -> bool:
    """Desactiva la membership si su baja ya es efectiva. Idempotente.

    Corta el acceso en la fecha comprometida aunque el SADM aún no la haya
    quitado en Clerk (RGPD: sin acceso tras la baja). Se conservan las fechas
    de la solicitud como rastro; se limpian solo si la membership se reactiva.

    Returns:
        True si la desactivó en esta llamada.
    """
    if not membership.is_active or not removal_is_due(membership, today):
        return False
    membership.is_active = False
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=membership.tenant_id,
        user_id=None,
        action=ACTION_MEMBER_REMOVAL_EXECUTED,
        resource_type=RESOURCE_MEMBERSHIP,
        resource_id=membership.id,
        metadata={
            "effective_date": str(membership.removal_effective_date),
            "source": source,
        },
    )
    log.info(
        "membership.removal_executed",
        membership_id=str(membership.id),
        tenant_id=str(membership.tenant_id),
        source=source,
    )
    return True


async def execute_due_removals(db: AsyncSession, *, today: date | None = None) -> int:
    """Aplica todas las bajas vencidas de todos los tenants (tarea programada).

    ``memberships`` tiene FORCE RLS y el worker no la salta: se recorre cada
    tenant fijando su contexto, como hace SADM.
    """
    day = today or display_today()
    tenant_ids = (await db.execute(select(Tenant.id))).scalars().all()
    executed = 0
    for tenant_id in tenant_ids:
        await set_tenant_context(db, str(tenant_id))
        due = await db.execute(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.is_active.is_(True),
                Membership.removal_effective_date <= day,
            )
        )
        for membership in due.scalars().all():
            if await apply_due_removal(db, membership, source=REMOVAL_SOURCE_SCHEDULED, today=day):
                executed += 1
    return executed


def build_creation_request_email(
    *,
    tenant: Tenant,
    payload: MemberCreationRequest,
    actor: User,
    actor_role: str,
    requested_at: datetime,
) -> tuple[str, str]:
    """Asunto y cuerpo del aviso de alta al SADM."""
    subject = f"{CREATION_REQUEST_SUBJECT_PREFIX}{_one_line(tenant.name)}"
    body = "\n".join(
        [
            "Se solicita dar de alta a un nuevo usuario en la organización.",
            "",
            f"Fecha alta: {payload.start_date:%d/%m/%Y}",
            "(desde ese día el usuario debe tener acceso)",
            "",
            "Organización",
            f"  Nombre: {_one_line(tenant.name)}",
            f"  ID tenant: {tenant.id}",
            f"  Clerk org ID: {tenant.clerk_org_id or '—'}",
            f"  Plan: {plan_ui_name(tenant.plan_code or tenant.plan)}",
            "",
            "Nuevo miembro",
            f"  Nombre: {payload.first_name}",
            f"  Apellidos: {payload.last_name}",
            f"  Alias: {payload.alias or '—'}",
            f"  Email: {payload.email}",
            f"  Rol: {role_label(payload.role)} ({APP_ROLE_TO_CLERK_ROLE[payload.role]} en Clerk)",
            "",
            "Solicitado por",
            f"  Nombre: {_one_line(actor.name)}",
            f"  Email: {actor.email}",
            f"  Rol: {role_label(actor_role)}",
            f"  Fecha solicitud (UTC): {requested_at:%Y-%m-%d %H:%M}",
            "",
            "Para completar el alta, invita al usuario en Clerk a la organización con",
            "el rol indicado en la fecha de alta.",
        ]
    )
    return subject, body


async def _require_manager_actor(
    db: AsyncSession, tenant_id: UUID, actor_user_id: UUID
) -> tuple[Membership, User]:
    """Actor activo con rol admin/co_admin, leído de BD (no del request)."""
    actor = await _active_membership_row(db, tenant_id, user_id=actor_user_id)
    if actor is None or not is_manager_role(actor[0].role):
        raise ForbiddenError(
            "Not allowed to request new members",
            details={"code": "creation_not_allowed"},
        )
    return actor


async def get_creation_form(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    actor_user_id: UUID,
) -> MemberCreationForm:
    """Datos del formulario de alta; falla si el actor no es admin/co_admin."""
    await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    actor_membership, actor = await _require_manager_actor(db, tenant_id, actor_user_id)
    min_date, max_date = request_date_bounds()
    return MemberCreationForm(
        actor_name=actor.name,
        actor_email=actor.email,
        actor_role=actor_membership.role,
        min_date=min_date,
        max_date=max_date,
    )


async def _ensure_can_add_member(db: AsyncSession, tenant_id: UUID, email: str) -> None:
    """Rechaza altas que el SADM no podría hacer: email ya miembro o plan sin plazas."""
    existing = await db.scalar(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
            User.email == email,
        )
        .limit(1)
    )
    if existing is not None:
        raise ValidationError(
            "User is already a member of this tenant",
            details={"code": "member_already_exists"},
        )

    from app.services import entitlement_service, plan_quota_service

    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    try:
        await plan_quota_service.ensure_member_capacity(db, ents, tenant_id)
    except ValidationError as exc:
        raise ValidationError(
            "Plan member limit reached",
            details={"code": "members_max_reached"},
        ) from exc


async def request_member_creation(
    db: AsyncSession,
    tenant_id: UUID,
    payload: MemberCreationRequest,
    *,
    actor_user_id: UUID,
    redis: Redis | None,
) -> None:
    """Envía al SADM la solicitud de alta de un miembro. No crea nada en Clerk ni en BD.

    Raises:
        ForbiddenError: el actor no es admin/co_admin del tenant.
        ValidationError: fecha fuera de rango, email ya miembro, plan sin
            plazas o falta EMAIL_SADM.
        ExternalServiceError: SMTP sin configurar o fallo de envío.
        RateLimitError: ya se solicitó el alta de ese email recientemente.
    """
    tenant = await _get_tenant_or_raise(db, tenant_id)
    await set_tenant_context(db, str(tenant_id))
    actor_membership, actor = await _require_manager_actor(db, tenant_id, actor_user_id)

    min_date, max_date = request_date_bounds()
    if not min_date <= payload.start_date <= max_date:
        raise ValidationError(
            "Start date out of range",
            details={"code": "creation_date_invalid"},
        )
    await _ensure_can_add_member(db, tenant_id, payload.email)

    subject, body = build_creation_request_email(
        tenant=tenant,
        payload=payload,
        actor=actor,
        actor_role=actor_membership.role,
        requested_at=datetime.now(UTC),
    )
    await _send_sadm_request(
        subject=subject,
        body=body,
        redis=redis,
        rate_key=f"{_CREATION_REQUEST_KEY_PREFIX}{tenant_id}:{payload.email}",
        code_prefix="creation_request",
        log_ctx={"tenant_id": str(tenant_id)},
    )

    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_MEMBER_CREATION_REQUESTED,
        resource_type=RESOURCE_MEMBERSHIP,
        resource_id=None,
        metadata={
            "email": payload.email,
            "role": payload.role,
            "start_date": payload.start_date.isoformat(),
            "actor_role": actor_membership.role,
        },
    )
    log.info("membership.creation_requested", tenant_id=str(tenant_id))
