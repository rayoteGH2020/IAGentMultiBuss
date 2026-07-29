"""Schemas de tenant para UI admin (sin ORM en rutas)."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003 — Pydantic requiere UUID en runtime para model_validate

from pydantic import BaseModel, ConfigDict


class TenantRead(BaseModel):
    """Proyección mínima de tenant para listados y detalle admin."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    plan: str
    clerk_org_id: str | None = None
