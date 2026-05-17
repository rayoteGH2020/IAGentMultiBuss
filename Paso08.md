# Paso 08 — Dashboard protegido con datos reales y patrón página/fragmento

## Objetivo

Refinar el dashboard ahora que hay autenticación: mostrar usuario y organización reales en el topbar, página de ajustes mostrando datos de la cuenta, endpoint de ejemplo que demuestra el patrón página/fragmento con datos del tenant, y test E2E con Playwright que recorre el flujo completo.

Al final del paso, el dashboard es funcional aunque vacío de contenido de negocio, y hay confianza en que la fundación (auth + tenant + RLS + render) está sólida.

## Pre-requisitos

- Pasos 01-07 completados.
- Cuenta Clerk con al menos un usuario y una organización creados.

## Contexto relevante

- `arquitectura.md` sección 7 (Frontend) — patrón página/fragmento.
- `Agents.md` secciones 6 (Página/fragmento) y 7 (Multi-tenancy).

## Tareas

- [ ] Actualizar `topbar.html` para mostrar `user.name` y `tenant.name`.
- [ ] Crear página `pages/settings/profile.html` con datos del usuario.
- [ ] Crear página `pages/settings/organization.html` con datos del tenant.
- [ ] Convertir `pages/settings/index.html` en index con sub-navegación.
- [ ] Implementar endpoint con patrón página/fragmento que actualiza el nombre del tenant.
- [ ] Cambiar `home.py` para mostrar saludo personalizado y stats placeholder.
- [ ] Crear `pages/auth/no_org.html` para usuarios sin organización.
- [ ] Configurar redirección a `/onboarding` si user sin tenant.
- [ ] Test E2E con Playwright: login → home → settings → editar nombre.
- [ ] Commit: `feat: protected dashboard with real user and tenant data`.

## Detalles técnicos

### `app/templates/components/topbar.html` (actualizado)

```html
<header class="h-16 bg-white border-b border-slate-200 flex items-center justify-between px-6">
  <div class="flex items-center gap-3">
    <button class="lg:hidden p-2 rounded-md hover:bg-slate-100">
      <span class="sr-only">Menú</span>
      ☰
    </button>
    <h1 class="text-lg font-semibold text-slate-900">
      {% block page_title %}{% endblock %}
    </h1>
  </div>

  <div class="flex items-center gap-4">
    <div class="hidden md:flex flex-col items-end leading-tight">
      <span class="text-sm font-medium text-slate-900">{{ tenant.name }}</span>
      <span class="text-xs text-slate-500">{{ user.email }}</span>
    </div>
    <div class="relative" x-data="{ open: false }">
      <button @click="open = !open" class="h-9 w-9 rounded-full bg-primary-600 text-white font-semibold flex items-center justify-center">
        {{ (user.name or user.email)[0] | upper }}
      </button>
      <div x-show="open" x-cloak @click.outside="open = false"
           class="absolute right-0 mt-2 w-48 bg-white border border-slate-200 rounded-lg shadow-lg py-1 z-10">
        <a href="/settings/profile" class="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-50">Mi cuenta</a>
        <a href="/settings/organization" class="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-50">Organización</a>
        <hr class="my-1 border-slate-200">
        <a href="/logout" class="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-50">Cerrar sesión</a>
      </div>
    </div>
  </div>
</header>
```

### `app/templates/pages/home/index.html` (refinado)

```html
{% extends "layouts/dashboard.html" %}

{% block title %}Inicio · {{ tenant.name }}{% endblock %}
{% block page_title %}Hola, {{ (user.name or user.email).split(' ')[0] }}{% endblock %}

{% block content %}
<div class="space-y-6 max-w-5xl">
  <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
    <div class="card">
      <p class="text-sm text-slate-500">Facturas este mes</p>
      <p class="text-3xl font-bold mt-2">0</p>
      <p class="text-xs text-slate-400 mt-1">se llenará en pasos posteriores</p>
    </div>
    <div class="card">
      <p class="text-sm text-slate-500">Conversaciones</p>
      <p class="text-3xl font-bold mt-2">0</p>
    </div>
    <div class="card">
      <p class="text-sm text-slate-500">Plan</p>
      <p class="text-3xl font-bold mt-2 capitalize">{{ tenant.plan }}</p>
    </div>
  </div>

  <div class="card">
    <h2 class="text-xl font-semibold">Empezar</h2>
    <div class="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
      <a href="/invoices" class="border border-slate-200 rounded-lg p-4 hover:border-primary-300 hover:bg-primary-50/30 transition-colors">
        <h3 class="font-semibold">Sube tu primera factura</h3>
        <p class="text-sm text-slate-600 mt-1">Extrae datos automáticamente.</p>
      </a>
      <a href="/chat" class="border border-slate-200 rounded-lg p-4 hover:border-primary-300 hover:bg-primary-50/30 transition-colors">
        <h3 class="font-semibold">Configura tu asistente</h3>
        <p class="text-sm text-slate-600 mt-1">Para responder a clientes automáticamente.</p>
      </a>
    </div>
  </div>
</div>
{% endblock %}
```

Y `home.py`:

```python
from fastapi import APIRouter, Depends, Request
from app.core.templating import render
from app.deps import current_tenant, current_user
from app.models import Tenant, User

router = APIRouter(tags=["web"])


@router.get("/")
async def home(
    request: Request,
    user: User = Depends(current_user),
    tenant: Tenant = Depends(current_tenant),
):
    return render(
        request,
        full="pages/home/index.html",
        ctx={"user": user, "tenant": tenant},
    )
```

### Sub-navegación de settings

`app/templates/pages/settings/_layout.html`:

```html
{% extends "layouts/dashboard.html" %}

{% block content %}
<div class="max-w-4xl space-y-6">
  <nav class="flex gap-1 border-b border-slate-200">
    {% set tabs = [
      ("/settings/profile", "Mi cuenta"),
      ("/settings/organization", "Organización"),
      ("/settings/billing", "Facturación"),
    ] %}
    {% for href, label in tabs %}
      {% set active = request.url.path == href %}
      <a href="{{ href }}"
         class="px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                {{ 'border-primary-600 text-primary-700' if active else 'border-transparent text-slate-600 hover:text-slate-900' }}">
        {{ label }}
      </a>
    {% endfor %}
  </nav>

  {% block settings_content %}{% endblock %}
</div>
{% endblock %}
```

`pages/settings/profile.html`:

```html
{% extends "pages/settings/_layout.html" %}

{% block title %}Mi cuenta{% endblock %}
{% block page_title %}Ajustes{% endblock %}

{% block settings_content %}
<div class="card space-y-4">
  <div>
    <h3 class="font-semibold">Datos personales</h3>
    <p class="text-sm text-slate-500">Gestionados por Clerk. Para cambiarlos abre tu perfil.</p>
  </div>
  <dl class="divide-y divide-slate-200">
    <div class="py-3 flex justify-between">
      <dt class="text-sm text-slate-500">Nombre</dt>
      <dd class="text-sm font-medium">{{ user.name or "—" }}</dd>
    </div>
    <div class="py-3 flex justify-between">
      <dt class="text-sm text-slate-500">Email</dt>
      <dd class="text-sm font-medium">{{ user.email }}</dd>
    </div>
    <div class="py-3 flex justify-between">
      <dt class="text-sm text-slate-500">Cuenta creada</dt>
      <dd class="text-sm font-medium">{{ user.created_at.strftime("%d/%m/%Y") }}</dd>
    </div>
  </dl>
</div>
{% endblock %}
```

`pages/settings/organization.html` (con patrón página/fragmento al editar nombre):

```html
{% extends "pages/settings/_layout.html" %}

{% block title %}Organización · {{ tenant.name }}{% endblock %}
{% block page_title %}Ajustes{% endblock %}

{% block settings_content %}
<div class="space-y-6">
  {% include "components/tenant_info.html" %}

  <div class="card space-y-2">
    <h3 class="font-semibold">Plan</h3>
    <p class="text-sm">Actualmente en <span class="font-medium capitalize">{{ tenant.plan }}</span>.</p>
  </div>

  <div class="card space-y-2">
    <h3 class="font-semibold">Miembros del equipo</h3>
    <p class="text-sm text-slate-500">Para invitar a más usuarios, usa el menú de organización de Clerk.</p>
  </div>
</div>
{% endblock %}
```

`app/templates/components/tenant_info.html` (fragmento reutilizable):

```html
<div id="tenant-info" class="card">
  <h3 class="font-semibold">Información de la organización</h3>

  <form
    hx-post="/settings/organization/name"
    hx-target="#tenant-info"
    hx-swap="outerHTML"
    class="mt-4 flex gap-2 items-end">
    <div class="flex-1">
      <label for="name" class="block text-sm text-slate-600 mb-1">Nombre</label>
      <input type="text" id="name" name="name" value="{{ tenant.name }}"
             class="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
             required minlength="2" maxlength="120">
    </div>
    <button type="submit" class="btn-primary">Guardar</button>
  </form>

  {% if saved %}
    <p class="mt-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-md px-3 py-2">
      ✓ Nombre actualizado correctamente.
    </p>
  {% endif %}
</div>
```

### `app/routes/web/settings.py` (con patrón página/fragmento)

```python
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.templating import render
from app.deps import current_tenant, current_user, get_db, require_role
from app.models import Tenant, User

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def settings_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/settings/profile", status_code=302)


@router.get("/profile")
async def settings_profile(
    request: Request,
    user: User = Depends(current_user),
    tenant: Tenant = Depends(current_tenant),
):
    return render(
        request,
        full="pages/settings/profile.html",
        ctx={"user": user, "tenant": tenant},
    )


@router.get("/organization")
async def settings_organization(
    request: Request,
    user: User = Depends(current_user),
    tenant: Tenant = Depends(current_tenant),
):
    return render(
        request,
        full="pages/settings/organization.html",
        ctx={"user": user, "tenant": tenant, "saved": False},
    )


@router.post("/organization/name")
async def update_organization_name(
    request: Request,
    name: str = Form(..., min_length=2, max_length=120),
    user: User = Depends(current_user),
    tenant: Tenant = Depends(current_tenant),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    tenant.name = name
    db.add(tenant)
    await db.flush()

    return render(
        request,
        full="components/tenant_info.html",
        partial="components/tenant_info.html",
        ctx={"user": user, "tenant": tenant, "saved": True},
    )
```

### Manejo de usuario sin organización

`app/templates/pages/auth/no_org.html`:

```html
{% extends "layouts/auth.html" %}
{% block title %}Crea una organización{% endblock %}
{% block content %}
<h2 class="text-lg font-semibold mb-3">Necesitas crear una organización</h2>
<p class="text-sm text-slate-600 mb-4">
  Tu cuenta de Mi SaaS funciona por organización (la pyme o negocio que vas a gestionar).
  Crea una para continuar.
</p>
<div id="clerk-create-org"></div>
<script>
  (async () => {
    await window.Clerk.load();
    window.Clerk.mountCreateOrganization(document.getElementById("clerk-create-org"));
  })();
</script>
<script
  async crossorigin="anonymous"
  data-clerk-publishable-key="{{ clerk_pub_key }}"
  src="https://{{ clerk_frontend_host }}/npm/@clerk/clerk-js@latest/dist/clerk.browser.js"></script>
{% endblock %}
```

En `app/core/middleware.py`, refinar para redirigir a `/onboarding` si user sin org:

```python
# Tras resolver token y ANTES de continuar
if clerk_user_id and not clerk_org_id:
    if request.url.path not in ("/onboarding", "/logout"):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/onboarding", status_code=302)
```

Y un endpoint `/onboarding` en `auth.py`:

```python
@router.get("/onboarding")
async def onboarding(request: Request):
    settings = get_settings()
    return render(
        request,
        full="pages/auth/no_org.html",
        ctx={
            "clerk_pub_key": settings.clerk_publishable_key,
            "clerk_frontend_host": _extract_host(settings.clerk_jwks_url),
        },
    )
```

### Test E2E con Playwright

`tests/e2e/test_dashboard_flow.py`:

```python
import os

import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.e2e

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
TEST_EMAIL = os.getenv("E2E_TEST_EMAIL")  # usuario de prueba en Clerk
TEST_PASSWORD = os.getenv("E2E_TEST_PASSWORD")


@pytest.mark.skipif(
    not TEST_EMAIL or not TEST_PASSWORD,
    reason="E2E_TEST_EMAIL/PASSWORD not configured",
)
@pytest.mark.asyncio
async def test_login_to_dashboard():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"{BASE_URL}/login")

        # Login con widget Clerk
        await page.fill("input[name='identifier']", TEST_EMAIL)
        await page.click("button[type='submit']")
        await page.fill("input[name='password']", TEST_PASSWORD)
        await page.click("button[type='submit']")

        # Verifica que entra al home
        await page.wait_for_url(f"{BASE_URL}/")
        assert await page.locator("h1").inner_text() != ""

        # Navega a ajustes
        await page.click("a[href='/settings/organization']")
        await page.wait_for_url(f"{BASE_URL}/settings/organization")

        # Edita el nombre
        await page.fill("input[name='name']", "Nuevo Nombre Test")
        await page.click("button[type='submit']")

        # Verifica mensaje de éxito
        await page.wait_for_selector("text=Nombre actualizado")

        await browser.close()
```

## Criterios de aceptación

- [ ] Tras login, el home muestra "Hola, [nombre]" y el nombre de la organización en el topbar.
- [ ] Click en avatar abre dropdown con "Mi cuenta", "Organización", "Cerrar sesión".
- [ ] `/settings/profile` muestra los datos del usuario.
- [ ] `/settings/organization` muestra los datos del tenant.
- [ ] Editar el nombre y guardar: el card se reemplaza inline (HTMX), aparece banner verde "actualizado", URL no cambia.
- [ ] F5 en `/settings/organization` muestra el nombre nuevo (persistido en BD).
- [ ] Verificar en psql: `SELECT name FROM tenants WHERE id = ...` devuelve el nombre nuevo.
- [ ] Si un user sin organización entra, va a `/onboarding`.
- [ ] (Opcional) Test E2E pasa con credenciales en `.env.test`.
- [ ] Commit hecho.

## Comandos útiles

```bash
# Levantar y probar
uv run uvicorn app.main:app --reload

# Tests E2E (requiere instalar navegadores de Playwright)
uv run playwright install chromium
E2E_TEST_EMAIL=tu@email.com E2E_TEST_PASSWORD=xxx uv run pytest tests/e2e -v

# Verificar cambio persistente en BD
psql postgresql://saas:saas@localhost:5432/saas \
  -c "SELECT name, plan FROM tenants"
```

## Lo que NO toca este paso

- Roles más finos / permisos por feature: paso posterior.
- Página de billing real: cuando integremos Stripe.
- Invitación de miembros: Clerk lo provee (UI propia es opcional).
- Borrado de cuenta: paso GDPR posterior.

## Posibles problemas

**El topbar no muestra `tenant.name`**: el template no recibe `tenant`. Pasa `tenant` en el contexto desde cada endpoint, o crea un context processor global.

**Mejor: context processor global**. Añade en `app/core/templating.py`:

```python
def _inject_user_context(request: Request) -> dict:
    return {
        "user": getattr(request.state, "user", None),
        "tenant": getattr(request.state, "tenant", None),
        "membership": getattr(request.state, "membership", None),
    }

# Y modifica render() para mergearlo:
def render(request, *, full, partial=None, ctx=None, status_code=200):
    ctx = {**_inject_user_context(request), **(ctx or {})}
    ...
```

**El form HTMX recarga la página entera**: revisa que `<form>` tiene `hx-post` y NO `action`/`method`. Y el `<body>` tiene `hx-boost`.

**`require_role("admin")` siempre falla**: verifica que en Clerk el rol del usuario en la org es `admin`. Y que el middleware extrae `org_role` del JWT.

**Tras editar nombre, F5 muestra el anterior**: la transacción no se hizo commit. Revisa que `get_db()` hace commit al salir del context.

## Siguiente paso

`Paso50.md` — Consola `/sadm/` SuperAdmin (SADM): crear/gestionar organizaciones y tenants enlazados a Clerk, alta de usuarios, primer login con cambio de contraseña obligatorio y webhooks de sincronización.
