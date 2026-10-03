"""Consumo de los cupos mensuales para la interfaz (bloque 7; spec planes §7, D027).

Una sola lectura de los contadores reales (``quota_usage``, archivo de contratos,
``usage_meter``) para tres sitios:

- **«Mi cuenta»:** «X de Y» y barra de cada cupo, con la fecha de renovación.
- **Avisos en la página donde se consume:** ``/documents`` (facturas y tickets y
  altas de contratos al 80 % y al 100 %; archivo de contratos vigentes lleno) y
  ``/chat`` (solo al 100 %: el chat es un tope técnico, sin aviso al 80 %, §4.4).
- **SADM:** marca «≥ 80 %» / «100 %» por tenant en la lista de planes.

El presupuesto de IA se muestra solo en porcentaje: los euros dejarían ver el
coste interno (decisión 2026-10-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

from app.core.billing_period import renewal_date, spanish_day_label
from app.core.db import set_tenant_context
from app.core.entitlement_codes import (
    LIMIT_CHAT_QUESTIONS_PER_MONTH,
    LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD,
    LIMIT_DOCUMENT_RETRIES_PER_MONTH,
    LIMIT_INVOICES_PER_MONTH,
    LIMIT_LLM_BUDGET_EUR_MONTH,
    LIMIT_TICKETS_PER_MONTH,
)
from app.core.plan_limits import resolve_budget_cap
from app.services import (
    contract_quota_service,
    entitlement_service,
    monthly_quota_service,
    usage_meter_service,
)

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

QuotaKey = Literal["documents", "retries", "chat", "contract_uploads", "contracts_active"]
AlertLevel = Literal["warning", "exhausted"]

WARN_PERCENT = 80


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    """Consumo de un cupo. ``cap`` None = sin límite."""

    key: QuotaKey
    label: str
    used: int
    cap: int | None
    renewal: date | None = None
    detail: str | None = None

    @property
    def included(self) -> bool:
        """Tope 0 = prestación no incluida en el plan: ni se muestra ni avisa."""
        return self.cap is None or self.cap > 0

    @property
    def percent(self) -> int | None:
        if self.cap is None or self.cap <= 0:
            return None
        return min(100, round(self.used * 100 / self.cap))

    @property
    def level(self) -> AlertLevel | None:
        if self.cap is None or self.cap <= 0:
            return None
        if self.used >= self.cap:
            return "exhausted"
        if self.used * 100 >= self.cap * WARN_PERCENT:
            return "warning"
        return None

    @property
    def renewal_label(self) -> str | None:
        return spanish_day_label(self.renewal) if self.renewal is not None else None


@dataclass(frozen=True, slots=True)
class QuotaAlert:
    """Aviso en la página donde se consume el cupo."""

    level: AlertLevel
    message: str


async def monthly_statuses(
    db: AsyncSession, ents: Entitlements, tenant_id: UUID
) -> list[QuotaStatus]:
    """Cupos para «Mi cuenta», en orden de presentación (sin los no incluidos)."""
    usage = await monthly_quota_service.get_usage(db, ents, tenant_id)
    renewal = renewal_date()
    invoices = usage[LIMIT_INVOICES_PER_MONTH]
    tickets = usage[LIMIT_TICKETS_PER_MONTH]
    docs_used, docs_cap = await monthly_quota_service.bag_usage(
        db, ents, tenant_id, LIMIT_INVOICES_PER_MONTH
    )
    retries = usage[LIMIT_DOCUMENT_RETRIES_PER_MONTH]
    chat = usage[LIMIT_CHAT_QUESTIONS_PER_MONTH]
    statuses = [
        QuotaStatus(
            key="documents",
            label="Facturas y tickets",
            used=docs_used,
            cap=docs_cap,
            renewal=renewal,
            detail=f"facturas {invoices.used} · tickets {tickets.used}",
        ),
        QuotaStatus(
            key="retries",
            label="Reintentos de procesado",
            used=retries.used,
            cap=retries.cap,
            renewal=renewal,
        ),
        QuotaStatus(
            key="chat",
            label="Preguntas al chat",
            used=chat.used,
            cap=chat.cap,
            renewal=renewal,
        ),
        await _contract_uploads_status(db, ents, tenant_id),
        await _contracts_active_status(db, ents, tenant_id),
    ]
    return [status for status in statuses if status.included]


async def _contract_uploads_status(
    db: AsyncSession, ents: Entitlements, tenant_id: UUID
) -> QuotaStatus:
    code, _period = await contract_quota_service.current_bag(db, tenant_id)
    used, cap = await monthly_quota_service.bag_usage(db, ents, tenant_id, code)
    renewal = await contract_quota_service.renewal_day(db, tenant_id)
    initial = code == LIMIT_CONTRACT_UPLOADS_FIRST_PERIOD
    return QuotaStatus(
        key="contract_uploads",
        label="Altas de contratos (carga inicial)" if initial else "Altas de contratos",
        used=used,
        cap=cap,
        renewal=renewal,
    )


async def _contracts_active_status(
    db: AsyncSession, ents: Entitlements, tenant_id: UUID
) -> QuotaStatus:
    return QuotaStatus(
        key="contracts_active",
        label="Contratos vigentes",
        used=await contract_quota_service.active_count(db, tenant_id),
        cap=contract_quota_service.active_cap(ents),
    )


async def ai_usage_percent(db: AsyncSession, ents: Entitlements, tenant_id: UUID) -> int | None:
    """Uso del presupuesto de IA del mes en %; None = sin tope."""
    budget = resolve_budget_cap(ents, LIMIT_LLM_BUDGET_EUR_MONTH, platform_cap_eur=None)
    if budget is None:
        return None
    if budget <= 0:
        return 100
    spent = await usage_meter_service.get_llm_cost_eur(db, tenant_id=tenant_id)
    return min(100, int(spent * 100 / budget))


# ── Avisos por página ────────────────────────────────────────────────────────


def _alert(status: QuotaStatus) -> QuotaAlert | None:
    level = status.level
    if level is None:
        return None
    when = status.renewal_label
    if status.key == "documents":
        if level == "warning":
            return QuotaAlert(
                level,
                f"Has usado {status.used} de {status.cap} facturas y tickets este mes. Al "
                f"llegar al límite, los nuevos quedarán pendientes hasta el {when}.",
            )
        return QuotaAlert(
            level,
            f"Has agotado las {status.cap} facturas y tickets de este mes: los nuevos quedan "
            f"pendientes hasta el {when} o hasta que se amplíe el cupo.",
        )
    if status.key == "contract_uploads":
        if level == "warning":
            return QuotaAlert(
                level,
                f"Has usado {status.used} de {status.cap} altas de contratos. Al llegar al "
                f"límite, los nuevos contratos quedarán pendientes hasta el {when}.",
            )
        return QuotaAlert(
            level,
            f"Has agotado las {status.cap} altas de contratos: los nuevos quedan pendientes "
            f"hasta el {when} o hasta que se amplíe el cupo.",
        )
    if status.key == "contracts_active" and level == "exhausted":
        return QuotaAlert(
            level,
            f"Tienes {status.used} contratos vigentes, el máximo de tu plan: para subir una "
            "renovación, marca antes el contrato anterior como sustituido.",
        )
    if status.key == "chat" and level == "exhausted":
        return QuotaAlert(
            level,
            f"Has usado las {status.cap} preguntas de este mes. Se renuevan el {when}; si las "
            "necesitas antes, contacta con el administrador de tu organización.",
        )
    return None


async def documents_alerts(db: AsyncSession, tenant_id: UUID) -> list[QuotaAlert]:
    """Avisos del panel de documentos (facturas y tickets, altas y archivo de contratos).

    Nunca hace caer la página: un fallo se registra y no se muestra aviso.
    """
    try:
        async with db.begin_nested():
            return await _documents_alerts(db, tenant_id)
    except Exception as exc:
        logger.warning(
            "quota_status.alerts_failed",
            tenant_id=str(tenant_id),
            page="documents",
            error_type=type(exc).__name__,
        )
        return []


async def _documents_alerts(db: AsyncSession, tenant_id: UUID) -> list[QuotaAlert]:
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    docs_used, docs_cap = await monthly_quota_service.bag_usage(
        db, ents, tenant_id, LIMIT_INVOICES_PER_MONTH
    )
    statuses = [
        QuotaStatus(
            key="documents",
            label="Facturas y tickets",
            used=docs_used,
            cap=docs_cap,
            renewal=renewal_date(),
        ),
        await _contract_uploads_status(db, ents, tenant_id),
        await _contracts_active_status(db, ents, tenant_id),
    ]
    return [alert for status in statuses if (alert := _alert(status)) is not None]


async def chat_alert(db: AsyncSession, ents: Entitlements, tenant_id: UUID) -> QuotaAlert | None:
    """Aviso del chat: solo con las preguntas del mes agotadas. Nunca hace caer la página."""
    try:
        async with db.begin_nested():
            return await _chat_alert(db, ents, tenant_id)
    except Exception as exc:
        logger.warning(
            "quota_status.alerts_failed",
            tenant_id=str(tenant_id),
            page="chat",
            error_type=type(exc).__name__,
        )
        return None


async def _chat_alert(db: AsyncSession, ents: Entitlements, tenant_id: UUID) -> QuotaAlert | None:
    usage = await monthly_quota_service.get_usage(db, ents, tenant_id)
    chat = usage[LIMIT_CHAT_QUESTIONS_PER_MONTH]
    return _alert(
        QuotaStatus(
            key="chat",
            label="Preguntas al chat",
            used=chat.used,
            cap=chat.cap,
            renewal=renewal_date(),
        )
    )


# ── SADM ─────────────────────────────────────────────────────────────────────


async def tenant_alert_level(
    db: AsyncSession, ents: Entitlements, tenant_id: UUID
) -> AlertLevel | None:
    """Peor nivel del tenant entre presupuesto de IA y cupos mensuales (SADM, P2b-18).

    Desde SADM la sesión no tiene tenant fijado: sin el contexto, RLS dejaría a 0
    el gasto de IA y los contratos.
    """
    await set_tenant_context(db, str(tenant_id))
    levels: list[AlertLevel] = []
    ai = await ai_usage_percent(db, ents, tenant_id)
    if ai is not None and ai >= 100:
        levels.append("exhausted")
    elif ai is not None and ai >= WARN_PERCENT:
        levels.append("warning")
    for status in await monthly_statuses(db, ents, tenant_id):
        if status.key != "contracts_active" and status.level is not None:
            levels.append(status.level)
    if "exhausted" in levels:
        return "exhausted"
    return "warning" if levels else None
