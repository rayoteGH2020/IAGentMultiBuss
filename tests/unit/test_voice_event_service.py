"""Tests unitarios de voice_event_service (Paso 23)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from app.core.errors import NotFoundError, RateLimitError, ValidationError
from app.models.calendar_integration import CalendarIntegration, CalendarIntegrationStatus
from app.schemas.calendar import CalendarEventCreate, VoiceEventDraft
from app.services import voice_event_service
from app.services.voice_event_service import VOICE_REMINDERS

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TENANT_ID = uuid4()
USER_ID = uuid4()

# Bytes de audio mínimos pero con firma OGG válida para no ser rechazados por MIME.
_OGG_MAGIC = b"OggS" + b"\x00" * 100


def _active_integration() -> CalendarIntegration:
    integ = MagicMock(spec=CalendarIntegration)
    integ.status = CalendarIntegrationStatus.active.value
    return integ


def _db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    return db


def _redis() -> AsyncMock:
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.decrby = AsyncMock()
    return redis


def _draft() -> VoiceEventDraft:
    return VoiceEventDraft(
        transcript="Reunión mañana a las cinco",
        summary="Reunión",
        start="2025-06-03T17:00:00+02:00",
        end="2025-06-03T18:00:00+02:00",
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# draft_from_audio
# ---------------------------------------------------------------------------


async def test_draft_requires_active_integration() -> None:
    """Sin integración activa debe lanzar NotFoundError."""
    db = _db()
    with (
        patch(
            "app.services.voice_event_service.calendar_service.get_integration",
            AsyncMock(return_value=None),
        ),
        pytest.raises(NotFoundError),
    ):
        await voice_event_service.draft_from_audio(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            audio=_OGG_MAGIC,
            mime_type="audio/ogg",
            redis=_redis(),
        )


async def test_draft_requires_active_integration_revoked() -> None:
    """Integración revocada también lanza NotFoundError."""
    revoked = MagicMock(spec=CalendarIntegration)
    revoked.status = CalendarIntegrationStatus.revoked.value
    db = _db()
    with (
        patch(
            "app.services.voice_event_service.calendar_service.get_integration",
            AsyncMock(return_value=revoked),
        ),
        pytest.raises(NotFoundError),
    ):
        await voice_event_service.draft_from_audio(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            audio=_OGG_MAGIC,
            mime_type="audio/ogg",
            redis=_redis(),
        )


async def test_draft_rejects_oversized_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Audio mayor que voice_max_audio_bytes debe lanzar ValidationError."""
    monkeypatch.setenv("VOICE_MAX_AUDIO_BYTES", "10")
    from app.config import get_settings

    get_settings.cache_clear()

    db = _db()
    with (
        patch(
            "app.services.voice_event_service.calendar_service.get_integration",
            AsyncMock(return_value=_active_integration()),
        ),
        pytest.raises(ValidationError, match="too large"),
    ):
        await voice_event_service.draft_from_audio(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            audio=b"x" * 20,  # 20 bytes > 10 bytes límite
            mime_type="audio/ogg",
            redis=_redis(),
        )

    get_settings.cache_clear()


async def test_draft_rejects_bad_mime() -> None:
    """MIME no permitido: bytes sin firma de audio → ValidationError."""
    db = _db()
    with (
        patch(
            "app.services.voice_event_service.calendar_service.get_integration",
            AsyncMock(return_value=_active_integration()),
        ),
        pytest.raises(ValidationError),
    ):
        await voice_event_service.draft_from_audio(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            audio=b"this is plain text, not audio",
            mime_type="text/plain",
            redis=_redis(),
        )


async def test_rate_limit_blocks_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si Redis reporta count > max, debe lanzar RateLimitError."""
    monkeypatch.setenv("VOICE_RATE_LIMIT_PER_HOUR", "5")
    from app.config import get_settings

    get_settings.cache_clear()

    # Redis devuelve count=6 (> límite de 5)
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=6)
    redis.expire = AsyncMock()
    redis.decrby = AsyncMock()

    db = _db()
    with (
        patch(
            "app.services.voice_event_service.calendar_service.get_integration",
            AsyncMock(return_value=_active_integration()),
        ),
        patch(
            "app.services.voice_event_service.validate_voice_upload",
            return_value="audio/ogg",
        ),
        pytest.raises(RateLimitError),
    ):
        await voice_event_service.draft_from_audio(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            audio=_OGG_MAGIC,
            mime_type="audio/ogg",
            redis=redis,
        )

    get_settings.cache_clear()


async def test_draft_assembles_voiceeventdraft() -> None:
    """draft_from_audio debe devolver VoiceEventDraft con transcript del paso 5."""
    from app.schemas.calendar import _VoiceEventExtraction

    extraction = _VoiceEventExtraction(
        summary="Reunión",
        start="2025-06-03T17:00:00+02:00",
        end="2025-06-03T18:00:00+02:00",
        confidence=0.85,
    )
    db = _db()
    with (
        patch(
            "app.services.voice_event_service.calendar_service.get_integration",
            AsyncMock(return_value=_active_integration()),
        ),
        patch(
            "app.services.voice_event_service.validate_voice_upload",
            return_value="audio/ogg",
        ),
        patch(
            "app.services.voice_event_service._check_voice_rate_limit",
            AsyncMock(),
        ),
        patch(
            "app.services.voice_event_service.voice_calendar.transcribe_audio",
            AsyncMock(return_value="Reunión mañana a las cinco"),
        ),
        patch(
            "app.services.voice_event_service.voice_calendar.draft_event_from_transcript",
            AsyncMock(return_value=extraction),
        ),
        patch(
            "app.services.voice_event_service.audit_service.log_action",
            AsyncMock(),
        ),
    ):
        draft = await voice_event_service.draft_from_audio(
            db,
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            audio=_OGG_MAGIC,
            mime_type="audio/ogg",
            redis=_redis(),
        )

    assert isinstance(draft, VoiceEventDraft)
    assert draft.transcript == "Reunión mañana a las cinco"
    assert draft.summary == "Reunión"
    assert draft.confidence == 0.85


# ---------------------------------------------------------------------------
# confirm_event
# ---------------------------------------------------------------------------


async def test_confirm_calls_calendar_create() -> None:
    """confirm_event debe delegar en calendar_service.create_calendar_event."""
    from app.schemas.calendar import CalendarEvent

    created = CalendarEvent(
        id="evt-123",
        summary="Reunión",
        start="2025-06-03T17:00:00+02:00",
        end="2025-06-03T18:00:00+02:00",
        html_link="https://calendar.google.com/event?eid=xyz",
    )
    db = _db()
    event = CalendarEventCreate(
        summary="Reunión",
        start="2025-06-03T17:00:00+02:00",
        end="2025-06-03T18:00:00+02:00",
    )
    with (
        patch(
            "app.services.voice_event_service.calendar_service.create_calendar_event",
            AsyncMock(return_value=created),
        ) as mock_create,
        patch(
            "app.services.voice_event_service.audit_service.log_action",
            AsyncMock(),
        ),
    ):
        result = await voice_event_service.confirm_event(
            db, tenant_id=TENANT_ID, user_id=USER_ID, event=event
        )

    mock_create.assert_awaited_once()
    assert result.id == "evt-123"


async def test_confirm_always_sets_voice_reminders() -> None:
    """confirm_event sobreescribe reminders con VOICE_REMINDERS aunque llegue None."""
    from app.schemas.calendar import CalendarEvent

    created = CalendarEvent(
        id="evt-456",
        summary="Cita",
        start="2025-06-04T10:00:00+02:00",
        end="2025-06-04T11:00:00+02:00",
    )
    db = _db()
    # Evento sin reminders (como llega del formulario antes de confirm_event)
    event_no_reminders = CalendarEventCreate(
        summary="Cita",
        start="2025-06-04T10:00:00+02:00",
        end="2025-06-04T11:00:00+02:00",
        reminders=None,
    )
    captured_event: list[CalendarEventCreate] = []

    async def fake_create(
        db: object, tid: object, uid: object, ev: CalendarEventCreate
    ) -> CalendarEvent:
        captured_event.append(ev)
        return created

    with (
        patch(
            "app.services.voice_event_service.calendar_service.create_calendar_event",
            fake_create,
        ),
        patch(
            "app.services.voice_event_service.audit_service.log_action",
            AsyncMock(),
        ),
    ):
        await voice_event_service.confirm_event(
            db, tenant_id=TENANT_ID, user_id=USER_ID, event=event_no_reminders
        )

    assert len(captured_event) == 1
    assert captured_event[0].reminders == VOICE_REMINDERS
    assert captured_event[0].reminders == [
        {"method": "popup", "minutes": 1440},
        {"method": "popup", "minutes": 60},
    ]
