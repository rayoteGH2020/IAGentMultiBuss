# Paso 07 — Autenticación con Clerk y middleware de tenant

## Objetivo

Integrar Clerk como proveedor de identidad multi-tenant. Implementar middleware que valida el JWT de Clerk, hace provisioning automático del `User`/`Tenant`/`Membership` la primera vez que entran, y aplica el contexto RLS en la sesión Postgres de cada request.

Al final del paso, hay páginas `/login`, `/signup`, `/logout` funcionales; cualquier endpoint puede pedir un usuario autenticado con `Depends(current_user)` y `Depends(current_tenant)`; y la base de datos respeta el aislamiento por tenant.

## Pre-requisitos

- Pasos 01-06 completados.
- Cuenta de Clerk (clerk.com) con un proyecto creado.
- **Activar Organizations** en el dashboard de Clerk (es lo que da el multi-tenancy).
- **Infisical** con el entorno de desarrollo configurado y estas claves creadas (nombres en MAYÚSCULAS, ver `Agents.md` §2): `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`, `CLERK_WEBHOOK_SECRET`. No usar fichero `.env`.

## Contexto relevante

- `arquitectura.md` sección 9 (Seguridad y multi-tenancy).
- `Agents.md` §7 (Multi-tenancy y seguridad) y **§2** (Infisical, sin `.env`).

## Tareas

- [ ] Crear proyecto en Clerk con Organizations habilitado.
- [ ] **Registrar en Infisical** las claves de Clerk (`CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`, `CLERK_WEBHOOK_SECRET`). No crear `.env`.
- [ ] Configurar URLs en Clerk Dashboard:
  - Sign-in: `http://localhost:8000/login`
  - Sign-up: `http://localhost:8000/signup`
  - After sign-in: `http://localhost:8000/`
  - After sign-up: `http://localhost:8000/`
- [ ] Implementar `app/core/security.py` con validación JWT contra JWKS.
- [ ] Implementar `app/services/auth_service.py` con provisioning de tenant/user/membership.
- [ ] Implementar middleware en `app/core/middleware.py`.
- [ ] Implementar `app/deps.py::current_user()` y `current_tenant()` y `current_membership()`.
- [ ] Crear páginas `pages/auth/login.html`, `pages/auth/signup.html` con widget de Clerk.
- [ ] Crear router `app/routes/web/auth.py` con `/login`, `/signup`, `/logout`.
- [ ] Implementar webhook de Clerk en `app/routes/api/webhooks.py` para sincronizar cambios.
- [ ] Verificar flujo completo: signup → callback → tenant creado → home protegido.
- [ ] Test de integración: request sin token → 401; request con token válido → 200.
- [ ] Commit: `feat: clerk auth with tenant provisioning and RLS context`.

## Detalles técnicos

### Configuración de Clerk

En el dashboard de Clerk:
1. Activar **Organizations**: Settings → Organizations → Enable.
2. Si solo se permite una org por user al inicio, está bien (lo cambiamos después si hace falta).
3. Copiar `Publishable key` y `Secret key` a **Infisical** (como `CLERK_PUBLISHABLE_KEY` y `CLERK_SECRET_KEY`, o los nombres que mapee `app.config.Settings`).
4. Copiar la `JWKS URL` (Settings → API Keys → JWT public keys): suele ser `https://<instance>.clerk.accounts.dev/.well-known/jwks.json`.

### `app/core/security.py`

```python
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from app.config import get_settings
from app.core.errors import AuthError

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.clerk_jwks_url:
            raise AuthError("Clerk JWKS URL not configured")
        _jwks_client = PyJWKClient(settings.clerk_jwks_url, cache_keys=True)
    return _jwks_client


def verify_clerk_jwt(token: str) -> dict[str, Any]:
    """Valida un JWT de Clerk y devuelve los claims."""
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Clerk usa azp, no aud
        )
        return claims
    except jwt.ExpiredSignatureError as e:
        raise AuthError("Token expired") from e
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}") from e


async def fetch_clerk_user(clerk_user_id: str) -> dict[str, Any]:
    """Obtiene el perfil completo de un usuario desde la API de Clerk."""
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.clerk.com/v1/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key.get_secret_value()}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()


async def fetch_clerk_org(clerk_org_id: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.clerk.com/v1/organizations/{clerk_org_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key.get_secret_value()}"},
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()
```

### `app/services/auth_service.py`

```python
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthError, ForbiddenError
from app.core.security import fetch_clerk_org, fetch_clerk_user
from app.models import Membership, Tenant, User


async def resolve_user(db: AsyncSession, clerk_user_id: str) -> User:
    """Obtiene el User local. Si no existe, lo crea pidiendo datos a Clerk."""
    result = await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    clerk_data = await fetch_clerk_user(clerk_user_id)
    email = next(
        (e["email_address"] for e in clerk_data.get("email_addresses", [])
         if e["id"] == clerk_data.get("primary_email_address_id")),
        None,
    )
    if email is None:
        raise AuthError("User has no primary email in Clerk")

    name_parts = [clerk_data.get("first_name"), clerk_data.get("last_name")]
    name = " ".join(p for p in name_parts if p) or email.split("@")[0]

    user = User(clerk_user_id=clerk_user_id, email=email, name=name)
    db.add(user)
    await db.flush()
    return user


async def resolve_tenant(db: AsyncSession, clerk_org_id: str) -> Tenant:
    """Obtiene el Tenant local. Si no existe, lo crea desde Clerk."""
    result = await db.execute(select(Tenant).where(Tenant.clerk_org_id == clerk_org_id))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant

    clerk_data = await fetch_clerk_org(clerk_org_id)
    tenant = Tenant(
        clerk_org_id=clerk_org_id,
        name=clerk_data.get("name", "Sin nombre"),
        plan="free",
    )
    db.add(tenant)
    await db.flush()
    return tenant


async def ensure_membership(
    db: AsyncSession, user_id: UUID, tenant_id: UUID, role: str = "member"
) -> Membership:
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id, Membership.tenant_id == tenant_id
        )
    )
    membership = result.scalar_one_or_none()
    if membership is not None:
        return membership

    membership = Membership(user_id=user_id, tenant_id=tenant_id, role=role)
    db.add(membership)
    await db.flush()
    return membership
```

### `app/core/middleware.py`

```python
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.db import get_sessionmaker, set_tenant_context
from app.core.errors import AuthError
from app.core.security import verify_clerk_jwt
from app.services.auth_service import ensure_membership, resolve_tenant, resolve_user

# Rutas que no requieren autenticación
PUBLIC_PATHS = {
    "/login", "/signup", "/health", "/health/db", "/health/redis",
    "/api/webhooks/clerk",
}
PUBLIC_PREFIXES = ("/static/", "/docs", "/redoc", "/openapi", "/demo")


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Resuelve user/tenant desde Clerk JWT en cada request y aplica RLS."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.user = None
        request.state.tenant = None
        request.state.membership = None

        if _is_public(request.url.path):
            return await call_next(request)

        token = self._extract_token(request)
        if not token:
            # No hay token: dejamos pasar, la dependencia current_user dará 401
            return await call_next(request)

        try:
            claims = verify_clerk_jwt(token)
        except AuthError:
            return await call_next(request)

        clerk_user_id = claims.get("sub")
        clerk_org_id = claims.get("org_id") or claims.get("o", {}).get("id")
        if not clerk_user_id or not clerk_org_id:
            return await call_next(request)

        sm = get_sessionmaker()
        async with sm() as session:
            try:
                user = await resolve_user(session, clerk_user_id)
                tenant = await resolve_tenant(session, clerk_org_id)
                membership = await ensure_membership(
                    session,
                    user.id,
                    tenant.id,
                    role=claims.get("org_role", "member").replace("org:", ""),
                )
                await session.commit()
                request.state.user = user
                request.state.tenant = tenant
                request.state.membership = membership
            except Exception:
                await session.rollback()
                raise

        return await call_next(request)

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        # 1. Authorization header
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth.removeprefix("Bearer ").strip()
        # 2. Cookie __session de Clerk
        return request.cookies.get("__session")
```

### Actualizar `app/deps.py`

```python
from collections.abc import AsyncIterator

import redis.asyncio as redis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.db import get_sessionmaker, set_tenant_context
from app.core.errors import AuthError
from app.models import Membership, Tenant, User


async def current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise AuthError("Not authenticated")
    return user


async def current_tenant(request: Request) -> Tenant:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise AuthError("No tenant context")
    return tenant


async def current_membership(request: Request) -> Membership:
    membership = getattr(request.state, "membership", None)
    if membership is None:
        raise AuthError("No membership")
    return membership


async def get_db(
    tenant: Tenant = Depends(current_tenant),
) -> AsyncIterator[AsyncSession]:
    """DB session con contexto RLS aplicado."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            await set_tenant_context(session, str(tenant.id))
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_no_tenant() -> AsyncIterator[AsyncSession]:
    """DB session SIN contexto RLS. Solo para endpoints públicos o admin."""
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def require_role(*roles: str):
    async def _dep(membership: Membership = Depends(current_membership)) -> Membership:
        if membership.role not in roles:
            from app.core.errors import ForbiddenError
            raise ForbiddenError(f"Requires role: {', '.join(roles)}")
        return membership
    return _dep


async def get_redis_dep() -> redis.Redis:
    return get_redis()
```

### `app/templates/layouts/auth.html`

```html
{% extends "base.html" %}
{% block body %}
<div class="min-h-screen flex items-center justify-center bg-slate-50 p-6">
  <div class="w-full max-w-md card">
    <h1 class="text-2xl font-bold text-center mb-6">Mi SaaS</h1>
    {% block content %}{% endblock %}
  </div>
</div>
{% endblock %}
```

### `app/templates/pages/auth/login.html`

```html
{% extends "layouts/auth.html" %}
{% block title %}Iniciar sesión{% endblock %}
{% block content %}
<div id="clerk-sign-in"></div>
<script>
  (async () => {
    const Clerk = window.Clerk;
    await Clerk.load();
    Clerk.mountSignIn(document.getElementById("clerk-sign-in"));
  })();
</script>
<script
  async crossorigin="anonymous"
  data-clerk-publishable-key="{{ clerk_pub_key }}"
  src="https://{{ clerk_frontend_host }}/npm/@clerk/clerk-js@latest/dist/clerk.browser.js"
  type="text/javascript"></script>
{% endblock %}
```

> El `clerk_frontend_host` se obtiene del dashboard (algo como `clean-llama-1.clerk.accounts.dev`).

`pages/auth/signup.html` es análogo con `mountSignUp`.

### `app/routes/web/auth.py`

```python
from fastapi import APIRouter, Request

from app.config import get_settings
from app.core.templating import render

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login_page(request: Request):
    settings = get_settings()
    return render(
        request,
        full="pages/auth/login.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )


@router.get("/signup")
async def signup_page(request: Request):
    settings = get_settings()
    return render(
        request,
        full="pages/auth/signup.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )


def _extract_host(jwks_url: str) -> str:
    # https://clean-llama-1.clerk.accounts.dev/.well-known/jwks.json -> clean-llama-1.clerk.accounts.dev
    from urllib.parse import urlparse
    return urlparse(jwks_url).netloc
```

### Webhook de Clerk

`app/routes/api/webhooks.py`:

```python
from fastapi import APIRouter, Header, Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.core.db import session_scope
from app.core.errors import AuthError
from app.core.logging import get_logger
from app.services.auth_service import resolve_tenant, resolve_user

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
log = get_logger(__name__)


@router.post("/clerk")
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(alias="svix-id"),
    svix_timestamp: str = Header(alias="svix-timestamp"),
    svix_signature: str = Header(alias="svix-signature"),
):
    settings = get_settings()
    payload = await request.body()
    try:
        wh = Webhook(settings.clerk_webhook_secret.get_secret_value())
        evt = wh.verify(
            payload,
            {"svix-id": svix_id, "svix-timestamp": svix_timestamp, "svix-signature": svix_signature},
        )
    except WebhookVerificationError as e:
        raise AuthError("Invalid webhook signature") from e

    event_type = evt.get("type")
    data = evt.get("data", {})

    async with session_scope() as db:
        if event_type == "user.created":
            await resolve_user(db, data["id"])
        elif event_type == "organization.created":
            await resolve_tenant(db, data["id"])
        elif event_type in ("user.deleted", "organization.deleted"):
            # TODO: soft delete o cleanup según política
            log.info("clerk.delete_event", type=event_type, id=data.get("id"))

    return {"received": True}
```

> Para que Clerk pueda llegar a tu webhook en dev, usa `ngrok` o el túnel de Clerk Cloud.

### Modificar `app/main.py`

```python
from app.core.middleware import AuthMiddleware
from app.routes.api import webhooks
from app.routes.web import auth as auth_routes


def create_app() -> FastAPI:
    app = FastAPI(...)

    register_error_handlers(app)
    app.add_middleware(AuthMiddleware)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.include_router(health.router)
    app.include_router(webhooks.router)
    app.include_router(auth_routes.router)
    app.include_router(home.router)
    app.include_router(invoices.router)
    app.include_router(chat.router)
    app.include_router(settings.router)
    app.include_router(demo.router)

    return app
```

### Proteger las páginas de Paso 05

En `app/routes/web/home.py` (y los demás), añadir dependency:

```python
from fastapi import Depends
from app.deps import current_user
from app.models import User


@router.get("/")
async def home(request: Request, user: User = Depends(current_user)):
    return render(request, full="pages/home/index.html", ctx={"user": user})
```

Actualizar `topbar.html`:

```html
<span class="text-sm text-slate-600">{{ user.name or user.email if user else "" }}</span>
<a href="/logout" class="ml-3 text-sm text-slate-500 hover:text-slate-700">Salir</a>
```

## Criterios de aceptación

- [ ] Visitar `/` sin estar logueado redirige (o muestra 401, según implementación) a `/login`.
- [ ] `/login` muestra el widget de Clerk.
- [ ] Tras login, el usuario aterriza en `/` y ve su nombre en el topbar.
- [ ] En BD: hay un `tenants`, un `users`, una `membership` creados automáticamente.
- [ ] Si hago F5 en `/invoices`, sigo logueado.
- [ ] Webhook de Clerk responde 200 a evento de `user.created`.
- [ ] Test de integración pasa: petición sin token → 401, petición con token mock válido → 200.
- [ ] Commit hecho.

## Comandos útiles

```bash
# Túnel ngrok para webhooks Clerk
ngrok http 8000
# Copiar URL https://<aleatorio>.ngrok.io/api/webhooks/clerk al dashboard Clerk

# Test del flujo en local (secretos vía Infisical)
infisical run -- uv run uvicorn app.main:app --reload
# Abrir http://localhost:8000/signup → crear cuenta → crear organización → volver a /

# Verificar que se creó el tenant
psql postgresql://saas:saas@localhost:5432/saas \
  -c "SELECT id, name, clerk_org_id FROM tenants"
```

## Lo que NO toca este paso

- UI propia de gestión de organización (invitar, cambiar nombre): Clerk lo provee.
- Roles más finos (admin/manager/viewer con permisos detallados): pulido posterior.
- Login social (Google, Apple): Clerk se encarga, solo activar en dashboard.
- Borrado de cuenta GDPR: paso posterior.

## Posibles problemas

**`AuthError: Invalid token`**: probablemente la `CLERK_JWKS_URL` en **Infisical** es incorrecta. Cópiala otra vez del dashboard.

**Webhook devuelve 401 incluso con firma correcta**: revisa que `CLERK_WEBHOOK_SECRET` en **Infisical** coincide con el del endpoint en Clerk Dashboard → Webhooks.

**El user/tenant se duplica con cada login**: olvidaste el `.scalar_one_or_none()` antes del `INSERT`. Verifica que la condición de búsqueda usa `clerk_user_id` / `clerk_org_id`.

**El middleware se aplica a `/static`**: revisa `PUBLIC_PREFIXES` en `middleware.py`.

**Clerk no redirige correctamente tras login**: configura "After sign-in URL" y "After sign-up URL" en Clerk Dashboard.

**`org_id` vacío en el JWT**: el usuario no pertenece a ninguna organización. En Clerk, fuerza la selección de org tras signup (o crea una automáticamente).

## Siguiente paso

`Paso08.md` — Refinar el dashboard protegido: páginas mostrando nombre/email del usuario y organización, ejemplo de endpoint con patrón página/fragmento usando datos reales del tenant, y test del flujo completo con Playwright.
