# VisualArq — Arquitectura de arranque, login y página principal

> Diagrama y descripción de todos los elementos que intervienen desde que se ejecuta el servidor hasta que el usuario ve `/` autenticado.

---

## 1. Mapa de componentes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  MÁQUINA DEL DESARROLLADOR                                                  │
│                                                                             │
│  ┌──────────────┐    inyecta vars    ┌──────────────────────────────────┐   │
│  │   Infisical  │ ─────────────────► │  infisical run -- uvicorn ...    │   │
│  │   CLI        │   en tiempo de     │                                  │   │
│  │              │   ejecución        │  FastAPI  (localhost:8000)        │   │
│  └──────────────┘                   │  ┌────────────────────────────┐  │   │
│                                     │  │  AuthMiddleware             │  │   │
│  Infisical Cloud ◄──── auth ──────  │  │  (starlette BaseHTTP)      │  │   │
│  (secretos reales)                  │  └────────────────────────────┘  │   │
│                                     │  ┌──────────────────────────┐    │   │
│  ┌──────────────┐                   │  │  Routers                 │    │   │
│  │  ngrok       │◄── túnel HTTP ──► │  │  /login  /signup         │    │   │
│  │  (opcional   │                   │  │  /logout /              │    │   │
│  │   para       │                   │  │  /documents /chat ...    │    │   │
│  │   webhooks)  │                   │  └──────────────────────────┘    │   │
│  └──────┬───────┘                   └──────────────────────────────────┘   │
│         │                                    │              │               │
│         │                            ┌───────┘              └──────────┐    │
│         │                            ▼                                 ▼    │
│         │                   ┌──────────────────┐          ┌──────────────┐ │
│         │                   │  PostgreSQL 16   │          │  Redis 7     │ │
│         │                   │  (Docker)        │          │  (Docker)    │ │
│         │                   │  + pgvector      │          │  cache/cola  │ │
│         │                   │  + RLS activo    │          └──────────────┘ │
│         │                   └──────────────────┘                           │
│         │                                                                   │
└─────────┼───────────────────────────────────────────────────────────────────┘
          │
          ▼  POST https://<id>.ngrok-free.app/api/webhooks/clerk
┌─────────────────────────────────────────────────────────────────────────────┐
│  CLERK  (SaaS externo)                                                      │
│                                                                             │
│  • Aloja la UI de login/signup (clerk.browser.js embebido en el HTML)       │
│  • Emite JWT firmado RS256 (cookie __session)                               │
│  • Expone JWKS:  https://<instancia>.clerk.accounts.dev/.well-known/        │
│                   jwks.json                                                 │
│  • Gestiona Organizations (multi-tenant)                                    │
│  • Envía webhooks con Svix (user.created, org.created, …)                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Flujo de arranque del servidor

```
Desarrollador
     │
     │  1. infisical run -- uv run uvicorn app.main:app --reload
     │
     ▼
Infisical CLI
     │  Lee project/environment configurado en local
     │  Descarga secretos de Infisical Cloud:
     │    DATABASE_URL, REDIS_URL, CLERK_SECRET_KEY,
     │    CLERK_PUBLISHABLE_KEY, CLERK_JWKS_URL,
     │    CLERK_WEBHOOK_SECRET, APP_SECRET_KEY, …
     │  Los inyecta como variables de entorno en el proceso hijo
     │
     ▼
uvicorn  →  app.main:create_app()
     │
     ├─ register_error_handlers(app)
     ├─ app.add_middleware(AuthMiddleware)   ← se instala antes de todos los routers
     ├─ app.mount("/static", StaticFiles)
     ├─ include_router(health)
     ├─ include_router(webhooks)
     ├─ include_router(auth_routes)         ← /login, /signup, /logout
     ├─ include_router(home)               ← /
     ├─ include_router(invoices)
     ├─ include_router(chat)
     ├─ include_router(settings)
     └─ include_router(demo)
     │
     ▼
lifespan (asynccontextmanager)
     ├─ configure_logging()                ← structlog ConsoleRenderer en dev
     ├─ get_engine()  →  SQLAlchemy async pool  →  Postgres
     └─ get_redis()   →  redis.asyncio          →  Redis

Servidor listo en http://localhost:8000
```

---

## 3. Flujo de login (primera vez / sin cookie)

```
Browser                     FastAPI                  Clerk Cloud         Postgres
  │                            │                         │                  │
  │  GET /  (sin cookie)        │                         │                  │
  │ ─────────────────────────► │                         │                  │
  │                            │ AuthMiddleware           │                  │
  │                            │  _extract_token() → None│                  │
  │                            │  (no hay Bearer ni       │                  │
  │                            │   cookie __session)      │                  │
  │                            │                         │                  │
  │                            │  call_next(request)      │                  │
  │                            │  home() → current_user()│                  │
  │                            │  request.state.user=None │                  │
  │                            │  → raise AuthError(401) │                  │
  │ ◄───────────────────────── │                         │                  │
  │  401 / redirect /login      │                         │                  │
  │                            │                         │                  │
  │  GET /login                │                         │                  │
  │ ─────────────────────────► │                         │                  │
  │                            │  ruta pública (_is_public) → salta AuthMiddleware
  │                            │  login_page()            │                  │
  │                            │  render("pages/auth/login.html")            │
  │                            │  ctx = {                 │                  │
  │                            │    clerk_pub_key: pk_... │                  │
  │                            │    clerk_frontend_host:  │                  │
  │                            │      <instancia>.clerk.  │                  │
  │                            │      accounts.dev        │                  │
  │                            │  }                       │                  │
  │ ◄───────────────────────── │                         │                  │
  │  HTML con <div id="clerk-sign-in">                   │                  │
  │  + <script src="https://clerk.../clerk.browser.js">  │                  │
  │                            │                         │                  │
  │  (browser carga clerk.browser.js desde Clerk CDN)   │                  │
  │ ─────────────────────────────────────────────────── ►│                  │
  │ ◄─────────────────────────────────────────────────── │                  │
  │                            │                         │                  │
  │  Usuario rellena email+pass │                         │                  │
  │  Clerk.mountSignIn() gestiona el form                │                  │
  │  → POST a Clerk (autenticación)                      │                  │
  │ ─────────────────────────────────────────────────── ►│                  │
  │ ◄─────────────────────────────────────────────────── │                  │
  │  Clerk emite JWT (RS256)    │                         │                  │
  │  y lo escribe en cookie __session (httpOnly)         │                  │
  │                            │                         │                  │
  │  Clerk redirige a After-sign-in URL: http://localhost:8000/             │
  │ ─────────────────────────► │                         │                  │
```

---

## 4. Flujo de request autenticado — GET /

```
Browser                    AuthMiddleware            auth_service        Postgres
  │                              │                       │                  │
  │  GET /                       │                       │                  │
  │  Cookie: __session=eyJ...    │                       │                  │
  │ ────────────────────────────►│                       │                  │
  │                              │                       │                  │
  │                    1. _extract_token()               │                  │
  │                       cookie "__session" → token     │                  │
  │                              │                       │                  │
  │                    2. verify_clerk_jwt(token)         │                  │
  │                       PyJWKClient descarga JWKS      │                  │
  │                       (cache 1h en memoria)          │                  │
  │                       ─────────────────────────────► Clerk JWKS        │
  │                       ◄───────────────────────────── public keys       │
  │                       jwt.decode RS256               │                  │
  │                       claims = {                     │                  │
  │                         sub: "user_2abc...",         │                  │
  │                         org_id: "org_xyz...",        │                  │
  │                         org_role: "org:admin"        │                  │
  │                       }                              │                  │
  │                              │                       │                  │
  │                    3. get_sessionmaker() → AsyncSession                 │
  │                              │                       │                  │
  │                    4. resolve_user(session, "user_2abc...")              │
  │                              │ ──────────────────── ►│                  │
  │                              │                       │ SELECT users     │
  │                              │                       │ WHERE clerk_user │
  │                              │                       │ _id=?           ►│
  │                              │                       │ ◄─────────────── │
  │                              │                       │  (si no existe:  │
  │                              │                       │   GET api.clerk  │
  │                              │                       │   .com/v1/users/ │
  │                              │                       │   ... + INSERT)  │
  │                              │ ◄───────────────────── user: User        │
  │                              │                       │                  │
  │                    5. resolve_tenant(session, "org_xyz...")              │
  │                              │ ──────────────────── ►│                  │
  │                              │                       │ SELECT tenants   │
  │                              │                       │ WHERE clerk_org  │
  │                              │                       │ _id=?           ►│
  │                              │                       │ ◄─────────────── │
  │                              │ ◄───────────────────── tenant: Tenant    │
  │                              │                       │                  │
  │                    6. set_tenant_context(session,                       │
  │                         tenant.id)                   │                  │
  │                       SET LOCAL app.current_tenant   │                  │
  │                       = '<uuid>'                    ►│                  │
  │                              │                       │ (RLS activo)     │
  │                              │                       │                  │
  │                    7. ensure_membership(session,      │                  │
  │                         user.id, tenant.id, "admin") │                  │
  │                              │ ──────────────────── ►│ SELECT/INSERT    │
  │                              │ ◄───────────────────── membership        │
  │                              │                       │                  │
  │                    8. session.commit()               ►│                  │
  │                              │                       │                  │
  │                    9. request.state.user    = user    │                  │
  │                       request.state.tenant  = tenant │                  │
  │                       request.state.membership = m   │                  │
  │                              │                       │                  │
  │                    call_next(request) → home()        │                  │
  │                              │                       │                  │
  │                         home(request, user=CurrentUser)                 │
  │                         current_user() → request.state.user (User)      │
  │                         render("pages/home/index.html",                 │
  │                                ctx={"user": user})                      │
  │ ◄────────────────────────────│                       │                  │
  │  200 OK — HTML completo del dashboard                │                  │
  │  sidebar + topbar (nombre del usuario) + contenido   │                  │
```

---

## 5. Flujo de webhook de Clerk (evento user.created / org.created)

```
Clerk Cloud                  ngrok túnel               FastAPI
     │                           │                        │
     │  POST webhook             │                        │
     │  (svix-id, svix-timestamp,│                        │
     │   svix-signature headers) │                        │
     │ ────────────────────────► │                        │
     │                           │  túnel TCP             │
     │                           │ ─────────────────────► │
     │                           │                        │ POST /api/webhooks/clerk
     │                           │                        │ (ruta pública: no pasa AuthMiddleware)
     │                           │                        │
     │                           │                        │ 1. Webhook(CLERK_WEBHOOK_SECRET)
     │                           │                        │    .verify(payload, headers)
     │                           │                        │    → svix valida HMAC
     │                           │                        │
     │                           │                        │ 2. Según event_type:
     │                           │                        │    "user.created"
     │                           │                        │    → resolve_user(db, data["id"])
     │                           │                        │    "organization.created"
     │                           │                        │    → resolve_tenant(db, data["id"])
     │                           │                        │
     │ ◄──────────────────────── │ ◄──────────────────── │ 200 {"received": true}
```

> ngrok solo es necesario para webhooks. La app funciona sin él; ngrok solo se necesita para que Clerk Cloud pueda alcanzar `localhost:8000`.

---

## 6. Rutas y su nivel de protección

| Ruta | Método | Protegida | Quién la maneja |
|---|---|---|---|
| `/health` | GET | No | `routes/api/health.py` |
| `/health/db` | GET | No | `routes/api/health.py` |
| `/health/redis` | GET | No | `routes/api/health.py` |
| `/login` | GET | No | `routes/web/auth.py` |
| `/signup` | GET | No | `routes/web/auth.py` |
| `/logout` | GET | No | `routes/web/auth.py` |
| `/api/webhooks/clerk` | POST | No (firma Svix) | `routes/api/webhooks.py` |
| `/static/*` | GET | No | StaticFiles |
| `/demo*` | GET/POST | No | `routes/web/demo.py` |
| `/` | GET | Sí (`current_user`) | `routes/web/home.py` |
| `/documents*` | GET | Sí | `routes/web/documents.py` |
| `/chat*` | GET | Sí | `routes/web/chat.py` |
| `/settings*` | GET | Sí | `routes/web/settings.py` |

---

## 7. Secretos en juego — nombres en Infisical

| Variable | Quién la usa | Cuándo |
|---|---|---|
| `DATABASE_URL` | `app/config.py` → `core/db.py` | Arranque / cada request DB |
| `REDIS_URL` | `app/config.py` → `core/cache.py` | Arranque / cache |
| `APP_SECRET_KEY` | `app/config.py` | Firmas internas |
| `CLERK_PUBLISHABLE_KEY` | `routes/web/auth.py` | Renderizar login.html / signup.html |
| `CLERK_SECRET_KEY` | `core/security.py` | `fetch_clerk_user` / `fetch_clerk_org` |
| `CLERK_JWKS_URL` | `core/security.py` → `PyJWKClient` | Verificar JWT en cada request |
| `CLERK_WEBHOOK_SECRET` | `routes/api/webhooks.py` | Validar firma Svix |
| `LANGFUSE_PUBLIC_KEY` | (disponible, usada en Paso 10) | Trazas LLM |
| `LANGFUSE_SECRET_KEY` | (disponible, usada en Paso 10) | Trazas LLM |
| `LANGFUSE_HOST` | (disponible) | `http://localhost:3000` en dev |
| `ANTHROPIC_API_KEY` | (disponible, usada en Paso 10) | Llamadas Claude |
| `GOOGLE_API_KEY` | (disponible, usada en Paso 10) | Llamadas Gemini |

---

## 8. Capas del código — visión vertical de una request a `/`

```
HTTP request  GET /  Cookie: __session=eyJ...
      │
      ▼
AuthMiddleware  (app/core/middleware.py)
  ├── _extract_token()          → lee cookie __session
  ├── verify_clerk_jwt()        → app/core/security.py :: PyJWKClient + jwt.decode
  ├── resolve_user()            → app/services/auth_service.py :: SELECT/INSERT users
  ├── resolve_tenant()          → app/services/auth_service.py :: SELECT/INSERT tenants
  ├── set_tenant_context()      → app/core/db.py :: SET LOCAL app.current_tenant
  ├── ensure_membership()       → app/services/auth_service.py :: SELECT/INSERT memberships
  └── request.state.{user, tenant, membership} = ...
      │
      ▼
Router  GET /  (app/routes/web/home.py)
  └── home(request, user: CurrentUser)
       └── current_user()  →  app/deps.py  →  request.state.user
           │
           ▼
       render(request, full="pages/home/index.html", ctx={"user": user})
           └── app/core/templating.py :: Jinja2Templates
               ├── extiende  layouts/dashboard.html
               │     ├── include components/sidebar.html  (ítem activo según URL)
               │     └── include components/topbar.html   (user.name)
               └── block content: tarjetas de inicio
      │
      ▼
200 OK  →  HTML completo con Tailwind + HTMX + Alpine
```

---

## 9. Comandos para arrancar el entorno completo

```bash
# Terminal 1 — servicios Docker (Postgres, Redis, Langfuse)
./scripts/dev_up.sh

# Terminal 2 — Tailwind en watch (recompila CSS)
./scripts/tailwind_watch.sh

# Terminal 3 — FastAPI con secretos inyectados por Infisical
infisical run -- uv run uvicorn app.main:app --reload

# Terminal 4 (opcional) — túnel ngrok para webhooks Clerk
ngrok http 8000
# → copiar URL https://<id>.ngrok-free.app/api/webhooks/clerk
#   al Dashboard de Clerk → Webhooks → Endpoint URL
```

---

## 10. Dependencias externas en dev local

| Servicio | URL local | Arranque |
|---|---|---|
| PostgreSQL 16 + pgvector | `localhost:5432` | `./scripts/dev_up.sh` |
| Redis 7 | `localhost:6379` | `./scripts/dev_up.sh` |
| Langfuse (self-hosted) | `http://localhost:3000` | `./scripts/dev_up.sh` |
| FastAPI app | `http://localhost:8000` | `infisical run -- uvicorn ...` |
| Tailwind watcher | — | `./scripts/tailwind_watch.sh` |
| ngrok túnel | `https://<id>.ngrok-free.app` | `ngrok http 8000` (opcional) |
| Clerk (SaaS) | `https://clerk.com` | Cuenta configurada |
| Infisical (SaaS) | `https://app.infisical.com` | CLI autenticado |
