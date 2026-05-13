# Paso 04 — Frontend base: Tailwind, HTMX y Alpine

## Objetivo

Montar el pipeline de frontend sin Node.js: descargar Tailwind CSS standalone como binario, HTMX y Alpine como ficheros estáticos servidos por FastAPI, crear `base.html` y una página `/demo` que demuestra que el stack funciona.

Al final del paso, visitar `http://localhost:8000/demo` muestra una página estilada con Tailwind, un botón con interactividad HTMX y un dropdown con Alpine.

## Pre-requisitos

- Pasos 01-03 completados.
- App FastAPI arrancando.

## Contexto relevante

- `arquitectura.md` sección 7 (Patrón frontend: HTMX + Jinja).
- `Agents.md` sección 5 (Convenciones de templates).

## Tareas

- [x] Descargar Tailwind CLI standalone para tu sistema operativo.
- [x] Crear `tailwind.config.js` mínimo.
- [x] Crear `app/static/css/input.css` con directivas Tailwind.
- [x] Crear `scripts/tailwind_watch.sh` y `scripts/tailwind_build.sh`.
- [x] Descargar HTMX y Alpine como ficheros estáticos a `app/static/js/`.
- [x] Configurar montaje de `/static` en FastAPI.
- [x] Configurar `Jinja2Templates` en FastAPI.
- [x] Crear `app/core/templating.py` con el helper `render()` (patrón página/fragmento).
- [x] Crear `app/templates/base.html`.
- [x] Crear `app/templates/pages/demo.html`.
- [x] Crear `app/templates/components/htmx_demo.html` (fragmento que reemplaza).
- [x] Crear `app/routes/web/__init__.py` y `app/routes/web/demo.py`.
- [x] Montar el router web en `app/main.py`.
- [x] Verificar que `/demo` carga con estilos.
- [x] Verificar que el botón HTMX cambia el contenido sin recargar.
- [x] Verificar que el dropdown Alpine abre/cierra.
- [x] Commit: `feat: frontend base with tailwind, htmx, alpine`.

## Detalles técnicos

### Descargar Tailwind standalone

```bash
# Linux x64
curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
chmod +x tailwindcss-linux-x64
mkdir -p bin
mv tailwindcss-linux-x64 bin/tailwindcss

# macOS arm64
curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-macos-arm64
chmod +x tailwindcss-macos-arm64
mkdir -p bin
mv tailwindcss-macos-arm64 bin/tailwindcss
```

Añadir `bin/tailwindcss` al `.gitignore` (binario grande, cada dev lo descarga).

### `tailwind.config.js`

```javascript
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        // Paleta base, ajustar cuando tengamos branding
        primary: {
          50:  "#f0f9ff",
          500: "#0ea5e9",
          600: "#0284c7",
          700: "#0369a1",
          900: "#0c4a6e",
        },
      },
      fontFamily: {
        sans: ['"Inter"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
```

### `app/static/css/input.css`

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    @apply bg-slate-50 text-slate-900 antialiased;
  }
}

@layer components {
  .btn {
    @apply inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2;
  }
  .btn-primary {
    @apply btn bg-primary-600 text-white hover:bg-primary-700 focus:ring-primary-500;
  }
  .btn-secondary {
    @apply btn bg-white text-slate-700 border border-slate-300 hover:bg-slate-50 focus:ring-primary-500;
  }
  .card {
    @apply rounded-xl bg-white shadow-sm border border-slate-200 p-6;
  }
}
```

### `scripts/tailwind_watch.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./bin/tailwindcss -i app/static/css/input.css -o app/static/css/app.css --watch
```

### `scripts/tailwind_build.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./bin/tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify
```

Hacer ejecutables:
```bash
chmod +x scripts/tailwind_watch.sh scripts/tailwind_build.sh
```

### Descargar HTMX y Alpine

```bash
mkdir -p app/static/js
curl -sL https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js -o app/static/js/htmx.min.js
curl -sL https://unpkg.com/htmx-ext-sse@2.2.2/sse.js -o app/static/js/htmx-sse.js
curl -sL https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js -o app/static/js/alpine.min.js
```

### `app/core/templating.py`

```python
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def render(
    request: Request,
    *,
    full: str,
    partial: str | None = None,
    ctx: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """
    Renderiza una página completa o un fragmento según el header HX-Request.

    - Sin HX-Request: usa `full` (página completa con layout).
    - Con HX-Request: usa `partial` (fragmento HTML).
    - Si `partial` no se pasa, siempre usa `full`.
    """
    ctx = ctx or {}
    is_htmx = request.headers.get("HX-Request") == "true"
    template = partial if (is_htmx and partial) else full
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=ctx,
        status_code=status_code,
    )
```

### `app/templates/base.html`

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="csrf-token" content="{{ csrf_token | default('') }}">

  <title>{% block title %}Mi SaaS{% endblock %}</title>

  <link rel="stylesheet" href="/static/css/app.css">

  <script src="/static/js/htmx.min.js" defer></script>
  <script src="/static/js/htmx-sse.js" defer></script>
  <script src="/static/js/alpine.min.js" defer></script>

  <script>
    document.body && document.body.addEventListener("htmx:configRequest", (e) => {
      // Espacio para inyectar CSRF cuando llegue
    });
  </script>
</head>
<body hx-boost="true" hx-ext="sse">
  {% block body %}
    {% block content %}{% endblock %}
  {% endblock %}

  <div id="toasts" class="fixed top-4 right-4 z-50 space-y-2"></div>
</body>
</html>
```

### `app/templates/pages/demo.html`

```html
{% extends "base.html" %}

{% block title %}Demo · Mi SaaS{% endblock %}

{% block content %}
<main class="min-h-screen flex items-center justify-center p-6">
  <div class="max-w-2xl w-full space-y-6">
    <header>
      <h1 class="text-4xl font-bold text-slate-900">Demo del stack</h1>
      <p class="mt-2 text-slate-600">
        Tailwind compila, HTMX intercepta, Alpine reacciona.
      </p>
    </header>

    <section class="card space-y-4">
      <h2 class="text-xl font-semibold">HTMX · reemplaza un fragmento</h2>
      <p class="text-sm text-slate-600">
        El botón pide <code>/demo/htmx</code> y reemplaza el bloque sin recargar.
      </p>

      <div id="htmx-result">
        <button
          class="btn-primary"
          hx-post="/demo/htmx"
          hx-target="#htmx-result"
          hx-swap="outerHTML"
          hx-indicator="#htmx-spinner">
          Pulsar
        </button>
        <span id="htmx-spinner" class="htmx-indicator ml-2 text-slate-500">cargando…</span>
      </div>
    </section>

    <section class="card space-y-4">
      <h2 class="text-xl font-semibold">Alpine · estado puramente cliente</h2>
      <div x-data="{ open: false }">
        <button class="btn-secondary" @click="open = !open" x-text="open ? 'Cerrar' : 'Abrir'">
        </button>
        <div x-show="open" x-cloak x-transition class="mt-3 p-4 bg-slate-100 rounded-lg">
          Contenido controlado por Alpine, sin servidor.
        </div>
      </div>
    </section>

    <footer class="text-center text-sm text-slate-500">
      Si ves estilos, el botón cambia el texto al pulsar y el dropdown abre: el stack funciona.
    </footer>
  </div>
</main>
{% endblock %}
```

### `app/templates/components/htmx_demo.html`

```html
<div id="htmx-result" class="p-4 bg-emerald-50 border border-emerald-200 rounded-lg">
  <p class="text-emerald-800 font-medium">
    ✓ Fragmento devuelto por el servidor a las {{ now }}
  </p>
  <button
    class="btn-secondary mt-3"
    hx-post="/demo/htmx"
    hx-target="#htmx-result"
    hx-swap="outerHTML">
    Pulsar de nuevo
  </button>
</div>
```

### Añadir CSS para `htmx-indicator` y `[x-cloak]`

En `app/static/css/input.css`, añadir al final de `@layer base`:

```css
@layer base {
  /* ... resto ... */
  .htmx-indicator { opacity: 0; transition: opacity 200ms ease-in; }
  .htmx-request .htmx-indicator { opacity: 1; }
  .htmx-request.htmx-indicator { opacity: 1; }
  [x-cloak] { display: none !important; }
}
```

### `app/routes/web/demo.py`

```python
from datetime import datetime

from fastapi import APIRouter, Request

from app.core.templating import render

router = APIRouter(tags=["demo"])


@router.get("/demo")
async def demo_page(request: Request):
    return render(request, full="pages/demo.html")


@router.post("/demo/htmx")
async def demo_htmx(request: Request):
    return render(
        request,
        full="components/htmx_demo.html",
        partial="components/htmx_demo.html",
        ctx={"now": datetime.now().strftime("%H:%M:%S")},
    )
```

### `app/routes/web/__init__.py`

```python
# Exporta routers
```

### Modificar `app/main.py` para montar estáticos y router web

```python
from fastapi.staticfiles import StaticFiles

from app.routes.web import demo


def create_app() -> FastAPI:
    app = FastAPI(...)

    register_error_handlers(app)

    # Static
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # API
    app.include_router(health.router)

    # Web
    app.include_router(demo.router)

    return app
```

## Criterios de aceptación

- [x] `./scripts/tailwind_build.sh` genera `app/static/css/app.css`.
- [x] `./scripts/tailwind_watch.sh` recompila cuando editas `input.css` o un `.html`.
- [x] `http://localhost:8000/demo` muestra la página con estilos Tailwind aplicados.
- [x] Pulsar el botón HTMX cambia el contenido SIN recargar la página (verifica en DevTools → Network: petición POST a `/demo/htmx` y respuesta HTML).
- [x] El dropdown Alpine abre/cierra al pulsar.
- [x] `app/static/css/app.css` está en `.gitignore` (es output compilado).
- [x] `bin/tailwindcss` está en `.gitignore`.
- [x] Commit hecho.

## Comandos útiles

```bash
# Compilar Tailwind una vez
./scripts/tailwind_build.sh

# Watch durante desarrollo (en otra terminal)
./scripts/tailwind_watch.sh

# Comprobar tamaño del CSS final
ls -lh app/static/css/app.css

# Servir y probar
uv run uvicorn app.main:app --reload
# y abrir http://localhost:8000/demo
```

### Workflow de desarrollo recomendado

Tres terminales:
1. `./scripts/dev_up.sh` (servicios Docker) — una vez al día.
2. `./scripts/tailwind_watch.sh` — Tailwind recompilando.
3. `uv run uvicorn app.main:app --reload` — servidor.

## Lo que NO toca este paso

- Layout de dashboard con sidebar: Paso 05.
- Autenticación / proteger páginas: Paso 07.
- Páginas reales de facturas, chat, etc.: pasos posteriores.

## Posibles problemas

**Tailwind no genera nada / archivo vacío**: revisa que `content` en `tailwind.config.js` apunta a `./app/templates/**/*.html`. Si las clases no aparecen en HTML, Tailwind las purga.

**El estilo no se aplica**: confirma que la ruta del CSS es `/static/css/app.css` y que el archivo existe. Mira la consola del navegador.

**HTMX no funciona**: abre DevTools → Console. Si dice `htmx is not defined`, el `<script src="/static/js/htmx.min.js">` no carga. Verifica la ruta.

**Alpine: "missing x-show"**: error de timing. Asegura que `defer` está en el `<script>` y que `[x-cloak]` está en el CSS.

**El swap HTMX produce contenido sin estilos**: el fragmento devuelto debe seguir usando clases Tailwind. Como Tailwind purga, asegúrate de que la clase está usada en algún `.html` indexado por `content`.

## Siguiente paso

`Paso05.md` — Layout de dashboard con sidebar, navegación con `hx-boost`, y 4 páginas placeholder (Inicio, Facturas, Chat, Ajustes) que demuestren la navegación.
