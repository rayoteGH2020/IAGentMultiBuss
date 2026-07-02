"""Rutas web de voz → Google Calendar (Paso 23).

GET  /calendar/voice            → página con grabador (o aviso si no hay integración).
POST /calendar/voice/transcribe → multipart audio → fragmento de confirmación editable.
POST /calendar/voice/confirm    → form summary/start/end/description → fragmento resultado.

La configuración OAuth sigue en /settings/integrations.
La visualización de eventos sigue en /calendar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request, UploadFile

if TYPE_CHECKING:
    from fastapi.responses import HTMLResponse
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.calendar_datetime import local_input_to_google_iso
from app.core.errors import AppError, ValidationError
from app.core.templating import render
from app.deps import CurrentTenant, CurrentUser, RedisDep, get_db
from app.schemas.calendar import CalendarEventCreate, CalendarIntegrationStatus
from app.services import calendar_service, voice_event_service
from app.services.audit_service import AuditRequestContext

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/calendar/voice", tags=["calendar-voice"])


def _request_ctx(request: Request) -> AuditRequestContext:
    client = request.client
    return AuditRequestContext(
        ip=client.host if client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.get("")
async def voice_index(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Página principal del grabador de voz."""
    integration = await calendar_service.get_integration(db, tenant.id, user.id)
    is_connected = (
        integration is not None and integration.status == CalendarIntegrationStatus.active.value
    )
    return render(
        request,
        full="pages/calendar/voice.html",
        ctx={"user": user, "tenant": tenant, "is_connected": is_connected},
    )


@router.post("/transcribe")
async def voice_transcribe(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    redis: RedisDep,
    db: AsyncSession = Depends(get_db),
    audio: UploadFile = Form(...),
) -> HTMLResponse:
    """Recibe el audio grabado, transcribe y devuelve el fragmento de confirmación."""
    try:
        audio_bytes = await audio.read()
        mime_type = audio.content_type or "application/octet-stream"

        draft = await voice_event_service.draft_from_audio(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            audio=audio_bytes,
            mime_type=mime_type,
            redis=redis,
            request_ctx=_request_ctx(request),
        )
        return render(
            request,
            full="components/voice_event_confirm.html",
            partial="components/voice_event_confirm.html",
            ctx={"draft": draft, "tenant": tenant, "user": user},
        )
    except AppError as exc:
        logger.warning(
            "voice.transcribe_error",
            tenant_id=str(tenant.id),
            user_id=str(user.id),
            error=exc.message,
        )
        return render(
            request,
            full="components/voice_event_recorder.html",
            partial="components/voice_event_recorder.html",
            ctx={"error_message": exc.message, "tenant": tenant, "user": user},
        )


@router.post("/confirm")
async def voice_confirm(
    request: Request,
    user: CurrentUser,
    tenant: CurrentTenant,
    db: AsyncSession = Depends(get_db),
    summary: Annotated[str, Form()] = "",
    description: Annotated[str | None, Form()] = None,
    start: Annotated[str, Form()] = "",
    end: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Recibe el formulario de confirmación y crea el evento en Google Calendar."""
    try:
        # Convertir datetime-local → ISO 8601 con offset de zona horaria.
        # Sin esta conversión la Google Calendar API rechaza el evento.
        start_iso = local_input_to_google_iso(start)
        end_iso = local_input_to_google_iso(end)

        event = CalendarEventCreate(
            summary=summary.strip(),
            description=description.strip() if description and description.strip() else None,
            start=start_iso,
            end=end_iso,
        )
        created = await voice_event_service.confirm_event(
            db,
            tenant_id=tenant.id,
            user_id=user.id,
            event=event,
            request_ctx=_request_ctx(request),
        )
        return render(
            request,
            full="components/voice_event_result.html",
            partial="components/voice_event_result.html",
            ctx={"created_event": created, "tenant": tenant, "user": user},
        )
    except ValidationError as exc:
        return render(
            request,
            full="components/voice_event_confirm.html",
            partial="components/voice_event_confirm.html",
            ctx={
                "error_message": exc.message,
                "form_summary": summary,
                "form_start": start,
                "form_end": end,
                "form_description": description,
                "tenant": tenant,
                "user": user,
            },
        )
    except AppError as exc:
        logger.warning(
            "voice.confirm_error",
            tenant_id=str(tenant.id),
            user_id=str(user.id),
            error=exc.message,
        )
        return render(
            request,
            full="components/voice_event_confirm.html",
            partial="components/voice_event_confirm.html",
            ctx={
                "error_message": exc.message,
                "form_summary": summary,
                "form_start": start,
                "form_end": end,
                "form_description": description,
                "tenant": tenant,
                "user": user,
            },
        )
