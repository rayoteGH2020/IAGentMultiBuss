"""Histórico visible por plan (``history_months``; D017, bloque 6).

- **Facturas y tickets:** se ven si su fecha de emisión (o, sin ella, la de
  subida) es del mes en curso o de los ``history_months`` anteriores.
- **Contratos:** los vigentes (activos y no vencidos) se ven siempre; los vencidos
  o sustituidos, si su fecha de fin (o, sin ella, la de sustitución) entra en el
  mismo límite.
- **Pólizas:** sin cambios (aparcadas, D017).

Se calcula al leer: nada se borra y, si el tenant sube de plan, lo oculto vuelve a
verse. Se aplica al panel y al chat; no al export RGPD, al SADM ni a los cupos.

**Documentos que ya nacen fuera del histórico** (facturas y tickets, decisión
2026-10-03): solo se procesa lo que entra en el histórico del plan.

- En la subida, si la fecha de emisión se conoce sin extraer (texto del PDF o la
  misma llamada de clasificación), se rechaza como un duplicado: sin R2, cupo ni
  extracción (``outside_history_upload_message``).
- Si no se conocía, tras la extracción: error visible ``outside_history``, sin
  reintento ni procesado del SADM, y se devuelve el cupo
  (``reject_if_outside_history``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import Date, and_, cast, func, literal, or_, select

from app.core.billing_period import history_visible_from, local_date
from app.core.entitlement_codes import LIMIT_HISTORY_MONTHS, PLAN_CODE_BASIC, PLAN_LIMITS
from app.core.errors import NotFoundError
from app.models import Contract, ContractLifecycle, Invoice, Ticket
from app.services import entitlement_service

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

# Si el catálogo no tuviera el límite (Entitlements.limit → 0), ocultarlo todo
# dejaría el panel vacío: se aplica el del plan más bajo.
_FALLBACK_MONTHS = int(PLAN_LIMITS[PLAN_CODE_BASIC][LIMIT_HISTORY_MONTHS] or 12)


def history_months(ents: Entitlements) -> int | None:
    """Meses de histórico del plan; ``None`` = sin límite."""
    raw = ents.limit(LIMIT_HISTORY_MONTHS)
    if raw is None:
        return None
    months = int(raw)
    if months < 1:
        logger.warning("document_history.limit_missing", plan_code=ents.plan_code)
        return _FALLBACK_MONTHS
    return months


def visible_from_for(ents: Entitlements, *, today: date | None = None) -> date | None:
    """Primer día visible; ``None`` = sin límite."""
    months = history_months(ents)
    return None if months is None else history_visible_from(months, today)


async def visible_from(db: AsyncSession, tenant_id: UUID) -> date | None:
    """Primer día visible del tenant (resuelve sus entitlements)."""
    return visible_from_for(await entitlement_service.resolve_tenant(db, tenant_id))


def invoice_visible(since: date) -> ColumnElement[bool]:
    return func.coalesce(Invoice.fecha, cast(Invoice.created_at, Date)) >= since


def ticket_visible(since: date) -> ColumnElement[bool]:
    return func.coalesce(Ticket.fecha, cast(Ticket.created_at, Date)) >= since


def contract_visible(since: date, *, today: date | None = None) -> ColumnElement[bool]:
    current_day = today or local_date()
    in_force = and_(
        Contract.lifecycle == ContractLifecycle.active,
        or_(Contract.fecha_fin.is_(None), Contract.fecha_fin >= current_day),
    )
    ended_on = func.coalesce(
        Contract.fecha_fin, cast(Contract.replaced_at, Date), cast(Contract.created_at, Date)
    )
    return or_(in_force, ended_on >= since)


async def ensure_visible(
    db: AsyncSession,
    identity: ColumnElement[bool],
    visible: ColumnElement[bool],
    *,
    document_id: UUID,
) -> None:
    """``NotFoundError`` si el documento está fuera del histórico (como si no existiera)."""
    found = await db.scalar(select(literal(True)).where(identity, visible))
    if not found:
        raise NotFoundError(f"Document {document_id} not found")


def outside_history_upload_message(
    *, filename: str, kind_label: str, issue_date: date, months: int | None, since: date
) -> str:
    """Rechazo en la subida de un documento anterior al histórico del plan."""
    return (
        f'"{filename}" es {kind_label} del {issue_date.strftime("%d/%m/%Y")}: tu plan procesa '
        f"documentos de los últimos {months} meses (desde el {since.strftime('%d/%m/%Y')}). "
        "No se ha subido ni consume cupo."
    )


async def reject_if_outside_history(
    db: AsyncSession,
    document: Invoice | Ticket,
    *,
    issue_date: date | None,
    llm_call_id: UUID | None,
) -> bool:
    """Tras la extracción: si la fecha es anterior al histórico, error definitivo.

    Devuelve el cupo reservado y cierra el intento como fallido. True si se rechazó.
    """
    if issue_date is None:
        return False
    since = await visible_from(db, document.tenant_id)
    if since is None or issue_date >= since:
        return False

    from app.core.document_processing_errors import DocumentErrorCode, rejection_message
    from app.models import InvoiceStatus, TicketStatus
    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service, document_quota_service

    kind: Literal["invoice", "ticket"]
    if isinstance(document, Invoice):
        kind = "invoice"
        document.status = InvoiceStatus.failed
    else:
        kind = "ticket"
        document.status = TicketStatus.failed
    document.error_code = DocumentErrorCode.outside_history.value
    document.error_message = rejection_message(
        DocumentErrorCode.outside_history,
        filename=document.source_filename,
        detail=(
            f"fecha del documento {issue_date.strftime('%d/%m/%Y')}; tu plan procesa "
            f"desde el {since.strftime('%d/%m/%Y')}"
        ),
    )
    await document_quota_service.release_reservation(db, document)
    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=document.tenant_id,
        document_kind=kind,
        document_id=document.id,
        status=ProcessingAttemptStatus.failed,
        llm_call_id=llm_call_id,
        error_message=document.error_message,
        error_code=document.error_code,
    )
    logger.info(
        "document_history.outside_history_after_extraction",
        tenant_id=str(document.tenant_id),
        document_kind=kind,
        document_id=str(document.id),
    )
    return True


def hidden_message(months: int | None) -> str:
    """Complemento del aviso de duplicado cuando el original está fuera del histórico."""
    return f"Queda fuera de los {months} meses de histórico de tu plan, por eso no lo ves."
