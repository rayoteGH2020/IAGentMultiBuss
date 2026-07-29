"""Tests de integración end-to-end de la subida multipart de facturas.

Ejercita la cadena completa: HTTP POST → route → service (fake storage) →
BD real → enqueue (mock ARQ). No prueba el worker ni el LLM (eso es
test_invoice_worker.py y test_extraction_real.py).

Por qué _FakeStorage en lugar de moto:
- Este test verifica la capa HTTP y de servicio, no la implementación de storage.
- FakeStorage es más simple, rápido y sin dependencias del sistema operativo.
- moto requiere configurar región y endpoint compatibles; FakeStorage no.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.core.csrf import CSRF_HEADER_NAME, generate_csrf_token
from app.core.db import set_tenant_context
from app.core.storage import reset_storage_for_tests
from app.jobs.queue import reset_arq_pool_for_tests
from app.main import create_app
from app.models import DocTypeCode, Invoice, InvoiceStatus, Membership, Tenant, User
from app.services import invoice_service
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

# PDF real leído una vez a nivel de módulo para que todos los tests compartan
# los mismos bytes sin releer el disco en cada caso.
_PDF_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_12.pdf"
).read_bytes()


class _FakeStorage:
    """Implementación mínima de Storage que acepta uploads sin tocar R2."""

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        # Descarta los bytes silenciosamente y devuelve la key para que el
        # service pueda guardarla en Invoice.source_file_key sin error.
        _ = len(data), content_type, self
        return key


def _fake_get_storage() -> _FakeStorage:
    return _FakeStorage()


def _csrf_headers(user_id: UUID, tenant_id: UUID, *, bearer: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer}",
        "HX-Request": "true",
        CSRF_HEADER_NAME: generate_csrf_token(user_id=user_id, tenant_id=tenant_id),
    }


async def _seed_tenant_bundle(
    rls_database_url: str,
    *,
    user_sub: str,
    org_id: str,
) -> tuple[UUID, UUID]:
    """Inserta Tenant/User/Membership alineados con el fake JWT del test.

    Se crea en la BD real para que las queries del service (que usan RLS)
    encuentren los datos y no fallen por FK inválida. Los IDs devueltos se
    usan en _fake_clerk_resolve_builder para que el middleware inyecte los
    mismos objetos que existen en BD.
    """
    # Motor propio: este helper corre fuera del ciclo de vida de conftest
    # (que gestiona db_session). Crear y disponer el motor aquí evita
    # interferir con las sesiones de otros tests.
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
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
        # RLS necesita el contexto antes de insertar Membership (que tiene tenant_id).
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
    """Crea un reemplazante del resolvedor de Clerk que inyecta el estado de auth.

    El middleware real valida el JWT contra Clerk JWKS y busca el tenant en BD.
    El fake se salta esa validación e inyecta directamente objetos ORM en
    request.state, con los mismos IDs que _seed_tenant_bundle insertó en BD.
    Esto garantiza que las queries del service encuentren filas reales.
    """
    # ID de membership fijo para toda la vida del builder (no por request),
    # lo que es suficiente para los tests que solo verifican que la auth pasó.
    mid = uuid4()

    async def fake_resolve(request: Request) -> None:
        now = datetime.now(tz=UTC)
        # Los objetos se construyen en memoria (no se leen de BD) para que
        # el middleware no necesite una sesión de BD durante la resolución.
        # Los IDs deben coincidir con los insertados por _seed_tenant_bundle.
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
    """Lee invoices con RLS activo para verificar lo que el test creó."""
    engine = create_async_engine(rls_database_url, poolclass=NullPool)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await set_tenant_context(session, str(tenant_id))
        result = await session.execute(select(Invoice))
        rows = result.scalars().all()
    await engine.dispose()
    return list(rows)


async def test_upload_invoice_creates_row(
    invoices_migration_applied: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"u_upload_{uuid4().hex[:12]}"
    org_id = f"o_upload_{uuid4().hex[:12]}"

    tid, uid = await _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr(invoice_service, "get_storage", _fake_get_storage)
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        AsyncMock(return_value="job-test"),
    )
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/documents/upload",
                files=[("files", ("ejemplo.pdf", BytesIO(_PDF_BYTES), "application/pdf"))],
                data={"doc_type_code": DocTypeCode.factura.value},
                headers=_csrf_headers(uid, tid, bearer="fake-jwt-upload"),
            )
        assert response.status_code == 200
        rows = await _list_invoices(rls_database_url, tid)
        assert len(rows) == 1
        assert rows[0].status == InvoiceStatus.processing
        assert rows[0].source_file_key is not None
        assert str(rows[0].source_file_key).startswith("invoices/")
        assert rows[0].tenant_id == tid
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()


async def test_upload_invoice_rejects_invalid_type(
    invoices_migration_applied: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"u_bad_{uuid4().hex[:12]}"
    org_id = f"o_bad_{uuid4().hex[:12]}"
    tid, uid = await _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        AsyncMock(return_value="job-test"),
    )
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/documents/upload",
                files=[
                    ("files", ("evil.txt", BytesIO(b"This is plain text."), "text/plain")),
                ],
                data={"doc_type_code": DocTypeCode.factura.value},
                headers=_csrf_headers(uid, tid, bearer="fake-jwt-bad-upload"),
            )
        assert response.status_code == 200
        assert "unsupported" in response.text.lower()

        rows = await _list_invoices(rls_database_url, tid)
        assert len(rows) == 0
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()


async def test_upload_rejects_pdf_over_page_limit_without_enqueueing(
    invoices_migration_applied: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF con más páginas de las admitidas: se guarda, se marca y no llega al worker."""
    user_sub = f"u_pages_{uuid4().hex[:12]}"
    org_id = f"o_pages_{uuid4().hex[:12]}"
    tid, uid = await _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id)

    enqueue = AsyncMock(return_value="job-should-not-run")
    monkeypatch.setattr(invoice_service, "get_storage", _fake_get_storage)
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        enqueue,
    )
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    big_pdf = buffer.getvalue()

    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/documents/upload",
                files=[("files", ("anual.pdf", BytesIO(big_pdf), "application/pdf"))],
                data={"doc_type_code": DocTypeCode.factura.value},
                headers=_csrf_headers(uid, tid, bearer="fake-jwt-pages"),
            )
        assert response.status_code == 200
        enqueue.assert_not_awaited()

        rows = await _list_invoices(rls_database_url, tid)
        assert len(rows) == 1
        assert rows[0].status == InvoiceStatus.failed
        assert rows[0].error_code == "too_many_pages"
        # El original queda en R2 para que el superadmin pueda revisarlo.
        assert rows[0].source_file_key is not None
        assert rows[0].error_message is not None
        assert "administrador del sitio" in rows[0].error_message
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()


async def test_upload_two_sequential_htmx_requests(
    invoices_migration_applied: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dos subidas seguidas deben devolver HTML 200 (no JSON 422 ni redirect)."""
    user_sub = f"u_seq_{uuid4().hex[:12]}"
    org_id = f"o_seq_{uuid4().hex[:12]}"
    tid, uid = await _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr(invoice_service, "get_storage", _fake_get_storage)
    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        AsyncMock(return_value="job-test"),
    )
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    headers = _csrf_headers(uid, tid, bearer="fake-jwt-seq")
    payload = {"doc_type_code": DocTypeCode.factura.value}

    try:
        # Cada request usa su propio AsyncClient (y por tanto su propio ciclo de
        # vida ASGI + engine singleton). Así se evita la contaminación de estado
        # asyncpg entre peticiones cuando el event loop es function-scoped.
        for name in ("primera.pdf", "segunda.pdf"):
            async with AsyncClient(
                transport=ASGITransport(app=create_app()), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/documents/upload",
                    files=[("files", (name, BytesIO(_PDF_BYTES), "application/pdf"))],
                    data=payload,
                    headers=headers,
                )
            assert response.status_code == 200
            assert "invoices-table-container" in response.text
            assert "application/json" not in response.headers.get("content-type", "")

        rows = await _list_invoices(rls_database_url, tid)
        assert len(rows) == 2
        assert all(row.status == InvoiceStatus.processing for row in rows)
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()


async def test_upload_without_files_field_returns_html_panel(
    invoices_migration_applied: None,
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin campo files en multipart: HTML 200 con aviso, no JSON 422."""
    user_sub = f"u_nof_{uuid4().hex[:12]}"
    org_id = f"o_nof_{uuid4().hex[:12]}"
    tid, uid = await _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    headers = _csrf_headers(uid, tid, bearer="fake-jwt-nof")

    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/documents/upload",
                data={"doc_type_code": DocTypeCode.factura.value},
                headers=headers,
            )
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "invoices-table-container" in response.text
        assert "No se ha seleccionado ningún fichero" in response.text
        rows = await _list_invoices(rls_database_url, tid)
        assert len(rows) == 0
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()
