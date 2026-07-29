"""Pólizas de seguro — listado, creación y persistencia de extracción LLM."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import ColumnElement, String, cast, func, literal, or_, select
from sqlalchemy.orm import selectinload

from app.core.document_processing_errors import DocumentErrorCode, failure_message
from app.core.errors import NotFoundError, ValidationError
from app.core.keys import insurance_key
from app.core.storage import get_storage
from app.core.text_normalization import ilike_pattern, normalize_search_text
from app.core.uploads import original_upload_filename
from app.models import DocTypeCode, Insurance, InsuranceStatus
from app.schemas.document_query import (
    AggregateGroupBy,
    AggregateMetric,
    AggregateResult,
    AggregateRow,
    DocumentSearchFilters,
    InsuranceRead,
)
from app.schemas.pagination import Page
from app.services import doc_type_service

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.insurance import SeguroPoliza

logger = structlog.get_logger(__name__)


async def list_insurances(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    status: InsuranceStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Insurance]:
    stmt = (
        select(Insurance)
        .where(Insurance.tenant_id == tenant_id, Insurance.dismissed_at.is_(None))
        .order_by(Insurance.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Insurance.status == status)
    stmt = stmt.options(selectinload(Insurance.llm_call), selectinload(Insurance.doc_type))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_insurance(
    db: AsyncSession,
    tenant_id: UUID,
    insurance_id: UUID,
) -> Insurance:
    stmt = (
        select(Insurance)
        .where(Insurance.tenant_id == tenant_id, Insurance.id == insurance_id)
        .options(selectinload(Insurance.llm_call))
    )
    result = await db.execute(stmt)
    insurance = result.scalar_one_or_none()
    if insurance is None:
        raise NotFoundError(f"Insurance {insurance_id} not found")
    return insurance


async def create_insurance_stub(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    source_file_key: str,
    source_filename: str,
    source_mime: str,
    doc_type: DocTypeCode = DocTypeCode.seguro,
) -> Insurance:
    doc_type_id = await doc_type_service.get_doc_type_id(db, doc_type)
    insurance = Insurance(
        tenant_id=tenant_id,
        doc_type_id=doc_type_id,
        status=InsuranceStatus.pending,
        source_file_key=source_file_key,
        source_filename=source_filename,
        source_mime=source_mime,
    )
    db.add(insurance)
    await db.flush()
    logger.info("insurance.created", insurance_id=str(insurance.id), tenant_id=str(tenant_id))
    return insurance


async def create_insurance_from_upload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    doc_type: DocTypeCode = DocTypeCode.seguro,
) -> Insurance:
    storage = get_storage()
    key = insurance_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)
    insurance = await create_insurance_stub(
        db,
        tenant_id,
        source_file_key=key,
        source_filename=original_upload_filename(filename),
        source_mime=mime_type,
        doc_type=doc_type,
    )
    insurance.status = InsuranceStatus.processing
    await db.flush()
    return insurance


async def apply_extraction_result(
    db: AsyncSession,
    *,
    insurance: Insurance,
    data: SeguroPoliza,
    llm_call_id: UUID,
) -> Insurance:
    insurance.llm_call_id = llm_call_id
    insurance.aseguradora = data.aseguradora[:300]
    insurance.numero_poliza = data.numero_poliza[:100] if data.numero_poliza else None
    insurance.tomador = data.tomador[:300]
    insurance.cif_nif = data.cif_nif[:50] if data.cif_nif else None
    insurance.tipo_seguro = data.tipo_seguro[:100] if data.tipo_seguro else None
    insurance.fecha_inicio = data.fecha_inicio
    insurance.fecha_fin = data.fecha_fin
    insurance.prima = data.prima
    insurance.currency = data.currency[:3]
    insurance.cobertura = data.cobertura
    insurance.confidence = Decimal(str(data.confidence)).quantize(Decimal("0.01"))
    insurance.raw_extraction = data.model_dump(mode="json")
    insurance.status = InsuranceStatus.ready
    insurance.updated_at = datetime.now(tz=UTC)
    insurance.error_message = None
    insurance.error_code = None
    await db.flush()
    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service

    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=insurance.tenant_id,
        document_kind="insurance",
        document_id=insurance.id,
        status=ProcessingAttemptStatus.ok,
        llm_call_id=llm_call_id,
    )
    return insurance


async def mark_failed(
    db: AsyncSession,
    *,
    insurance_id: UUID,
    tenant_id: UUID,
    error: str,
    llm_call_id: UUID | None = None,
    error_code: DocumentErrorCode = DocumentErrorCode.extraction_failed,
    detail: str | None = None,
) -> None:
    insurance = await get_insurance(db, tenant_id, insurance_id)
    insurance.status = InsuranceStatus.failed
    if llm_call_id is not None:
        insurance.llm_call_id = llm_call_id
    logger.warning(
        "insurance.processing_failed",
        insurance_id=str(insurance_id),
        tenant_id=str(tenant_id),
        source_filename=insurance.source_filename,
        error_code=error_code.value,
        technical_error=error[:2000],
    )
    insurance.error_code = error_code.value
    insurance.error_message = failure_message(
        error,
        error_code=error_code,
        filename=insurance.source_filename,
        detail=detail,
    )
    insurance.updated_at = datetime.now(tz=UTC)

    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service

    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind="insurance",
        document_id=insurance_id,
        status=ProcessingAttemptStatus.failed,
        llm_call_id=llm_call_id,
        error_message=insurance.error_message,
        error_code=error_code.value,
    )


def _insurance_to_read(insurance: Insurance) -> InsuranceRead:
    return InsuranceRead(
        id=insurance.id,
        status=insurance.status.value,
        aseguradora=insurance.aseguradora,
        numero_poliza=insurance.numero_poliza,
        tomador=insurance.tomador,
        cif_nif=insurance.cif_nif,
        tipo_seguro=insurance.tipo_seguro,
        fecha_inicio=insurance.fecha_inicio,
        fecha_fin=insurance.fecha_fin,
        prima=insurance.prima,
        currency=insurance.currency,
        cobertura=insurance.cobertura,
        confidence=insurance.confidence,
        source_filename=insurance.source_filename,
    )


def _insurance_search_conditions(
    tenant_id: UUID,
    filters: DocumentSearchFilters,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Insurance.tenant_id == tenant_id]

    if filters.fecha_from is not None:
        conditions.append(Insurance.fecha_inicio >= filters.fecha_from)
    if filters.fecha_to is not None:
        conditions.append(Insurance.fecha_inicio <= filters.fecha_to)
    if filters.total_min is not None:
        conditions.append(Insurance.prima >= filters.total_min)
    if filters.total_max is not None:
        conditions.append(Insurance.prima <= filters.total_max)
    if filters.status:
        statuses = [InsuranceStatus(s) for s in filters.status]
        conditions.append(Insurance.status.in_(statuses))
    if filters.cif_nif:
        pattern = ilike_pattern(filters.cif_nif)
        conditions.append(
            func.lower(func.coalesce(Insurance.cif_nif, "")).like(pattern, escape="\\"),
        )
    if filters.numero_poliza:
        pattern = ilike_pattern(filters.numero_poliza)
        conditions.append(
            func.lower(func.coalesce(Insurance.numero_poliza, "")).like(
                pattern,
                escape="\\",
            ),
        )
    if filters.tipo_seguro:
        pattern = ilike_pattern(filters.tipo_seguro)
        conditions.append(
            func.lower(func.coalesce(Insurance.tipo_seguro, "")).like(pattern, escape="\\"),
        )
    if filters.aseguradora_query:
        pattern = ilike_pattern(filters.aseguradora_query)
        normalized = normalize_search_text(filters.aseguradora_query)
        aseg_col = func.unaccent(func.coalesce(Insurance.aseguradora, ""))
        conditions.append(
            or_(
                func.lower(aseg_col).like(pattern, escape="\\"),
                func.similarity(aseg_col, literal(normalized)) >= 0.2,
            ),
        )
    if filters.text_query:
        pattern = ilike_pattern(filters.text_query)
        normalized = normalize_search_text(filters.text_query)
        aseg_col = func.unaccent(func.coalesce(Insurance.aseguradora, ""))
        tomador_col = func.unaccent(func.coalesce(Insurance.tomador, ""))
        filename_col = func.lower(func.coalesce(Insurance.source_filename, ""))
        poliza_col = func.lower(func.coalesce(Insurance.numero_poliza, ""))
        conditions.append(
            or_(
                func.lower(aseg_col).like(pattern, escape="\\"),
                func.lower(tomador_col).like(pattern, escape="\\"),
                filename_col.like(pattern, escape="\\"),
                poliza_col.like(pattern, escape="\\"),
                func.similarity(aseg_col, literal(normalized)) >= 0.2,
            ),
        )
    return conditions


async def search_insurances(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[InsuranceRead]:
    conditions = _insurance_search_conditions(tenant_id, filters)
    count_stmt = select(func.count()).select_from(Insurance).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = (
        select(Insurance)
        .where(*conditions)
        .order_by(Insurance.fecha_inicio.desc().nulls_last(), Insurance.created_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    result = await db.execute(stmt)
    insurances = result.scalars().all()
    return Page(
        items=[_insurance_to_read(i) for i in insurances],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


async def get_insurance_detail(
    db: AsyncSession,
    tenant_id: UUID,
    insurance_id: UUID,
) -> InsuranceRead:
    insurance = await get_insurance(db, tenant_id, insurance_id)
    return _insurance_to_read(insurance)


async def aggregate_insurances(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    conditions = _insurance_search_conditions(tenant_id, filters)
    metric_expr = (
        func.count()
        if metric == AggregateMetric.metric_count
        else func.coalesce(func.sum(Insurance.prima), 0)
    )

    if group_by == AggregateGroupBy.none:
        stmt = select(metric_expr).select_from(Insurance).where(*conditions)
        raw = (await db.execute(stmt)).scalar_one()
        if metric == AggregateMetric.metric_count:
            total_value: Decimal | int = int(raw or 0)
        else:
            total_value = Decimal(str(raw)) if raw is not None else Decimal("0")
        return AggregateResult(
            doc_type_code=DocTypeCode.seguro.value,
            metric=metric,
            group_by=group_by,
            total_value=total_value,
        )

    if group_by == AggregateGroupBy.aseguradora:
        group_key = func.lower(
            func.unaccent(func.coalesce(Insurance.aseguradora, "(sin aseguradora)")),
        ).label("group_key")
    elif group_by == AggregateGroupBy.month:
        group_key = func.to_char(Insurance.fecha_inicio, "YYYY-MM").label("group_key")
    elif group_by == AggregateGroupBy.year:
        group_key = func.to_char(Insurance.fecha_inicio, "YYYY").label("group_key")
    elif group_by == AggregateGroupBy.status:
        group_key = cast(Insurance.status, String).label("group_key")
    else:
        raise ValidationError(f"Unsupported group_by for insurances: {group_by.value}")

    stmt = (
        select(group_key, metric_expr)
        .select_from(Insurance)
        .where(*conditions)
        .group_by(group_key)
        .order_by(metric_expr.desc())
    )
    rows_result = await db.execute(stmt)
    rows = [
        AggregateRow(
            group_key=str(row.group_key or ""),
            value=int(row[1]) if metric == AggregateMetric.metric_count else row[1],
        )
        for row in rows_result.all()
    ]
    return AggregateResult(
        doc_type_code=DocTypeCode.seguro.value,
        metric=metric,
        group_by=group_by,
        rows=rows,
    )


async def list_aseguradoras(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
    limit: int = 50,
) -> list[str]:
    stmt = (
        select(Insurance.aseguradora)
        .where(
            Insurance.tenant_id == tenant_id,
            Insurance.aseguradora.is_not(None),
            Insurance.aseguradora != "",
        )
        .distinct()
        .order_by(Insurance.aseguradora)
        .limit(limit)
    )
    if query:
        pattern = ilike_pattern(query)
        aseg_col = func.unaccent(Insurance.aseguradora)
        stmt = stmt.where(func.lower(aseg_col).like(pattern, escape="\\"))
    result = await db.execute(stmt)
    return [str(row[0]) for row in result.all() if row[0]]
