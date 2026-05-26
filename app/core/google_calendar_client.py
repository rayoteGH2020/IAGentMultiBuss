"""Cliente async httpx para Google Calendar OAuth y API v3 (Paso 17)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.config import Settings, get_settings
from app.core.errors import AuthError, ExternalServiceError, ForbiddenError, ValidationError
from app.schemas.calendar import (
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
    TokenResponse,
)

logger = structlog.get_logger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

DEFAULT_TIMEOUT = httpx.Timeout(30.0)


def build_auth_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: str,
) -> str:
    """Construye la URL de autorización OAuth 2.0 de Google."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _calendar_time_payload(iso_value: str) -> dict[str, str]:
    """Convierte ISO 8601 a payload start/end de Google Calendar API."""
    if len(iso_value) == 10 and iso_value[4] == "-" and iso_value[7] == "-":
        return {"date": iso_value}
    return {"dateTime": iso_value}


def _parse_calendar_event(raw: dict[str, Any]) -> CalendarEvent:
    start_obj = raw.get("start") or {}
    end_obj = raw.get("end") or {}
    start = start_obj.get("dateTime") or start_obj.get("date")
    end = end_obj.get("dateTime") or end_obj.get("date")
    if not isinstance(start, str) or not isinstance(end, str):
        msg = "Google Calendar event missing start/end"
        raise ExternalServiceError(msg)

    event_id = raw.get("id")
    if not isinstance(event_id, str):
        msg = "Google Calendar event missing id"
        raise ExternalServiceError(msg)

    summary = raw.get("summary")
    description = raw.get("description")
    html_link = raw.get("htmlLink")
    return CalendarEvent(
        id=event_id,
        summary=summary if isinstance(summary, str) else None,
        description=description if isinstance(description, str) else None,
        start=start,
        end=end,
        html_link=html_link if isinstance(html_link, str) else None,
    )


def _google_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            err = payload.get("error")
            desc = payload.get("error_description")
            if isinstance(err, str) and isinstance(desc, str):
                return f"{err}: {desc}"
            if isinstance(err, str):
                return err
    except (ValueError, TypeError):
        pass
    return str(response.text)[:300]


def _handle_google_response(response: httpx.Response, *, context: str) -> None:
    if response.status_code == 401:
        raise AuthError("google_token_expired")
    if response.status_code == 403:
        raise ForbiddenError(
            f"Google Calendar access forbidden ({context}): {_google_error_detail(response)}"
        )
    if response.status_code >= 400:
        raise ExternalServiceError(
            f"Google API error ({context}): HTTP {response.status_code}",
            details={"body": _google_error_detail(response)},
        )


class GoogleCalendarClient:
    """Cliente HTTP async para OAuth y Google Calendar API v3."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GoogleCalendarClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    def _oauth_credentials(self) -> tuple[str, str]:
        client_id = self._settings.google_oauth_client_id.strip()
        client_secret = self._settings.google_oauth_client_secret.get_secret_value().strip()
        if not client_id or not client_secret:
            raise ValidationError("Google OAuth is not configured")
        return client_id, client_secret

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        """Intercambia el authorization code por access y refresh token."""
        client_id, client_secret = self._oauth_credentials()
        response = await self._client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        _handle_google_response(response, context="token_exchange")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Invalid Google token response")
        return TokenResponse.model_validate(payload)

    async def refresh_access_token(self, refresh_token: str) -> TokenResponse:
        """Renueva el access token usando el refresh token."""
        client_id, client_secret = self._oauth_credentials()
        response = await self._client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        _handle_google_response(response, context="token_refresh")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Invalid Google token refresh response")
        return TokenResponse.model_validate(payload)

    async def get_user_email(self, access_token: str) -> str:
        """Obtiene el email de la cuenta Google autenticada."""
        response = await self._client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        _handle_google_response(response, context="userinfo")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Invalid Google userinfo response")
        email = payload.get("email")
        if not isinstance(email, str) or not email.strip():
            raise ExternalServiceError("Google userinfo missing email")
        return email.strip()

    async def list_events(
        self,
        access_token: str,
        calendar_id: str,
        *,
        time_min: str | None = None,
        time_max: str | None = None,
        max_results: int = 25,
    ) -> list[CalendarEvent]:
        """Lista eventos de un calendario en el rango indicado."""
        params: dict[str, str | int] = {
            "maxResults": max(1, min(max_results, 250)),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max

        url = f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events"
        response = await self._client.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        _handle_google_response(response, context="list_events")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Invalid Google Calendar list response")
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise ExternalServiceError("Invalid Google Calendar items payload")
        return [_parse_calendar_event(item) for item in items if isinstance(item, dict)]

    async def create_event(
        self,
        access_token: str,
        calendar_id: str,
        event_data: CalendarEventCreate,
    ) -> CalendarEvent:
        """Crea un evento en el calendario indicado."""
        body = {
            "summary": event_data.summary,
            "start": _calendar_time_payload(event_data.start),
            "end": _calendar_time_payload(event_data.end),
        }
        if event_data.description:
            body["description"] = event_data.description

        url = f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events"
        response = await self._client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        _handle_google_response(response, context="create_event")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Invalid Google Calendar create response")
        return _parse_calendar_event(payload)

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        event_data: CalendarEventUpdate,
    ) -> CalendarEvent:
        """Actualiza un evento existente en Google Calendar."""
        body = {
            "summary": event_data.summary,
            "start": _calendar_time_payload(event_data.start),
            "end": _calendar_time_payload(event_data.end),
        }
        if event_data.description is not None:
            body["description"] = event_data.description

        url = f"{GOOGLE_CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        response = await self._client.patch(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        _handle_google_response(response, context="update_event")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ExternalServiceError("Invalid Google Calendar update response")
        return _parse_calendar_event(payload)

    async def revoke_token(self, token: str) -> None:
        """Revoca un access o refresh token en Google."""
        response = await self._client.post(
            GOOGLE_REVOKE_URL,
            params={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 401:
            raise AuthError("google_token_expired")
        if response.status_code == 403:
            raise ForbiddenError("Google token revoke forbidden")
        if response.status_code >= 400:
            raise ExternalServiceError(
                f"Google token revoke failed: HTTP {response.status_code}",
                details={"body": response.text[:300]},
            )
