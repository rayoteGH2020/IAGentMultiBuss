"""Schemas de tenant para UI admin (sin ORM en rutas)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from uuid import UUID


class TenantRead(BaseModel):
    """Proyección mínima de tenant para listados y detalle admin."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    plan: str
    clerk_org_id: str | None = None
