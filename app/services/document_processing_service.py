"""Reintento, abandono de processing huérfano y ocultación en /documents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.core.document_processing_errors import (
    PROCESSING_INTERRUPTED_USER_MESSAGE,
    DocumentErrorCode,
    is_retryable,
    rejection_message,
)
from app.core.errors import ValidationError
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
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

DocumentKindLiteral = Literal["invoice", "ticket", "contract", "insurance"]

_NOT_RETRYABLE_MESSAGE = (
    "Este documento no se puede reintentar porque no cumple los límites de procesado. "
    "Ponte en contacto con el administrador del sitio."
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
            error=str(exc),
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


async def retry_processing(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    document_kind: DocumentKindLiteral,
    document_id: UUID,
) -> None:
    """Reencola extracción sobre el mismo registro y fichero R2.

    Acepta ``failed`` (reintento normal) o ``processing``/``pending`` stale
    (abandona el attempt huérfano y reencola).
    """
    from app.core.cache import get_redis
    from app.services import entitlement_service, plan_quota_service

    redis = get_redis()
    ents = await entitlement_service.resolve_tenant(db, tenant_id)
    await plan_quota_service.ensure_document_retry(redis, ents, tenant_id)

    now = datetime.now(tz=UTC)

    if document_kind == DocumentKind.invoice.value:
        from app.services import invoice_service

        invoice_row = await invoice_service.get_invoice(db, tenant_id, document_id)
        await _prepare_retry_or_raise(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
            status=invoice_row.status,
            failed_status=InvoiceStatus.failed,
            processing_statuses=(InvoiceStatus.processing, InvoiceStatus.pending),
            error_code=invoice_row.error_code,
            source_file_key=invoice_row.source_file_key,
            updated_at=invoice_row.updated_at,
        )
        # Releer tras posible abandon.
        invoice_row = await invoice_service.get_invoice(db, tenant_id, document_id)
        invoice_row.status = InvoiceStatus.processing
        invoice_row.error_code = None
        invoice_row.error_message = None
        invoice_row.dismissed_at = None
        invoice_row.updated_at = now
        await db.flush()
        await begin_processing_attempt(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        try:
            await enqueue_invoice_processing(
                invoice_row.id,
                tenant_id,
                replace_existing=True,
            )
        except Exception as exc:
            raise RuntimeError("No se pudo encolar el reintento.") from exc
    elif document_kind == DocumentKind.ticket.value:
        from app.services import ticket_service

        ticket_row = await ticket_service.get_ticket(db, tenant_id, document_id)
        await _prepare_retry_or_raise(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
            status=ticket_row.status,
            failed_status=TicketStatus.failed,
            processing_statuses=(TicketStatus.processing, TicketStatus.pending),
            error_code=ticket_row.error_code,
            source_file_key=ticket_row.source_file_key,
            updated_at=ticket_row.updated_at,
        )
        ticket_row = await ticket_service.get_ticket(db, tenant_id, document_id)
        ticket_row.status = TicketStatus.processing
        ticket_row.error_code = None
        ticket_row.error_message = None
        ticket_row.dismissed_at = None
        ticket_row.updated_at = now
        await db.flush()
        await begin_processing_attempt(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        try:
            await enqueue_ticket_processing(
                ticket_row.id,
                tenant_id,
                replace_existing=True,
            )
        except Exception as exc:
            raise RuntimeError("No se pudo encolar el reintento.") from exc
    elif document_kind == DocumentKind.contract.value:
        from app.services import contract_service

        contract_row = await contract_service.get_contract(db, tenant_id, document_id)
        await _prepare_retry_or_raise(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
            status=contract_row.status,
            failed_status=ContractStatus.failed,
            processing_statuses=(ContractStatus.processing, ContractStatus.pending),
            error_code=contract_row.error_code,
            source_file_key=contract_row.source_file_key,
            updated_at=contract_row.updated_at,
        )
        contract_row = await contract_service.get_contract(db, tenant_id, document_id)
        contract_row.status = ContractStatus.processing
        contract_row.error_code = None
        contract_row.error_message = None
        contract_row.dismissed_at = None
        contract_row.updated_at = now
        await db.flush()
        await begin_processing_attempt(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        try:
            await enqueue_contract_processing(
                contract_row.id,
                tenant_id,
                replace_existing=True,
            )
        except Exception as exc:
            raise RuntimeError("No se pudo encolar el reintento.") from exc
    elif document_kind == DocumentKind.insurance.value:
        from app.services import insurance_service

        insurance_row = await insurance_service.get_insurance(db, tenant_id, document_id)
        await _prepare_retry_or_raise(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
            status=insurance_row.status,
            failed_status=InsuranceStatus.failed,
            processing_statuses=(InsuranceStatus.processing, InsuranceStatus.pending),
            error_code=insurance_row.error_code,
            source_file_key=insurance_row.source_file_key,
            updated_at=insurance_row.updated_at,
        )
        insurance_row = await insurance_service.get_insurance(db, tenant_id, document_id)
        insurance_row.status = InsuranceStatus.processing
        insurance_row.error_code = None
        insurance_row.error_message = None
        insurance_row.dismissed_at = None
        insurance_row.updated_at = now
        await db.flush()
        await begin_processing_attempt(
            db,
            tenant_id=tenant_id,
            document_kind=document_kind,
            document_id=document_id,
        )
        try:
            await enqueue_insurance_processing(
                insurance_row.id,
                tenant_id,
                replace_existing=True,
            )
        except Exception as exc:
            raise RuntimeError("No se pudo encolar el reintento.") from exc
    else:
        raise ValidationError("Tipo de documento no válido.")

    await plan_quota_service.record_document_retry(redis, tenant_id)

    logger.info(
        "document.retry_enqueued",
        tenant_id=str(tenant_id),
        document_kind=document_kind,
        document_id=str(document_id),
    )
