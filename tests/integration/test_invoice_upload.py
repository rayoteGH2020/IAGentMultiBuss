"""Subida multipart de facturas contra storage y BD (Paso 13)."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.storage import reset_storage_for_tests
from app.jobs.queue import reset_arq_pool_for_tests
from app.main import create_app
from app.models import Invoice, InvoiceStatus, Membership, Tenant, User
from app.services import invoice_service
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PDF_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_01.pdf"
).read_bytes()


class _FakeStorage:
    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        _ = len(data), content_type, self
        return key


def _fake_get_storage() -> _FakeStorage:
    return _FakeStorage()


async def _seed_tenant_bundle(
    rls_database_url: str,
    *,
    user_sub: str,
    org_id: str,
) -> tuple[UUID, UUID]:
    """Inserta Tenant/User/Membership alineados con el fake JWT del test."""
    engine = create_async_engine(rls_database_url, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        tenant = Tenant(
            clerk_org_id=org_id,
            name="Upload Org",
            plan="free",
            settings={},
        )
        user = User(
            clerk_user_id=user_sub,
            email=f"{user_sub}@upload.test",
            name="Uploader",
        )
        session.add(tenant)
        session.add(user)
        await session.flush()
        await set_tenant_context(session, str(tenant.id))
        session.add(Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))
        await session.commit()
        tid = tenant.id
        uid = user.id
    await engine.dispose()
    return tid, uid


def _fake_clerk_resolve_builder(
    tid: UUID,
    uid: UUID,
    *,
    user_sub: str,
    org_id: str,
) -> Callable[[Request], Awaitable[None]]:
    mid = uuid4()

    async def fake_resolve(request: Request) -> None:
        now = datetime.now(tz=UTC)
        user = User(
            clerk_user_id=user_sub,
            email=f"{user_sub}@upload.test",
            name="Uploader",
            created_at=now,
            updated_at=now,
        )
        user.id = uid
        tenant = Tenant(
            clerk_org_id=org_id,
            name="Upload Org",
            plan="free",
            settings={},
            created_at=now,
            updated_at=now,
        )
        tenant.id = tid
        membership = Membership(
            user_id=uid,
            tenant_id=tid,
            role="admin",
            created_at=now,
            updated_at=now,
        )
        membership.id = mid
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership

    return fake_resolve


async def _list_invoices(
    rls_database_url: str,
    tenant_id: UUID,
) -> list[Invoice]:
    engine = create_async_engine(rls_database_url, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await set_tenant_context(session, str(tenant_id))
        result = await session.execute(select(Invoice))
        rows = result.scalars().all()
    await engine.dispose()
    return list(rows)


def test_upload_invoice_creates_row(
    invoices_migration_applied_sync: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"u_upload_{uuid4().hex[:12]}"
    org_id = f"o_upload_{uuid4().hex[:12]}"

    tid, uid = asyncio.run(
        _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id),
    )

    monkeypatch.setattr(invoice_service, "get_storage", _fake_get_storage)
    monkeypatch.setattr(
        "app.routes.web.invoices.enqueue_invoice_processing",
        AsyncMock(return_value="job-test"),
    )
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    try:
        with TestClient(create_app(), raise_server_exceptions=True) as client:
            files = [
                ("files", ("ejemplo.pdf", BytesIO(_PDF_BYTES), "application/pdf")),
            ]
            response = client.post(
                "/invoices/upload",
                files=files,
                headers={
                    "Authorization": "Bearer fake-jwt-upload",
                    "HX-Request": "true",
                },
            )
        assert response.status_code == 200
        rows = asyncio.run(_list_invoices(rls_database_url, tid))
        assert len(rows) == 1
        assert rows[0].status == InvoiceStatus.processing
        assert rows[0].source_file_key is not None
        assert str(rows[0].source_file_key).startswith("invoices/")
        assert rows[0].tenant_id == tid
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()


def test_upload_invoice_rejects_invalid_type(
    invoices_migration_applied_sync: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"u_bad_{uuid4().hex[:12]}"
    org_id = f"o_bad_{uuid4().hex[:12]}"
    tid, uid = asyncio.run(
        _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id),
    )

    monkeypatch.setattr(
        "app.routes.web.invoices.enqueue_invoice_processing",
        AsyncMock(return_value="job-test"),
    )
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    try:
        with TestClient(create_app(), raise_server_exceptions=True) as client:
            response = client.post(
                "/invoices/upload",
                files=[
                    ("files", ("evil.txt", BytesIO(b"This is plain text."), "text/plain")),
                ],
                headers={
                    "Authorization": "Bearer fake-jwt-bad-upload",
                    "HX-Request": "true",
                },
            )
        assert response.status_code == 200
        assert "unsupported" in response.text.lower()

        rows = asyncio.run(_list_invoices(rls_database_url, tid))
        assert len(rows) == 0
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()
