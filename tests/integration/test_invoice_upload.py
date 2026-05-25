"""Tests de integración end-to-end de la subida multipart de facturas.

Ejercita la cadena completa: HTTP POST → route → service (fake storage) →
BD real → enqueue (mock ARQ). No prueba el worker ni el LLM (eso es
test_invoice_worker.py y test_extraction_real.py).

Por qué _FakeStorage en lugar de moto:
- Este test verifica la capa HTTP y de servicio, no la implementación de storage.
- FakeStorage es más simple, rápido y sin dependencias del sistema operativo.
- moto requiere configurar región y endpoint compatibles; FakeStorage no.
"""

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
from app.models import DocTypeCode, Invoice, InvoiceStatus, Membership, Tenant, User
from app.services import invoice_service
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# PDF real leído una vez a nivel de módulo para que todos los tests compartan
# los mismos bytes sin releer el disco en cada caso.
_PDF_BYTES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "invoices" / "ejemplo_01.pdf"
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
    engine = create_async_engine(rls_database_url, pool_pre_ping=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await set_tenant_context(session, str(tenant_id))
        result = await session.execute(select(Invoice))
        rows = result.scalars().all()
    await engine.dispose()
    return list(rows)


def test_upload_invoice_creates_row(
    # invoices_migration_applied_sync: garantiza que la tabla invoices existe
    # antes del test. Es síncrono para poder usarse en tests síncronos (TestClient).
    invoices_migration_applied_sync: None,
    # rls_database_url: URL de la BD de test inyectada por conftest.py.
    rls_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # UUIDs únicos por test: evitan colisiones si los tests se ejecutan en paralelo
    # o si la BD no se limpia entre ejecuciones.
    user_sub = f"u_upload_{uuid4().hex[:12]}"
    org_id = f"o_upload_{uuid4().hex[:12]}"

    # asyncio.run(): el test es síncrono (TestClient) pero el seed es async.
    # asyncio.run() crea un event loop temporal solo para el seed.
    tid, uid = asyncio.run(
        _seed_tenant_bundle(rls_database_url, user_sub=user_sub, org_id=org_id),
    )

    # Patch de storage en el service (no en el módulo storage): el service importa
    # get_storage de app.core.storage; parchear allí afectaría a todos los módulos.
    # Parchear en invoice_service afecta solo a las llamadas desde ese módulo.
    monkeypatch.setattr(invoice_service, "get_storage", _fake_get_storage)

    monkeypatch.setattr(
        "app.services.document_upload_service.enqueue_invoice_processing",
        AsyncMock(return_value="job-test"),
    )
    # Reemplaza la resolución de Clerk JWT por el fake que inyecta los objetos
    # de auth directamente en request.state.
    monkeypatch.setattr(
        "app.core.middleware.try_resolve_clerk_session",
        _fake_clerk_resolve_builder(tid, uid, user_sub=user_sub, org_id=org_id),
    )

    try:
        # create_app(): instancia fresca de la app para cada test, evitando
        # que el estado de singletons del módulo app contamine otros tests.
        # raise_server_exceptions=True: propaga excepciones del servidor al test
        # en lugar de devolverlas como respuestas 500 silenciosas.
        with TestClient(create_app(), raise_server_exceptions=True) as client:
            files = [
                ("files", ("ejemplo.pdf", BytesIO(_PDF_BYTES), "application/pdf")),
            ]
            response = client.post(
                "/invoices/upload",
                files=files,
                data={"doc_type_code": DocTypeCode.factura.value},
                headers={
                    "Authorization": "Bearer fake-jwt-upload",
                    # HX-Request: activa la rama de respuesta de fragmento HTML.
                    # El test verifica el flujo HTMX completo.
                    "HX-Request": "true",
                },
            )
        assert response.status_code == 200
        rows = asyncio.run(_list_invoices(rls_database_url, tid))
        assert len(rows) == 1
        # El status debe ser "processing" (no "pending"): create_invoice_from_upload
        # avanza el estado justo después de subir a R2.
        assert rows[0].status == InvoiceStatus.processing
        assert rows[0].source_file_key is not None
        # Verificación de la estructura de la key: debe seguir el formato de invoice_key().
        assert str(rows[0].source_file_key).startswith("invoices/")
        # Verificación de aislamiento por tenant: el registro pertenece al tenant correcto.
        assert rows[0].tenant_id == tid
    finally:
        # Limpiar singletons para que el siguiente test empiece con estado fresco.
        # Sin esto, un singleton con configuración de test contaminaría otros tests.
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
                    # Texto plano: tipo no permitido. Los magic bytes de "This is
                    # plain text." no coinciden con ninguna firma válida.
                    ("files", ("evil.txt", BytesIO(b"This is plain text."), "text/plain")),
                ],
                data={"doc_type_code": DocTypeCode.factura.value},
                headers={
                    "Authorization": "Bearer fake-jwt-bad-upload",
                    "HX-Request": "true",
                },
            )
        # El endpoint devuelve 200 con HTML de error (no 4xx): el patrón HTMX
        # usa fragmentos HTML para comunicar errores por fichero. Un 4xx rompería
        # el swap de HTMX y no mostraría el mensaje al usuario.
        assert response.status_code == 200
        # El error debe aparecer en el HTML devuelto (fragmento de panel).
        assert "unsupported" in response.text.lower()

        # El fichero inválido no debe haber creado ningún registro en BD.
        rows = asyncio.run(_list_invoices(rls_database_url, tid))
        assert len(rows) == 0
    finally:
        reset_storage_for_tests()
        reset_arq_pool_for_tests()
