"""Altas de contratos y archivo de contratos activos (bloque 5; spec planes §4.3, D027).

- **Altas por tramos de páginas:** un contrato consume 1, 2 o 3 altas según sus
  páginas (``CONTRACT_UPLOAD_PAGE_TIERS``, por defecto ``30,60``); una imagen
  cuenta como 1 página. Por encima de ``contract_max_pages`` del plan se rechaza
  al subir, sin R2 ni LLM.
- **Bolsa:** durante la ventana de carga inicial (del alta al final del primer mes
  completo) se consume ``contract_uploads_first_period``, contada en el mes del
  alta; después, ``contract_uploads_per_month``. La reserva guarda bolsa, mes y
  unidades para devolver exactamente lo mismo.
- **Archivo de activos:** ``contracts_active_max`` cuenta los contratos vigentes
  (``lifecycle`` active), no fallidos, no ocultos y no vencidos (``fecha_fin``
  vacía o de hoy en adelante). Un contrato vencido o sustituido libera su hueco.
  Lleno = la subida se rechaza; para una renovación, el usuario marca antes el
  contrato anterior como sustituido.

Reserva, devolución y pendientes los orquesta ``document_quota_service``, que
llama aquí para lo propio de los contratos.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import func, or_, select, text

from app.config import get_settings
from app.core.billing_period import (
    current_period_start,
    initial_load_window_end,
    local_date,
    renewal_date,
)
from app.core.db import set_tenant_context
from app.core.entitlement_codes import (
    LIMIT_CONTRACT_MAX_PAGES,
    LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD,
    LIMIT_CONTRACT_UPLOADS_PER_MONTH,
    LIMIT_CONTRACTS_ACTIVE_MAX,
)
from app.core.errors import ValidationError
from app.core.plan_limits import resolve_quota_cap
from app.models import Contract, ContractLifecycle, ContractStatus, Tenant
from app.services import audit_service, monthly_quota_service

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements
    from app.services.audit_service import AuditRequestContext

logger = structlog.get_logger(__name__)

CONTRACT_UPLOAD_CODES: frozenset[str] = frozenset(
    {LIMIT_CONTRACT_UPLOADS_PER_MONTH, LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD}
)

ACTION_CONTRACT_REPLACED = "contract.replaced"
ACTION_CONTRACT_REACTIVATED = "contract.reactivated"
RESOURCE_DOCUMENT = "document"

MSG_ACTIVE_FULL = (
    "Has llegado al máximo de {cap} contratos vigentes de tu plan. Si es una "
    "renovación, marca primero el contrato anterior como sustituido."
)
MSG_REACTIVATE_FULL = (
    "No se puede volver a marcar como vigente: ya tienes {cap} contratos vigentes, "
    "el máximo de tu plan. Marca antes otro como sustituido."
)
MSG_TOO_MANY_PAGES = (
    '"{filename}" no se ha subido: tu plan admite contratos de hasta {cap} páginas ({detail}).'
)
MSG_ONLY_READY_CAN_BE_REPLACED = "Solo se puede marcar como sustituido un contrato ya procesado."

# Estados que ocupan hueco de activo: todo menos ``failed`` (un pendiente o en
# proceso acabará vigente; contarlo evita pasarse con subidas simultáneas).
_ACTIVE_STATUSES = (
    ContractStatus.pending,
    ContractStatus.processing,
    ContractStatus.quota_pending,
    ContractStatus.ready,
    ContractStatus.reviewed,
)


# ── Páginas y unidades ───────────────────────────────────────────────────────


def upload_units(pages: int, tiers: Sequence[int] | None = None) -> int:
    """Altas que consume un contrato: 1 + umbrales que superan sus páginas."""
    thresholds = tiers if tiers is not None else get_settings().contract_upload_page_tiers
    return 1 + sum(1 for threshold in thresholds if pages > threshold)


def max_pages(ents: Entitlements) -> int:
    """Páginas máximas de un contrato: el plan, nunca por encima del techo del worker."""
    hard_limit = get_settings().document_override_max_pdf_pages
    cap = resolve_quota_cap(ents, LIMIT_CONTRACT_MAX_PAGES)
    return hard_limit if cap is None else min(cap, hard_limit)


def too_many_pages_message(*, filename: str, detail: str | None, cap: int) -> str:
    return MSG_TOO_MANY_PAGES.format(
        filename=filename, cap=cap, detail=detail or "demasiadas páginas"
    )


# ── Bolsa de altas ───────────────────────────────────────────────────────────


async def _initial_load_end(db: AsyncSession, tenant_id: UUID) -> date | None:
    """Último día de la carga inicial si hoy está dentro; None si ya terminó."""
    created = await db.scalar(select(Tenant.created_at).where(Tenant.id == tenant_id))
    if created is None:
        return None
    window_end = initial_load_window_end(created)
    return window_end if local_date() <= window_end else None


async def in_initial_load_window(db: AsyncSession, tenant_id: UUID) -> bool:
    return await _initial_load_end(db, tenant_id) is not None


async def renewal_day(db: AsyncSession, tenant_id: UUID) -> date:
    """Día en que hay altas nuevas: tras la carga inicial o el 1 del mes que viene."""
    window_end = await _initial_load_end(db, tenant_id)
    if window_end is not None:
        return window_end + timedelta(days=1)
    return renewal_date()


async def current_bag(db: AsyncSession, tenant_id: UUID) -> tuple[str, date]:
    """(código, periodo) del que se consumen altas hoy."""
    if await in_initial_load_window(db, tenant_id):
        period = await monthly_quota_service.initial_load_period(db, tenant_id)
        return LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD, period
    return LIMIT_CONTRACT_UPLOADS_PER_MONTH, current_period_start()


async def try_reserve(db: AsyncSession, ents: Entitlements, contract: Contract) -> str | None:
    """Reserva ``upload_units`` altas. Devuelve el código consumido o None si no caben."""
    code, period = await current_bag(db, contract.tenant_id)
    units = max(contract.upload_units, 1)
    if not await monthly_quota_service.try_consume(
        db, ents, contract.tenant_id, code, delta=units, period=period
    ):
        return None
    contract.quota_code = code
    contract.quota_period = period
    await db.flush()
    return code


async def release(db: AsyncSession, contract: Contract) -> date | None:
    """Devuelve las altas reservadas a su bolsa y mes. Devuelve el mes o None si no había."""
    period = contract.quota_period
    code = contract.quota_code
    if period is None or code is None:
        contract.quota_period = None
        contract.quota_code = None
        return None
    await monthly_quota_service.release(
        db, contract.tenant_id, code, period=period, delta=max(contract.upload_units, 1)
    )
    contract.quota_period = None
    contract.quota_code = None
    await db.flush()
    return period


def is_finished(contract: Contract) -> bool:
    return contract.status in (ContractStatus.ready, ContractStatus.reviewed)


# ── Archivo de activos ───────────────────────────────────────────────────────


def active_cap(ents: Entitlements) -> int | None:
    return resolve_quota_cap(ents, LIMIT_CONTRACTS_ACTIVE_MAX)


def _counts_as_active(contract: Contract) -> bool:
    if contract.lifecycle != ContractLifecycle.active or contract.dismissed_at is not None:
        return False
    if contract.status not in _ACTIVE_STATUSES:
        return False
    return contract.fecha_fin is None or contract.fecha_fin >= local_date()


async def active_count(db: AsyncSession, tenant_id: UUID, *, exclude_id: UUID | None = None) -> int:
    """Contratos que ocupan hueco en el archivo de activos."""
    stmt = (
        select(func.count())
        .select_from(Contract)
        .where(
            Contract.tenant_id == tenant_id,
            Contract.lifecycle == ContractLifecycle.active,
            Contract.status.in_(_ACTIVE_STATUSES),
            Contract.dismissed_at.is_(None),
            or_(Contract.fecha_fin.is_(None), Contract.fecha_fin >= local_date()),
        )
    )
    if exclude_id is not None:
        stmt = stmt.where(Contract.id != exclude_id)
    return int(await db.scalar(stmt) or 0)


async def _lock_active_archive(db: AsyncSession, tenant_id: UUID) -> None:
    # Serializa altas, reintentos y reactivaciones del tenant: dos subidas
    # simultáneas no pueden ocupar el último hueco a la vez.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"contracts_active:{tenant_id}"},
    )


async def ensure_active_slot(
    db: AsyncSession,
    ents: Entitlements,
    tenant_id: UUID,
    *,
    exclude_id: UUID | None = None,
    message: str = MSG_ACTIVE_FULL,
) -> None:
    """Lanza ``ValidationError`` si el archivo de activos está lleno."""
    cap = active_cap(ents)
    if cap is None:
        return
    await _lock_active_archive(db, tenant_id)
    used = await active_count(db, tenant_id, exclude_id=exclude_id)
    if used >= cap:
        logger.info("contracts_quota.active_full", tenant_id=str(tenant_id), used=used, cap=cap)
        raise ValidationError(message.format(cap=cap))


# ── Sustitución (renovaciones) ───────────────────────────────────────────────


async def _load(db: AsyncSession, tenant_id: UUID, contract_id: UUID) -> Contract:
    from app.services import contract_service

    await set_tenant_context(db, str(tenant_id))
    return await contract_service.get_contract(db, tenant_id, contract_id)


async def mark_replaced(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    contract_id: UUID,
    user_id: UUID | None,
    request_ctx: AuditRequestContext | None = None,
) -> Contract:
    """«Marcar como sustituido»: libera su hueco; sigue en el histórico. Auditado."""
    contract = await _load(db, tenant_id, contract_id)
    if contract.lifecycle == ContractLifecycle.replaced:
        return contract
    if not is_finished(contract):
        raise ValidationError(MSG_ONLY_READY_CAN_BE_REPLACED)
    contract.lifecycle = ContractLifecycle.replaced
    contract.replaced_at = datetime.now(UTC)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CONTRACT_REPLACED,
        resource_type=RESOURCE_DOCUMENT,
        resource_id=contract.id,
        metadata={"document_kind": "contract"},
        request_ctx=request_ctx,
    )
    logger.info("contract.replaced", tenant_id=str(tenant_id), contract_id=str(contract.id))
    return contract


async def reactivate(
    db: AsyncSession,
    ents: Entitlements,
    *,
    tenant_id: UUID,
    contract_id: UUID,
    user_id: UUID | None,
    request_ctx: AuditRequestContext | None = None,
) -> Contract:
    """«Volver a vigente»: comprueba hueco si va a ocuparlo. Auditado."""
    contract = await _load(db, tenant_id, contract_id)
    if contract.lifecycle == ContractLifecycle.active:
        return contract
    contract.lifecycle = ContractLifecycle.active
    if _counts_as_active(contract):
        try:
            await ensure_active_slot(
                db, ents, tenant_id, exclude_id=contract.id, message=MSG_REACTIVATE_FULL
            )
        except ValidationError:
            contract.lifecycle = ContractLifecycle.replaced
            raise
    contract.replaced_at = None
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_CONTRACT_REACTIVATED,
        resource_type=RESOURCE_DOCUMENT,
        resource_id=contract.id,
        metadata={"document_kind": "contract"},
        request_ctx=request_ctx,
    )
    logger.info("contract.reactivated", tenant_id=str(tenant_id), contract_id=str(contract.id))
    return contract
