"""Tools de calendario para canales externos (WhatsApp, Telegram) — familia calendar."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.llm.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolFamily,
    ToolRegistry,
    ToolResult,
)
from app.services import calendar_service

_MAX_DAYS_AHEAD = 60
_SUMMARY_MAX = 300
_DESC_MAX = 1000


# ---------------------------------------------------------------------------
# Arg models
# ---------------------------------------------------------------------------


class CheckAvailabilityArgs(BaseModel):
    """Comprueba la disponibilidad del negocio en un rango de fecha/hora."""

    model_config = ConfigDict(extra="forbid")

    time_min: str = Field(
        description=(
            "Inicio del rango en ISO 8601 con offset (ej. 2024-06-10T09:00:00+02:00). Obligatorio."
        )
    )
    time_max: str = Field(
        description=(
            "Fin del rango en ISO 8601 con offset (ej. 2024-06-10T11:00:00+02:00). Obligatorio."
        )
    )

    @field_validator("time_min", "time_max")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("time value cannot be empty")
        return v.strip()


class ListAppointmentsArgs(BaseModel):
    """Lista las próximas citas del negocio."""

    model_config = ConfigDict(extra="forbid")

    days_ahead: int = Field(
        default=7,
        ge=1,
        le=_MAX_DAYS_AHEAD,
        description="Número de días vista (1-60). Por defecto 7.",
    )


class CreateAppointmentArgs(BaseModel):
    """Crea una nueva cita en el calendario del negocio."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        min_length=1,
        max_length=_SUMMARY_MAX,
        description="Título de la cita (ej. 'Cita con Ana García').",
    )
    start: str = Field(description="Inicio en ISO 8601 con offset (ej. 2024-06-10T10:00:00+02:00).")
    end: str = Field(description="Fin en ISO 8601 con offset (ej. 2024-06-10T11:00:00+02:00).")
    description: str | None = Field(
        default=None,
        max_length=_DESC_MAX,
        description="Notas opcionales (teléfono de contacto, motivo, etc.).",
    )

    @field_validator("start", "end")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("datetime value cannot be empty")
        return v.strip()


class CancelAppointmentArgs(BaseModel):
    """Cancela (elimina) una cita existente por su ID de Google Calendar."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(
        min_length=1,
        description="ID del evento de Google Calendar tal como aparece en list_appointments.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_to_dict(event: Any) -> dict[str, object]:
    return {
        "id": event.id,
        "summary": event.summary,
        "start": event.start,
        "end": event.end,
        "description": event.description,
    }


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


async def execute_check_availability(
    ctx: ToolContext,
    args: CheckAvailabilityArgs,
) -> ToolResult:
    try:
        events = await calendar_service.check_tenant_availability(
            ctx.db,
            ctx.tenant_id,
            time_min=args.time_min,
            time_max=args.time_max,
            user_id=ctx.user_id,
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            data={
                "error": str(exc)[:300],
                "note": "Error técnico al consultar el calendario. No indica falta de disponibilidad.",
            },
            citations=[],
            error="calendar_unavailable",
        )

    # All-day events (date-only strings, no "T") don't block specific time slots —
    # e.g. birthdays, holidays or reminders should not mark a time slot as busy.
    timed_events = [e for e in events if "T" in e.start]
    occupied = [_event_to_dict(e) for e in timed_events]
    return ToolResult(
        ok=True,
        data={
            "time_min": args.time_min,
            "time_max": args.time_max,
            "occupied_slots": occupied,
            "slot_is_free": len(occupied) == 0,
        },
        citations=[],
    )


async def execute_list_appointments(
    ctx: ToolContext,
    args: ListAppointmentsArgs,
) -> ToolResult:
    try:
        events = await calendar_service.list_tenant_upcoming_events(
            ctx.db,
            ctx.tenant_id,
            days_ahead=args.days_ahead,
            user_id=ctx.user_id,
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            data={"error": str(exc)[:300]},
            citations=[],
            error="calendar_unavailable",
        )

    return ToolResult(
        ok=True,
        data={
            "days_ahead": args.days_ahead,
            "total": len(events),
            "appointments": [_event_to_dict(e) for e in events],
        },
        citations=[],
    )


async def execute_create_appointment(
    ctx: ToolContext,
    args: CreateAppointmentArgs,
) -> ToolResult:
    from app.schemas.calendar import CalendarEventCreate

    event_data = CalendarEventCreate(
        summary=args.summary,
        description=args.description,
        start=args.start,
        end=args.end,
    )
    try:
        created = await calendar_service.create_tenant_calendar_event(
            ctx.db,
            ctx.tenant_id,
            event_data,
            user_id=ctx.user_id,
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            data={"error": str(exc)[:300]},
            citations=[],
            error="calendar_create_failed",
        )

    return ToolResult(
        ok=True,
        data={"appointment": _event_to_dict(created)},
        citations=[],
    )


async def execute_cancel_appointment(
    ctx: ToolContext,
    args: CancelAppointmentArgs,
) -> ToolResult:
    try:
        await calendar_service.cancel_tenant_calendar_event(
            ctx.db,
            ctx.tenant_id,
            event_id=args.event_id,
            user_id=ctx.user_id,
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            data={"error": str(exc)[:300]},
            citations=[],
            error="calendar_cancel_failed",
        )

    return ToolResult(
        ok=True,
        data={"cancelled_event_id": args.event_id},
        citations=[],
    )


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def register_calendar_tools(registry: ToolRegistry) -> None:
    """Registra las 4 tools de calendario (familia ``calendar``)."""
    registry.register(
        ToolDefinition(
            name="check_availability",
            family=ToolFamily.calendar,
            description=(
                "Comprueba si el negocio tiene hueco en un rango de fecha y hora concreto. "
                "Devuelve los eventos que ya ocupan ese rango y si el slot está libre. "
                "Úsala cuando el cliente pregunte por disponibilidad en una fecha/hora específica."
            ),
            parameters_model=CheckAvailabilityArgs,
            executor=execute_check_availability,
        ),
    )
    registry.register(
        ToolDefinition(
            name="list_appointments",
            family=ToolFamily.calendar,
            description=(
                "Lista las próximas citas del calendario del negocio. "
                "Úsala cuando el cliente quiera saber qué citas tiene confirmadas "
                "o necesite conocer el calendario próximo."
            ),
            parameters_model=ListAppointmentsArgs,
            executor=execute_list_appointments,
        ),
    )
    registry.register(
        ToolDefinition(
            name="create_appointment",
            family=ToolFamily.calendar,
            description=(
                "Crea una nueva cita en el calendario del negocio. "
                "Antes de llamar a esta tool, confirma siempre con el cliente: "
                "fecha, hora de inicio, hora de fin y nombre de contacto. "
                "Úsala solo cuando el cliente haya confirmado todos los datos."
            ),
            parameters_model=CreateAppointmentArgs,
            executor=execute_create_appointment,
        ),
    )
    registry.register(
        ToolDefinition(
            name="cancel_appointment",
            family=ToolFamily.calendar,
            description=(
                "Cancela (elimina) una cita existente por su ID. "
                "Antes de llamar a esta tool, confirma SIEMPRE con el cliente "
                "qué cita quiere cancelar (usa list_appointments si es necesario) "
                "y pide confirmación explícita antes de borrarla."
            ),
            parameters_model=CancelAppointmentArgs,
            executor=execute_cancel_appointment,
        ),
    )
