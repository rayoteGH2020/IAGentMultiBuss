"""resolve_user con emails ya existentes: invitación, usuario de Clerk borrado y conflicto."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.core.db import set_tenant_context
from app.core.errors import AuthError
from app.models import Membership, Tenant, User
from app.services import auth_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _clerk_profile(clerk_user_id: str, email: str, *, verified: bool = True) -> dict[str, Any]:
    return {
        "id": clerk_user_id,
        "primary_email_address_id": "e1",
        "email_addresses": [
            {
                "id": "e1",
                "email_address": email,
                "verification": {"status": "verified" if verified else "unverified"},
            }
        ],
        "first_name": "Noe",
        "last_name": "",
    }


def _mock_clerk(
    monkeypatch: pytest.MonkeyPatch,
    profile: dict[str, Any],
    *,
    old_user_exists: bool = False,
) -> AsyncMock:
    monkeypatch.setattr(auth_service, "fetch_clerk_user", AsyncMock(return_value=profile))
    exists = AsyncMock(return_value=old_user_exists)
    monkeypatch.setattr(auth_service, "clerk_user_exists", exists)
    return exists


async def _memberships_active(db: AsyncSession, tenant: Tenant, user_id: Any) -> list[bool]:
    await set_tenant_context(db, str(tenant.id))
    rows = await db.execute(select(Membership.is_active).where(Membership.user_id == user_id))
    return list(rows.scalars().all())


async def test_recreated_clerk_user_gets_clean_account(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Usuario borrado en Clerk y recreado con el mismo email: no hereda nada."""
    email = f"noe-{uuid4().hex[:6]}@test.local"
    tenant = await tenant_factory()
    old = User(clerk_user_id=f"user_old_{uuid4().hex[:8]}", email=email, name="Noe")
    db_session.add(old)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=old.id, tenant_id=tenant.id, role="co_admin", is_active=True))
    await db_session.flush()
    old_clerk_id = old.clerk_user_id
    new_clerk_id = f"user_new_{uuid4().hex[:8]}"
    exists = _mock_clerk(monkeypatch, _clerk_profile(new_clerk_id, email))

    user = await auth_service.resolve_user(db_session, new_clerk_id)

    exists.assert_awaited_once_with(old_clerk_id)
    assert user.id != old.id
    assert (user.clerk_user_id, user.email) == (new_clerk_id, email)
    await db_session.refresh(old)
    assert old.clerk_user_id is None
    assert old.email == f"deleted+{old.id}@deleted.invalid"
    assert old.name is None
    assert await _memberships_active(db_session, tenant, old.id) == [False]


async def test_invited_user_is_linked_on_first_login(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alta desde la app (sin id de Clerk): el primer login lo vincula."""
    email = f"Invitada-{uuid4().hex[:6]}@Test.local"
    invited = User(email=email.lower(), name="Invitada")
    db_session.add(invited)
    await db_session.flush()
    clerk_id = f"user_{uuid4().hex[:8]}"
    exists = _mock_clerk(monkeypatch, _clerk_profile(clerk_id, email))

    user = await auth_service.resolve_user(db_session, clerk_id)

    assert user.id == invited.id
    assert user.clerk_user_id == clerk_id
    exists.assert_not_awaited()


async def test_invited_user_needs_verified_email(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    email = f"sin-verificar-{uuid4().hex[:6]}@test.local"
    invited = User(email=email, name="X")
    db_session.add(invited)
    await db_session.flush()
    _mock_clerk(monkeypatch, _clerk_profile("user_x", email, verified=False))

    with pytest.raises(AuthError):
        await auth_service.resolve_user(db_session, f"user_{uuid4().hex[:8]}")
    assert invited.clerk_user_id is None


async def test_email_linked_to_live_clerk_user_is_rejected(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el id antiguo sigue vivo en Clerk no se toca nada."""
    email = f"dup-{uuid4().hex[:6]}@test.local"
    live = User(clerk_user_id=f"user_live_{uuid4().hex[:8]}", email=email, name="Live")
    db_session.add(live)
    await db_session.flush()
    _mock_clerk(monkeypatch, _clerk_profile("user_other", email), old_user_exists=True)

    with pytest.raises(AuthError):
        await auth_service.resolve_user(db_session, f"user_{uuid4().hex[:8]}")
    assert live.email == email
    assert live.clerk_user_id is not None


async def test_user_deleted_webhook_anonymizes(
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    clerk_id = f"user_{uuid4().hex[:8]}"
    user = User(clerk_user_id=clerk_id, email=f"bye-{uuid4().hex[:6]}@test.local", name="Bye")
    db_session.add(user)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))
    db_session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin", is_active=True))
    await db_session.flush()

    assert await auth_service.handle_clerk_user_deleted(db_session, clerk_id)
    assert not await auth_service.handle_clerk_user_deleted(db_session, "user_desconocido")

    await db_session.refresh(user)
    assert user.clerk_user_id is None
    assert user.email.endswith("@deleted.invalid")
    assert await _memberships_active(db_session, tenant, user.id) == [False]


async def test_clerk_user_exists_only_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import security

    def _status_error(code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "https://api.clerk.com/v1/users/x")
        return httpx.HTTPStatusError(
            "error", request=request, response=httpx.Response(code, request=request)
        )

    monkeypatch.setattr(security, "fetch_clerk_user", AsyncMock(side_effect=_status_error(404)))
    assert await security.clerk_user_exists("user_x") is False

    monkeypatch.setattr(security, "fetch_clerk_user", AsyncMock(return_value={"id": "user_x"}))
    assert await security.clerk_user_exists("user_x") is True

    # Un fallo de Clerk no se interpreta como "borrado".
    monkeypatch.setattr(security, "fetch_clerk_user", AsyncMock(side_effect=_status_error(500)))
    with pytest.raises(httpx.HTTPStatusError):
        await security.clerk_user_exists("user_x")
