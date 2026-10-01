"""Reintento, abandono de processing huérfano y ocultación en /documents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.core.billing_period import renewal_date, spanish_day_label
from app.core.document_processing_errors import (
    PROCESSING_INTERRUPTED_USER_MESSAGE,
    DocumentErrorCode,
    is_free_retry,
    is_retryable,
    rejection_message,
)
from app.core.entitlement_codes import LIMIT_DOCUMENT_RETRIES_PER_MONTH
from app.core.errors import RateLimitError, ValidationError
from app.jobs.queue import (
    enqueue_contract_processing,
    enqueue_insurance_processing,
    enqueue_invoice_processing,
    enqueue_ticket_processing,
    purge_document_processing_job,
)
from app.models.contract import ContractStatus
from app.models.document_processing_attempt import (
    DocumentKind,
    DocumentProcessingAttempt,
    ProcessingAttemptStatus,
)
from app.models.insurance import InsuranceStatus
from app.models.invoice import InvoiceStatus
from app.models.ticket import TicketStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.entitlements import Entitlements

logger = structlog.get_logger(__name__)

DocumentKindLiteral = Literal["invoice", "ticket", "contract", "insurance"]

_NOT_RETRYABLE_MESSAGE = (
    "Este documento no se puede reintentar porque no cumple los límites de procesado. "
    "Ponte en contacto con el administrador del sitio."
)
# Bloque 3 (D027): máximo de reintentos manuales por documento.
MAX_MANUAL_RETRIES_PER_DOCUMENT = 3
MSG_DOCUMENT_RETRIES_EXHAUSTED = (
    "Este documento ya se ha reintentado 3 veces y queda para revisión manual. "
    "Comprueba que el fichero sea legible o contacta con soporte."
)
MSG_RETRIES_MONTH = "Has agotado los reintentos de procesado de este mes. Se renuevan el {renewal}."
MSG_RETRY_NO_DOCUMENT_QUOTA = (
    "No se puede reintentar: has usado todas las facturas y tickets de este mes. "
    "El cupo se renueva el {renewal}."
)
_STILL_PROCESSING_MESSAGE = (
    "Este documento sigue en procesado. Espera a que termine o inténtalo más tarde "
    "si el estado no cambia."
)


def _ensure_retryable(error_code: str | None) -> None:
    """Impide reintentar rechazos que volverían a fallar con el mismo fichero."""
    if not is_retryable(error_code):
        raise ValidationError(_NOT_RETRYABLE_MESSAGE)


def is_processing_stale(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: int | None = None,
) -> bool:
    """True si un intento/documento en processing supera el umbral de huérfano."""
    if started_at is None:
        return False
    threshold = (
        stale_after_seconds
        if stale_after_seconds is not None
        else get_settings().document_processing_stale_after_seconds
    )
    if threshold <= 0:
        return False
    instant = now or datetime.now(tz=UTC)
    aware = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    return instant - aware.astimezone(UTC) >= timedelta(seconds=threshold)


async def _next_attempt_number(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> int:
    stmt = select(func.coalesce(func.max(DocumentProcessingAttempt.attempt_number), 0)).where(
        DocumentProcessingAttempt.tenant_id == tenant_id,
        DocumentProcessingAttempt.document_kind == document_kind,
        DocumentProcessingAttempt.document_id == document_id,
    )
    result = await db.execute(stmt)
    current_max = result.scalar_one()
    return int(current_max) + 1


async def _open_processing_attempt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> DocumentProcessingAttempt | None:
    stmt = (
        select(DocumentProcessingAttempt)
        .where(
            DocumentProcessingAttempt.tenant_id == tenant_id,
            DocumentProcessingAttempt.document_kind == document_kind,
            DocumentProcessingAttempt.document_id == document_id,
            DocumentProcessingAttempt.status == ProcessingAttemptStatus.processing.value,
        )
        .order_by(DocumentProcessingAttempt.attempt_number.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def begin_processing_attempt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> DocumentProcessingAttempt:
    """Abre un intento de extracción (idempotente si ya hay uno en curso)."""
    open_attempt = await _open_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    if open_attempt is not None:
        return open_attempt

    attempt_number = await _next_attempt_number(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    attempt = DocumentProcessingAttempt(
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        attempt_number=attempt_number,
        status=ProcessingAttemptStatus.processing.value,
    )
    db.add(attempt)
    await db.flush()
    logger.info(
        "document_processing.attempt_started",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        attempt_number=attempt_number,
    )
    return attempt


async def finalize_processing_attempt(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    status: ProcessingAttemptStatus,
    llm_call_id: UUID | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
) -> None:
    """Cierra el intento en curso o crea uno retroactivo si no existía."""
    attempt = await _open_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    now = datetime.now(tz=UTC)
    if attempt is None:
        attempt_number = await _next_attempt_number(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        attempt = DocumentProcessingAttempt(
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
            attempt_number=attempt_number,
            status=status.value,
            llm_call_id=llm_call_id,
            error_message=error_message,
            error_code=error_code,
            finished_at=now,
        )
        db.add(attempt)
    else:
        attempt.status = status.value
        attempt.llm_call_id = llm_call_id
        attempt.error_message = error_message
        attempt.error_code = error_code
        attempt.finished_at = now
    await db.flush()
    logger.info(
        "document_processing.attempt_finished",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        attempt_number=attempt.attempt_number,
        status=status.value,
        llm_call_id=str(llm_call_id) if llm_call_id else None,
        error_code=error_code,
    )


async def _release_quota(db: AsyncSession, document: Any) -> None:
    """Un documento que no termina bien no consume cupo (bloque 2)."""
    from app.services import document_quota_service

    await document_quota_service.release_reservation(db, document)


async def abandon_stale_processing(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    force: bool = False,
) -> bool:
    """Marca documento+attempt huérfanos como failed (processing_interrupted).

    Args:
        force: Si True, abandona aunque no haya superado el umbral stale
            (uso operativo / script de recuperación).

    Returns:
        True si se abandonó algo; False si no había processing o aún no es stale.
    """
    open_attempt = await _open_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )

    if document_kind == DocumentKind.invoice.value:
        from app.services import invoice_service

        invoice_row = await invoice_service.get_invoice(db, tenant_id, document_id)
        if invoice_row.status not in (InvoiceStatus.processing, InvoiceStatus.pending):
            return False
        started = open_attempt.created_at if open_attempt is not None else invoice_row.updated_at
        if not force and not is_processing_stale(started):
            return False
        user_msg = rejection_message(
            DocumentErrorCode.processing_interrupted,
            filename=invoice_row.source_filename,
        )
        invoice_row.status = InvoiceStatus.failed
        invoice_row.error_code = DocumentErrorCode.processing_interrupted.value
        invoice_row.error_message = user_msg
        invoice_row.updated_at = datetime.now(tz=UTC)
        await _release_quota(db, invoice_row)
    elif document_kind == DocumentKind.ticket.value:
        from app.services import ticket_service

        ticket_row = await ticket_service.get_ticket(db, tenant_id, document_id)
        if ticket_row.status not in (TicketStatus.processing, TicketStatus.pending):
            return False
        started = open_attempt.created_at if open_attempt is not None else ticket_row.updated_at
        if not force and not is_processing_stale(started):
            return False
        user_msg = rejection_message(
            DocumentErrorCode.processing_interrupted,
            filename=ticket_row.source_filename,
        )
        ticket_row.status = TicketStatus.failed
        ticket_row.error_code = DocumentErrorCode.processing_interrupted.value
        ticket_row.error_message = user_msg
        ticket_row.updated_at = datetime.now(tz=UTC)
        await _release_quota(db, ticket_row)
    elif document_kind == DocumentKind.contract.value:
        from app.services import contract_service

        contract_row = await contract_service.get_contract(db, tenant_id, document_id)
        if contract_row.status not in (ContractStatus.processing, ContractStatus.pending):
            return False
        started = open_attempt.created_at if open_attempt is not None else contract_row.updated_at
        if not force and not is_processing_stale(started):
            return False
        user_msg = rejection_message(
            DocumentErrorCode.processing_interrupted,
            filename=contract_row.source_filename,
        )
        contract_row.status = ContractStatus.failed
        contract_row.error_code = DocumentErrorCode.processing_interrupted.value
        contract_row.error_message = user_msg
        contract_row.updated_at = datetime.now(tz=UTC)
    elif document_kind == DocumentKind.insurance.value:
        from app.services import insurance_service

        insurance_row = await insurance_service.get_insurance(db, tenant_id, document_id)
        if insurance_row.status not in (InsuranceStatus.processing, InsuranceStatus.pending):
            return False
        started = open_attempt.created_at if open_attempt is not None else insurance_row.updated_at
        if not force and not is_processing_stale(started):
            return False
        user_msg = rejection_message(
            DocumentErrorCode.processing_interrupted,
            filename=insurance_row.source_filename,
        )
        insurance_row.status = InsuranceStatus.failed
        insurance_row.error_code = DocumentErrorCode.processing_interrupted.value
        insurance_row.error_message = user_msg
        insurance_row.updated_at = datetime.now(tz=UTC)
    else:
        raise ValidationError("Tipo de documento no válido.")

    await finalize_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        status=ProcessingAttemptStatus.failed,
        error_message=PROCESSING_INTERRUPTED_USER_MESSAGE,
        error_code=DocumentErrorCode.processing_interrupted.value,
    )
    await db.flush()
    # Evita que un worker tardío marque ready sobre un documento ya abandonado.
    try:
        await purge_document_processing_job(document_kind, document_id)
    except Exception as exc:  # pragma: no cover - Redis opcional en tests
        logger.warning(
            "document_processing.purge_job_failed",
            tenant_id=str(tenant_id),
            document_kind=document_kind,
            document_id=str(document_id),
            error_type=type(exc).__name__,
        )
    logger.warning(
        "document_processing.abandoned_stale",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        force=force,
    )
    return True


async def dismiss_from_panel(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> None:
    """Oculta un documento fallido del listado sin borrarlo de BD."""
    now = datetime.now(tz=UTC)
    if document_kind == DocumentKind.invoice.value:
        from app.services import invoice_service

        invoice_row = await invoice_service.get_invoice(db, tenant_id, document_id)
        if invoice_row.status != InvoiceStatus.failed:
            raise ValidationError("Solo se pueden ocultar documentos con error de procesamiento.")
        if invoice_row.dismissed_at is not None:
            return
        invoice_row.dismissed_at = now
        invoice_row.updated_at = now
    elif document_kind == DocumentKind.ticket.value:
        from app.services import ticket_service

        ticket_row = await ticket_service.get_ticket(db, tenant_id, document_id)
        if ticket_row.status != TicketStatus.failed:
            raise ValidationError("Solo se pueden ocultar documentos con error de procesamiento.")
        if ticket_row.dismissed_at is not None:
            return
        ticket_row.dismissed_at = now
        ticket_row.updated_at = now
    elif document_kind == DocumentKind.contract.value:
        from app.services import contract_service

        contract_row = await contract_service.get_contract(db, tenant_id, document_id)
        if contract_row.status != ContractStatus.failed:
            raise ValidationError("Solo se pueden ocultar documentos con error de procesamiento.")
        if contract_row.dismissed_at is not None:
            return
        contract_row.dismissed_at = now
        contract_row.updated_at = now
    elif document_kind == DocumentKind.insurance.value:
        from app.services import insurance_service

        insurance_row = await insurance_service.get_insurance(db, tenant_id, document_id)
        if insurance_row.status != InsuranceStatus.failed:
            raise ValidationError("Solo se pueden ocultar documentos con error de procesamiento.")
        if insurance_row.dismissed_at is not None:
            return
        insurance_row.dismissed_at = now
        insurance_row.updated_at = now
    else:
        raise ValidationError("Tipo de documento no válido.")

    await db.flush()
    logger.info(
        "document.dismissed_from_panel",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
    )


async def _prepare_retry_or_raise(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
    status: object,
    failed_status: object,
    processing_statuses: tuple[object, ...],
    error_code: str | None,
    source_file_key: str | None,
    updated_at: datetime,
) -> None:
    """Valida estado para reintento; abandona processing stale si aplica."""
    if not source_file_key:
        raise ValidationError("El documento no tiene fichero asociado para reintentar.")

    if status == failed_status:
        _ensure_retryable(error_code)
        return

    if status in processing_statuses:
        open_attempt = await _open_processing_attempt(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        started = open_attempt.created_at if open_attempt is not None else updated_at
        if not is_processing_stale(started):
            raise ValidationError(_STILL_PROCESSING_MESSAGE)
        abandoned = await abandon_stale_processing(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        if not abandoned:
            raise ValidationError(_STILL_PROCESSING_MESSAGE)
        return

    raise ValidationError("Solo se puede reintentar un documento en estado de error.")


async def _load_document(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> Any:
    if document_kind == DocumentKind.invoice.value:
        from app.services import invoice_service

        return await invoice_service.get_invoice(db, tenant_id, document_id)
    if document_kind == DocumentKind.ticket.value:
        from app.services import ticket_service

        return await ticket_service.get_ticket(db, tenant_id, document_id)
    if document_kind == DocumentKind.contract.value:
        from app.services import contract_service

        return await contract_service.get_contract(db, tenant_id, document_id)
    if document_kind == DocumentKind.insurance.value:
        from app.services import insurance_service

        return await insurance_service.get_insurance(db, tenant_id, document_id)
    raise ValidationError("Tipo de documento no válido.")


_RETRY_STATUSES: dict[str, tuple[object, object, tuple[object, ...]]] = {
    # kind: (failed, processing, estados en curso que admiten reintento si están atascados)
    "invoice": (
        InvoiceStatus.failed,
        InvoiceStatus.processing,
        (InvoiceStatus.processing, InvoiceStatus.pending),
    ),
    "ticket": (
        TicketStatus.failed,
        TicketStatus.processing,
        (TicketStatus.processing, TicketStatus.pending),
    ),
    "contract": (
        ContractStatus.failed,
        ContractStatus.processing,
        (ContractStatus.processing, ContractStatus.pending),
    ),
    "insurance": (
        InsuranceStatus.failed,
        InsuranceStatus.processing,
        (InsuranceStatus.processing, InsuranceStatus.pending),
    ),
}


def _enqueue_for_retry(document_kind: DocumentKindLiteral) -> Callable[..., Awaitable[str]]:
    # Se resuelve al llamar (no en un dict de módulo) para que los tests puedan
    # parchear enqueue_*_processing en este módulo.
    return {
        "invoice": enqueue_invoice_processing,
        "ticket": enqueue_ticket_processing,
        "contract": enqueue_contract_processing,
        "insurance": enqueue_insurance_processing,
    }[document_kind]


def retries_exhausted(manual_retry_count: int, error_code: str | None) -> bool:
    """True si el documento ya agotó sus reintentos manuales («Revisión manual»).

    Un fallo que no causó el usuario (``FREE_RETRY_ERROR_CODES``) se puede reintentar
    siempre: ese reintento no cuenta.
    """
    return manual_retry_count >= MAX_MANUAL_RETRIES_PER_DOCUMENT and not is_free_retry(error_code)


async def _charge_retry(
    db: AsyncSession,
    ents: Entitlements,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    row: Any,
) -> bool:
    """Aplica los límites del reintento. Devuelve True si el reintento cuenta.

    Orden (todo en la transacción de la petición: si algo falla, no se gasta nada):
    1. Máximo de reintentos por documento.
    2. Facturas y tickets: reserva del cupo de documentos (se devolvió al fallar).
    3. Reintento del mes.
    """
    from app.services import document_quota_service, monthly_quota_service

    free = is_free_retry(row.error_code)
    if retries_exhausted(row.manual_retry_count, row.error_code):
        raise ValidationError(MSG_DOCUMENT_RETRIES_EXHAUSTED)

    if document_kind in ("invoice", "ticket") and not await document_quota_service.reserve(
        db, ents, row
    ):
        renewal = spanish_day_label(renewal_date())
        raise RateLimitError(MSG_RETRY_NO_DOCUMENT_QUOTA.format(renewal=renewal))

    if free:
        return False
    if not await monthly_quota_service.try_consume(
        db, ents, tenant_id, LIMIT_DOCUMENT_RETRIES_PER_MONTH
    ):
        renewal = spanish_day_label(renewal_date())
        raise RateLimitError(MSG_RETRIES_MONTH.format(renewal=renewal))
    row.manual_retry_count += 1
    return True


async def retry_processing(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> None:
    """Reencola extracción sobre el mismo registro y fichero R2.

    Acepta ``failed`` (reintento normal) o ``processing``/``pending`` atascados
    (abandona el attempt huérfano y reencola). Límites del bloque 3 (D027): máximo
    3 reintentos manuales por documento y ``document_retries_per_month``; un fallo
    que no causó el usuario no gasta reintento.
    """
    from app.services import entitlement_service

    if document_kind not in _RETRY_STATUSES:
        raise ValidationError("Tipo de documento no válido.")
    failed_status, processing_status, busy_statuses = _RETRY_STATUSES[document_kind]

    row = await _load_document(
        db, tenant_id=tenant_id, document_kind=document_kind, document_id=document_id
    )
    await _prepare_retry_or_raise(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
        status=row.status,
        failed_status=failed_status,
        processing_statuses=busy_statuses,
        error_code=row.error_code,
        source_file_key=row.source_file_key,
        updated_at=row.updated_at,
    )
    # Releer tras un posible abandono (cambia estado y error_code).
    row = await _load_document(
        db, tenant_id=tenant_id, document_kind=document_kind, document_id=document_id
    )
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    counted = await _charge_retry(
        db, ents, tenant_id=tenant_id, document_kind=document_kind, row=row
    )

    row.status = processing_status
    row.error_code = None
    row.error_message = None
    row.dismissed_at = None
    row.updated_at = datetime.now(tz=UTC)
    await db.flush()
    await begin_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind=document_kind,
        document_id=document_id,
    )
    try:
        await _enqueue_for_retry(document_kind)(row.id, tenant_id, replace_existing=True)
    except Exception as exc:
        raise RuntimeError("No se pudo encolar el reintento.") from exc

    logger.info(
        "document.retry_enqueued",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
        manual_retry_count=row.manual_retry_count,
        retry_counted=counted,
    )
