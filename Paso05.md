# Paso 05 — Layout de dashboard con sidebar y navegación

## Objetivo

Crear el layout `dashboard.html` con sidebar de navegación y header, y 4 páginas placeholder (Inicio, Facturas, Chat, Ajustes) que demuestren la navegación fluida con `hx-boost`. Establecer la arquitectura visual sobre la que se construirán los módulos reales.

Al final del paso, las 4 páginas son navegables sin recargar la página completa (solo el contenido cambia), y son accesibles directamente por URL (deep-linkable).

## Pre-requisitos

- Pasos 01-04 completados.
- Tailwind y servidor funcionando.

## Contexto relevante

- `arquitectura.md` sección 7 (Frontend) — patrón página/fragmento, hx-boost.
- `Agents.md` sección 6 (Patrón página/fragmento).

## Tareas

- [x] Crear `app/templates/layouts/dashboard.html` extendiendo `base.html`.
- [x] Crear `app/templates/components/sidebar.html`.
- [x] Crear `app/templates/components/topbar.html`.
- [x] Crear las 4 páginas placeholder en `app/templates/pages/`:
  - `pages/home/index.html`
  - `pages/invoices/index.html`
  - `pages/chat/index.html`
  - `pages/settings/index.html`
- [x] Crear routers en `app/routes/web/`:
  - `home.py`, `invoices.py`, `chat.py`, `settings.py`
- [x] Cada router aplica el patrón página/fragmento con `render()`.
- [x] Montar los 4 routers en `app/main.py`.
- [x] Sidebar marca el ítem activo según la URL.
- [x] `hx-boost="true"` en el `<body>` para navegación SPA-like.
- [x] Verificar navegación: click en sidebar → cambia contenido sin recargar, URL cambia, back button funciona.
- [x] Verificar deep link: pegar `/invoices` en el navegador → carga directa correcta.
- [x] Commit: `feat: dashboard layout with sidebar navigation`.

## Detalles técnicos

### `app/templates/layouts/dashboard.html`

```html
{% extends "base.html" %}

{% block body %}
<div class="min-h-screen flex bg-slate-50">
  {% include "components/sidebar.html" %}

  <div class="flex-1 flex flex-col min-w-0">
    {% include "components/topbar.html" %}

    <main id="main-content" class="flex-1 p-6 lg:p-8 overflow-y-auto">
      {% block content %}{% endblock %}
    </main>
  </div>
</div>
{% endblock %}
```

### `app/templates/components/sidebar.html`

```html
<aside class="w-64 bg-white border-r border-slate-200 flex-shrink-0 hidden lg:flex flex-col">
  <div class="h-16 flex items-center px-6 border-b border-slate-200">
    <a href="/" class="text-xl font-bold text-slate-900">Mi SaaS</a>
  </div>

  <nav class="flex-1 px-4 py-6 space-y-1">
    {% set nav_items = [
      ("/", "Inicio", "home"),
      ("/invoices", "Facturas", "invoice"),
      ("/chat", "Chat", "chat"),
      ("/settings", "Ajustes", "settings"),
    ] %}

    {% for href, label, icon in nav_items %}
      {% set active = request.url.path == href or (href != "/" and request.url.path.startswith(href)) %}
      <a href="{{ href }}"
         class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors
                {{ 'bg-primary-50 text-primary-700' if active else 'text-slate-700 hover:bg-slate-100' }}">
        {% include "components/icons/" ~ icon ~ ".html" ignore missing %}
        <span>{{ label }}</span>
      </a>
    {% endfor %}
  </nav>

  <div class="p-4 border-t border-slate-200">
    <p class="text-xs text-slate-500">v0.1.0 · dev</p>
  </div>
</aside>
```

### `app/templates/components/topbar.html`

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

  <div class="flex items-center gap-3">
    <span class="text-sm text-slate-600">dev@local</span>
  </div>
</header>
```

### Iconos SVG mínimos

Crear `app/templates/components/icons/home.html`:

```html
<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
</svg>
```

`app/templates/components/icons/invoice.html`:

```html
<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
</svg>
```

`app/templates/components/icons/chat.html`:

```html
<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/>
</svg>
```

`app/templates/components/icons/settings.html`:

```html
<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
        d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
</svg>
```

### Páginas placeholder

`app/templates/pages/home/index.html`:

```html
{% extends "layouts/dashboard.html" %}

{% block title %}Inicio · Mi SaaS{% endblock %}
{% block page_title %}Inicio{% endblock %}

{% block content %}
<div class="space-y-6 max-w-4xl">
  <div class="card">
    <h2 class="text-2xl font-bold">Bienvenido</h2>
    <p class="mt-2 text-slate-600">
      Esta es la página de inicio. Usa el menú lateral para navegar.
    </p>
  </div>

  <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
    <a href="/invoices" class="card hover:shadow-md transition-shadow">
      <h3 class="font-semibold">Facturas</h3>
      <p class="text-sm text-slate-600 mt-1">Sube y gestiona tus facturas.</p>
    </a>
    <a href="/chat" class="card hover:shadow-md transition-shadow">
      <h3 class="font-semibold">Chat</h3>
      <p class="text-sm text-slate-600 mt-1">Asistente conversacional.</p>
    </a>
    <a href="/settings" class="card hover:shadow-md transition-shadow">
      <h3 class="font-semibold">Ajustes</h3>
      <p class="text-sm text-slate-600 mt-1">Configura tu cuenta.</p>
    </a>
  </div>
</div>
{% endblock %}
```

`app/templates/pages/invoices/index.html`:

```html
{% extends "layouts/dashboard.html" %}

{% block title %}Facturas · Mi SaaS{% endblock %}
{% block page_title %}Facturas{% endblock %}

{% block content %}
<div class="card max-w-3xl">
  <h2 class="text-xl font-semibold">Facturas</h2>
  <p class="mt-2 text-slate-600">
    Aquí aparecerán tus facturas procesadas. Se construye en el Paso 13.
  </p>
</div>
{% endblock %}
```

`app/templates/pages/chat/index.html`:

```html
{% extends "layouts/dashboard.html" %}

{% block title %}Chat · Mi SaaS{% endblock %}
{% block page_title %}Chat{% endblock %}

{% block content %}
<div class="card max-w-3xl">
  <h2 class="text-xl font-semibold">Chat</h2>
  <p class="mt-2 text-slate-600">
    El chat conversacional con RAG se construye en pasos posteriores (módulo 2).
  </p>
</div>
{% endblock %}
```

`app/templates/pages/settings/index.html`:

```html
{% extends "layouts/dashboard.html" %}

{% block title %}Ajustes · Mi SaaS{% endblock %}
{% block page_title %}Ajustes{% endblock %}

{% block content %}
<div class="card max-w-3xl">
  <h2 class="text-xl font-semibold">Ajustes</h2>
  <p class="mt-2 text-slate-600">
    Configuración de cuenta y organización (placeholder).
  </p>
</div>
{% endblock %}
```

### Routers web

`app/routes/web/home.py`:

```python
from fastapi import APIRouter, Request

from app.core.templating import render

router = APIRouter(tags=["web"])


@router.get("/")
async def home(request: Request):
    return render(request, full="pages/home/index.html")
```

`app/routes/web/invoices.py`:

```python
from fastapi import APIRouter, Request

from app.core.templating import render

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("")
async def invoices_index(request: Request):
    return render(request, full="pages/invoices/index.html")
```

`app/routes/web/chat.py`:

```python
from fastapi import APIRouter, Request

from app.core.templating import render

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("")
async def chat_index(request: Request):
    return render(request, full="pages/chat/index.html")
```

`app/routes/web/settings.py`:

```python
from fastapi import APIRouter, Request

from app.core.templating import render

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def settings_index(request: Request):
    return render(request, full="pages/settings/index.html")
```

### Actualizar `app/main.py`

```python
from app.routes.web import chat, demo, home, invoices, settings


def create_app() -> FastAPI:
    app = FastAPI(...)

    register_error_handlers(app)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # API
    app.include_router(health.router)

    # Web
    app.include_router(home.router)
    app.include_router(invoices.router)
    app.include_router(chat.router)
    app.include_router(settings.router)
    app.include_router(demo.router)

    return app
```

### Nota sobre `hx-boost`

Con `hx-boost="true"` en `<body>`, todos los `<a>` y `<form>` capturan el click y hacen AJAX en su lugar. HTMX reemplaza `<body>` por el `<body>` de la respuesta (pero por defecto solo el contenido nuevo). Para navegación parcial más fina, en pasos posteriores podemos targetear solo `#main-content` con `hx-target="#main-content" hx-select="#main-content"`.

Por ahora, `hx-boost` por defecto es suficiente para mostrar el patrón.

## Criterios de aceptación

- [x] Visitar `/` carga la página de inicio con sidebar.
- [x] Pulsar "Facturas" en sidebar cambia la URL a `/invoices` y el contenido, sin recargar la página completa (verifica en DevTools: la request es AJAX, no navegación tradicional).
- [x] El back button del navegador funciona y vuelve a la página anterior.
- [x] Pegar `http://localhost:8000/chat` directamente en la barra de URL carga `/chat` con todo el layout.
- [x] El ítem activo del sidebar tiene fondo azul claro.
- [x] En móvil (DevTools responsive < 1024px) el sidebar se oculta.
- [x] `uv run mypy app` pasa.
- [ ] Commit hecho.

## Comandos útiles

```bash
# Ejecutar app
uv run uvicorn app.main:app --reload

# En otra terminal: tailwind watch
./scripts/tailwind_watch.sh

# Verificar tamaño del CSS final
ls -lh app/static/css/app.css

# Inspeccionar peticiones HTMX
# Abrir DevTools → Network → filtrar XHR/Fetch
```

## Lo que NO toca este paso

- Autenticación: Paso 07. Por ahora cualquiera accede.
- Lógica real de las páginas (facturas, chat, settings): pasos posteriores.
- Avatar / nombre de usuario real en topbar: Paso 08.
- Sidebar responsivo con drawer móvil: pulido posterior.

## Posibles problemas

**Navegar con hx-boost recarga la página completa**: probablemente `<body>` no tiene `hx-boost="true"` o el target/select no están bien. Sin `hx-target`/`hx-select` explícitos, HTMX reemplaza `<body>` entero (que es OK pero parpadea más).

**El ítem activo del sidebar no funciona**: verifica que `request` está disponible en el template. En FastAPI con `Jinja2Templates.TemplateResponse`, debes pasar `request` (se hace en `render()` con `request=request`).

**Pegar la URL en barra direcciones da 404 o página rota**: el endpoint debe responder página completa (no fragmento). El helper `render()` ya lo hace: sin `HX-Request`, devuelve `full`.

**Tailwind no purga clases del fragmento porque no las ve**: si una clase solo aparece en fragmentos cargados por HTMX, asegúrate de que el fichero `.html` del fragmento esté indexado en `content` del `tailwind.config.js`.

## Siguiente paso

`Paso06.md` — Modelos de identidad (Tenant, User, Membership), Alembic inicializado, primera migración, y RLS activado en Postgres con test de aislamiento.
