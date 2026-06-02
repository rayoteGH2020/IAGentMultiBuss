"""Tests unitarios del schema VoiceEventDraft (Paso 23)."""

from __future__ import annotations

import pytest
from app.schemas.calendar import CalendarEventCreate, VoiceEventDraft
from pydantic import ValidationError


def _make_draft(**kwargs: object) -> VoiceEventDraft:
    defaults: dict[str, object] = {
        "transcript": "Reunión con Juan mañana a las cinco",
        "summary": "Reunión con Juan",
        "start": "2025-06-02T17:00:00+02:00",
        "end": "2025-06-02T18:00:00+02:00",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    return VoiceEventDraft(**defaults)


def test_to_event_create_maps_fields() -> None:
    draft = _make_draft(description="Sala 3")
    event = draft.to_event_create()
    assert isinstance(event, CalendarEventCreate)
    assert event.summary == "Reunión con Juan"
    assert event.start == "2025-06-02T17:00:00+02:00"
    assert event.end == "2025-06-02T18:00:00+02:00"
    assert event.description == "Sala 3"


def test_to_event_create_reminders_is_none() -> None:
    """to_event_create() nunca incluye reminders: los inyecta voice_event_service."""
    draft = _make_draft()
    event = draft.to_event_create()
    assert event.reminders is None


def test_to_event_create_no_description() -> None:
    draft = _make_draft()
    event = draft.to_event_create()
    assert event.description is None


def test_all_day_default_is_false() -> None:
    draft = _make_draft()
    assert draft.all_day is False


def test_all_day_true() -> None:
    draft = _make_draft(all_day=True)
    assert draft.all_day is True


def test_needs_clarification_default() -> None:
    draft = _make_draft()
    assert draft.needs_clarification is False


def test_needs_clarification_with_reason() -> None:
    draft = _make_draft(needs_clarification=True, clarification_reason="Fecha ambigua")
    assert draft.needs_clarification is True
    assert draft.clarification_reason == "Fecha ambigua"


def test_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        _make_draft(confidence=1.1)
    with pytest.raises(ValidationError):
        _make_draft(confidence=-0.1)


def test_summary_max_length() -> None:
    with pytest.raises(ValidationError):
        _make_draft(summary="x" * 501)


def test_calendar_event_create_reminders_optional() -> None:
    """CalendarEventCreate acepta reminders None y lista de dicts."""
    event_no_reminders = CalendarEventCreate(
        summary="Test",
        start="2025-06-02T10:00:00+02:00",
        end="2025-06-02T11:00:00+02:00",
    )
    assert event_no_reminders.reminders is None

    event_with_reminders = CalendarEventCreate(
        summary="Test",
        start="2025-06-02T10:00:00+02:00",
        end="2025-06-02T11:00:00+02:00",
        reminders=[{"method": "popup", "minutes": 60}],
    )
    assert event_with_reminders.reminders == [{"method": "popup", "minutes": 60}]
