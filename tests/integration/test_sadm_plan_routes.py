"""Rutas SADM de planes (D027): cupos del mes, ampliación y cambios programados."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.csrf import CSRF_HEADER_NAME, generate_csrf_token
from app.core.db import set_tenant_context
from app.models import Membership, Tenant, User
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.db_target import resolve_test_urls

pytestmark = pytest.mark.integration


@dataclass
class _Sadm:
    admin_tenant: Tenant
    user: User
    membership: Membership
    target_id: UUID


async def _seed() -> _Sadm:
    admin_url, _ = resolve_test_urls(os.environ)
    engine = create_async_engine(admin_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:8]
    async with sm() as db:
        admin_tenant = Tenant(clerk_org_id=f"org_sadm_{suffix}", name=f"SADM {suffix}", settings={})
        target = Tenant(name=f"Cliente {suffix}", plan="basic", plan_code="basic", settings={})
        user = User(clerk_user_id=f"user_sadm_{suffix}", email=f"sadm_{suffix}@test.local")
        db.add_all([admin_tenant, target, user])
        await db.flush()
        await set_tenant_context(db, str(admin_tenant.id))
        membership = Membership(user_id=user.id, tenant_id=admin_tenant.id, role="admin")
        db.add(membership)
        await db.commit()
        seeded = _Sadm(admin_tenant, user, membership, target.id)
    await engine.dispose()
    return seeded


async def _cleanup(seeded: _Sadm) -> None:
    admin_url, _ = resolve_test_urls(os.environ)
    engine = create_async_engine(admin_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM tenants WHERE id IN (:a, :b)"),
            {"a": seeded.admin_tenant.id, "b": seeded.target_id},
        )
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": seeded.user.id})
    await engine.dispose()


@pytest.fixture
async def sadm(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Sadm]:
    from app.config import get_settings

    seeded = await _seed()
    monkeypatch.setenv("ADMIN_CLERK_ORG_ID", seeded.admin_tenant.clerk_org_id or "")
    monkeypatch.setenv("SUPERADMIN_CLERK_USER_IDS", "")
    get_settings.cache_clear()

    async def _resolve(request: Any) -> None:
        request.state.tenant = seeded.admin_tenant
        request.state.user = seeded.user
        request.state.membership = seeded.membership
        request.state.is_superadmin = True
        request.state.force_password_reset = False

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", _resolve)
    yield seeded
    await _cleanup(seeded)
    get_settings.cache_clear()


def _headers(seeded: _Sadm) -> dict[str, str]:
    token = generate_csrf_token(user_id=seeded.user.id, tenant_id=seeded.admin_tenant.id)
    return {
        "Authorization": "Bearer fake-jwt",
        "Accept": "text/html",
        "HX-Request": "true",
        CSRF_HEADER_NAME: token,
    }


def test_tenant_page_shows_monthly_quotas(sadm: _Sadm) -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/sadm/plans/tenants/{sadm.target_id}", headers=_headers(sadm))
    assert r.status_code == 200
    assert "Cupos del mes" in r.text
    assert "Facturas al mes" in r.text
    assert "Preguntas de chat al mes" in r.text


def test_quota_extra_then_plan_change_flow(sadm: _Sadm) -> None:
    from app.main import app

    base = f"/sadm/plans/tenants/{sadm.target_id}"
    with TestClient(app, raise_server_exceptions=True) as client:
        extra = client.post(
            f"{base}/quota-extra",
            headers=_headers(sadm),
            data={"code": "chat_questions_per_month", "amount": "25", "reason": "piloto"},
        )
        first = client.post(base, headers=_headers(sadm), data={"plan_code": "basic"})
        scheduled = client.post(base, headers=_headers(sadm), data={"plan_code": "advanced"})
        cancelled = client.post(f"{base}/scheduled/cancel", headers=_headers(sadm))

    assert extra.status_code == 200
    assert "ampliado en 25" in extra.text
    assert first.status_code == 200
    assert "Plan asignado" in first.text
    assert scheduled.status_code == 200
    assert "programado para el" in scheduled.text
    assert "Anular cambio" in scheduled.text
    assert cancelled.status_code == 200
    assert "anulado" in cancelled.text
    assert "Anular cambio" not in cancelled.text


def test_quota_extra_rejects_unknown_code(sadm: _Sadm) -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            f"/sadm/plans/tenants/{sadm.target_id}/quota-extra",
            headers=_headers(sadm),
            data={"code": "documents_per_day", "amount": "5"},
        )
    assert r.status_code in {400, 422}
