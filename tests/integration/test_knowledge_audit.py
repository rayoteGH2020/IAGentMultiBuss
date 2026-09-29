"""Lecturas y descargas de knowledge quedan en audit_log (AGENTS.md §7)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.models import AuditLog, Membership, Tenant, User
from app.models.knowledge import KnowledgeDocument, KnowledgeDocumentKind, KnowledgeDocumentStatus
from app.schemas.knowledge import KnowledgeDocumentRead
from app.services import knowledge_document_service
from app.services.audit_service import AuditRequestContext
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class _FakeStorage:
    def __init__(self) -> None:
        self.ttl: int | None = None

    async def presigned_url_get(self, key: str, ttl: int | None = None) -> str:
        self.ttl = ttl
        return f"https://r2.test/{key}?signed=1"


async def _seed(db: AsyncSession, tenant: Tenant) -> tuple[KnowledgeDocument, User]:
    await set_tenant_context(db, str(tenant.id))
    user = User(
        clerk_user_id=f"user_{uuid4().hex[:12]}",
        email=f"k_{uuid4().hex[:8]}@test.local",
        name="Lector",
    )
    db.add(user)
    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        kind=KnowledgeDocumentKind.other,
        name="Tarifas",
        original_filename="tarifas.pdf",
        source_file_key=f"tenants/{tenant.id}/knowledge/tarifas.pdf",
        source_mime="application/pdf",
        status=KnowledgeDocumentStatus.ready,
        chunk_count=1,
        file_size_bytes=100,
        uploaded_by=None,
    )
    db.add(doc)
    await db.flush()
    return doc, user


async def _audit_rows(db: AsyncSession, tenant_id: UUID) -> list[AuditLog]:
    result = await db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_view_document_logs_knowledge_view(
    knowledge_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    doc, user = await _seed(db_session, tenant)

    read = await knowledge_document_service.view_document(
        db_session,
        tenant_id=tenant.id,
        document_id=doc.id,
        user_id=user.id,
        request_ctx=AuditRequestContext(ip="10.0.0.2", user_agent="pytest"),
    )

    assert read.id == doc.id
    [row] = await _audit_rows(db_session, tenant.id)
    assert row.action == "knowledge.view"
    assert row.user_id == user.id
    assert row.resource_id == doc.id
    assert row.metadata_ == {"section": "detail"}
    assert row.ip == "10.0.0.2"


@pytest.mark.asyncio
async def test_get_document_does_not_audit(
    knowledge_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    """Lectura interna (refresco de fila tras una acción): sin ruido en audit_log."""
    tenant = await tenant_factory()
    doc, _ = await _seed(db_session, tenant)

    await knowledge_document_service.get_document(
        db_session, tenant_id=tenant.id, document_id=doc.id
    )

    assert await _audit_rows(db_session, tenant.id) == []


@pytest.mark.asyncio
async def test_download_url_is_short_lived_and_audited(
    knowledge_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    doc, user = await _seed(db_session, tenant)
    storage = _FakeStorage()

    with patch.object(knowledge_document_service, "get_storage", lambda: storage):
        url = await knowledge_document_service.download_url(
            db_session, tenant_id=tenant.id, document_id=doc.id, user_id=user.id
        )

    assert url == f"https://r2.test/{doc.source_file_key}?signed=1"
    assert storage.ttl == knowledge_document_service.DOWNLOAD_URL_TTL_SECONDS
    [row] = await _audit_rows(db_session, tenant.id)
    assert row.action == "knowledge.download"
    assert row.metadata_ == {"kind": "other", "mime": "application/pdf"}


@pytest.mark.asyncio
async def test_download_url_other_tenant_is_not_found_and_not_audited(
    knowledge_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant_a = await tenant_factory()
    tenant_b = await tenant_factory()
    doc, user = await _seed(db_session, tenant_a)
    await set_tenant_context(db_session, str(tenant_b.id))

    with pytest.raises(NotFoundError):
        await knowledge_document_service.download_url(
            db_session, tenant_id=tenant_b.id, document_id=doc.id, user_id=user.id
        )

    assert await _audit_rows(db_session, tenant_b.id) == []


@pytest.mark.asyncio
async def test_faq_edit_context_logs_view(
    knowledge_schema_ready: None,
    audit_schema_ready: None,
    db_session: AsyncSession,
    tenant_factory: Callable[..., Coroutine[Any, Any, Tenant]],
) -> None:
    tenant = await tenant_factory()
    doc, user = await _seed(db_session, tenant)

    await knowledge_document_service.get_faq_edit_context(
        db_session, tenant_id=tenant.id, document_id=doc.id, user_id=user.id
    )

    [row] = await _audit_rows(db_session, tenant.id)
    assert row.action == "knowledge.view"
    assert row.metadata_ == {"section": "faq"}


# ---------------------------------------------------------------------------
# Rutas: cableado de usuario/contexto y redirect de descarga
# ---------------------------------------------------------------------------


@pytest.fixture
def knowledge_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, UUID, UUID]]:
    user_id, tenant_id = uuid4(), uuid4()

    async def fake_resolve(request: Request) -> None:
        now = datetime.now(tz=UTC)
        user = User(clerk_user_id="user_k", email="k@test.local", name="K")
        user.id = user_id
        tenant = Tenant(
            clerk_org_id="org_k",
            name="Org",
            plan="premium",
            plan_code="premium",
            settings={},
            created_at=now,
            updated_at=now,
        )
        tenant.id = tenant_id
        membership = Membership(user_id=user_id, tenant_id=tenant_id, role="admin")
        membership.id = uuid4()
        request.state.user = user
        request.state.tenant = tenant
        request.state.membership = membership

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.core.entitlement_codes import FEATURE_CODES, PLAN_CODE_PREMIUM, PLAN_LIMITS
    from app.deps import get_db, get_entitlements
    from app.main import create_app
    from app.schemas.entitlements import Entitlements

    app = create_app()

    async def mock_db() -> Any:
        yield AsyncMock()

    async def mock_entitlements() -> Entitlements:
        return Entitlements(
            plan_code=PLAN_CODE_PREMIUM,
            features=frozenset(FEATURE_CODES),
            limits=dict(PLAN_LIMITS[PLAN_CODE_PREMIUM]),
            fail_closed=False,
        )

    app.dependency_overrides[get_db] = mock_db
    app.dependency_overrides[get_entitlements] = mock_entitlements
    yield TestClient(app, raise_server_exceptions=False), user_id, tenant_id


def test_download_route_redirects_with_audit_context(
    knowledge_client: tuple[TestClient, UUID, UUID],
) -> None:
    client, user_id, tenant_id = knowledge_client
    document_id = uuid4()
    download = AsyncMock(return_value="https://r2.test/k?signed=1")

    with patch("app.routes.web.knowledge.knowledge_document_service.download_url", download):
        r = client.get(
            f"/knowledge/{document_id}/file",
            headers={"user-agent": "pytest-ua"},
            follow_redirects=False,
        )

    assert r.status_code == 302
    assert r.headers["location"] == "https://r2.test/k?signed=1"
    kwargs = download.await_args.kwargs
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["document_id"] == document_id
    assert kwargs["user_id"] == user_id
    assert kwargs["request_ctx"].user_agent == "pytest-ua"


def test_detail_route_uses_audited_view_and_links_to_file_endpoint(
    knowledge_client: tuple[TestClient, UUID, UUID],
) -> None:
    client, user_id, tenant_id = knowledge_client
    document_id = uuid4()
    now = datetime.now(tz=UTC)
    doc = KnowledgeDocumentRead.model_validate(
        {
            "id": document_id,
            "tenant_id": tenant_id,
            "kind": KnowledgeDocumentKind.other,
            "name": "Tarifas",
            "original_filename": "tarifas.pdf",
            "source_file_key": f"tenants/{tenant_id}/knowledge/tarifas.pdf",
            "source_mime": "application/pdf",
            "status": KnowledgeDocumentStatus.ready,
            "chunk_count": 1,
            "error_message": None,
            "file_size_bytes": 100,
            "uploaded_by": None,
            "ingested_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    view = AsyncMock(return_value=doc)

    with patch("app.routes.web.knowledge.knowledge_document_service.view_document", view):
        r = client.get(f"/knowledge/{document_id}", headers={"HX-Request": "true"})

    assert r.status_code == 200
    assert f'href="/knowledge/{document_id}/file"' in r.text
    # Sin URL prefirmada en el HTML: solo se descarga pasando por la ruta auditada.
    assert "X-Amz-Signature" not in r.text
    assert view.await_args.kwargs["user_id"] == user_id
