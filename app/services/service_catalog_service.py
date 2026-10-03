"""Catálogo de servicios del centro (Paso 30 Fase B)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, select
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
ACTION_SERVICE_DELETED = "scheduling.service_deleted"
RESOURCE_SERVICE = "scheduling_service"


def slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    slug = slug.strip("-")
    return (slug or "service")[:128]


def _normalize_notes(notes: str | None) -> str | None:
    if notes is None:
        return None
    cleaned = notes.strip()
    return cleaned or None


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
        name=payload.name.strip(),
        slug=slug,
        duration_minutes=payload.duration_minutes,
        notes=_normalize_notes(payload.notes),
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
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        service.name = data["name"].strip()
        if "slug" not in data:
            service.slug = slugify_name(service.name)
    if "slug" in data and data["slug"] is not None:
        service.slug = data["slug"]
    if "duration_minutes" in data and data["duration_minutes"] is not None:
        service.duration_minutes = data["duration_minutes"]
    if "notes" in data:
        service.notes = _normalize_notes(data["notes"])
    if "is_active" in data and data["is_active"] is not None:
        service.is_active = data["is_active"]
    if "sort_order" in data and data["sort_order"] is not None:
        service.sort_order = data["sort_order"]
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


async def delete_service(
    db: AsyncSession,
    tenant_id: UUID,
    service_id: UUID,
    *,
    user_id: UUID | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> None:
    """Elimina el servicio. Citas históricas quedan con service_id NULL (FK SET NULL)."""
    service = await get_service(db, tenant_id, service_id)
    name = service.name
    await db.execute(
        delete(SchedulingService).where(
            SchedulingService.id == service_id,
            SchedulingService.tenant_id == tenant_id,
        )
    )
    await db.flush()
    await audit_service.log_action(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=ACTION_SERVICE_DELETED,
        resource_type=RESOURCE_SERVICE,
        resource_id=service_id,
        metadata={"name": name},
        request_ctx=request_ctx,
    )


async def require_active_service(
    db: AsyncSession,
    tenant_id: UUID,
    service_id: UUID,
) -> SchedulingService:
    service = await get_service(db, tenant_id, service_id)
    if not service.is_active:
        raise ValidationError("Service is inactive")
    return service
