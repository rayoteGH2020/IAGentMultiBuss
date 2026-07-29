"""Tests de integración de las rutas web de voz → Google Calendar (Paso 23).

Mockea el middleware de auth y la capa de servicio para evitar dependencias
externas (BD, Redis, LLM, Google Calendar API).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from app.core.csrf import CSRF_HEADER_NAME, generate_csrf_token
from app.models import Membership, Tenant, User
from app.schemas.calendar import CalendarEvent, VoiceEventDraft
from fastapi import Request
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_clerk_resolve(
    request: Request,
    *,
    user_sub: str,
    org_id: str,
    user_id: UUID,
    tenant_id: UUID,
) -> None:
    now = datetime.now(tz=UTC)
    user = User(
        clerk_user_id=user_sub,
        email=f"{user_sub}@test.local",
        name="Test",
        created_at=now,
        updated_at=now,
    )
    user.id = user_id
    tenant = Tenant(
        clerk_org_id=org_id,
        name="Test Org",
        plan="free",
        settings={},
        created_at=now,
        updated_at=now,
    )
    tenant.id = tenant_id
    membership = Membership(
        user_id=user_id,
        tenant_id=tenant_id,
        role="admin",
        created_at=now,
        updated_at=now,
    )
    membership.id = uuid4()
    request.state.user = user
    request.state.tenant = tenant
    request.state.membership = membership


def _mock_draft() -> VoiceEventDraft:
    return VoiceEventDraft(
        transcript="Reunión con el equipo mañana a las diez",
        summary="Reunión con el equipo",
        start="2025-06-03T10:00:00+02:00",
        end="2025-06-03T11:00:00+02:00",
        confidence=0.92,
    )


def _mock_created_event() -> CalendarEvent:
    return CalendarEvent(
        id="evt-test-001",
        summary="Reunión con el equipo",
        start="2025-06-03T10:00:00+02:00",
        end="2025-06-03T11:00:00+02:00",
        html_link="https://calendar.google.com/event?eid=test",
    )


# ---------------------------------------------------------------------------
# Fixture: cliente HTTP con auth y DB mockeados
# ---------------------------------------------------------------------------


@pytest.fixture
def voice_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    user_sub = f"user_{uuid4().hex[:12]}"
    org_id = f"org_{uuid4().hex[:12]}"
    user_id = uuid4()
    tenant_id = uuid4()

    async def fake_resolve(request: Request) -> None:
        _fake_clerk_resolve(
            request,
            user_sub=user_sub,
            org_id=org_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.deps import get_db, get_redis_dep
    from app.main import create_app

    app = create_app()

    async def mock_db():
        yield AsyncMock()

    async def mock_redis() -> AsyncMock:
        return AsyncMock()

    app.dependency_overrides[get_db] = mock_db
    app.dependency_overrides[get_redis_dep] = mock_redis

    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update(
        {CSRF_HEADER_NAME: generate_csrf_token(user_id=user_id, tenant_id=tenant_id)}
    )
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_voice_index_connected(voice_client: TestClient) -> None:
    """GET /calendar/voice muestra el grabador cuando hay integración activa."""
    with patch(
        "app.routes.web.calendar_voice.calendar_service.get_integration",
        AsyncMock(return_value=object()),  # objeto no-None → is_connected=True
    ):
        # Necesitamos que el objeto tenga .status
        from unittest.mock import MagicMock

        from app.models.calendar_integration import CalendarIntegrationStatus

        integ = MagicMock()
        integ.status = CalendarIntegrationStatus.active.value
        with patch(
            "app.routes.web.calendar_voice.calendar_service.get_integration",
            AsyncMock(return_value=integ),
        ):
            r = voice_client.get("/calendar/voice")

    assert r.status_code == 200
    assert "voice-container" in r.text


def test_voice_index_not_connected(voice_client: TestClient) -> None:
    """GET /calendar/voice muestra aviso cuando no hay integración."""
    with patch(
        "app.routes.web.calendar_voice.calendar_service.get_integration",
        AsyncMock(return_value=None),
    ):
        r = voice_client.get("/calendar/voice")

    assert r.status_code == 200
    assert "Conecta tu Google Calendar" in r.text


def test_transcribe_returns_confirmation_fragment(voice_client: TestClient) -> None:
    """POST /calendar/voice/transcribe devuelve el fragmento de confirmación."""
    draft = _mock_draft()
    with patch(
        "app.routes.web.calendar_voice.voice_event_service.draft_from_audio",
        AsyncMock(return_value=draft),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggS" + b"\x00" * 50, "audio/ogg")},
        )

    assert r.status_code == 200
    assert "Crear evento" in r.text
    assert draft.transcript in r.text


def test_transcribe_error_returns_recorder_with_message(voice_client: TestClient) -> None:
    """Si draft_from_audio falla, la ruta devuelve el grabador con mensaje de error."""
    from app.core.errors import ValidationError as AppValidationError

    with patch(
        "app.routes.web.calendar_voice.voice_event_service.draft_from_audio",
        AsyncMock(side_effect=AppValidationError("Audio demasiado grande")),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggS", "audio/ogg")},
        )

    assert r.status_code == 200
    assert "Audio demasiado grande" in r.text


def test_transcribe_rejects_oversized_audio_before_service(voice_client: TestClient) -> None:
    """La ruta corta la lectura si el audio supera el lÃ­mite configurado."""
    draft_mock = AsyncMock()
    settings = SimpleNamespace(voice_max_audio_bytes=4)

    with (
        patch("app.routes.web.calendar_voice.get_settings", return_value=settings),
        patch("app.routes.web.calendar_voice.voice_event_service.draft_from_audio", draft_mock),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggSxx", "audio/ogg")},
        )

    assert r.status_code == 200
    assert "Audio too large" in r.text
    draft_mock.assert_not_awaited()


def test_confirm_creates_event_and_returns_result(voice_client: TestClient) -> None:
    """POST /calendar/voice/confirm crea el evento y devuelve el fragmento de resultado."""
    created = _mock_created_event()
    with patch(
        "app.routes.web.calendar_voice.voice_event_service.confirm_event",
        AsyncMock(return_value=created),
    ):
        r = voice_client.post(
            "/calendar/voice/confirm",
            data={
                "summary": "Reunión con el equipo",
                "start": "2025-06-03T10:00",
                "end": "2025-06-03T11:00",
                "description": "",
            },
        )

    assert r.status_code == 200
    assert "Evento creado" in r.text
    assert "Dictar otro" in r.text


def test_voice_disabled_returns_friendly_message(
    voice_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con voice_calendar_enabled=False, transcribe devuelve el recorder con aviso."""
    from app.core.errors import ValidationError as AppValidationError

    with patch(
        "app.routes.web.calendar_voice.voice_event_service.draft_from_audio",
        AsyncMock(
            side_effect=AppValidationError(
                "La función de dictado por voz no está disponible en este momento."
            )
        ),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggS" + b"\x00" * 20, "audio/ogg")},
        )

    assert r.status_code == 200
    assert "no está disponible" in r.text


def test_transcribe_needs_clarification_shows_warning_banner(voice_client: TestClient) -> None:
    """Dictado ambiguo → banner de aviso; no crea evento hasta confirm."""
    draft = VoiceEventDraft(
        transcript="quedamos un día de estos",
        summary="Quedada",
        start="2025-06-03T10:00:00+02:00",
        end="2025-06-03T11:00:00+02:00",
        confidence=0.4,
        needs_clarification=True,
        clarification_reason="No se pudo determinar la fecha concreta.",
    )
    with patch(
        "app.routes.web.calendar_voice.voice_event_service.draft_from_audio",
        AsyncMock(return_value=draft),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggS" + b"\x00" * 50, "audio/ogg")},
        )

    assert r.status_code == 200
    assert "Revisa los datos antes de confirmar" in r.text
    assert draft.clarification_reason in r.text
    assert "Crear evento" in r.text


def test_transcribe_without_calendar_shows_connect_message(voice_client: TestClient) -> None:
    """Usuario sin Google Calendar → mensaje accionable en el grabador."""
    from app.core.errors import NotFoundError

    with patch(
        "app.routes.web.calendar_voice.voice_event_service.draft_from_audio",
        AsyncMock(
            side_effect=NotFoundError(
                "Google Calendar no está conectado. Ve a Ajustes > Integraciones para vincularlo."
            )
        ),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggS" + b"\x00" * 50, "audio/ogg")},
        )

    assert r.status_code == 200
    assert "Google Calendar no está conectado" in r.text
    assert "Integraciones" in r.text


def test_transcribe_rate_limit_shows_friendly_message(voice_client: TestClient) -> None:
    """Superar rate-limit → mensaje claro, sin fragmento de confirmación."""
    from app.core.errors import RateLimitError

    with patch(
        "app.routes.web.calendar_voice.voice_event_service.draft_from_audio",
        AsyncMock(
            side_effect=RateLimitError(
                "Has superado el límite de 30 notas de voz por hora. Inténtalo de nuevo más tarde."
            )
        ),
    ):
        r = voice_client.post(
            "/calendar/voice/transcribe",
            files={"audio": ("recording.ogg", b"OggS" + b"\x00" * 50, "audio/ogg")},
        )

    assert r.status_code == 200
    assert "límite" in r.text.lower()
    assert "Crear evento" not in r.text
