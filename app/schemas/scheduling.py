"""Schemas de scheduling — Paso 30 Fase B."""

from __future__ import annotations

import enum
import re
from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.scheduling_granularity import DEFAULT_SLOT_GRANULARITY_MINUTES

MIN_SERVICE_DURATION = 15
MAX_SERVICE_DURATION = 240
DEFAULT_PROFESSIONAL_COLOR = "#6366f1"
_PROFESSIONAL_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Paleta fija (~30) para el selector de profesionales: sin RGB libre.
PROFESSIONAL_COLOR_PALETTE: tuple[str, ...] = (
    "#ef4444",
    "#f97316",
    "#f59e0b",
    "#eab308",
    "#84cc16",
    "#22c55e",
    "#10b981",
    "#14b8a6",
    "#06b6d4",
    "#0ea5e9",
    "#3b82f6",
    "#6366f1",
    "#8b5cf6",
    "#a855f7",
    "#d946ef",
    "#ec4899",
    "#f43f5e",
    "#fb7185",
    "#fdba74",
    "#fde047",
    "#86efac",
    "#5eead4",
    "#7dd3fc",
    "#a5b4fc",
    "#c4b5fd",
    "#f0abfc",
    "#78716c",
    "#64748b",
    "#334155",
    "#171717",
)


def validate_professional_color(value: str) -> str:
    """Valida color hex #RRGGBB (escritura)."""
    candidate = value.strip()
    if not _PROFESSIONAL_COLOR_RE.fullmatch(candidate):
        raise ValueError("color must be a 6-digit hex value like #6366f1")
    return candidate.lower()


def sanitize_professional_color(
    value: str | None,
    *,
    default: str = DEFAULT_PROFESSIONAL_COLOR,
) -> str:
    """Devuelve un hex seguro para CSS o el default si el valor es inválido."""
    if isinstance(value, str):
        candidate = value.strip()
        if _PROFESSIONAL_COLOR_RE.fullmatch(candidate):
            return candidate.lower()
    return default


def resolve_palette_color(value: str | None) -> str:
    """Elige un color de la paleta (el actual si está, si no el default)."""
    sanitized = sanitize_professional_color(value)
    if sanitized in PROFESSIONAL_COLOR_PALETTE:
        return sanitized
    return DEFAULT_PROFESSIONAL_COLOR


def build_professional_color_swatches(
    *,
    selected_color: str,
    taken_colors: set[str] | list[str] | tuple[str, ...],
) -> list[dict[str, object]]:
    """Paleta 6x5 con flags explícitos (evita `hex in taken` frágil en Jinja).

    ``taken_colors`` = colores de *otros* profesionales (el actual ya excluido).
    """
    selected = resolve_palette_color(selected_color)
    taken_normalized = {
        resolve_palette_color(color) if isinstance(color, str) else DEFAULT_PROFESSIONAL_COLOR
        for color in taken_colors
    }
    # El color del profesional en edición no se marca como ocupado.
    taken_normalized.discard(selected)
    return [
        {
            "hex": color,
            "is_taken": color in taken_normalized,
            "is_selected": color == selected,
        }
        for color in PROFESSIONAL_COLOR_PALETTE
    ]


class AppointmentStatus(enum.StrEnum):
    scheduled = "scheduled"
    confirmed = "confirmed"
    cancelled = "cancelled"
    completed = "completed"
    no_show = "no_show"


class TimeSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opens_at: time | None = None
    closes_at: time | None = None


class BusinessHourRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    weekday: int
    sort_order: int
    opens_at: time | None
    closes_at: time | None


class BusinessHourSlotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    sort_order: int = Field(ge=0, le=3)
    opens_at: time | None = None
    closes_at: time | None = None

    @model_validator(mode="after")
    def _validate_slot(self) -> BusinessHourSlotUpdate:
        if (self.opens_at is None) != (self.closes_at is None):
            raise ValueError("opens_at and closes_at must both be set or both null")
        if self.opens_at and self.closes_at and self.opens_at >= self.closes_at:
            raise ValueError("opens_at must be before closes_at")
        return self


class BusinessHoursUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[BusinessHourSlotUpdate]


class ProfessionalWorkingHourRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    weekday: int
    sort_order: int
    opens_at: time | None
    closes_at: time | None


class ProfessionalWorkingHourSlotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    sort_order: int = Field(ge=0, le=3)
    opens_at: time | None = None
    closes_at: time | None = None

    @model_validator(mode="after")
    def _validate_slot(self) -> ProfessionalWorkingHourSlotUpdate:
        if (self.opens_at is None) != (self.closes_at is None):
            raise ValueError("opens_at and closes_at must both be set or both null")
        if self.opens_at and self.closes_at and self.opens_at >= self.closes_at:
            raise ValueError("opens_at must be before closes_at")
        return self


class ProfessionalWorkingHoursUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[ProfessionalWorkingHourSlotUpdate]


class ScheduleExceptionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exception_date: date
    label: str | None = Field(default=None, max_length=255)
    is_closed: bool = True


class ScheduleExceptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exception_date: date
    label: str | None
    is_closed: bool


class TenantSchedulingSettingsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Madrid"
    search_horizon_days: int = Field(default=14, ge=1, le=90)
    slot_granularity_minutes: int = Field(default=DEFAULT_SLOT_GRANULARITY_MINUTES, ge=5, le=60)
    buffer_minutes: int = Field(default=10, ge=0, le=15)


class TenantSchedulingSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str | None = None
    search_horizon_days: int | None = Field(default=None, ge=1, le=90)
    slot_granularity_minutes: int | None = Field(default=None, ge=5, le=60)
    buffer_minutes: int | None = Field(default=None, ge=0, le=15)


class SchedulingServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=128)
    duration_minutes: int = Field(ge=MIN_SERVICE_DURATION, le=MAX_SERVICE_DURATION)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    sort_order: int = 0


class SchedulingServiceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=128)
    duration_minutes: int | None = Field(
        default=None, ge=MIN_SERVICE_DURATION, le=MAX_SERVICE_DURATION
    )
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    sort_order: int | None = None


class SchedulingServiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    duration_minutes: int
    notes: str | None = None
    is_active: bool
    sort_order: int


class ProfessionalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=255)
    user_id: UUID | None = None
    color: str = Field(default=DEFAULT_PROFESSIONAL_COLOR, max_length=7)
    is_active: bool = True
    is_bookable: bool = True
    sort_order: int = 0
    specialty_service_ids: list[UUID] = Field(default_factory=list, max_length=3)

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_PROFESSIONAL_COLOR
        return validate_professional_color(str(value))


class ProfessionalUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    user_id: UUID | None = None
    color: str | None = Field(default=None, max_length=7)
    is_active: bool | None = None
    is_bookable: bool | None = None
    sort_order: int | None = None
    specialty_service_ids: list[UUID] | None = Field(default=None, max_length=3)

    @field_validator("color", mode="before")
    @classmethod
    def _validate_optional_color(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return validate_professional_color(str(value))


class ProfessionalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    user_id: UUID | None
    color: str
    is_active: bool
    is_bookable: bool
    sort_order: int
    specialty_service_ids: list[UUID] = Field(default_factory=list)
    specialty_names: list[str] = Field(default_factory=list)

    @field_validator("color", mode="before")
    @classmethod
    def _sanitize_color_from_db(cls, value: object) -> str:
        if isinstance(value, str):
            return sanitize_professional_color(value)
        return DEFAULT_PROFESSIONAL_COLOR


class AppointmentPayloadBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: UUID | None = None
    professional_id: UUID | None = None
    start_at: datetime
    duration_minutes: int | None = Field(
        default=None, ge=MIN_SERVICE_DURATION, le=MAX_SERVICE_DURATION
    )
    client_name: str = Field(min_length=1, max_length=255)
    client_phone: str = Field(min_length=1, max_length=64)
    client_email: str | None = Field(default=None, max_length=255)
    notes: str | None = None

    @field_validator("start_at")
    @classmethod
    def _start_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("start_at must be timezone-aware")
        return value


class AppointmentCreate(AppointmentPayloadBase):
    pass


class AppointmentUpdate(AppointmentPayloadBase):
    pass


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID | None
    professional_id: UUID | None
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    client_name: str
    client_phone: str
    client_email: str | None
    notes: str | None
    source: str
    created_by_user_id: UUID | None
    cancelled_at: datetime | None
    cancellation_reason: str | None


class AppointmentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AppointmentStatus


class AppointmentCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancellation_reason: str | None = None


class FindSlotsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    after: datetime
    professional_id: UUID | None = None
    count: int = Field(default=3, ge=1, le=10)

    @field_validator("after")
    @classmethod
    def _after_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("after must be timezone-aware")
        return value


class AvailableSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    professional_id: UUID
    professional_name: str
    start: datetime
    end: datetime
    service_id: UUID
    service_name: str


class FindSlotsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: list[AvailableSlot]
    same_start_time_warning: bool = False


class ReassignAppointmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_professional_id: UUID
    appointment_ids: list[UUID] = Field(min_length=1)
