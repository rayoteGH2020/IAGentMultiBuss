"""Integración Google Calendar: persistencia cifrada y acceso a eventos (Paso 17)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from app.config import get_settings
from app.core.calendar_datetime import week_bounds_google_iso
from app.core.crypto import decrypt_token, encrypt_token
from app.core.errors import AuthError, ExternalServiceError, NotFoundError, ValidationError
from app.core.google_calendar_client import GoogleCalendarClient
from app.models.calendar_integration import (
    CalendarIntegration,
    CalendarIntegrationProvider,
    CalendarIntegrationStatus,
)
from app.schemas.calendar import (
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    TokenResponse,
)
from app.services import audit_service
from app.services.audit_service import AuditRequestContext

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


def _require_encryption_key() -> str:
    key = get_settings().encryption_key.get_secret_value().strip()
    if not key:
        raise ValidationError("ENCRYPTION_KEY is not configured")
    return key


async def get_integration(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    *,
    provider: str = CalendarIntegrationProvider.google.value,
) -> CalendarIntegration | None:
    """Devuelve la integración del usuario en el tenant (RLS aplicado)."""
    stmt = select(CalendarIntegration).where(
        CalendarIntegration.tenant_id == tenant_id,
        CalendarIntegration.user_id == user_id,
        CalendarIntegration.provider == provider,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def save_integration(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    token_response: TokenResponse,
    google_email: str,
    provider: str = CalendarIntegrationProvider.google.value,
    request_ctx: AuditRequestContext | None = None,
) -> CalendarIntegration:
    """Upsert de integración con tokens cifrados en BD."""
    enc_key = _require_encryption_key()
    access_enc = encrypt_token(token_response.access_token, enc_key)
    refresh_enc = (
        encrypt_token(token_response.refresh_token, enc_key)
        if token_response.refresh_token
        else None
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=token_response.expires_in)
    settings = get_settings()

    integration = await get_integration(db, tenant_id, user_id, provider=provider)
    if integration is None:
        integration = CalendarIntegration(
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
        )
        db.add(integration)

    integration.status = CalendarIntegrationStatus.active.value
    integration.google_email = google_email.strip()
    integration.access_token_enc = access_enc
    if refresh_enc is not None:
        integration.refresh_token_enc = refresh_enc
    integration.token_expires_at = expires_at
    integration.scopes = token_response.scope or settings.google_calendar_scopes

    await db.flush()
    logger.info(
        "calendar.integration.saved",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        integration_id=str(integration.id),
        google_email=integration.google_email,
    )
    await audit_service.log_calendar_integration_linked(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        integration_id=integration.id,
        google_email=integration.google_email,
        provider=provider,
        request_ctx=request_ctx,
    )
    return integration


async def get_decrypted_tokens(
    db: AsyncSession,
    integration: CalendarIntegration,
) -> tuple[str, str]:
    """Descifra access y refresh token de una integración."""
    _ = db
    enc_key = _require_encryption_key()
    if not integration.access_token_enc:
        raise ValidationError("Missing encrypted access token")
    access = decrypt_token(integration.access_token_enc, enc_key)
    if not integration.refresh_token_enc:
        raise ValidationError("Missing encrypted refresh token")
    refresh = decrypt_token(integration.refresh_token_enc, enc_key)
    return access, refresh


async def ensure_fresh_token(
    db: AsyncSession,
    integration: CalendarIntegration,
    *,
    client: GoogleCalendarClient | None = None,
) -> str:
    """Devuelve un access token válido; refresca si expira en menos de 5 minutos."""
    now = datetime.now(UTC)
    threshold = now + TOKEN_REFRESH_BUFFER
    expires_at = integration.token_expires_at
    if expires_at is not None and expires_at > threshold:
        access, _ = await get_decrypted_tokens(db, integration)
        return access

    _, refresh = await get_decrypted_tokens(db, integration)
    owns_client = client is None
    cal_client = client or GoogleCalendarClient()
    try:
        try:
            token_resp = await cal_client.refresh_access_token(refresh)
        except (AuthError, ExternalServiceError) as exc:
            integration.status = CalendarIntegrationStatus.error.value
            await db.flush()
            logger.warning(
                "calendar.token.refresh_failed",
                integration_id=str(integration.id),
                error=str(exc),
            )
            raise

        enc_key = _require_encryption_key()
        integration.access_token_enc = encrypt_token(token_resp.access_token, enc_key)
        integration.token_expires_at = now + timedelta(seconds=token_resp.expires_in)
        if token_resp.refresh_token:
            integration.refresh_token_enc = encrypt_token(token_resp.refresh_token, enc_key)
        integration.status = CalendarIntegrationStatus.active.value
        await db.flush()
        logger.info(
            "calendar.token.refreshed",
            integration_id=str(integration.id),
            tenant_id=str(integration.tenant_id),
        )
        return token_resp.access_token
    finally:
        if owns_client:
            await cal_client.aclose()


async def revoke_integration(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    *,
    client: GoogleCalendarClient | None = None,
    request_ctx: AuditRequestContext | None = None,
) -> None:
    """Revoca tokens en Google y marca la integración como ``revoked``."""
    integration = await get_integration(db, tenant_id, user_id)
    if integration is None:
        raise NotFoundError("Google Calendar integration not found")

    integration_id = integration.id
    google_email = integration.google_email
    owns_client = client is None
    cal_client = client or GoogleCalendarClient()
    try:
        try:
            access, refresh = await get_decrypted_tokens(db, integration)
            await cal_client.revoke_token(refresh)
            if access != refresh:
                await cal_client.revoke_token(access)
        except (ValidationError, AuthError, ExternalServiceError) as exc:
            logger.warning(
                "calendar.token.revoke_partial",
                integration_id=str(integration.id),
                error=str(exc),
            )

        integration.status = CalendarIntegrationStatus.revoked.value
        integration.access_token_enc = None
        integration.refresh_token_enc = None
        integration.token_expires_at = None
        await db.flush()
        logger.info(
            "calendar.token.revoked",
            integration_id=str(integration_id),
            tenant_id=str(tenant_id),
            user_id=str(user_id),
        )
        await audit_service.log_calendar_integration_unlinked(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            integration_id=integration_id,
            google_email=google_email,
            request_ctx=request_ctx,
        )
    finally:
        if owns_client:
            await cal_client.aclose()


async def list_events_for_week(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    week_start: date,
    *,
    max_results: int = 50,
    client: GoogleCalendarClient | None = None,
) -> list[CalendarEvent]:
    """Lista eventos en una ventana de 7 días desde ``week_start`` (inclusive)."""
    integration = await get_integration(db, tenant_id, user_id)
    if integration is None or integration.status != CalendarIntegrationStatus.active.value:
        raise NotFoundError("Google Calendar integration not found or inactive")

    time_min, time_max = week_bounds_google_iso(week_start)
    access_token = await ensure_fresh_token(db, integration, client=client)
    owns_client = client is None
    cal_client = client or GoogleCalendarClient()
    try:
        return await cal_client.list_events(
            access_token,
            integration.google_calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )
    finally:
        if owns_client:
            await cal_client.aclose()


async def list_upcoming_events(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    days_ahead: int = 7,
    *,
    max_results: int = 25,
    client: GoogleCalendarClient | None = None,
) -> list[CalendarEvent]:
    """Lista eventos próximos del calendario conectado del usuario."""
    if days_ahead < 1 or days_ahead > 365:
        raise ValidationError("days_ahead must be between 1 and 365")

    integration = await get_integration(db, tenant_id, user_id)
    if integration is None or integration.status != CalendarIntegrationStatus.active.value:
        raise NotFoundError("Google Calendar integration not found or inactive")

    access_token = await ensure_fresh_token(db, integration, client=client)
    owns_client = client is None
    cal_client = client or GoogleCalendarClient()
    try:
        now = datetime.now(UTC)
        time_min = now.isoformat().replace("+00:00", "Z")
        time_max = (now + timedelta(days=days_ahead)).isoformat().replace("+00:00", "Z")
        return await cal_client.list_events(
            access_token,
            integration.google_calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
        )
    finally:
        if owns_client:
            await cal_client.aclose()


async def create_calendar_event(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    event_data: CalendarEventCreate,
    *,
    client: GoogleCalendarClient | None = None,
) -> CalendarEvent:
    """Crea un evento en el calendario conectado del usuario."""
    integration = await get_integration(db, tenant_id, user_id)
    if integration is None or integration.status != CalendarIntegrationStatus.active.value:
        raise NotFoundError("Google Calendar integration not found or inactive")

    access_token = await ensure_fresh_token(db, integration, client=client)
    owns_client = client is None
    cal_client = client or GoogleCalendarClient()
    try:
        created = await cal_client.create_event(
            access_token,
            integration.google_calendar_id,
            event_data,
        )
        logger.info(
            "calendar.event.created",
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            event_id=created.id,
        )
        return created
    finally:
        if owns_client:
            await cal_client.aclose()


async def update_calendar_event(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    event_id: str,
    event_data: CalendarEventUpdate,
    *,
    client: GoogleCalendarClient | None = None,
) -> CalendarEvent:
    """Actualiza un evento del calendario conectado del usuario."""
    integration = await get_integration(db, tenant_id, user_id)
    if integration is None or integration.status != CalendarIntegrationStatus.active.value:
        raise NotFoundError("Google Calendar integration not found or inactive")

    access_token = await ensure_fresh_token(db, integration, client=client)
    owns_client = client is None
    cal_client = client or GoogleCalendarClient()
    try:
        updated = await cal_client.update_event(
            access_token,
            integration.google_calendar_id,
            event_id,
            event_data,
        )
        logger.info(
            "calendar.event.updated",
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            event_id=updated.id,
        )
        return updated
    finally:
        if owns_client:
            await cal_client.aclose()
