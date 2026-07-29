"""Catálogo de servicios del centro (Paso 30 Fase B)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.models.scheduling_service import SchedulingService
from app.schemas.scheduling import (
    SchedulingServiceCreate,
    SchedulingServiceRead,
    SchedulingServiceUpdate,
)
from app.services import audit_service

if TYPE_CHECKING:
    from app.services.audit_service import AuditRequestContext

ACTION_SERVICE_CREATED = "scheduling.service_created"
ACTION_SERVICE_UPDATED = "scheduling.service_updated"
RESOURCE_SERVICE = "scheduling_service"


def slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    slug = slug.strip("-")
    return (slug or "service")[:128]


async def list_services(db: AsyncSession, tenant_id: UUID) -> list[SchedulingServiceRead]:
    result = await db.execute(
        select(SchedulingService)
        .where(SchedulingService.tenant_id == tenant_id)
        .order_by(SchedulingService.sort_order, SchedulingService.name)
    )
    return [SchedulingServiceRead.model_validate(row) for row in result.scalars().all()]


async def get_service(
    db: AsyncSession,
    tenant_id: UUID,
    service_id: UUID,
) -> SchedulingService:
    result = await db.execute(
        select(SchedulingService).where(
            SchedulingService.id == service_id,
            SchedulingService.tenant_id == tenant_id,
        )
    )
    service = result.scalar_one_or_none()
    if service is None:
        raise NotFoundError("Service not found")
    return service


async def create_service(
    db: AsyncSession,
    tenant_id: UUID,
    payload: SchedulingServiceCreate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> SchedulingServiceRead:
    slug = payload.slug or slugify_name(payload.name)
    service = SchedulingService(
        tenant_id=tenant_id,
        name=payload.name,
        slug=slug,
        duration_minutes=payload.duration_minutes,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    db.add(service)
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_SERVICE_CREATED,
        resource_type=RESOURCE_SERVICE,
        resource_id=service.id,
        metadata={"name": payload.name, "slug": slug},
        request_ctx=request_ctx,
    )
    return SchedulingServiceRead.model_validate(service)


async def update_service(
    db: AsyncSession,
    tenant_id: UUID,
    service_id: UUID,
    payload: SchedulingServiceUpdate,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> SchedulingServiceRead:
    service = await get_service(db, tenant_id, service_id)
    if payload.name is not None:
        service.name = payload.name
    if payload.slug is not None:
        service.slug = payload.slug
    elif payload.name is not None:
        service.slug = slugify_name(payload.name)
    if payload.duration_minutes is not None:
        service.duration_minutes = payload.duration_minutes
    if payload.is_active is not None:
        service.is_active = payload.is_active
    if payload.sort_order is not None:
        service.sort_order = payload.sort_order
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_SERVICE_UPDATED,
        resource_type=RESOURCE_SERVICE,
        resource_id=service.id,
        request_ctx=request_ctx,
    )
    return SchedulingServiceRead.model_validate(service)


async def require_active_service(
    db: AsyncSession,
    tenant_id: UUID,
    service_id: UUID,
) -> SchedulingService:
    service = await get_service(db, tenant_id, service_id)
    if not service.is_active:
        raise ValidationError("Service is inactive")
    return service
