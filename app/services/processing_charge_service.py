"""Cargos por procesado excepcional: estimación, registro y liquidación.

Cuando el superadmin autoriza procesar un documento que incumple los límites,
el coste de proveedor deja de ser un gasto anónimo de la plataforma y pasa a
imputarse al tenant que lo pidió. Este servicio registra ese cargo para que un
proceso mensual futuro pueda repercutirlo. No emite ningún documento fiscal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.llm.client import DEFAULT_MODELS
from app.llm.pricing import compute_cost_eur
from app.models import LLMCall, ProcessingCharge, ProcessingChargeStatus

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

DocumentKindLiteral = Literal["invoice", "ticket"]


@dataclass(frozen=True, slots=True)
class ProcessingEstimate:
    """Coste y tiempo previstos de un procesado, para decidir antes de gastar."""

    pages: int
    model: str
    input_tokens: int
    output_tokens: int
    provider_cost_eur: Decimal
    billable_eur: Decimal
    seconds: int


def estimate_processing(pages: int) -> ProcessingEstimate:
    """Estima coste y duración de extraer un documento de `pages` páginas.

    La estimación es lineal en páginas con los tokens medios configurados. No
    pretende ser exacta: sirve para que el superadmin distinga "céntimos" de
    "esto cuesta veinte euros" antes de autorizar.
    """
    settings = get_settings()
    safe_pages = max(1, pages)
    model = settings.llm_model_extraction or DEFAULT_MODELS["extraction"]
    input_tokens = safe_pages * settings.document_estimated_input_tokens_per_page
    output_tokens = safe_pages * settings.document_estimated_output_tokens_per_page
    provider_cost = compute_cost_eur(model, input_tokens, output_tokens)
    return ProcessingEstimate(
        pages=safe_pages,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        provider_cost_eur=provider_cost,
        billable_eur=_billable_from_cost(provider_cost),
        seconds=int(safe_pages * settings.document_estimated_seconds_per_page),
    )


async def create_authorized_charge(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    estimate: ProcessingEstimate,
    authorized_by: UUID | None,
    reason: str | None = None,
) -> ProcessingCharge:
    """Registra el cargo en el momento de autorizar, con el coste estimado.

    El coste real se rellena en `settle_charge` cuando el worker termina; si el
    job nunca llega a ejecutarse queda el cargo con la estimación, que es
    justamente la traza que interesa auditar.
    """
    charge = ProcessingCharge(
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        period=_current_period(),
        pages=estimate.pages,
        estimated_cost_eur=estimate.provider_cost_eur,
        status=ProcessingChargeStatus.pending.value,
        authorized_by=authorized_by,
        reason=reason,
    )
    db.add(charge)
    await db.flush()
    logger.info(
        "processing_charge.authorized",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        pages=estimate.pages,
        estimated_cost_eur=str(estimate.provider_cost_eur),
        authorized_by=str(authorized_by) if authorized_by else None,
    )
    return charge


async def settle_charge(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    llm_call_id: UUID,
) -> ProcessingCharge | None:
    """Cierra el cargo con el coste real de la llamada LLM, si existe cargo.

    Devuelve None en el caso normal (documento sin autorización excepcional),
    por lo que puede llamarse siempre desde el worker sin condicionales.
    """
    stmt = (
        select(ProcessingCharge)
        .where(
            ProcessingCharge.tenant_id == tenant_id,
            ProcessingCharge.document_kind == document_kind,
            ProcessingCharge.document_id == document_id,
            ProcessingCharge.provider_cost_eur.is_(None),
        )
        .order_by(ProcessingCharge.created_at.desc())
        .limit(1)
    )
    charge = (await db.execute(stmt)).scalar_one_or_none()
    if charge is None:
        return None

    cost = await _llm_call_cost(db, llm_call_id)
    charge.provider_cost_eur = cost
    charge.billable_eur = _billable_from_cost(cost)
    charge.llm_call_id = llm_call_id
    await db.flush()
    logger.info(
        "processing_charge.settled",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        provider_cost_eur=str(cost),
        billable_eur=str(charge.billable_eur),
    )
    return charge


async def list_charges(
    db: AsyncSession,
    *,
    tenant_id: UUID | None = None,
    limit: int = 100,
) -> list[ProcessingCharge]:
    """Cargos más recientes, opcionalmente de un solo tenant (consola SADM)."""
    stmt = select(ProcessingCharge).order_by(ProcessingCharge.created_at.desc()).limit(limit)
    if tenant_id is not None:
        stmt = stmt.where(ProcessingCharge.tenant_id == tenant_id)
    return list((await db.execute(stmt)).scalars().all())


async def _llm_call_cost(db: AsyncSession, llm_call_id: UUID) -> Decimal:
    stmt = select(LLMCall.cost_eur).where(LLMCall.id == llm_call_id)
    cost = (await db.execute(stmt)).scalar_one_or_none()
    return Decimal(cost) if cost is not None else Decimal("0")


def _billable_from_cost(cost: Decimal) -> Decimal:
    multiplier = Decimal(str(get_settings().document_override_charge_multiplier))
    return (cost * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _current_period() -> date:
    now = datetime.now(tz=UTC)
    return date(now.year, now.month, 1)
