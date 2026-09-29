"""Schemas de membership y permisos (Paso 30 Fase B)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.scheduling_defaults import DEFAULT_MEMBERSHIP_PERMISSIONS

AppRole = Literal["admin", "co_admin", "member", "viewer"]


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


_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ]{5,19}$")


class TenantMemberUpdate(BaseModel):
    """Permisos de app (citas) y teléfono. Identidad/rol viven en Clerk.

    ``phone`` solo se aplica si viene en la petición (``model_fields_set``);
    vacío lo borra. Solo el admin puede cambiarlo (lo impone el servicio).
    """

    model_config = ConfigDict(extra="forbid")

    permissions: MembershipPermissions
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = " ".join(value.split())
        if not clean:
            return None
        if not _PHONE_RE.match(clean):
            raise ValueError("phone must contain digits, spaces and an optional leading +")
        return clean


class TenantMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: UUID
    user_id: UUID
    email: str
    name: str | None
    phone: str | None = None
    role: str
    permissions: MembershipPermissions
    clerk_user_id: str | None = None
    # Baja solicitada al SADM pendiente de ejecutar (None = sin solicitud).
    removal_requested_at: datetime | None = None
    removal_effective_date: date | None = None

    @property
    def removal_pending(self) -> bool:
        return self.removal_effective_date is not None


RequestableRole = Literal["co_admin", "member"]

# Validación de forma, no de entregabilidad: Clerk verifica el email al invitar.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _single_line(value: str) -> str:
    """Colapsa espacios y saltos de línea (los valores van a un email en texto plano)."""
    return " ".join(value.split())


class MemberCreationRequest(BaseModel):
    """Datos del nuevo miembro que se solicita al SADM (no crea nada en Clerk)."""

    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=150)
    alias: str | None = Field(default=None, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    role: RequestableRole
    start_date: date

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def _clean_name(cls, value: object) -> object:
        return _single_line(value) if isinstance(value, str) else value

    @field_validator("alias", mode="before")
    @classmethod
    def _clean_alias(cls, value: object) -> object:
        if isinstance(value, str):
            return _single_line(value) or None
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValueError("invalid email")
        return email


class MemberCreationForm(BaseModel):
    """Contexto del modal de solicitud de alta (solicitante y rango de fechas)."""

    actor_name: str | None
    actor_email: str
    actor_role: str
    min_date: date
    max_date: date


class MemberRemovalForm(BaseModel):
    """Contexto del modal de solicitud de baja (miembro, solicitante y rango de fechas)."""

    member: TenantMemberRead
    actor_name: str | None
    actor_email: str
    actor_role: str
    min_date: date
    max_date: date
