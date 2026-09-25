"""Tests unitarios de calendar_tools (tools de citas para canales externos)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.llm.tools.calendar_tools import (
    CancelAppointmentArgs,
    CheckAvailabilityArgs,
    CreateAppointmentArgs,
    ListAppointmentsArgs,
    execute_cancel_appointment,
    execute_check_availability,
    execute_create_appointment,
    execute_list_appointments,
    register_calendar_tools,
)
from app.llm.tools.registry import ToolContext, ToolFamily, ToolRegistry
from app.schemas.calendar import CalendarEvent
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ctx() -> ToolContext:
    return ToolContext(db=AsyncMock(), tenant_id=uuid4())


def _sample_event(event_id: str = "evt1") -> CalendarEvent:
    return CalendarEvent(
        id=event_id,
        summary="Reunión",
        start="2024-06-10T10:00:00+02:00",
        end="2024-06-10T11:00:00+02:00",
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_check_availability_args_rejects_empty_time() -> None:
    with pytest.raises(ValidationError):
        CheckAvailabilityArgs(time_min="  ", time_max="2024-06-10T10:00:00+02:00")


def test_list_appointments_args_defaults() -> None:
    args = ListAppointmentsArgs()
    assert args.days_ahead == 7


def test_list_appointments_args_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ListAppointmentsArgs(days_ahead=0)
    with pytest.raises(ValidationError):
        ListAppointmentsArgs(days_ahead=61)


def test_create_appointment_rejects_empty_summary() -> None:
    with pytest.raises(ValidationError):
        CreateAppointmentArgs(
            summary="",
            start="2024-06-10T10:00:00+02:00",
            end="2024-06-10T11:00:00+02:00",
        )


def test_cancel_appointment_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        CancelAppointmentArgs(event_id="")


# ---------------------------------------------------------------------------
# execute_check_availability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_availability_free_slot() -> None:
    ctx = _ctx()
    args = CheckAvailabilityArgs(
        time_min="2024-06-10T09:00:00+02:00",
        time_max="2024-06-10T10:00:00+02:00",
    )
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.check_tenant_availability",
        new=AsyncMock(return_value=[]),
    ):
        result = await execute_check_availability(ctx, args)

    assert result.ok is True
    assert result.data["slot_is_free"] is True
    assert result.data["occupied_slots"] == []


@pytest.mark.asyncio
async def test_check_availability_occupied_slot() -> None:
    ctx = _ctx()
    args = CheckAvailabilityArgs(
        time_min="2024-06-10T10:00:00+02:00",
        time_max="2024-06-10T11:00:00+02:00",
    )
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.check_tenant_availability",
        new=AsyncMock(return_value=[_sample_event()]),
    ):
        result = await execute_check_availability(ctx, args)

    assert result.ok is True
    assert result.data["slot_is_free"] is False
    assert len(result.data["occupied_slots"]) == 1  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_check_availability_service_error_returns_ok_false() -> None:
    ctx = _ctx()
    args = CheckAvailabilityArgs(
        time_min="2024-06-10T10:00:00+02:00",
        time_max="2024-06-10T11:00:00+02:00",
    )
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.check_tenant_availability",
        new=AsyncMock(side_effect=RuntimeError("Google API error")),
    ):
        result = await execute_check_availability(ctx, args)

    assert result.ok is False
    assert result.error == "calendar_unavailable"


# ---------------------------------------------------------------------------
# execute_list_appointments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_appointments_returns_events() -> None:
    ctx = _ctx()
    args = ListAppointmentsArgs(days_ahead=3)
    events = [_sample_event("e1"), _sample_event("e2")]
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.list_tenant_upcoming_events",
        new=AsyncMock(return_value=events),
    ):
        result = await execute_list_appointments(ctx, args)

    assert result.ok is True
    assert result.data["total"] == 2
    assert result.data["days_ahead"] == 3


@pytest.mark.asyncio
async def test_list_appointments_service_error() -> None:
    ctx = _ctx()
    args = ListAppointmentsArgs()
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.list_tenant_upcoming_events",
        new=AsyncMock(side_effect=RuntimeError("no calendar")),
    ):
        result = await execute_list_appointments(ctx, args)

    assert result.ok is False
    assert result.error == "calendar_unavailable"


# ---------------------------------------------------------------------------
# execute_create_appointment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_appointment_success() -> None:
    ctx = _ctx()
    args = CreateAppointmentArgs(
        summary="Cita con Pedro",
        start="2024-06-10T10:00:00+02:00",
        end="2024-06-10T11:00:00+02:00",
        description="Consulta inicial",
    )
    created = _sample_event("new_evt")
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.create_tenant_calendar_event",
        new=AsyncMock(return_value=created),
    ):
        result = await execute_create_appointment(ctx, args)

    assert result.ok is True
    appt = result.data["appointment"]
    assert isinstance(appt, dict)
    assert appt["id"] == "new_evt"


@pytest.mark.asyncio
async def test_create_appointment_service_error() -> None:
    ctx = _ctx()
    args = CreateAppointmentArgs(
        summary="Cita",
        start="2024-06-10T10:00:00+02:00",
        end="2024-06-10T11:00:00+02:00",
    )
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.create_tenant_calendar_event",
        new=AsyncMock(side_effect=RuntimeError("API down")),
    ):
        result = await execute_create_appointment(ctx, args)

    assert result.ok is False
    assert result.error == "calendar_create_failed"


# ---------------------------------------------------------------------------
# execute_cancel_appointment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_appointment_success() -> None:
    ctx = _ctx()
    args = CancelAppointmentArgs(event_id="evt_to_delete")
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.cancel_tenant_calendar_event",
        new=AsyncMock(return_value=None),
    ):
        result = await execute_cancel_appointment(ctx, args)

    assert result.ok is True
    assert result.data["cancelled_event_id"] == "evt_to_delete"


@pytest.mark.asyncio
async def test_cancel_appointment_not_found() -> None:
    ctx = _ctx()
    args = CancelAppointmentArgs(event_id="ghost_evt")
    with patch(
        "app.llm.tools.calendar_tools.calendar_service.cancel_tenant_calendar_event",
        new=AsyncMock(side_effect=RuntimeError("not found")),
    ):
        result = await execute_cancel_appointment(ctx, args)

    assert result.ok is False
    assert result.error == "calendar_cancel_failed"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_register_calendar_tools_adds_four_tools() -> None:
    registry = ToolRegistry()
    register_calendar_tools(registry)
    names = {t.name for t in registry.list_definitions()}
    assert names == {
        "check_availability",
        "list_appointments",
        "create_appointment",
        "cancel_appointment",
    }


def test_all_calendar_tools_have_calendar_family() -> None:
    registry = ToolRegistry()
    register_calendar_tools(registry)
    for tool in registry.list_definitions():
        assert tool.family == ToolFamily.calendar


def test_build_channel_registry_includes_calendar_and_knowledge() -> None:
    from app.llm.tools.knowledge_tools import build_channel_registry

    registry = build_channel_registry()
    families = {t.family for t in registry.list_definitions()}
    assert ToolFamily.knowledge in families
    assert ToolFamily.calendar in families


def test_build_channel_registry_excludes_document_family() -> None:
    from app.llm.tools.knowledge_tools import build_channel_registry

    registry = build_channel_registry()
    for tool in registry.list_definitions():
        assert tool.family != ToolFamily.document
