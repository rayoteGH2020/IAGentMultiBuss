"""Schemas de membership y permisos (Paso 30 Fase B)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.scheduling_defaults import DEFAULT_MEMBERSHIP_PERMISSIONS

AppRole = Literal["admin", "member", "viewer"]


class AppointmentPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: bool = True
    create: bool = False
    edit: bool = False
    cancel: bool = False


class MembershipPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appointments: AppointmentPermissions = Field(default_factory=AppointmentPermissions)

    def to_json_dict(self) -> dict[str, object]:
        return self.model_dump()

    @classmethod
    def from_json_dict(cls, data: dict[str, object] | None) -> MembershipPermissions:
        if not data:
            return cls()
        raw_appts = data.get("appointments")
        if isinstance(raw_appts, dict):
            return cls(appointments=AppointmentPermissions.model_validate(raw_appts))
        return cls()


class TenantMemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    role: AppRole = "member"
    permissions: MembershipPermissions = Field(
        default_factory=lambda: MembershipPermissions.from_json_dict(DEFAULT_MEMBERSHIP_PERMISSIONS)
    )

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TenantMemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    role: AppRole | None = None
    permissions: MembershipPermissions | None = None


class TenantMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: UUID
    user_id: UUID
    email: str
    name: str | None
    role: str
    permissions: MembershipPermissions
    clerk_user_id: str | None = None
