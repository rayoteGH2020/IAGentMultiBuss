"""Contratos — listado, creación y persistencia de extracción LLM."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import ColumnElement, String, cast, func, literal, or_, select
from sqlalchemy.orm import selectinload

from app.core.document_processing_errors import DocumentErrorCode, failure_message
from app.core.errors import NotFoundError, ValidationError
from app.core.keys import contract_key
from app.core.storage import get_storage
from app.core.text_normalization import ilike_pattern, normalize_search_text
from app.core.uploads import original_upload_filename
from app.models import Contract, ContractStatus, DocTypeCode
from app.schemas.document_query import (
    AggregateGroupBy,
    AggregateMetric,
    AggregateResult,
    AggregateRow,
    ContractRead,
    DocumentSearchFilters,
)
from app.schemas.pagination import Page
from app.services import doc_type_service

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.schemas.contract import ContratoDocumento

logger = structlog.get_logger(__name__)


async def list_contracts(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    status: ContractStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Contract]:
    stmt = (
        select(Contract)
        .where(Contract.tenant_id == tenant_id, Contract.dismissed_at.is_(None))
        .order_by(Contract.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        stmt = stmt.where(Contract.status == status)
    stmt = stmt.options(selectinload(Contract.llm_call), selectinload(Contract.doc_type))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_contract(
    db: AsyncSession,
    tenant_id: UUID,
    contract_id: UUID,
) -> Contract:
    stmt = (
        select(Contract)
        .where(Contract.tenant_id == tenant_id, Contract.id == contract_id)
        .options(selectinload(Contract.llm_call))
    )
    result = await db.execute(stmt)
    contract = result.scalar_one_or_none()
    if contract is None:
        raise NotFoundError(f"Contract {contract_id} not found")
    return contract


async def create_contract_stub(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    source_file_key: str,
    source_filename: str,
    source_mime: str,
    doc_type: DocTypeCode = DocTypeCode.contrato,
) -> Contract:
    doc_type_id = await doc_type_service.get_doc_type_id(db, doc_type)
    contract = Contract(
        tenant_id=tenant_id,
        doc_type_id=doc_type_id,
        status=ContractStatus.pending,
        source_file_key=source_file_key,
        source_filename=source_filename,
        source_mime=source_mime,
    )
    db.add(contract)
    await db.flush()
    logger.info("contract.created", contract_id=str(contract.id), tenant_id=str(tenant_id))
    return contract


async def create_contract_from_upload(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    filename: str,
    file_bytes: bytes,
    mime_type: str,
    doc_type: DocTypeCode = DocTypeCode.contrato,
) -> Contract:
    storage = get_storage()
    key = contract_key(tenant_id, filename)
    await storage.upload_bytes(key, file_bytes, content_type=mime_type)
    contract = await create_contract_stub(
        db,
        tenant_id,
        source_file_key=key,
        source_filename=original_upload_filename(filename),
        source_mime=mime_type,
        doc_type=doc_type,
    )
    contract.status = ContractStatus.processing
    await db.flush()
    return contract


async def apply_extraction_result(
    db: AsyncSession,
    *,
    contract: Contract,
    data: ContratoDocumento,
    llm_call_id: UUID,
) -> Contract:
    contract.llm_call_id = llm_call_id
    contract.titulo = data.titulo[:300]
    contract.numero_contrato = data.numero_contrato[:100] if data.numero_contrato else None
    contract.parte_contraria = data.parte_contraria[:300]
    contract.cif_nif = data.cif_nif[:50] if data.cif_nif else None
    contract.fecha_inicio = data.fecha_inicio
    contract.fecha_fin = data.fecha_fin
    contract.importe = data.importe
    contract.currency = data.currency[:3]
    contract.objeto = data.objeto
    contract.confidence = Decimal(str(data.confidence)).quantize(Decimal("0.01"))
    contract.raw_extraction = data.model_dump(mode="json")
    contract.status = ContractStatus.ready
    contract.updated_at = datetime.now(tz=UTC)
    contract.error_message = None
    contract.error_code = None
    await db.flush()
    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service

    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=contract.tenant_id,
        document_kind="contract",
        document_id=contract.id,
        status=ProcessingAttemptStatus.ok,
        llm_call_id=llm_call_id,
    )
    return contract


async def mark_failed(
    db: AsyncSession,
    *,
    contract_id: UUID,
    tenant_id: UUID,
    error: str,
    llm_call_id: UUID | None = None,
    error_code: DocumentErrorCode = DocumentErrorCode.extraction_failed,
    detail: str | None = None,
) -> None:
    contract = await get_contract(db, tenant_id, contract_id)
    contract.status = ContractStatus.failed
    if llm_call_id is not None:
        contract.llm_call_id = llm_call_id
    logger.warning(
        "contract.processing_failed",
        contract_id=str(contract_id),
        tenant_id=str(tenant_id),
        source_filename=contract.source_filename,
        error_code=error_code.value,
        technical_error=error[:2000],
    )
    contract.error_code = error_code.value
    contract.error_message = failure_message(
        error,
        error_code=error_code,
        filename=contract.source_filename,
        detail=detail,
    )
    contract.updated_at = datetime.now(tz=UTC)

    from app.models.document_processing_attempt import ProcessingAttemptStatus
    from app.services import document_processing_service

    await document_processing_service.finalize_processing_attempt(
        db,
        tenant_id=tenant_id,
        document_kind="contract",
        document_id=contract_id,
        status=ProcessingAttemptStatus.failed,
        llm_call_id=llm_call_id,
        error_message=contract.error_message,
        error_code=error_code.value,
    )


def _contract_to_read(contract: Contract) -> ContractRead:
    return ContractRead(
        id=contract.id,
        status=contract.status.value,
        titulo=contract.titulo,
        numero_contrato=contract.numero_contrato,
        parte_contraria=contract.parte_contraria,
        cif_nif=contract.cif_nif,
        fecha_inicio=contract.fecha_inicio,
        fecha_fin=contract.fecha_fin,
        importe=contract.importe,
        currency=contract.currency,
        objeto=contract.objeto,
        confidence=contract.confidence,
        source_filename=contract.source_filename,
    )


def _contract_search_conditions(
    tenant_id: UUID,
    filters: DocumentSearchFilters,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Contract.tenant_id == tenant_id]

    if filters.fecha_from is not None:
        conditions.append(Contract.fecha_inicio >= filters.fecha_from)
    if filters.fecha_to is not None:
        conditions.append(Contract.fecha_inicio <= filters.fecha_to)
    if filters.total_min is not None:
        conditions.append(Contract.importe >= filters.total_min)
    if filters.total_max is not None:
        conditions.append(Contract.importe <= filters.total_max)
    if filters.status:
        statuses = [ContractStatus(s) for s in filters.status]
        conditions.append(Contract.status.in_(statuses))
    if filters.cif_nif:
        pattern = ilike_pattern(filters.cif_nif)
        conditions.append(
            func.lower(func.coalesce(Contract.cif_nif, "")).like(pattern, escape="\\"),
        )
    if filters.numero_contrato:
        pattern = ilike_pattern(filters.numero_contrato)
        conditions.append(
            func.lower(func.coalesce(Contract.numero_contrato, "")).like(
                pattern,
                escape="\\",
            ),
        )
    if filters.parte_contraria_query:
        pattern = ilike_pattern(filters.parte_contraria_query)
        normalized = normalize_search_text(filters.parte_contraria_query)
        parte_col = func.unaccent(func.coalesce(Contract.parte_contraria, ""))
        conditions.append(
            or_(
                func.lower(parte_col).like(pattern, escape="\\"),
                func.similarity(parte_col, literal(normalized)) >= 0.2,
            ),
        )
    if filters.text_query:
        pattern = ilike_pattern(filters.text_query)
        normalized = normalize_search_text(filters.text_query)
        parte_col = func.unaccent(func.coalesce(Contract.parte_contraria, ""))
        titulo_col = func.unaccent(func.coalesce(Contract.titulo, ""))
        filename_col = func.lower(func.coalesce(Contract.source_filename, ""))
        numero_col = func.lower(func.coalesce(Contract.numero_contrato, ""))
        conditions.append(
            or_(
                func.lower(parte_col).like(pattern, escape="\\"),
                func.lower(titulo_col).like(pattern, escape="\\"),
                filename_col.like(pattern, escape="\\"),
                numero_col.like(pattern, escape="\\"),
                func.similarity(parte_col, literal(normalized)) >= 0.2,
            ),
        )
    return conditions


async def search_contracts(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
) -> Page[ContractRead]:
    conditions = _contract_search_conditions(tenant_id, filters)
    count_stmt = select(func.count()).select_from(Contract).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = (
        select(Contract)
        .where(*conditions)
        .order_by(Contract.fecha_inicio.desc().nulls_last(), Contract.created_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    result = await db.execute(stmt)
    contracts = result.scalars().all()
    return Page(
        items=[_contract_to_read(c) for c in contracts],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


async def get_contract_detail(
    db: AsyncSession,
    tenant_id: UUID,
    contract_id: UUID,
) -> ContractRead:
    contract = await get_contract(db, tenant_id, contract_id)
    return _contract_to_read(contract)


async def aggregate_contracts(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    filters: DocumentSearchFilters,
    metric: AggregateMetric,
    group_by: AggregateGroupBy,
) -> AggregateResult:
    conditions = _contract_search_conditions(tenant_id, filters)
    metric_expr = (
        func.count()
        if metric == AggregateMetric.metric_count
        else func.coalesce(func.sum(Contract.importe), 0)
    )

    if group_by == AggregateGroupBy.none:
        stmt = select(metric_expr).select_from(Contract).where(*conditions)
        raw = (await db.execute(stmt)).scalar_one()
        if metric == AggregateMetric.metric_count:
            total_value: Decimal | int = int(raw or 0)
        else:
            total_value = Decimal(str(raw)) if raw is not None else Decimal("0")
        return AggregateResult(
            doc_type_code=DocTypeCode.contrato.value,
            metric=metric,
            group_by=group_by,
            total_value=total_value,
        )

    if group_by == AggregateGroupBy.parte_contraria:
        group_key = func.lower(
            func.unaccent(func.coalesce(Contract.parte_contraria, "(sin parte)")),
        ).label("group_key")
    elif group_by == AggregateGroupBy.month:
        group_key = func.to_char(Contract.fecha_inicio, "YYYY-MM").label("group_key")
    elif group_by == AggregateGroupBy.year:
        group_key = func.to_char(Contract.fecha_inicio, "YYYY").label("group_key")
    elif group_by == AggregateGroupBy.status:
        group_key = cast(Contract.status, String).label("group_key")
    else:
        raise ValidationError(f"Unsupported group_by for contracts: {group_by.value}")

    stmt = (
        select(group_key, metric_expr)
        .select_from(Contract)
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
        doc_type_code=DocTypeCode.contrato.value,
        metric=metric,
        group_by=group_by,
        rows=rows,
    )


async def list_partes_contrarias(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    query: str | None = None,
    limit: int = 50,
) -> list[str]:
    stmt = (
        select(Contract.parte_contraria)
        .where(
            Contract.tenant_id == tenant_id,
            Contract.parte_contraria.is_not(None),
            Contract.parte_contraria != "",
        )
        .distinct()
        .order_by(Contract.parte_contraria)
        .limit(limit)
    )
    if query:
        pattern = ilike_pattern(query)
        parte_col = func.unaccent(Contract.parte_contraria)
        stmt = stmt.where(func.lower(parte_col).like(pattern, escape="\\"))
    result = await db.execute(stmt)
    return [str(row[0]) for row in result.all() if row[0]]
