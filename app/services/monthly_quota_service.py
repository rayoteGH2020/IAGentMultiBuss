"""Cupos mensuales persistidos en PostgreSQL (D027, spec planes §4.1 y §4.2).

Un contador por tenant, periodo (mes natural en hora de España) y código de
límite en ``quota_usage``. El consumo es atómico por bolsa: un bloqueo
transaccional de Postgres serializa los consumos de la misma bolsa, y el
contador se incrementa en la transacción del caller (si hace rollback, el
consumo desaparece con ella).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.billing_period import current_period_start
from app.core.db import set_tenant_context
from app.core.entitlement_codes import MONTHLY_QUOTA_CODES, monthly_quota_bag
from app.core.errors import ValidationError
from app.core.plan_limits import resolve_quota_cap
from app.models.quota_usage import QuotaUsage
from app.services import audit_service

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

ACTION_QUOTA_EXTRA_ADDED = "sadm.quota_extra_added"
RESOURCE_TENANT = "tenant"
# Techo de una ampliación puntual: evita errores de tecleo (un cero de más).
MAX_EXTRA_PER_REQUEST = 10_000


@dataclass(frozen=True)
class MonthlyQuota:
    """Estado de un cupo mensual en un periodo."""

    code: str
    used: int
    extra: int
    plan_cap: int | None

    @property
    def cap(self) -> int | None:
        """Tope efectivo del periodo: plan (con override) + ampliación. ``None`` = sin tope."""
        if self.plan_cap is None:
            return None
        return self.plan_cap + self.extra


def _require_monthly_code(code: str) -> None:
    if code not in MONTHLY_QUOTA_CODES:
        raise ValidationError(f"'{code}' no es un cupo mensual.")


def _plan_cap(ents: Entitlements, code: str) -> int | None:
    return resolve_quota_cap(ents, code, platform_cap=None)


async def _lock_bag(db: AsyncSession, tenant_id: UUID, period: date, bag: tuple[str, ...]) -> None:
    key = f"quota:{tenant_id}:{period.isoformat()}:{','.join(bag)}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


async def _rows(
    db: AsyncSession, tenant_id: UUID, period: date, codes: tuple[str, ...] | frozenset[str]
) -> dict[str, QuotaUsage]:
    # populate_existing: los upserts van por SQL directo; sin esto el identity map
    # de la sesión devolvería valores de una lectura anterior.
    result = await db.execute(
        select(QuotaUsage)
        .where(
            QuotaUsage.tenant_id == tenant_id,
            QuotaUsage.period == period,
            QuotaUsage.code.in_(codes),
        )
        .execution_options(populate_existing=True)
    )
    return {row.code: row for row in result.scalars().all()}


async def _upsert_increment(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    period: date,
    code: str,
    used: int = 0,
    extra: int = 0,
) -> None:
    stmt = insert(QuotaUsage).values(
        tenant_id=tenant_id, period=period, code=code, used=used, extra=extra
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "period", "code"],
            set_={
                "used": QuotaUsage.used + stmt.excluded.used,
                "extra": QuotaUsage.extra + stmt.excluded.extra,
                "updated_at": text("now()"),
            },
        )
    )


async def try_consume(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
    code: str,
    *,
    delta: int = 1,
    period: date | None = None,
) -> bool:
    """Consume ``delta`` del cupo si cabe en el tope de su bolsa; si no, no toca nada.

    Returns:
        ``True`` si se consumió; ``False`` si no cabe (el caller decide: rechazar
        o dejar el trabajo en ``quota_pending``).
    """
    _require_monthly_code(code)
    if delta <= 0:
        raise ValidationError("El consumo debe ser positivo.")
    current = period or current_period_start()
    bag = monthly_quota_bag(code)
    await _lock_bag(db, tenant_id, current, bag)
    rows = await _rows(db, tenant_id, current, bag)

    caps = [_plan_cap(ents, member) for member in bag]
    if all(cap is not None for cap in caps):
        total_cap = sum(cap for cap in caps if cap is not None) + sum(
            row.extra for row in rows.values()
        )
        used = sum(row.used for row in rows.values())
        if used + delta > total_cap:
            logger.info(
                "quota.monthly_exceeded",
                tenant_id=str(tenant_id),
                code=code,
                period=current.isoformat(),
                used=used,
                cap=total_cap,
            )
            return False

    await _upsert_increment(db, tenant_id=tenant_id, period=current, code=code, used=delta)
    return True


async def release(
    db: AsyncSession,
    tenant_id: UUID,
    code: str,
    *,
    period: date,
    delta: int = 1,
) -> None:
    """Devuelve ``delta`` al cupo del periodo en que se consumió (nunca por debajo de 0)."""
    _require_monthly_code(code)
    if delta <= 0:
        raise ValidationError("La devolución debe ser positiva.")
    await db.execute(
        update(QuotaUsage)
        .where(
            QuotaUsage.tenant_id == tenant_id,
            QuotaUsage.period == period,
            QuotaUsage.code == code,
        )
        .values(used=func.greatest(QuotaUsage.used - delta, 0), updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


async def add_extra(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    code: str,
    amount: int,
    actor_user_id: UUID | None,
    reason: str | None = None,
) -> int:
    """Amplía el cupo ``code`` solo para el periodo en curso (SADM). Devuelve el extra total."""
    _require_monthly_code(code)
    if amount <= 0 or amount > MAX_EXTRA_PER_REQUEST:
        raise ValidationError(f"La ampliación debe estar entre 1 y {MAX_EXTRA_PER_REQUEST}.")
    period = current_period_start()
    await set_tenant_context(db, str(tenant_id))
    await _upsert_increment(db, tenant_id=tenant_id, period=period, code=code, extra=amount)
    rows = await _rows(db, tenant_id, period, (code,))
    total_extra = rows[code].extra
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=actor_user_id,
        action=ACTION_QUOTA_EXTRA_ADDED,
        resource_type=RESOURCE_TENANT,
        resource_id=tenant_id,
        metadata={
            "code": code,
            "period": period.isoformat(),
            "amount": amount,
            "extra_total": total_extra,
            "reason": reason,
        },
    )
    return total_extra


async def get_usage(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
    *,
    period: date | None = None,
) -> dict[str, MonthlyQuota]:
    """Consumo, ampliación y tope de cada cupo mensual en el periodo (en curso por defecto)."""
    current = period or current_period_start()
    # Desde SADM la sesión no tiene tenant fijado: sin esto, RLS devolvería 0 filas.
    await set_tenant_context(db, str(tenant_id))
    rows = await _rows(db, tenant_id, current, MONTHLY_QUOTA_CODES)
    return {
        code: MonthlyQuota(
            code=code,
            used=rows[code].used if code in rows else 0,
            extra=rows[code].extra if code in rows else 0,
            plan_cap=_plan_cap(ents, code),
        )
        for code in sorted(MONTHLY_QUOTA_CODES)
    }
