"""Integracion: cuotas de documentos por plan (Paso04)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.entitlement_codes import LIMIT_DOCUMENTS_PER_DAY
from app.core.errors import RateLimitError
from app.models import DocTypeCode, Tenant
from app.schemas.entitlements import Entitlements
from app.services import document_upload_service, plan_service
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _FakeStorage:
    """Evita R2 real en CI (R2_ACCOUNT_ID vacio → endpoint invalido)."""

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        _ = data, content_type
        return key


@pytest.fixture
async def plans_catalog_ready(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'plans'"
        )
    )
    if result.scalar_one_or_none() is None:
        pytest.skip("Run migration p64_plans_entitlements_01.")
    await plan_service.seed_plan_catalog(db_session)


@pytest.mark.asyncio
async def test_upload_under_limit_enqueues(
    db_session: AsyncSession,
    plans_catalog_ready: None,
    invoices_schema_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.db import set_tenant_context

    tenant = Tenant(
        name=f"Quota doc {uuid4().hex[:8]}",
        plan="basic",
        plan_code="basic",
    )
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    ents = Entitlements(
        plan_code="basic",
        features=frozenset({"documents"}),
        limits={LIMIT_DOCUMENTS_PER_DAY: Decimal("5")},
    )
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=1)
    redis.expire = AsyncMock()

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        enqueue,
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.asyncio.to_thread",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.invoice_service.get_storage",
        lambda: _FakeStorage(),
    )

    result = await document_upload_service.ingest_uploaded_document(
        db_session,
        tenant_id=tenant.id,
        filename="factura.pdf",
        file_bytes=b"%PDF-1.4 minimal",
        mime_type="application/pdf",
        doc_type=DocTypeCode.factura,
        redis=redis,
        ents=ents,
    )

    assert result.rejected is False
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_over_limit_raises_before_enqueue(
    db_session: AsyncSession,
    plans_catalog_ready: None,
    invoices_schema_ready: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.db import set_tenant_context

    tenant = Tenant(name=f"Quota block {uuid4().hex[:8]}", plan_code="basic")
    db_session.add(tenant)
    await db_session.flush()
    await set_tenant_context(db_session, str(tenant.id))

    ents = Entitlements(
        plan_code="basic",
        features=frozenset({"documents"}),
        limits={LIMIT_DOCUMENTS_PER_DAY: Decimal("1")},
    )
    redis = AsyncMock()
    redis.incrby = AsyncMock(return_value=2)
    redis.expire = AsyncMock()
    redis.decrby = AsyncMock()

    enqueue = AsyncMock()
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        enqueue,
    )
    monkeypatch.setattr(
        "app.services.document_upload_service.asyncio.to_thread",
        AsyncMock(return_value=None),
    )

    with pytest.raises(RateLimitError, match="documentos"):
        await document_upload_service.ingest_uploaded_document(
            db_session,
            tenant_id=tenant.id,
            filename="factura.pdf",
            file_bytes=b"%PDF-1.4 minimal",
            mime_type="application/pdf",
            doc_type=DocTypeCode.factura,
            redis=redis,
            ents=ents,
        )

    enqueue.assert_not_awaited()
