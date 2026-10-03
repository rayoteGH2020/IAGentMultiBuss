"""Cupo mensual de facturas y tickets (bloque 2) y altas de contratos (bloque 5).

Spec planes §4.2 y §4.3, D027. Lo propio de los contratos (tramos de páginas,
bolsa de carga inicial, archivo de activos) está en ``contract_quota_service``;
aquí se orquesta igual para los tres tipos.

- **Reserva al encolar, devolución si no termina bien.** Una unidad de la bolsa
  facturas + tickets se reserva antes de encolar la extracción y se devuelve si
  el documento falla, se interrumpe, se borra en el mismo mes o pasa a esperar
  presupuesto de IA. El efecto neto es que solo cuenta lo que se extrae bien, sin
  pasarse del tope con subidas simultáneas. ``quota_period`` guarda el mes de la
  reserva: la devolución va siempre a ese mes y nunca se devuelve dos veces.
- **Al 100 %:** el documento se guarda en ``quota_pending`` (no se encola).
  ``process_pending`` los procesa del más antiguo al más nuevo cuando hay hueco:
  cron horario (renovación del día 1), ampliación del SADM o una reserva devuelta.
- **Duplicados:** SHA-256 del fichero contra las facturas y tickets (o contra los
  contratos) del tenant que no estén ocultos, antes de subir a R2 y de cualquier
  llamada al LLM.
- **Avisos al admin:** email al cruzar el 80 % de la bolsa y con el primer
  documento pendiente del mes (una vez por mes cada uno y bolsa).
- **Contratos:** borrar uno ya procesado nunca devuelve sus altas.

El procesado excepcional que autoriza el SADM no reserva: se cobra aparte
(``processing_charges``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import ColumnElement, func, literal, select, text

from app.core.activity.context import job_parent_kwargs
from app.core.billing_period import (
    current_period_start,
    renewal_date,
    spanish_day_label,
)
from app.core.cache import get_redis
from app.core.db import set_tenant_context
from app.core.document_processing_errors import DocumentErrorCode, is_retryable
from app.core.entitlement_codes import LIMIT_INVOICES_PER_MONTH, LIMIT_TICKETS_PER_MONTH
from app.core.errors import RateLimitError
from app.models import Contract, ContractStatus, Invoice, InvoiceStatus, Ticket, TicketStatus
from app.services import (
    contract_quota_service,
    document_history_service,
    entitlement_service,
    monthly_quota_service,
    plan_quota_service,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

QuotaKind = Literal["invoice", "ticket", "contract"]
QuotaDocument = Invoice | Ticket | Contract
QuotaAlertKind = Literal[
    "documents_warning", "documents_exhausted", "contracts_warning", "contracts_exhausted"
]
QUOTA_ALERT_KINDS: dict[str, QuotaAlertKind] = {
    "documents_warning": "documents_warning",
    "documents_exhausted": "documents_exhausted",
    "contracts_warning": "contracts_warning",
    "contracts_exhausted": "contracts_exhausted",
}

_QUOTA_CODES: dict[QuotaKind, str] = {
    "invoice": LIMIT_INVOICES_PER_MONTH,
    "ticket": LIMIT_TICKETS_PER_MONTH,
}
_KIND_LABELS: dict[QuotaKind, str] = {
    "invoice": "factura",
    "ticket": "ticket",
    "contract": "contrato",
}
_WARN_RATIO_PCT = 80
# Más que un mes: la clave de aviso caduca sola tras el cambio de mes.
_ALERT_KEY_TTL_SECONDS = 40 * 24 * 3600
# Tope por pasada: con un cupo de cientos, un lote acotado evita transacciones largas.
_PENDING_BATCH = 200
_PENDING_CHECK_DELAY_SECONDS = 5


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """Documento existente con el mismo fichero (mismo SHA-256)."""

    kind: QuotaKind
    document_id: UUID
    status: str
    created_at: datetime
    error_code: str | None = None
    # Fuera del histórico visible del plan (history_months, D017): no lo ve.
    hidden_by_history: bool = False

    @property
    def kind_label(self) -> str:
        return _KIND_LABELS[self.kind]


@dataclass(frozen=True, slots=True)
class PendingToEnqueue:
    """Documento que ha salido de ``quota_pending`` y hay que encolar tras el commit."""

    kind: QuotaKind
    document_id: UUID
    tenant_id: UUID


def file_sha256(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def quota_kind(document: QuotaDocument) -> QuotaKind:
    if isinstance(document, Invoice):
        return "invoice"
    if isinstance(document, Contract):
        return "contract"
    return "ticket"


def _bag_key(kind: QuotaKind) -> str:
    return "contracts" if kind == "contract" else "documents"


def _set_status(document: QuotaDocument, status: Literal["processing", "quota_pending"]) -> None:
    # Ramas por tipo: cada modelo tiene su propio enum de estado.
    if isinstance(document, Invoice):
        document.status = InvoiceStatus(status)
    elif isinstance(document, Contract):
        document.status = ContractStatus(status)
    else:
        document.status = TicketStatus(status)


def pending_message(
    reason: DocumentErrorCode, kind: QuotaKind = "invoice", *, renewal: date | None = None
) -> str:
    """Texto de la fila en ``quota_pending`` (no es un error del documento).

    ``renewal``: día en que se renueva la bolsa; por defecto, el 1 del mes que viene
    (los contratos en carga inicial lo calculan con ``contract_quota_service``).
    """
    when = spanish_day_label(renewal or renewal_date())
    if reason == DocumentErrorCode.llm_budget:
        return (
            "Pendiente: se ha agotado el presupuesto de IA del mes. Se procesará "
            f"automáticamente el {when} o si se amplía el presupuesto."
        )
    if kind == "contract":
        return (
            "Pendiente de cupo: has usado todas las altas de contratos disponibles. Se "
            f"procesará automáticamente el {when} o cuando se amplíe el cupo."
        )
    return (
        "Pendiente de cupo: has usado todas las facturas y tickets del mes. Se "
        f"procesará automáticamente el {when} o cuando se amplíe el cupo."
    )


# ── Duplicados ───────────────────────────────────────────────────────────────


_DUPLICATE_MODELS: dict[QuotaKind, type[Invoice] | type[Ticket] | type[Contract]] = {
    "invoice": Invoice,
    "ticket": Ticket,
    "contract": Contract,
}


_HISTORY_CONDITIONS: dict[QuotaKind, Callable[[date], ColumnElement[bool]]] = {
    "invoice": document_history_service.invoice_visible,
    "ticket": document_history_service.ticket_visible,
    "contract": lambda since: document_history_service.contract_visible(since),
}


async def find_duplicate(
    db: AsyncSession,
    tenant_id: UUID,
    sha256: str,
    *,
    kinds: Sequence[QuotaKind] = ("invoice", "ticket"),
    visible_from: date | None = None,
) -> DuplicateMatch | None:
    """Documento no oculto del tenant, de los tipos ``kinds``, con el mismo fichero.

    Facturas y tickets se comprueban juntos (un mismo fichero no es las dos
    cosas); los contratos, entre sí (vigentes o sustituidos).

    Toma un bloqueo transaccional por (tenant, hash): dos subidas simultáneas del
    mismo fichero (doble clic) se serializan y la segunda ve la primera. Un
    documento oculto por el histórico del plan también bloquea (``visible_from``
    solo sirve para avisar de por qué no se ve).
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"doc_sha256:{tenant_id}:{sha256}"},
    )
    for kind in kinds:
        model = _DUPLICATE_MODELS[kind]
        visible = literal(True) if visible_from is None else _HISTORY_CONDITIONS[kind](visible_from)
        row = (
            await db.execute(
                select(
                    model.id,
                    model.status,
                    model.created_at,
                    model.error_code,
                    visible.label("visible"),
                )
                .where(
                    model.tenant_id == tenant_id,
                    model.file_sha256 == sha256,
                    model.dismissed_at.is_(None),
                )
                .order_by(model.created_at.asc())
                .limit(1)
            )
        ).first()
        if row is not None:
            return DuplicateMatch(
                kind=kind,
                document_id=row.id,
                status=str(row.status.value),
                created_at=row.created_at,
                error_code=row.error_code,
                hidden_by_history=not row.visible,
            )
    return None


def duplicate_message(
    match: DuplicateMatch, *, filename: str, history_months: int | None = None
) -> str:
    uploaded = match.created_at.strftime("%d/%m/%Y")
    base = (
        f'"{filename}" ya está subido como {match.kind_label} (subido el {uploaded}). '
        "No se ha vuelto a procesar ni consume cupo."
    )
    if match.hidden_by_history:
        return f"{base} {document_history_service.hidden_message(history_months)}"
    if match.status == "failed" and not is_retryable(match.error_code):
        return f"{base} Ese documento no se pudo procesar y no admite reintento."
    if match.status == "failed":
        return f"{base} Ese documento falló al procesarse: usa «Reintentar» en él."
    return base


# ── Reserva y devolución ─────────────────────────────────────────────────────


async def reserve(db: AsyncSession, ents: Entitlements, document: QuotaDocument) -> bool:
    """Reserva una unidad de la bolsa para ``document``. False si no hay hueco.

    Un contrato reserva sus ``upload_units`` en la bolsa vigente (carga inicial o
    mensual).
    """
    if document.quota_period is not None:
        return True
    if isinstance(document, Contract):
        contract_code = await contract_quota_service.try_reserve(db, ents, document)
        if contract_code is None or document.quota_period is None:
            return False
        await _maybe_warn(db, ents, document.tenant_id, contract_code, document.quota_period)
        return True
    code = _QUOTA_CODES[quota_kind(document)]
    period = current_period_start()
    if not await monthly_quota_service.try_consume(
        db, ents, document.tenant_id, code, period=period
    ):
        return False
    document.quota_period = period
    await db.flush()
    await _maybe_warn(db, ents, document.tenant_id, code, period)
    return True


async def release_reservation(db: AsyncSession, document: QuotaDocument) -> bool:
    """Devuelve la unidad reservada (al mes en que se reservó). Idempotente."""
    period = document.quota_period
    if period is None:
        return False
    if isinstance(document, Contract):
        await contract_quota_service.release(db, document)
    else:
        code = _QUOTA_CODES[quota_kind(document)]
        await monthly_quota_service.release(db, document.tenant_id, code, period=period)
        document.quota_period = None
        await db.flush()
    logger.info(
        "documents_quota.released",
        tenant_id=str(document.tenant_id),
        document_kind=quota_kind(document),
        document_id=str(document.id),
        period=period.isoformat(),
    )
    # La carga inicial de contratos se cuenta en el mes del alta: su devolución
    # también puede dejar sitio a un pendiente.
    if period == current_period_start() or isinstance(document, Contract):
        await schedule_pending_check(db, document.tenant_id)
    return True


async def release_on_delete(db: AsyncSession, document: QuotaDocument) -> None:
    """Al borrar: devuelve la unidad si es del mes en curso (decisión 2026-10-01).

    Cubre los duplicados que el hash no detecta (otra foto del mismo ticket) y los
    ficheros subidos por error. Un documento que no llegó a terminar bien la
    devuelve siempre; uno procesado en un mes ya cerrado, no.
    """
    period = document.quota_period
    if period is None:
        return
    if isinstance(document, Contract):
        # Contratos: borrar uno procesado nunca devuelve sus altas (D027, bloque 5).
        if not contract_quota_service.is_finished(document):
            await release_reservation(db, document)
        return
    finished = document.status in (
        InvoiceStatus.ready,
        InvoiceStatus.reviewed,
        TicketStatus.ready,
        TicketStatus.reviewed,
    )
    if finished and period != current_period_start():
        return
    await release_reservation(db, document)


# ── Pendientes de cupo ───────────────────────────────────────────────────────


async def hold(
    db: AsyncSession,
    document: QuotaDocument,
    reason: DocumentErrorCode,
) -> None:
    """Deja el documento en ``quota_pending`` sin encolar (y devuelve su reserva)."""
    await release_reservation(db, document)
    _set_status(document, "quota_pending")
    document.error_code = reason.value
    document.error_message = await _pending_message_for(db, document, reason)
    await db.flush()
    logger.info(
        "documents_quota.held",
        tenant_id=str(document.tenant_id),
        document_kind=quota_kind(document),
        document_id=str(document.id),
        error_code=reason.value,
    )
    if reason == DocumentErrorCode.monthly_quota:
        exhausted: QuotaAlertKind = (
            "contracts_exhausted" if isinstance(document, Contract) else "documents_exhausted"
        )
        await _maybe_alert(document.tenant_id, exhausted, current_period_start())


async def _pending_message_for(
    db: AsyncSession, document: QuotaDocument, reason: DocumentErrorCode
) -> str:
    renewal = None
    if isinstance(document, Contract) and reason == DocumentErrorCode.monthly_quota:
        renewal = await contract_quota_service.renewal_day(db, document.tenant_id)
    return pending_message(reason, quota_kind(document), renewal=renewal)


async def reserve_or_hold(db: AsyncSession, ents: Entitlements, document: QuotaDocument) -> bool:
    """Reserva para encolar; si no hay hueco, deja el documento pendiente. True = encolar."""
    if await reserve(db, ents, document):
        return True
    await hold(db, document, DocumentErrorCode.monthly_quota)
    return False


async def llm_budget_exhausted(db: AsyncSession, ents: Entitlements, tenant_id: UUID) -> bool:
    try:
        await plan_quota_service.ensure_llm_budget(db, ents, tenant_id)
    except RateLimitError:
        return True
    return False


async def hold_if_budget_exhausted(db: AsyncSession, document: QuotaDocument) -> bool:
    """Worker: si el presupuesto de IA está agotado, pendiente en vez de fallar (spec §4.7)."""
    ents = await entitlement_service.resolve_tenant(db, document.tenant_id)
    if not await llm_budget_exhausted(db, ents, document.tenant_id):
        return False
    await hold(db, document, DocumentErrorCode.llm_budget)
    return True


async def has_pending(db: AsyncSession, tenant_id: UUID) -> bool:
    for model, status in (
        (Invoice, InvoiceStatus.quota_pending),
        (Ticket, TicketStatus.quota_pending),
        (Contract, ContractStatus.quota_pending),
    ):
        count = await db.scalar(
            select(func.count())
            .select_from(model)
            .where(model.tenant_id == tenant_id, model.status == status)
        )
        if count:
            return True
    return False


async def schedule_pending_check(db: AsyncSession, tenant_id: UUID) -> None:
    """Encola ``process_quota_pending`` del tenant si tiene documentos esperando.

    Nunca interrumpe a quien lo llama. Se difiere unos segundos para que el job vea
    el commit de la transacción que liberó el hueco.
    """
    try:
        if not await has_pending(db, tenant_id):
            return
        from app.jobs.queue import get_arq_pool

        pool = await get_arq_pool()
        await pool.enqueue_job(
            "process_quota_pending",
            str(tenant_id),
            _defer_by=_PENDING_CHECK_DELAY_SECONDS,
            **job_parent_kwargs(),
        )
    except Exception as exc:
        logger.warning(
            "documents_quota.pending_check_failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )


async def on_quota_extra_added(db: AsyncSession, *, tenant_id: UUID, code: str) -> None:
    """Tras una ampliación del SADM de un cupo de documentos o contratos, procesa pendientes."""
    if code in _QUOTA_CODES.values() or code in contract_quota_service.CONTRACT_UPLOAD_CODES:
        await set_tenant_context(db, str(tenant_id))
        await schedule_pending_check(db, tenant_id)


async def _pending_documents(db: AsyncSession, tenant_id: UUID) -> list[QuotaDocument]:
    # SKIP LOCKED: dos pasadas simultáneas (cron + ampliación) no cogen el mismo documento.
    invoices = await db.execute(
        select(Invoice)
        .where(Invoice.tenant_id == tenant_id, Invoice.status == InvoiceStatus.quota_pending)
        .order_by(Invoice.created_at.asc())
        .limit(_PENDING_BATCH)
        .with_for_update(skip_locked=True)
    )
    tickets = await db.execute(
        select(Ticket)
        .where(Ticket.tenant_id == tenant_id, Ticket.status == TicketStatus.quota_pending)
        .order_by(Ticket.created_at.asc())
        .limit(_PENDING_BATCH)
        .with_for_update(skip_locked=True)
    )
    contracts = await db.execute(
        select(Contract)
        .where(Contract.tenant_id == tenant_id, Contract.status == ContractStatus.quota_pending)
        .order_by(Contract.created_at.asc())
        .limit(_PENDING_BATCH)
        .with_for_update(skip_locked=True)
    )
    documents: list[QuotaDocument] = [
        *invoices.scalars().all(),
        *tickets.scalars().all(),
        *contracts.scalars().all(),
    ]
    documents.sort(key=lambda doc: doc.created_at)
    return documents


async def process_pending(db: AsyncSession, tenant_id: UUID) -> list[PendingToEnqueue]:
    """Saca de ``quota_pending`` lo que quepa en el cupo, del más antiguo al más nuevo.

    No encola: devuelve qué encolar para que el caller lo haga **después** del commit
    (si el commit fallara, no habría extracciones sin reserva).
    """
    await set_tenant_context(db, str(tenant_id))
    documents = await _pending_documents(db, tenant_id)
    if not documents:
        return []
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    if await llm_budget_exhausted(db, ents, tenant_id):
        return []

    released: list[PendingToEnqueue] = []
    # Una bolsa sin hueco se cierra para el resto de la pasada (orden de llegada
    # dentro de cada bolsa), pero no bloquea a la otra.
    blocked: set[str] = set()
    for document in documents:
        kind = quota_kind(document)
        if _bag_key(kind) in blocked:
            continue
        if not await reserve(db, ents, document):
            if document.error_code != DocumentErrorCode.monthly_quota.value:
                document.error_code = DocumentErrorCode.monthly_quota.value
                document.error_message = await _pending_message_for(
                    db, document, DocumentErrorCode.monthly_quota
                )
            blocked.add(_bag_key(kind))
            continue
        _set_status(document, "processing")
        document.error_code = None
        document.error_message = None
        released.append(
            PendingToEnqueue(
                kind=quota_kind(document), document_id=document.id, tenant_id=tenant_id
            )
        )
    await db.flush()
    if released:
        logger.info(
            "documents_quota.pending_released",
            tenant_id=str(tenant_id),
            released_count=len(released),
            still_pending_count=len(documents) - len(released),
        )
    return released


# ── Avisos ───────────────────────────────────────────────────────────────────


async def _maybe_warn(
    db: AsyncSession, ents: Entitlements, tenant_id: UUID, code: str, period: date
) -> None:
    try:
        used, cap = await monthly_quota_service.bag_usage(db, ents, tenant_id, code, period=period)
        if cap is None or cap <= 0 or used * 100 < cap * _WARN_RATIO_PCT:
            return
        warning: QuotaAlertKind = (
            "contracts_warning"
            if code in contract_quota_service.CONTRACT_UPLOAD_CODES
            else "documents_warning"
        )
        await _maybe_alert(tenant_id, warning, period)
    except Exception as exc:
        logger.warning(
            "documents_quota.warn_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__
        )


async def _maybe_alert(tenant_id: UUID, kind: QuotaAlertKind, period: date) -> None:
    """Encola el email si es el primero del mes de este tipo. Nunca lanza."""
    try:
        redis = get_redis()
        key = f"documents_quota:{kind}:{tenant_id}:{period.isoformat()}"
        if not await redis.set(key, "1", nx=True, ex=_ALERT_KEY_TTL_SECONDS):
            return
        from app.jobs.queue import get_arq_pool

        pool = await get_arq_pool()
        await pool.enqueue_job(
            "send_documents_quota_alert", str(tenant_id), kind, **job_parent_kwargs()
        )
        logger.info("documents_quota.alert_enqueued", tenant_id=str(tenant_id), kind=kind)
    except Exception as exc:
        logger.warning(
            "documents_quota.alert_failed", tenant_id=str(tenant_id), error_type=type(exc).__name__
        )


_ALERT_SUBJECTS: dict[QuotaAlertKind, str] = {
    "documents_warning": "Aviso: has usado el {pct} % de las facturas y tickets de este mes",
    "documents_exhausted": "Has agotado las facturas y tickets de este mes",
    "contracts_warning": "Aviso: has usado el {pct} % de las altas de contratos",
    "contracts_exhausted": "Has agotado las altas de contratos",
}
_ALERT_BODIES: dict[QuotaAlertKind, str] = {
    "documents_warning": (
        "Tu organización {org} ha usado {used} de las {cap} facturas y tickets que "
        "incluye su plan este mes.\n\n"
        "Al llegar al límite, los documentos que se suban se guardarán como pendientes "
        "y se procesarán automáticamente el {renewal}. Si necesitas ampliarlo antes, "
        "contacta con soporte."
    ),
    "documents_exhausted": (
        "Tu organización {org} ha usado las {cap} facturas y tickets que incluye su "
        "plan este mes.\n\n"
        "Los documentos que se suban a partir de ahora quedan pendientes y se "
        "procesarán automáticamente el {renewal}, o en cuanto se amplíe el cupo. Si "
        "necesitas ampliarlo, contacta con soporte."
    ),
    "contracts_warning": (
        "Tu organización {org} ha usado {used} de las {cap} altas de contratos "
        "disponibles ({bag}).\n\n"
        "Al llegar al límite, los contratos que se suban se guardarán como pendientes "
        "y se procesarán automáticamente el {renewal}. Si necesitas ampliarlo antes, "
        "contacta con soporte."
    ),
    "contracts_exhausted": (
        "Tu organización {org} ha usado las {cap} altas de contratos disponibles "
        "({bag}).\n\n"
        "Los contratos que se suban a partir de ahora quedan pendientes y se "
        "procesarán automáticamente el {renewal}, o en cuanto se amplíe el cupo. Si "
        "necesitas ampliarlo, contacta con soporte."
    ),
}
_BAG_LABELS: dict[str, str] = {
    "contract_uploads_first_period": "carga inicial",
    "contract_uploads_per_month": "este mes",
}


async def send_alert(db: AsyncSession, tenant_id: UUID, kind: QuotaAlertKind) -> bool:
    """Email al admin del tenant. False si no hay admin con email."""
    from app.core.email import send_email
    from app.models import Tenant
    from app.services import llm_budget_alert_service

    admin = await llm_budget_alert_service.tenant_admin(db, tenant_id)
    if admin is None or not admin.email:
        logger.warning("documents_quota.alert_no_admin", tenant_id=str(tenant_id), kind=kind)
        return False
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    renewal = renewal_date()
    if kind.startswith("contracts_"):
        code, _period = await contract_quota_service.current_bag(db, tenant_id)
        renewal = await contract_quota_service.renewal_day(db, tenant_id)
    else:
        code = LIMIT_INVOICES_PER_MONTH
    used, cap = await monthly_quota_service.bag_usage(db, ents, tenant_id, code)
    values = {
        "bag": _BAG_LABELS.get(code, ""),
        "org": tenant.name,
        "used": used,
        "cap": cap if cap is not None else "-",
        "pct": int(used * 100 / cap) if cap else 0,
        "renewal": spanish_day_label(renewal),
    }
    await send_email(
        to=admin.email,
        subject=_ALERT_SUBJECTS[kind].format(**values),
        body=_ALERT_BODIES[kind].format(**values),
    )
    logger.info("documents_quota.alert_sent", tenant_id=str(tenant_id), kind=kind)
    return True
