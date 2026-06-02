"""Tests unitarios de app/llm/voice_calendar.py (Paso 23).

Mockea LLMClient para verificar que draft_event_from_transcript inyecta
correctamente now_iso/timezone en el prompt y devuelve _VoiceEventExtraction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.llm.client import LLMCompleteResult
from app.llm.voice_calendar import draft_event_from_transcript
from app.schemas.calendar import _VoiceEventExtraction

pytestmark = pytest.mark.asyncio

TENANT_ID = uuid4()


def _extraction(**kwargs: object) -> _VoiceEventExtraction:
    defaults: dict[str, object] = {
        "summary": "Reunión de equipo",
        "start": "2025-06-03T17:30:00+02:00",
        "end": "2025-06-03T18:30:00+02:00",
        "confidence": 0.9,
    }
    defaults.update(kwargs)
    return _VoiceEventExtraction(**defaults)


async def test_draft_event_from_transcript_returns_extraction() -> None:
    """draft_event_from_transcript devuelve la extracción del LLM."""
    expected = _extraction()
    mock_result = LLMCompleteResult(result=expected, llm_call_id=uuid4())
    db = AsyncMock()

    with patch("app.llm.voice_calendar.get_llm_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=mock_result)
        mock_get_client.return_value = mock_client

        result = await draft_event_from_transcript(
            "Reunión de equipo mañana a las cinco y media",
            now_iso="2025-06-02T10:00:00+02:00",
            timezone="Europe/Madrid",
            default_duration_min=60,
            tenant_id=TENANT_ID,
            db=db,
        )

    assert result.summary == "Reunión de equipo"
    assert result.start == "2025-06-03T17:30:00+02:00"
    assert result.confidence == 0.9


async def test_draft_event_from_transcript_injects_temporal_context() -> None:
    """El prompt renderizado contiene now_iso y timezone."""
    expected = _extraction()
    mock_result = LLMCompleteResult(result=expected, llm_call_id=uuid4())
    db = AsyncMock()
    captured_messages: list[list[dict[str, object]]] = []

    async def fake_complete(**kwargs: object) -> LLMCompleteResult[_VoiceEventExtraction]:
        captured_messages.append(kwargs["messages"])
        return mock_result

    with patch("app.llm.voice_calendar.get_llm_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.complete = fake_complete
        mock_get_client.return_value = mock_client

        await draft_event_from_transcript(
            "Cita con el médico el viernes a las cuatro",
            now_iso="2025-06-02T09:00:00+02:00",
            timezone="Europe/Madrid",
            default_duration_min=30,
            tenant_id=TENANT_ID,
            db=db,
        )

    assert len(captured_messages) == 1
    content = str(captured_messages[0])
    assert "2025-06-02T09:00:00+02:00" in content
    assert "Europe/Madrid" in content
    assert "30" in content


async def test_draft_event_from_transcript_uses_classify_task() -> None:
    """draft_event_from_transcript usa task='classify' (Claude Haiku)."""
    expected = _extraction()
    mock_result = LLMCompleteResult(result=expected, llm_call_id=uuid4())
    db = AsyncMock()
    captured_tasks: list[str] = []

    async def fake_complete(**kwargs: object) -> LLMCompleteResult[_VoiceEventExtraction]:
        captured_tasks.append(str(kwargs.get("task")))
        return mock_result

    with patch("app.llm.voice_calendar.get_llm_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.complete = fake_complete
        mock_get_client.return_value = mock_client

        await draft_event_from_transcript(
            "Comida con clientes el lunes",
            now_iso="2025-06-02T08:00:00+02:00",
            timezone="Europe/Madrid",
            default_duration_min=60,
            tenant_id=TENANT_ID,
            db=db,
        )

    assert captured_tasks == ["classify"]


async def test_draft_event_needs_clarification() -> None:
    """Si el LLM marca needs_clarification, el campo se preserva en el resultado."""
    expected = _extraction(
        needs_clarification=True,
        clarification_reason="No se especificó la hora",
        confidence=0.3,
    )
    mock_result = LLMCompleteResult(result=expected, llm_call_id=uuid4())
    db = AsyncMock()

    with patch("app.llm.voice_calendar.get_llm_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.complete = AsyncMock(return_value=mock_result)
        mock_get_client.return_value = mock_client

        result = await draft_event_from_transcript(
            "Quedar con alguien un día de estos",
            now_iso="2025-06-02T10:00:00+02:00",
            timezone="Europe/Madrid",
            default_duration_min=60,
            tenant_id=TENANT_ID,
            db=db,
        )

    assert result.needs_clarification is True
    assert result.clarification_reason == "No se especificó la hora"
    assert result.confidence == 0.3
