"""Avisos del presupuesto mensual de IA (`llm_budget_eur_month`) y corte del chat.

El presupuesto es único para toda la IA del tenant (extracción, conocimiento y
chat). Para que el chat no deje al tenant sin extracción de documentos:

- Al cruzar ``LLM_BUDGET_WARN_RATIO`` (80 %) con cualquier gasto de IA, email al
  admin del tenant, una vez por mes.
- Desde ``CHAT_BUDGET_CUTOFF_RATIO`` (90 %) el chat de la app no llama al LLM:
  responde con un mensaje fijo con el teléfono y el email del admin del tenant
  (tabla ``users``, D020) y se notifica al admin como máximo una vez cada 24 h y
  3 veces por mes. Al cruzar ese umbral, además, email al SADM (``EMAIL_SADM``)
  con nombre y apellido (Clerk), email y teléfono (``users``) del admin, una vez
  por mes.
- Al 100 % bloquea ``plan_quota_service.ensure_llm_budget`` (resto de la IA) y el
  panel muestra a todos los usuarios el aviso ``LLM_BUDGET_EXHAUSTED_NOTICE``.

Los emails se envían desde un job ARQ para no añadir la latencia SMTP al chat
ni a las extracciones. Los avisos nunca interrumpen el flujo que los dispara.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.core import clerk_client
from app.core.activity.context import job_parent_kwargs
from app.core.cache import get_redis
from app.core.email import send_email
from app.core.entitlement_codes import LIMIT_LLM_BUDGET_EUR_MONTH
from app.core.permissions import ORG_ADMIN_ROLE
from app.core.plan_limits import resolve_budget_cap
from app.models import Membership, Tenant, User
from app.services import entitlement_service, usage_meter_service

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

AlertKind = Literal["budget_warning", "chat_cutoff", "sadm_cutoff"]
AdminAlertKind = Literal["budget_warning", "chat_cutoff"]

SADM_CUTOFF_SUBJECT = "Cuota de uso de IA de uno de los tenant al {pct}%"
SADM_CUTOFF_BODY = (
    "El tenant {org}-{tenant_id}, ha llegado al {pct}% de su cupo de uso de IA para este "
    "mes. Contacta con su admin para gestionarlo.\n"
    "Admin del tenant: {first_name}, {last_name}, {email} y {phone}."
)
_NOT_AVAILABLE = "no disponible"
_EXHAUSTED_CACHE_TTL_SECONDS = 60

CHAT_CUTOFF_BASE_MESSAGE = (
    "En estos momentos no puedo responderte, ponte en contacto con nosotros y te ayudaremos"
)

_ALERT_SUBJECTS: dict[AdminAlertKind, str] = {
    "budget_warning": "Aviso: has usado el {pct} % del presupuesto de IA de este mes",
    "chat_cutoff": "El asistente de chat está pausado por el presupuesto de IA",
}
_ALERT_BODIES: dict[AdminAlertKind, str] = {
    "budget_warning": (
        "Tu organización {org} ha consumido el {pct} % del presupuesto mensual de IA "
        "de su plan ({spent} € de {budget} €).\n\n"
        "Al llegar al {cutoff} % el chat dejará de responder para reservar el resto a "
        "la extracción de documentos, y al 100 % se detendrá toda la IA hasta el mes "
        "siguiente. Si necesitas ampliarlo, contacta con soporte."
    ),
    "chat_cutoff": (
        "El chat de tu organización {org} ha dejado de responder porque se ha consumido "
        "el {pct} % del presupuesto mensual de IA ({spent} € de {budget} €). El resto se "
        "reserva para la extracción de documentos.\n\n"
        "El chat volverá a funcionar el mes que viene o si se amplía el presupuesto. "
        "Si lo necesitas antes, contacta con soporte."
    ),
}

# Más que un mes: la clave del periodo caduca sola tras el cambio de mes.
_PERIOD_KEY_TTL_SECONDS = 40 * 24 * 3600


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    """Gasto de IA del periodo frente al presupuesto (``budget`` None = ilimitado)."""

    spent: Decimal
    budget: Decimal | None

    @property
    def ratio(self) -> float:
        if self.budget is None:
            return 0.0
        if self.budget <= 0:
            return 1.0
        return float(self.spent / self.budget)


async def get_budget_usage(db: AsyncSession, tenant_id: UUID) -> BudgetUsage:
    """Gasto del mes en curso y presupuesto efectivo del plan del tenant."""
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    budget = resolve_budget_cap(ents, LIMIT_LLM_BUDGET_EUR_MONTH, platform_cap_eur=None)
    spent = await usage_meter_service.get_llm_cost_eur(db, tenant_id=tenant_id)
    return BudgetUsage(spent=spent, budget=budget)


def _period_key(prefix: str, tenant_id: UUID) -> str:
    period = usage_meter_service.current_billing_period().isoformat()
    return f"llm_budget:{prefix}:{tenant_id}:{period}"


async def _enqueue_alert(tenant_id: UUID, kind: AlertKind) -> None:
    from app.jobs.queue import get_arq_pool

    pool = await get_arq_pool()
    await pool.enqueue_job("send_llm_budget_alert", str(tenant_id), kind, **job_parent_kwargs())


def _monthly_thresholds() -> tuple[tuple[str, float, AlertKind], ...]:
    """(clave Redis, umbral, aviso) de los emails que se envían una vez al mes."""
    settings = get_settings()
    return (
        ("warned", settings.llm_budget_warn_ratio, "budget_warning"),
        ("sadm_notified", settings.chat_budget_cutoff_ratio, "sadm_cutoff"),
    )


async def maybe_warn_budget(db: AsyncSession, tenant_id: UUID) -> None:
    """Tras sumar gasto: avisos que se envían la primera vez del mes que se cruza su umbral.

    80 % → email al admin del tenant; 90 % → email al SADM con los datos del admin.
    """
    try:
        redis = get_redis()
        pending = [
            (_period_key(prefix, tenant_id), ratio, kind)
            for prefix, ratio, kind in _monthly_thresholds()
        ]
        pending = [item for item in pending if not await redis.exists(item[0])]
        if not pending:
            return
        usage = await get_budget_usage(db, tenant_id)
        for key, ratio, kind in pending:
            if usage.ratio < ratio:
                continue
            if not await redis.set(key, "1", nx=True, ex=_PERIOD_KEY_TTL_SECONDS):
                continue
            await _enqueue_alert(tenant_id, kind)
            logger.info("llm_budget.alert_enqueued", tenant_id=str(tenant_id), kind=kind)
    except Exception as exc:
        logger.warning(
            "llm_budget.warning_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__
        )


async def chat_cutoff_reached(db: AsyncSession, tenant_id: UUID) -> bool:
    """True si el gasto del mes alcanza el umbral de corte del chat (90 %)."""
    usage = await get_budget_usage(db, tenant_id)
    return usage.ratio >= get_settings().chat_budget_cutoff_ratio


async def notify_chat_cutoff(tenant_id: UUID) -> None:
    """Notifica al admin el corte del chat: 1 cada 24 h, máximo N por mes."""
    settings = get_settings()
    try:
        redis = get_redis()
        daily_key = _period_key("chat_cutoff_daily", tenant_id)
        count_key = _period_key("chat_cutoff_count", tenant_id)
        count = int(await redis.get(count_key) or 0)
        if count >= settings.chat_cutoff_notify_max_per_month:
            return
        if not await redis.set(
            daily_key, "1", nx=True, ex=settings.chat_cutoff_notify_interval_seconds
        ):
            return
        await redis.incr(count_key)
        await redis.expire(count_key, _PERIOD_KEY_TTL_SECONDS)
        await _enqueue_alert(tenant_id, "chat_cutoff")
        logger.info(
            "llm_budget.chat_cutoff_notify_enqueued", tenant_id=str(tenant_id), sent=count + 1
        )
    except Exception as exc:
        logger.warning(
            "llm_budget.chat_cutoff_notify_failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )


def build_chat_cutoff_message(phone: str | None, email: str | None) -> str:
    """Mensaje fijo del chat: ``(teléfono - email)`` con lo que haya disponible."""
    contact = " - ".join(part for part in (phone, email) if part)
    return f"{CHAT_CUTOFF_BASE_MESSAGE} ({contact})" if contact else f"{CHAT_CUTOFF_BASE_MESSAGE}."


async def chat_cutoff_message(db: AsyncSession, tenant_id: UUID) -> str:
    """Mensaje fijo del chat con el contacto del negocio (sin fallar si falta).

    Teléfono y email son los del admin del tenant en ``users`` (D020): el
    teléfono lo mantiene el propio admin desde la ficha de miembro.
    """
    admin = await tenant_admin(db, tenant_id)
    phone = admin.phone if admin is not None else None
    email = admin.email if admin is not None else None
    if phone is None or email is None:
        logger.warning(
            "llm_budget.contact_incomplete",
            tenant_id=str(tenant_id),
            has_phone=phone is not None,
            has_email=email is not None,
        )
    return build_chat_cutoff_message(phone, email)


async def tenant_admin(db: AsyncSession, tenant_id: UUID) -> User | None:
    """Miembro activo con rol admin (uno por tenant)."""
    stmt = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.tenant_id == tenant_id,
            Membership.role == ORG_ADMIN_ROLE,
            Membership.is_active.is_(True),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _format_eur(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",")


async def _admin_contact(admin: User) -> dict[str, str]:
    """Datos del admin para el SADM: nombre y apellido de Clerk, email y teléfono de ``users``."""
    first_name, _, last_name = (admin.name or "").partition(" ")
    if admin.clerk_user_id:
        try:
            clerk_user = await clerk_client.get_user(admin.clerk_user_id)
            first_name = str(clerk_user.get("first_name") or first_name)
            last_name = str(clerk_user.get("last_name") or last_name)
        except Exception as exc:
            logger.warning("llm_budget.admin_lookup_failed", error_type=type(exc).__name__)
    return {
        "first_name": first_name or _NOT_AVAILABLE,
        "last_name": last_name or _NOT_AVAILABLE,
        "email": admin.email,
        "phone": admin.phone or _NOT_AVAILABLE,
    }


async def send_sadm_alert(db: AsyncSession, tenant_id: UUID) -> bool:
    """Email al SADM: el tenant ha llegado al umbral de corte, con los datos de su admin."""
    to = get_settings().email_sadm.strip()
    if not to:
        logger.warning("llm_budget.sadm_email_missing", tenant_id=str(tenant_id))
        return False
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    admin = await tenant_admin(db, tenant_id)
    contact = (
        await _admin_contact(admin)
        if admin is not None
        else dict.fromkeys(("first_name", "last_name", "email", "phone"), _NOT_AVAILABLE)
    )
    pct = int(get_settings().chat_budget_cutoff_ratio * 100)
    await send_email(
        to=to,
        subject=SADM_CUTOFF_SUBJECT.format(pct=pct),
        body=SADM_CUTOFF_BODY.format(org=tenant.name, tenant_id=tenant.id, pct=pct, **contact),
    )
    logger.info("llm_budget.alert_sent", tenant_id=str(tenant_id), kind="sadm_cutoff")
    return True


async def is_budget_exhausted(db: AsyncSession, tenant_id: UUID, ents: Entitlements) -> bool:
    """True si el gasto de IA del mes alcanza el presupuesto (banner del 100 %).

    Se consulta en cada petición del panel: caché de 60 s en Redis por tenant.
    """
    budget = resolve_budget_cap(ents, LIMIT_LLM_BUDGET_EUR_MONTH, platform_cap_eur=None)
    if budget is None:
        return False
    redis = get_redis()
    cache_key = f"llm_budget:exhausted:{tenant_id}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return (cached.decode() if isinstance(cached, bytes) else str(cached)) == "1"
    spent = await usage_meter_service.get_llm_cost_eur(db, tenant_id=tenant_id)
    exhausted = budget <= 0 or spent >= budget
    await redis.set(cache_key, "1" if exhausted else "0", ex=_EXHAUSTED_CACHE_TTL_SECONDS)
    return exhausted


async def send_admin_alert(db: AsyncSession, tenant_id: UUID, kind: AdminAlertKind) -> bool:
    """Envía el email de aviso al admin del tenant. False si no hay destinatario."""
    admin = await tenant_admin(db, tenant_id)
    to = admin.email if admin is not None else None
    if not to:
        logger.warning("llm_budget.alert_no_admin", tenant_id=str(tenant_id), kind=kind)
        return False
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    usage = await get_budget_usage(db, tenant_id)
    settings = get_settings()
    values = {
        "org": tenant.name,
        "pct": int(usage.ratio * 100),
        "spent": _format_eur(usage.spent),
        "budget": _format_eur(usage.budget) if usage.budget is not None else "-",
        "cutoff": int(settings.chat_budget_cutoff_ratio * 100),
    }
    await send_email(
        to=to,
        subject=_ALERT_SUBJECTS[kind].format(**values),
        body=_ALERT_BODIES[kind].format(**values),
    )
    logger.info("llm_budget.alert_sent", tenant_id=str(tenant_id), kind=kind)
    return True
