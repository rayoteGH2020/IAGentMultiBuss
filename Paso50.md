# Paso50 — Consola de Administración SuperAdmin

## Objetivo

Implementar una consola `/sadm/` accesible únicamente para el usuario SuperAdmin (SADM) que permite:

- Crear y gestionar **organizaciones** (→ `tenants` en BD) sincronizadas con Clerk.
- Crear **usuarios** y asignarlos a una organización.
- Forzar cambio de contraseña en el **primer login** de cada usuario nuevo.
- Webhook de Clerk como net de seguridad de sincronización.

El SADM es gestionado manualmente por el dueño del sistema (una sola persona), no hay UI para crear otros SADMs.

---

## Encaje en la arquitectura actual

| Elemento actual | Uso en Paso50 |
|---|---|
| `app/core/security.py` — `fetch_clerk_user/org` | Base para el nuevo `ClerkClient` |
| `app/services/auth_service.py` — `resolve_user/tenant` | Reutilizados para el webhook de sync |
| `app/deps.py` — `require_role()` | Patrón a extender con `require_sadm()` |
| `app/core/middleware.py` — `try_resolve_clerk_session` | Requiere cambio: hoy no resuelve user sin org (ver riesgos) |
| `svix` ya en deps | Verificación de webhooks Clerk |
| `httpx` ya en deps | Llamadas a Clerk Backend API |
| `routes/api/` | Webhook `POST /api/webhooks/clerk` (camino de sincronización pasivo) |
| `errors.py` — `AuthError / ForbiddenError` | Respuestas de las guards de seguridad |
| `get_db_no_tenant` en `deps.py` | Consultas cross-tenant del SADM |

---

## Riesgos y decisiones de diseño

### R1 — Bloqueador: el middleware actual no resuelve el usuario sin `org_id`

**Problema.** En `try_resolve_clerk_session` (middleware.py:56), si el JWT no contiene `org_id` se setea `auth_missing_organization = True` y se hace `return` **antes** de resolver el usuario en BD. Un SADM que no haya seleccionado org en Clerk nunca tendrá `request.state.user` seteado.

**Solución adoptada.** Modificar el orden del flujo: siempre resolver el usuario si el JWT es válido; solo después comprobar si hay `org_id`. Si no hay `org_id` y el usuario tiene `is_superadmin = True`, dejar pasar sin tenant context. Si no es SADM, mantener el comportamiento actual (`auth_missing_organization = True`).

**Consecuencia.** En rutas `/sadm/*`, `request.state.tenant` es `None`. Todas las deps de esas rutas deben usar `get_db_no_tenant`, nunca `get_db`.

---

### R2 — Clerk no tiene "force password reset" nativo por API

**Problema.** Clerk no expone un flag server-side de "forzar cambio de contraseña en el siguiente login". La alternativa de envitar una "invitation" delega toda la configuración de credenciales al usuario desde el primer acceso, pero no encaja con el flujo de "el admin crea la cuenta con contraseña temporal".

**Solución adoptada:**

1. SADM crea usuario vía Clerk Backend API con una contraseña generada aleatoriamente (nunca mostrada a nadie).
2. BD local: columna `users.force_password_reset = True`.
3. Middleware intercepta cualquier request del usuario con ese flag activo y redirige a `/auth/change-password`.
4. El usuario cambia la contraseña mediante ClerkJS (`user.updatePassword()`).
5. Una vez completado, el frontend llama a `POST /auth/complete-password-reset`; el backend limpia el flag.

**Riesgo residual.** Si el usuario abandona `/auth/change-password` sin completar el cambio, es redirigido de vuelta en el siguiente request. No existe ventana de escape hasta que lo complete.

---

### R3 — Inconsistencia Clerk ↔ BD si falla un paso del proceso de creación

**Escenario.** SADM crea una org: (1) llamada a Clerk API con éxito → (2) insert en `tenants` falla por error de BD. Clerk tiene la org, la BD no.

**Solución adoptada: doble mecanismo.**

- **Primario (síncrono):** La creación en BD ocurre en la misma operación del `admin_service`. Si falla el insert, se intenta borrar la org recién creada en Clerk (best-effort rollback). Si ese borrado también falla, se loguea con `structlog` para revisión manual.
- **Secundario (asíncrono):** El webhook `organization.created` de Clerk (implementado en este paso) garantiza convergencia eventual: si la org existe en Clerk y no en BD, el webhook la crea.

---

### R4 — Las rutas `/sadm/*` necesitan acceso cross-tenant

Las consultas del SADM (listar todos los tenants, ver usuarios de cualquier org) no deben ejecutarse con un `app.current_tenant` activo, ya que RLS filtraría los resultados. Se usa `get_db_no_tenant` en todas las deps del admin.

**Riesgo.** Cualquier consulta dentro de una ruta `/sadm/` que use accidentalmente `get_db` (que sí aplica RLS) silenciosamente devolvería solo datos del tenant activo (o fallaría si no hay tenant). Mitigación: los tests deben cubrir que el listado global devuelve N tenants, no 1.

---

### R5 — Configuración Clerk: instancia con email + contraseña

La creación de usuarios con contraseña vía Clerk API (`POST /v1/users` con campo `password`) solo funciona si la instancia Clerk tiene habilitado el método de autenticación **Email + Password**. Si la instancia solo usa OAuth o magic link, la creación de contraseña temporal falla con 422.

**Acción requerida antes de implementar:** verificar en Clerk Dashboard → Configure → Email & Password que está habilitado.

---

### R6 — `Tenant.monthly_budget_eur` ausente del modelo ORM

`arquitectura.md §5` incluye `monthly_budget_eur` en el esquema de `tenants`, pero `app/models/tenant.py` no lo tiene. No es bloqueante para este paso, pero hay que añadirlo en la migración de este paso para no acumular deuda.

---

## Nuevas variables de entorno (Infisical)

No hay nuevas variables. `CLERK_SECRET_KEY` ya existe desde Paso07. Confirmar que el valor es la **Secret Key** (empieza por `sk_...`), no la Publishable Key.

---

## Criterios de aceptación

- [ ] SADM puede hacer login en la app sin tener org activa en Clerk y acceder a `/sadm/`.
- [ ] Un usuario no-SADM que accede a cualquier ruta `/sadm/*` recibe 403.
- [ ] SADM puede crear una organización; la organización aparece en Clerk **y** en la tabla `tenants` de BD.
- [ ] SADM puede crear un usuario; el usuario aparece en Clerk **y** en la tabla `users`, con `force_password_reset = True`.
- [ ] El nuevo usuario se añade como miembro de la organización indicada (tabla `memberships`).
- [ ] Al hacer login el usuario nuevo, es redirigido a `/auth/change-password` antes de poder acceder a cualquier otra ruta.
- [ ] Tras completar el cambio de contraseña, el usuario accede normalmente y `force_password_reset = False`.
- [ ] El webhook `POST /api/webhooks/clerk` verifica la firma svix y crea el tenant/user/membership si no existen (idempotente).
- [ ] SADM puede listar todas las organizaciones y sus miembros.
- [ ] SADM puede eliminar un usuario de una organización (soft-remove: elimina membership en BD y en Clerk).
- [ ] Tests de integración cubren: create org, create user, forced password reset redirect, webhook idempotency.

---

## Implementación

### Paso 1 — Migración de BD

**Archivo:** `migrations/versions/xxxx_paso50_admin_fields.py` (generado con alembic).

Cambios en el esquema:

```python
# En la migración:
# users: dos columnas nuevas
op.add_column("users", sa.Column("is_superadmin", sa.Boolean(), server_default="false", nullable=False))
op.add_column("users", sa.Column("force_password_reset", sa.Boolean(), server_default="false", nullable=False))
op.create_index("ix_users_is_superadmin", "users", ["is_superadmin"])

# tenants: columna ausente de arquitectura.md §5
op.add_column("tenants", sa.Column("monthly_budget_eur", sa.Numeric(10, 2), nullable=True))
```

Actualizar `app/models/user.py`:

```python
is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
force_password_reset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

Actualizar `app/models/tenant.py`:

```python
from decimal import Decimal
monthly_budget_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
```

Generar y revisar antes de commitear:

```bash
uv run alembic revision --autogenerate -m "paso50 admin fields"
uv run alembic upgrade head
```

---

### Paso 2 — Actualizar `app/core/middleware.py`

**Cambio 1:** Mover la resolución del usuario ANTES de comprobar `org_id`.

```python
# try_resolve_clerk_session — flujo actualizado

async def try_resolve_clerk_session(request: Request) -> None:
    token = _extract_token(request)
    if not token:
        return
    try:
        claims = verify_clerk_jwt(token)
    except AuthError:
        return

    clerk_user_id = claims.get("sub")
    if not isinstance(clerk_user_id, str) or not clerk_user_id:
        return

    clerk_org_id = org_id_from_claims(claims)

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            user = await resolve_user(session, clerk_user_id)

            if not clerk_org_id:
                if user.is_superadmin:
                    # SADM sin org activa: solo contexto de usuario, sin tenant
                    await session.commit()
                    request.state.user = user
                else:
                    request.state.auth_missing_organization = True
                return

            tenant = await resolve_tenant(session, clerk_org_id)
            await set_tenant_context(session, str(tenant.id))
            membership = await ensure_membership(
                session,
                user.id,
                tenant.id,
                role=org_role_from_claims(claims),
            )
            await session.commit()
            request.state.user = user
            request.state.tenant = tenant
            request.state.membership = membership

            if user.force_password_reset:
                request.state.force_password_reset = True

        except Exception:
            await session.rollback()
            raise
```

**Cambio 2:** En `AuthMiddleware.dispatch`, interceptar `force_password_reset` antes de llamar `call_next`:

```python
CHANGE_PASSWORD_PATHS = frozenset({"/auth/change-password", "/auth/complete-password-reset"})

# En dispatch, después de try_resolve_clerk_session:
if (
    getattr(request.state, "force_password_reset", False)
    and request.url.path not in CHANGE_PASSWORD_PATHS
    and not _is_public(request.url.path)
):
    if request.headers.get("HX-Request"):
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(
            status_code=200,
            headers={"HX-Redirect": "/auth/change-password"},
            content={},
        )
    return RedirectResponse(url="/auth/change-password", status_code=302)
```

---

### Paso 3 — Actualizar `app/deps.py`

Añadir dependency `require_sadm`:

```python
from app.models import Membership, Tenant, User


async def require_sadm(user: User = Depends(current_user)) -> User:
    """Dependency que exige que el usuario autenticado sea SuperAdmin."""
    if not user.is_superadmin:
        raise ForbiddenError("Requires superadmin privileges")
    return user


SuperAdmin = Annotated[User, Depends(require_sadm)]
```

Nota: `current_user` funciona para SADM porque el middleware ya seteó `request.state.user` sin `auth_missing_organization`.

---

### Paso 4 — Crear `app/core/clerk_client.py`

Encapsula todas las llamadas a la Clerk Backend API necesarias para la consola admin. Sigue el patrón ya establecido en `security.py`.

```python
"""
Clerk Backend API client.

Punto único de acceso a operaciones administrativas de Clerk.
No usar directamente desde routes/ ni desde otros módulos que no sean services/.
"""

from typing import Any, cast

import httpx

from app.config import get_settings
from app.core.errors import ExternalServiceError

_BASE = "https://api.clerk.com/v1"


def _headers() -> dict[str, str]:
    secret = get_settings().clerk_secret_key.get_secret_value()
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.request(method, f"{_BASE}{path}", headers=_headers(), **kwargs)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError(
                f"Clerk API error {exc.response.status_code}: {exc.response.text}"
            ) from exc
        if r.status_code == 204:
            return {}
        return cast("dict[str, Any]", r.json())


# --- Organizations ---

async def create_organization(name: str) -> dict[str, Any]:
    return await _request("POST", "/organizations", json={"name": name})


async def list_organizations(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return await _request("GET", "/organizations", params={"limit": limit, "offset": offset})


async def delete_organization(clerk_org_id: str) -> None:
    await _request("DELETE", f"/organizations/{clerk_org_id}")


# --- Users ---

async def create_user(
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email_address": [email],
        "password": password,
    }
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    return await _request("POST", "/users", json=payload)


async def delete_user(clerk_user_id: str) -> None:
    await _request("DELETE", f"/users/{clerk_user_id}")


async def set_user_password(clerk_user_id: str, password: str) -> dict[str, Any]:
    return await _request(
        "PATCH",
        f"/users/{clerk_user_id}",
        json={"password": password},
    )


# --- Organization memberships ---

async def add_org_member(clerk_org_id: str, clerk_user_id: str, role: str = "org:member") -> dict[str, Any]:
    return await _request(
        "POST",
        f"/organizations/{clerk_org_id}/memberships",
        json={"user_id": clerk_user_id, "role": role},
    )


async def list_org_members(clerk_org_id: str, limit: int = 100) -> dict[str, Any]:
    return await _request(
        "GET",
        f"/organizations/{clerk_org_id}/memberships",
        params={"limit": limit},
    )


async def remove_org_member(clerk_org_id: str, clerk_user_id: str) -> None:
    await _request("DELETE", f"/organizations/{clerk_org_id}/memberships/{clerk_user_id}")
```

---

### Paso 5 — Crear `app/services/admin_service.py`

Orquesta operaciones de Clerk + BD. Las rutas llaman aquí, nunca a `clerk_client` directamente.

```python
"""Admin service: gestión de orgs y usuarios por el SuperAdmin."""

import secrets
import string
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clerk_client
from app.core.db import set_tenant_context
from app.core.errors import NotFoundError
from app.models import Membership, Tenant, User
from app.services.auth_service import ensure_membership, resolve_tenant, resolve_user


def _generate_temp_password(length: int = 20) -> str:
    """Genera una contraseña temporal que cumple los requisitos de Clerk."""
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def create_org_with_tenant(db: AsyncSession, name: str) -> Tenant:
    """
    Crea una organización en Clerk y su Tenant equivalente en BD.

    Si el insert en BD falla, intenta eliminar la org en Clerk (best-effort).
    El webhook organization.created actúa como safety net de convergencia.
    """
    clerk_org = await clerk_client.create_organization(name)
    clerk_org_id: str = clerk_org["id"]

    try:
        tenant = await resolve_tenant(db, clerk_org_id)
        await db.flush()
    except Exception:
        # best-effort rollback en Clerk
        try:
            await clerk_client.delete_organization(clerk_org_id)
        except Exception:
            pass
        raise

    return tenant


async def create_user_in_org(
    db: AsyncSession,
    email: str,
    first_name: str,
    last_name: str,
    tenant_id: UUID,
    role: str = "member",
) -> User:
    """
    Crea un usuario en Clerk, lo añade a la org y registra force_password_reset.

    La contraseña temporal nunca se expone: el usuario debe cambiarla en el primer login.
    """
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.clerk_org_id is None:
        raise NotFoundError(f"Tenant {tenant_id} not found or not linked to Clerk")

    temp_password = _generate_temp_password()
    clerk_user = await clerk_client.create_user(
        email=email,
        password=temp_password,
        first_name=first_name,
        last_name=last_name,
    )
    clerk_user_id: str = clerk_user["id"]

    try:
        await clerk_client.add_org_member(tenant.clerk_org_id, clerk_user_id, role=f"org:{role}")

        user = await resolve_user(db, clerk_user_id)
        user.force_password_reset = True
        # set_tenant_context es necesario: memberships tiene FORCE ROW LEVEL SECURITY
        await set_tenant_context(db, str(tenant_id))
        await ensure_membership(db, user.id, tenant_id, role=role)
        await db.flush()
    except Exception:
        try:
            await clerk_client.delete_user(clerk_user_id)
        except Exception:
            pass
        raise

    return user


async def remove_user_from_org(
    db: AsyncSession,
    user_id: UUID,
    tenant_id: UUID,
) -> None:
    """Elimina la membresía en BD y en Clerk."""
    # Necesario antes de cualquier query a memberships (FORCE ROW LEVEL SECURITY)
    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Membership not found")

    result_user = await db.execute(select(User).where(User.id == user_id))
    user = result_user.scalar_one_or_none()

    result_tenant = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result_tenant.scalar_one_or_none()

    if user and user.clerk_user_id and tenant and tenant.clerk_org_id:
        await clerk_client.remove_org_member(tenant.clerk_org_id, user.clerk_user_id)

    await db.delete(membership)
    await db.flush()


async def list_all_tenants(db: AsyncSession) -> list[Tenant]:
    # tenants no tiene tenant_id → sin RLS, no requiere set_tenant_context
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return list(result.scalars().all())


async def list_tenant_members(db: AsyncSession, tenant_id: UUID) -> list[tuple[User, Membership]]:
    # memberships tiene FORCE ROW LEVEL SECURITY: sin contexto devolverá vacío
    await set_tenant_context(db, str(tenant_id))
    result = await db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.tenant_id == tenant_id)
        .order_by(User.email)
    )
    return [(row[0], row[1]) for row in result.all()]
```

---

### Paso 6 — Crear rutas SADM: `app/routes/web/admin/`

Estructura:

```
app/routes/web/admin/
├── __init__.py
├── dashboard.py
├── organizations.py
└── users.py
```

Prefijo del router: `/sadm`. Todas las rutas usan `SuperAdmin = Depends(require_sadm)` y `db: AsyncSession = Depends(get_db_no_tenant)`.

**`app/routes/web/admin/organizations.py`** (estructura orientativa):

```python
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.services import admin_service

router = APIRouter(prefix="/sadm/organizations", tags=["sadm"])


@router.get("", response_class=HTMLResponse)
async def list_organizations(
    request: Request,
    _sadm: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenants = await admin_service.list_all_tenants(db)
    return render(
        request,
        full="pages/sadm/organizations/index.html",
        partial="pages/sadm/organizations/_list.html",
        ctx={"tenants": tenants},
    )


@router.post("", response_class=HTMLResponse)
async def create_organization(
    request: Request,
    _sadm: SuperAdmin,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    tenant = await admin_service.create_org_with_tenant(db, name)
    # Sin db.commit() explícito: get_db_no_tenant lo hace al salir.
    # set_config LOCAL se mantiene activo durante la transacción.
    tenants = await admin_service.list_all_tenants(db)
    return render(
        request,
        full="pages/sadm/organizations/index.html",
        partial="pages/sadm/organizations/_list.html",
        ctx={"tenants": tenants, "created": tenant},
    )


@router.get("/{tenant_id}/members", response_class=HTMLResponse)
async def list_members(
    request: Request,
    tenant_id: UUID,
    _sadm: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    members = await admin_service.list_tenant_members(db, tenant_id)
    return render(
        request,
        full="pages/sadm/organizations/members.html",
        partial="pages/sadm/organizations/_members.html",
        ctx={"members": members, "tenant_id": tenant_id},
    )
```

**`app/routes/web/admin/users.py`** (estructura orientativa):

```python
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import SuperAdmin, get_db_no_tenant
from app.services import admin_service

router = APIRouter(prefix="/sadm/users", tags=["sadm"])


@router.post("", response_class=HTMLResponse)
async def create_user(
    request: Request,
    _sadm: SuperAdmin,
    email: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    tenant_id: UUID = Form(...),
    role: str = Form("member"),
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    user = await admin_service.create_user_in_org(
        db, email, first_name, last_name, tenant_id, role
    )
    # Sin db.commit() explícito: get_db_no_tenant lo hace al salir.
    return render(
        request,
        full="pages/sadm/organizations/members.html",
        partial="pages/sadm/organizations/_members.html",
        ctx={"created_user": user, "tenant_id": tenant_id},
    )


@router.delete("/{user_id}/orgs/{tenant_id}", response_class=HTMLResponse)
async def remove_member(
    request: Request,
    user_id: UUID,
    tenant_id: UUID,
    _sadm: SuperAdmin,
    db: AsyncSession = Depends(get_db_no_tenant),
) -> HTMLResponse:
    await admin_service.remove_user_from_org(db, user_id, tenant_id)
    # Sin db.commit() explícito: set_config LOCAL se mantiene activo para la query siguiente.
    members = await admin_service.list_tenant_members(db, tenant_id)
    return render(
        request,
        full="pages/sadm/organizations/members.html",
        partial="pages/sadm/organizations/_members.html",
        ctx={"members": members, "tenant_id": tenant_id},
    )
```

Registrar los routers en `app/main.py`:

```python
from app.routes.web.admin import dashboard, organizations, users as admin_users
app.include_router(dashboard.router)
app.include_router(organizations.router)
app.include_router(admin_users.router)
```

---

### Paso 7 — Templates SADM

Estructura de templates:

```
app/templates/pages/sadm/
├── dashboard.html
└── organizations/
    ├── index.html          ← página completa (lista de orgs + formulario crear)
    ├── _list.html          ← fragmento HTMX: tabla de orgs
    ├── members.html        ← página completa (miembros de una org + formulario crear usuario)
    └── _members.html       ← fragmento HTMX: tabla de miembros
```

Todos extienden `layouts/dashboard.html`. El sidebar debe mostrar las secciones SADM condicionalmente:

```html
{# En components/sidebar.html — añadir bloque condicional #}
{% if request.state.user and request.state.user.is_superadmin %}
<li>
  <a href="/sadm/organizations"
     class="..."
     hx-get="/sadm/organizations"
     hx-target="#main-content"
     hx-push-url="true">
    Administración
  </a>
</li>
{% endif %}
```

Formulario de creación de org (dentro de `index.html` o como fragmento):

```html
<form hx-post="/sadm/organizations"
      hx-target="#org-list"
      hx-swap="outerHTML"
      hx-indicator="#spinner-create-org">
  <input type="text" name="name" placeholder="Nombre de la organización" required>
  <button type="submit">Crear organización</button>
  <span id="spinner-create-org" class="htmx-indicator">…</span>
</form>
```

Formulario inline de creación de usuario (dentro de `members.html`):

```html
<form hx-post="/sadm/users"
      hx-target="#members-list"
      hx-swap="outerHTML"
      hx-indicator="#spinner-create-user">
  <input type="hidden" name="tenant_id" value="{{ tenant_id }}">
  <input type="email" name="email" placeholder="Email" required>
  <input type="text" name="first_name" placeholder="Nombre">
  <input type="text" name="last_name" placeholder="Apellidos">
  <select name="role">
    <option value="member">Member</option>
    <option value="admin">Admin</option>
  </select>
  <button type="submit">Crear usuario</button>
</form>
```

---

### Paso 8 — Webhook de Clerk: `POST /api/webhooks/clerk`

Implementar en `app/routes/api/webhooks.py`. Ya existe `/api/webhooks/clerk` en `PUBLIC_PATHS`.

```python
"""Clerk webhook handler — sincronización pasiva Clerk → BD."""

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.core.db import get_sessionmaker, set_tenant_context
from app.services.auth_service import ensure_membership, resolve_tenant, resolve_user

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


async def _handle_event(event_type: str, data: dict[str, Any], db: AsyncSession) -> None:
    if event_type == "organization.created":
        clerk_org_id: str = data["id"]
        await resolve_tenant(db, clerk_org_id)

    elif event_type == "organizationMembership.created":
        clerk_org_id = data["organization"]["id"]
        clerk_user_id: str = data["public_user_data"]["user_id"]
        role: str = data.get("role", "org:member").replace("org:", "")
        user = await resolve_user(db, clerk_user_id)
        tenant = await resolve_tenant(db, clerk_org_id)
        # memberships tiene FORCE ROW LEVEL SECURITY: necesario antes del INSERT
        await set_tenant_context(db, str(tenant.id))
        await ensure_membership(db, user.id, tenant.id, role=role)

    elif event_type in ("organization.deleted",):
        log.warning("clerk_org_deleted", clerk_org_id=data.get("id"))
        # No se borra el tenant automáticamente; requiere decisión manual (datos del cliente).

    else:
        log.debug("clerk_webhook_ignored", event_type=event_type)


@router.post("/clerk")
async def clerk_webhook(
    request: Request,
    svix_id: str = Header(..., alias="svix-id"),
    svix_timestamp: str = Header(..., alias="svix-timestamp"),
    svix_signature: str = Header(..., alias="svix-signature"),
) -> dict[str, str]:
    payload = await request.body()
    secret = get_settings().clerk_webhook_secret.get_secret_value()

    try:
        wh = Webhook(secret)
        event = wh.verify(
            payload,
            {
                "svix-id": svix_id,
                "svix-timestamp": svix_timestamp,
                "svix-signature": svix_signature,
            },
        )
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook signature") from exc

    event_type: str = event.get("type", "")
    data: dict[str, Any] = event.get("data", {})

    sm = get_sessionmaker()
    async with sm() as session:
        try:
            await _handle_event(event_type, data, session)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("clerk_webhook_error", event_type=event_type)
            raise

    return {"status": "ok"}
```

**`CLERK_WEBHOOK_SECRET`**: ya declarada en `app/config.py` línea 37 (`clerk_webhook_secret: SecretStr = SecretStr("")`). Solo requiere asignar el valor en Infisical. El signing secret se obtiene en Clerk Dashboard → Webhooks → endpoint → Signing Secret.

Eventos a suscribir en Clerk Dashboard:
- `organization.created`
- `organization.deleted`
- `organizationMembership.created`
- `organizationMembership.deleted`

---

### Paso 9 — Flujo de cambio de contraseña en primer login

**Ruta:** `GET /auth/change-password` y `POST /auth/complete-password-reset` en `app/routes/web/auth.py`.

```python
@router.get("/auth/change-password")
async def change_password_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return render(
        request,
        full="pages/auth/change_password.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )


@router.post("/auth/complete-password-reset")
async def complete_password_reset(
    request: Request,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db_no_tenant),
) -> Response:
    """El frontend llama a este endpoint tras completar el cambio con ClerkJS."""
    # Importar User aquí causaría conflicto de nombre con el parámetro;
    # se usa select de sqlalchemy importado al inicio del módulo.
    result = await db.execute(select(User).where(User.id == user.id))
    db_user = result.scalar_one()
    db_user.force_password_reset = False
    return RedirectResponse(url="/", status_code=302)
```

Los imports necesarios al inicio de `auth.py` (añadir a los existentes):
```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import Response, RedirectResponse
from app.deps import current_user, get_db_no_tenant
from app.models import User
```

**Template `pages/auth/change_password.html`** (extiende `layouts/auth.html`):

Debe montar el componente `<UserButton>` de ClerkJS o usar `Clerk.user.updatePassword()` en un formulario Alpine/HTMX. Tras completar el cambio, el JS hace un `fetch("POST /auth/complete-password-reset")` y redirige a `/`.

**No añadir estas rutas a `SESSION_OPTIONAL_PATHS`**. Ese conjunto redirige a `/` a usuarios ya autenticados — exactamente lo contrario de lo que necesitamos aquí. La protección contra bucle está en `CHANGE_PASSWORD_PATHS` dentro del dispatch del middleware (Paso 2).

---

### Paso 10 — Setup manual del SADM (one-time)

El SADM **no se crea desde la UI**; se configura una sola vez directamente en la BD:

```sql
-- Ejecutar una vez tras el primer login del usuario SADM en la app
UPDATE users
SET is_superadmin = TRUE
WHERE email = 'tu-email@dominio.com';
```

Alternativa: script de seed en `scripts/seed_sadm.py` que acepta el email como argumento y usa la sesión async de la app.

El usuario SADM debe existir en Clerk (creado desde Clerk Dashboard) y haber hecho al menos un login para que `resolve_user` lo registre en BD. Después se ejecuta el UPDATE anterior.

---

### Paso 11 — Tests

**`tests/integration/test_admin_service.py`:**

- `test_create_org_with_tenant` — mockear `clerk_client.create_organization`, verificar que se crea el tenant en BD.
- `test_create_user_in_org` — mockear `clerk_client.create_user` + `add_org_member`, verificar `force_password_reset = True` y membership creada.
- `test_create_user_rollback_on_db_error` — si el flush falla, verificar que se llama a `clerk_client.delete_user`.
- `test_remove_user_from_org` — verifica eliminación de membership en BD y llamada a Clerk.

**`tests/integration/test_sadm_routes.py`:**

- `test_sadm_requires_auth` — request sin JWT a `/sadm/organizations` → 401 redirect.
- `test_sadm_requires_superadmin` — usuario normal autenticado → 403.
- `test_sadm_list_orgs` — SADM con `is_superadmin = True` → 200 con lista.

**`tests/integration/test_force_password_reset.py`:**

- `test_redirect_on_force_reset` — usuario con `force_password_reset = True` hace GET a `/` → redirect a `/auth/change-password`.
- `test_no_redirect_on_change_password_path` — mismo usuario accede a `/auth/change-password` → 200, no loop.
- `test_complete_reset_clears_flag` — POST a `/auth/complete-password-reset` → `force_password_reset = False` en BD.

**`tests/integration/test_webhook_clerk.py`:**

- `test_organization_created_event` — evento válido con firma svix → tenant creado en BD (idempotente).
- `test_invalid_signature` → 400.
- `test_membership_created_event` → user + membership creados en BD.

---

## Orden de implementación recomendado

1. Migración de BD (Paso 1).
2. Actualizar modelos ORM (Paso 1, continuación).
3. `clerk_client.py` (Paso 4) — independiente, testeable por separado.
4. Middleware update (Paso 2) — desbloquea el login del SADM.
5. `deps.py` update con `require_sadm` (Paso 3).
6. `admin_service.py` (Paso 5).
7. Webhook (Paso 8) — necesita `CLERK_WEBHOOK_SECRET` en Infisical.
8. Rutas SADM (Paso 6).
9. Templates (Paso 7).
10. Flujo change-password (Paso 9).
11. Setup manual SADM (Paso 10) — solo una vez en cada entorno.
12. Tests (Paso 11).

---

## Checklist de seguridad específico de este paso

- [ ] Ninguna ruta `/sadm/*` es accesible sin `require_sadm`.
- [ ] `get_db` (con RLS) no se usa en ninguna ruta `/sadm/*` — solo `get_db_no_tenant`.
- [ ] La contraseña temporal nunca aparece en logs, respuestas HTTP ni templates.
- [ ] El webhook verifica la firma svix antes de procesar cualquier dato.
- [ ] `CLERK_WEBHOOK_SECRET` está en Infisical y no hardcodeado.
- [ ] El UPDATE que marca a alguien como SADM solo puede ejecutarse con acceso directo a BD (no hay endpoint para ello).
- [ ] Tests cubren el caso en que un `member` intenta acceder a `/sadm/` — debe obtener 403, no 401.
