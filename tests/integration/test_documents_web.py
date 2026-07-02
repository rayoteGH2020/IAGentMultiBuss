"""Tests HTTP del listado de documentos.



Verifica el comportamiento del endpoint GET /documents en dos escenarios:

1. Visita directa (sin HX-Request): debe devolver la página completa.

2. Petición HTMX (con HX-Request): debe devolver solo el fragmento HTML.



No se inserta ningún dato en BD: el test verifica el estado vacío (empty state).

El tenant se resuelve en memoria (sin validar JWT contra Clerk), lo que evita

necesitar credenciales de Clerk en CI.

"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import Membership, Tenant, User
from fastapi import Request
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _fake_clerk_resolve(request: Request, *, user_sub: str, org_id: str) -> None:
    """Rellena request.state con objetos de auth construidos en memoria.



    A diferencia de test_invoice_upload.py, aquí los IDs son completamente

    aleatorios y no se insertan en BD. El test solo lee (GET /documents) y la

    BD devuelve una lista vacía para cualquier tenant_id desconocido gracias

    a RLS (que filtra por current_tenant, que apunta a un UUID que no existe).

    """

    user_id = uuid4()

    tenant_id = uuid4()

    membership_id = uuid4()

    now = datetime.now(tz=UTC)

    user = User(
        clerk_user_id=user_sub,
        email=f"{user_sub}@test.local",
        name="Test",
        created_at=now,
        updated_at=now,
    )

    user.id = user_id

    tenant = Tenant(
        clerk_org_id=org_id,
        name="Test Org",
        plan="free",
        settings={},
        created_at=now,
        updated_at=now,
    )

    tenant.id = tenant_id

    membership = Membership(
        user_id=user_id,
        tenant_id=tenant_id,
        role="admin",
        created_at=now,
        updated_at=now,
    )

    membership.id = membership_id

    request.state.user = user

    request.state.tenant = tenant

    request.state.membership = membership


def test_documents_get_shows_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # UUIDs únicos por invocación: garantizan que incluso si la BD tiene datos

    # de otros tests, el tenant de este test no los verá (aislamiento por RLS).

    user_sub = f"user_{uuid4().hex[:16]}"

    org_id = f"org_{uuid4().hex[:16]}"

    async def fake_resolve(request: Request) -> None:
        _fake_clerk_resolve(request, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    # Se usa el singleton `app` (no create_app()) para probar el comportamiento

    # del módulo en su estado normal, sin reiniciar routers ni middleware.

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/documents",
            # Sin HX-Request: el servidor debe devolver la página completa
            # (con layout, sidebar, DOCTYPE…).
            headers={"Authorization": "Bearer fake-jwt", "Accept": "text/html"},
        )

    assert r.status_code == 200

    # El texto del empty_state.html debe estar presente cuando no hay facturas.

    assert "Aún no hay documentos" in r.text


def test_documents_htmx_returns_fragment_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_sub = f"user_{uuid4().hex[:16]}"

    org_id = f"org_{uuid4().hex[:16]}"

    async def fake_resolve(request: Request) -> None:
        _fake_clerk_resolve(request, user_sub=user_sub, org_id=org_id)

    monkeypatch.setattr("app.core.middleware.try_resolve_clerk_session", fake_resolve)

    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(
            "/documents",
            headers={
                "Authorization": "Bearer fake-jwt",
                "Accept": "text/html",
                # HX-Request: activa la rama de fragmento en render().
                # El servidor debe devolver solo invoices_panel.html, sin layout.
                "HX-Request": "true",
            },
        )

    assert r.status_code == 200

    assert "Aún no hay documentos" in r.text

    # Verificación clave del patrón página/fragmento: la respuesta HTMX no

    # debe incluir el DOCTYPE ni el layout completo. Si lo incluye, HTMX

    # insertaría una página entera dentro del div de swap, rompiendo el DOM.

    assert "<!DOCTYPE" not in r.text


def test_legacy_invoices_redirects_to_documents() -> None:
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/invoices", follow_redirects=False)

    assert r.status_code == 308

    assert r.headers["location"] == "/documents"
