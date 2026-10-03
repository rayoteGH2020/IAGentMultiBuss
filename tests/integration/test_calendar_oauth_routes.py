"""Tests de rutas OAuth Google Calendar (callback público).

Regresión 7bc1aea: ``require_feature`` a nivel de ``auth_router`` exigía un
tenant autenticado en una ruta pública (el middleware no resuelve sesión en
PUBLIC_PATHS) y el callback devolvía 401 siempre. El plan se revalida ahora
dentro del callback con el tenant del ``state``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from app.config import get_settings
from app.core.entitlement_codes import OVERRIDE_SETTINGS_KEY
from app.main import create_app
from app.models import Tenant
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

_CALLBACK = "/auth/google/callback"
_PARAMS = {"code": "fake-code", "state": "a" * 64}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


async def _seed_tenant(rls_database_url: str, *, calendar_override: bool) -> UUID:
    """Tenant ``basic`` (sin calendar_google en catálogo, D012), con override opcional."""
    settings: dict[str, Any] = {}
    if calendar_override:
        settings[OVERRIDE_SETTINGS_KEY] = {"features": {"calendar_google": True}}
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        tenant = Tenant(
            name=f"OAuth Tenant {uuid4().hex[:8]}",
            plan="basic",
            plan_code="basic",
            settings=settings,
        )
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id
    await engine.dispose()
    return tenant_id


async def _delete_tenant(rls_database_url: str, tenant_id: UUID) -> None:
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(delete(Tenant).where(Tenant.id == tenant_id))
    await engine.dispose()


@pytest.fixture
async def basic_tenant(rls_database_url: str) -> AsyncIterator[UUID]:
    tenant_id = await _seed_tenant(rls_database_url, calendar_override=False)
    yield tenant_id
    await _delete_tenant(rls_database_url, tenant_id)


@pytest.fixture
async def calendar_tenant(rls_database_url: str) -> AsyncIterator[UUID]:
    tenant_id = await _seed_tenant(rls_database_url, calendar_override=True)
    yield tenant_id
    await _delete_tenant(rls_database_url, tenant_id)


@pytest.fixture
def oauth_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret"
    )  # pragma: allowlist secret
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _google_client_mock() -> MagicMock:
    google = MagicMock()
    google.exchange_code = AsyncMock(return_value=SimpleNamespace(access_token="tok"))
    google.get_user_email = AsyncMock(return_value="user@example.com")
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=google)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=ctx)


def test_oauth_callback_invalid_state_redirects(client: TestClient) -> None:
    with patch(
        "app.routes.web.integrations.consume_state",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.get(_CALLBACK, params=_PARAMS, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/settings/integrations?error=oauth_state"


def test_oauth_callback_is_reachable_without_session(client: TestClient) -> None:
    """Sin cookie ni Bearer no debe haber 401: la identidad viaja en el state."""
    with patch(
        "app.routes.web.integrations.consume_state",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = client.get(_CALLBACK, params=_PARAMS, follow_redirects=False)

    assert response.status_code != 401


@pytest.mark.usefixtures("oauth_configured")
def test_oauth_callback_denies_tenant_without_calendar_feature(
    client: TestClient, basic_tenant: UUID
) -> None:
    google_cls = _google_client_mock()
    state = {"tenant_id": str(basic_tenant), "user_id": str(uuid4())}
    with (
        patch(
            "app.routes.web.integrations.consume_state",
            new_callable=AsyncMock,
            return_value=state,
        ),
        patch("app.routes.web.integrations.GoogleCalendarClient", google_cls),
    ):
        response = client.get(_CALLBACK, params=_PARAMS, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/settings/integrations?error=plan_required"
    google_cls.assert_not_called()  # no se canjea el code sin plan


@pytest.mark.usefixtures("oauth_configured")
def test_oauth_callback_connects_tenant_with_calendar_feature(
    client: TestClient, calendar_tenant: UUID
) -> None:
    google_cls = _google_client_mock()
    save = AsyncMock()
    state = {"tenant_id": str(calendar_tenant), "user_id": str(uuid4())}
    with (
        patch(
            "app.routes.web.integrations.consume_state",
            new_callable=AsyncMock,
            return_value=state,
        ),
        patch("app.routes.web.integrations.GoogleCalendarClient", google_cls),
        patch("app.routes.web.integrations.calendar_service.save_integration", save),
    ):
        response = client.get(_CALLBACK, params=_PARAMS, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/settings/integrations?connected=google"
    save.assert_awaited_once()
    assert save.await_args is not None
    assert save.await_args.kwargs["tenant_id"] == calendar_tenant
