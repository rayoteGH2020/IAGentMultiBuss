# Paso 17 — Integración con Google Calendar

## Objetivo

Conectar la aplicación con Google Calendar mediante OAuth 2.0 para que cada usuario pueda vincular su cuenta de Google y que la plataforma pueda **leer y crear eventos** en su calendario.

Al final del paso:
- El usuario entra en `/settings/integrations`, hace clic en «Conectar Google Calendar», pasa por el flujo OAuth de Google y vuelve a la app con la integración activa.
- La app puede listar eventos del calendario del usuario (útil como base para futuras features: recordatorios de vencimientos, contexto en el chat, etc.).
- Los tokens OAuth se almacenan **cifrados** en BD (pgcrypto), nunca en texto plano.
- El usuario puede desconectar la integración y revocar el acceso desde la misma pantalla.

---

## Pre-requisitos

- Pasos 01–16 completados.
- `app/routes/web/settings.py` ya registrado en `main.py`.
- `pgcrypto` disponible en Postgres (ya habilitado en migraciones anteriores).
- Redis activo (para estado OAuth CSRF).
- Variable `ENCRYPTION_KEY` inyectada por Infisical (ya existe, se reutiliza para cifrar tokens).
- Cuenta de Google con acceso a Google Cloud Console.

---

## Contexto relevante

- `arquitectura.md` §9 (Seguridad): cifrado de tokens OAuth con pgcrypto.
- `arquitectura.md` §5: `data_sources` usa `connection_encrypted bytea` como patrón de referencia.
- `Agents.md` §2: secretos vía Infisical; prohibido `.env`.
- `Agents.md` §3: `routes/web/` → `services/` → `core/`; tokens nunca fuera de `services/`.
- `Agents.md` §7: RLS obligatorio en toda tabla con `tenant_id`.
- Stack HTTP: `httpx` async. No usar `google-api-python-client` (síncrono).

---

## Decisiones arquitectónicas

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| `httpx` async para Google Calendar API | `google-api-python-client` | Síncrono, bloquea event loop |
| Tokens cifrados con `pgcrypto` en `calendar_integrations` | Redis / fichero | Persistencia, RLS, patrón existente en `data_sources` |
| Una integración por `(tenant_id, user_id)` | Solo por `tenant_id` | Google Calendar es una cuenta personal; cada usuario conecta la suya |
| Estado OAuth en Redis (TTL 10 min) | Cookie firmada | No expone estado al cliente; Redis ya está en stack |
| Scopes: `calendar.readonly` + `calendar.events` | Solo `calendar` (full) | Principio de mínimo privilegio; basta para leer y crear |
| Refresh proactivo: si expira en <5 min, refrescar antes de la llamada | Refresh solo en error 401 | Evita fallo en medio de una operación de usuario |

---

## Variables de entorno nuevas

Añadir a Infisical (entorno `development` y `production`):

| Variable | Descripción |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | Client ID de la app OAuth de Google |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Client Secret |

`APP_BASE_URL` ya existe y se usa para construir el `redirect_uri`.

---

## Tareas

### Fase A — Configuración manual en Google Cloud Console (acción manual)

- [x] **[MANUAL]** Ir a [console.cloud.google.com](https://console.cloud.google.com) y crear un proyecto nuevo (o reusar el del proyecto).
- [x] **[MANUAL]** Habilitar la **Google Calendar API**: APIs & Services → Library → buscar "Google Calendar API" → Enable.
- [x] **[MANUAL]** Crear credenciales OAuth 2.0: APIs & Services → Credentials → Create Credentials → OAuth client ID.
  - Application type: **Web application**.
  - Authorized JavaScript origins: `http://localhost:8000` (dev), tu dominio en prod.
  - Authorized redirect URIs: `http://localhost:8000/auth/google/callback` (dev); añadir también la URI de producción cuando proceda.
- [x] **[MANUAL]** Descargar el JSON de credenciales → copiar `client_id` y `client_secret`.
- [x] **[MANUAL]** Si la app está en modo "Testing" en Google, añadir tu cuenta de Google como usuario de prueba en OAuth consent screen → Test users.
- [x] **[MANUAL]** Configurar OAuth Consent Screen: nombre de la app, email de soporte, scopes (`calendar.readonly`, `calendar.events`). En producción, publicar la app.
- [x] **[MANUAL]** Guardar `GOOGLE_OAUTH_CLIENT_ID` y `GOOGLE_OAUTH_CLIENT_SECRET` en Infisical (entorno `development`).

### Fase B — Configuración en código

- [x] Añadir a `app/config.py`:
  ```python
  google_oauth_client_id: str = ""
  google_oauth_client_secret: SecretStr = SecretStr("")
  # Scopes que se solicitan al usuario en el flujo OAuth
  google_calendar_scopes: str = (
      "https://www.googleapis.com/auth/calendar.readonly "
      "https://www.googleapis.com/auth/calendar.events"
  )
  ```

### Fase C — Modelo de datos y migración

- [x] Crear `app/models/calendar_integration.py`:
  - Tabla `calendar_integrations` con campos: `id`, `tenant_id` (FK+RLS), `user_id` (FK), `provider` (default `"google"`), `status` (`active`|`revoked`|`error`), `google_email`, `google_calendar_id` (default `"primary"`), `access_token_enc` (bytea), `refresh_token_enc` (bytea), `token_expires_at` (timestamptz), `scopes` (text), `created_at`, `updated_at`.
  - Constraint `UNIQUE (tenant_id, user_id, provider)`.
- [x] Exportar el modelo en `app/models/__init__.py`.
- [x] Crear migración Alembic `p17_calendar_01_add_calendar_integrations`:
  - Tabla `calendar_integrations` con índices en `(tenant_id, user_id)`.
  - `ALTER TABLE calendar_integrations ENABLE ROW LEVEL SECURITY`.
  - `CREATE POLICY tenant_isolation ON calendar_integrations USING (tenant_id = current_setting('app.current_tenant', true)::uuid)`.
  - `GRANT SELECT, INSERT, UPDATE, DELETE ON calendar_integrations TO saas_app`.
  - Los campos `access_token_enc` y `refresh_token_enc` son `bytea`; el cifrado/descifrado ocurre en la capa de servicio (no en SQL).
- [x] **[MANUAL]** Aplicar la migración: `infisical run -- uv run alembic upgrade head`.

### Fase D — Cliente Google Calendar (httpx async)

- [x] Crear `app/core/google_calendar_client.py`:
  - Clase `GoogleCalendarClient` con `httpx.AsyncClient` interno.
  - Método `exchange_code(code, redirect_uri) -> TokenResponse` — intercambia el code OAuth por access+refresh token.
  - Método `refresh_access_token(refresh_token) -> TokenResponse` — renueva el access token.
  - Método `list_events(access_token, calendar_id, time_min, time_max, max_results) -> list[CalendarEvent]`.
  - Método `create_event(access_token, calendar_id, event_data) -> CalendarEvent`.
  - Método `revoke_token(token) -> None` — llama a `https://oauth2.googleapis.com/revoke`.
  - Pydantic schemas `TokenResponse` y `CalendarEvent` en `app/schemas/calendar.py`.
  - Manejo explícito de errores: 401 → `AuthError("google_token_expired")`; 403 → `ForbiddenError`; otros → `ExternalServiceError`.
- [x] Tests unitarios: `tests/unit/test_google_calendar_client.py`.

### Fase E — Servicio de integración de calendario

- [x] Crear `app/core/crypto.py` — Fernet encrypt/decrypt con `ENCRYPTION_KEY`.
- [x] Crear `app/services/calendar_service.py`:
  - `get_integration(db, tenant_id, user_id) -> CalendarIntegration | None` — lectura simple (bajo RLS).
  - `save_integration(db, tenant_id, user_id, token_response, google_email) -> CalendarIntegration` — upsert cifrado:
    - Cifra `access_token` y `refresh_token` con `ENCRYPTION_KEY` antes de escribir en BD.
    - Usa AES-256-GCM o Fernet (adaptar al helper de cifrado existente en `app/core/`).
  - `revoke_integration(db, tenant_id, user_id) -> None` — llama a `GoogleCalendarClient.revoke_token` y marca status=`revoked`.
  - `get_decrypted_tokens(db, integration) -> tuple[str, str]` — descifra y devuelve `(access_token, refresh_token)`.
  - `ensure_fresh_token(db, integration) -> str` — si `token_expires_at < now + 5min`, refresca y persiste; devuelve `access_token` válido.
  - `list_upcoming_events(db, tenant_id, user_id, days_ahead) -> list[CalendarEvent]` — helper de alto nivel: obtiene integración, asegura token fresco, llama al cliente.
- [x] Tests unitarios: `tests/unit/test_calendar_service.py`.

### Fase F — OAuth state y prevención de CSRF

- [x] Crear `app/core/oauth_state.py`:
  - `generate_state(redis_client, user_id, tenant_id) -> str` — genera nonce aleatorio (32 bytes hex), guarda en Redis con clave `oauth:state:{nonce}` → `{user_id, tenant_id}` y TTL 600 s.
  - `consume_state(redis_client, state) -> dict | None` — lee y elimina la clave; devuelve None si no existe (expiró o reutilización).
- [x] Tests unitarios: `tests/unit/test_oauth_state.py`.

### Fase G — Rutas OAuth y settings

- [x] Añadir rutas en `app/routes/web/integrations.py`:
  - `GET /settings/integrations` — página de integraciones (full + partial según HX-Request).
  - `GET /settings/integrations/google/connect` — genera state, redirige a Google OAuth.
  - `GET /auth/google/callback` — recibe `code` + `state`; valida state en Redis; intercambia code; guarda tokens; redirige a `/settings/integrations`.
  - `POST /settings/integrations/google/disconnect` — revoca y elimina integración.
  - `GET /settings/integrations/google/status` — fragmento HTMX con estado actual (activo/inactivo + email vinculado).
- [x] Plantillas mínimas para que las rutas respondan: `pages/settings/integrations.html`, `components/integration_google_calendar.html`.
- [x] Añadir `/auth/google/callback` a `PUBLIC_PATHS` en `app/core/middleware.py` para que el middleware no rechace la petición sin sesión activa durante el callback.
  - **Ojo**: el callback valida identidad vía state de Redis (`user_id`, `tenant_id`).
- [x] Registrar routers en `app/main.py` (`integrations.router`, `integrations.auth_router`).
- [x] Test integración: `tests/integration/test_calendar_oauth_routes.py`.

### Fase H — Templates y UI

- [x] Crear o ampliar `app/templates/pages/settings/integrations.html` — página full con lista de integraciones disponibles.
- [x] Crear `app/templates/components/integration_google_calendar.html` — card con:
  - Estado (conectado / desconectado).
  - Si conectado: email de la cuenta Google + botón «Desconectar» (`hx-post`, `hx-confirm`).
  - Si desconectado: botón «Conectar con Google» (link a `/settings/integrations/google/connect`).
  - Spinner HTMX en operaciones asíncronas.
- [x] Añadir enlace «Integraciones» en el sidebar (`app/templates/components/sidebar.html`).

### Fase I — Tests

- [x] Unit `tests/unit/test_oauth_state.py` — generar state, consumir una vez, segunda consumición devuelve None.
- [x] Unit `tests/unit/test_calendar_service.py` — cifrado/descifrado de tokens; `ensure_fresh_token` llama a refresh cuando `expires_at` es inminente.
- [x] Unit `tests/unit/test_google_calendar_client.py` — mock httpx; `exchange_code` parsea `TokenResponse`; error 401 levanta `AuthError`.
- [x] Integración `tests/integration/test_calendar_integration.py` — save integration → get integration → tokens descifrados coinciden; revoke → status=`revoked`.
- [x] Test E2E mínimo (no requiere cuenta Google real): mock del callback con code fijo → verifica redirección a `/settings/integrations`.

### Fase J — Observabilidad y seguridad

- [x] Loguear con `structlog` cada evento del flujo OAuth: `calendar.oauth.start`, `calendar.oauth.success`, `calendar.oauth.error`, `calendar.token.refreshed`, `calendar.token.revoked`.
- [x] `audit_log` para: vincular calendario, desvincular calendario (resource_type=`calendar_integration`).
- [x] Nunca loguear tokens en texto plano (usar `structlog.stdlib.BoundLogger`, verificar que los campos son opacos).
- [x] Verificar que `ruff check` y `mypy --strict` pasan antes del commit.

---

## Detalles técnicos

### Flujo OAuth completo

```
Usuario                   App (FastAPI)                 Google
  │                            │                            │
  │── GET /settings/integ. ───>│                            │
  │<── render integrations ────│                            │
  │                            │                            │
  │── GET /integrations/       │                            │
  │   google/connect ─────────>│                            │
  │                            │── genera state (Redis) ───>│
  │<── 302 accounts.google.com/o/oauth2/v2/auth?...        │
  │                            │                            │
  │── (usuario autoriza) ──────────────────────────────────>│
  │<── 302 /auth/google/callback?code=X&state=Y ──────────│
  │                            │                            │
  │── GET /auth/google/        │                            │
  │   callback?code=X&state=Y─>│                            │
  │                            │── valida state en Redis    │
  │                            │── POST /token ────────────>│
  │                            │<── {access_token, refresh} │
  │                            │── cifra y guarda en BD     │
  │<── 302 /settings/integ. ───│                            │
```

### Cifrado de tokens

Reutilizar el helper de cifrado existente si lo hay, o implementar en `app/core/crypto.py`:

```python
from cryptography.fernet import Fernet
import base64

def _fernet(key: str) -> Fernet:
    # key debe ser 32 bytes en base64url; ENCRYPTION_KEY de Settings
    return Fernet(key.encode() if len(key) == 44 else base64.urlsafe_b64encode(key.encode()[:32]))

def encrypt_token(plain: str, key: str) -> bytes:
    return _fernet(key).encrypt(plain.encode())

def decrypt_token(cipher: bytes, key: str) -> str:
    return _fernet(key).decrypt(cipher).decode()
```

### `CalendarIntegration` ORM

```python
class CalendarIntegration(Base):
    __tablename__ = "calendar_integrations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="google")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked | error
    google_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_calendar_id: Mapped[str] = mapped_column(String(255), default="primary")
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "provider", name="uq_calendar_integration_per_user"),
    )
```

### Schemas Pydantic (`app/schemas/calendar.py`)

```python
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int  # segundos hasta expiración
    token_type: str = "Bearer"
    scope: str | None = None

class CalendarEvent(BaseModel):
    id: str
    summary: str | None = None
    description: str | None = None
    start: str  # ISO 8601
    end: str
    html_link: str | None = None

class CalendarIntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    google_email: str | None
    google_calendar_id: str
    scopes: str | None
    created_at: datetime
    updated_at: datetime
```

### URL de autorización Google

```python
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"

def build_auth_url(client_id: str, redirect_uri: str, state: str, scopes: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "access_type": "offline",   # para recibir refresh_token
        "prompt": "consent",         # fuerza re-consent para siempre recibir refresh_token
    }
    return GOOGLE_AUTH_URL + "?" + urlencode(params)
```

### Endpoint callback (esqueleto)

```python
@router.get("/auth/google/callback", include_in_schema=False)
async def google_oauth_callback(
    request: Request,
    code: str,
    state: str,
    redis: RedisDep,
    db_no_tenant: AsyncSession = Depends(get_db_no_tenant),
) -> RedirectResponse:
    ctx = await oauth_state.consume_state(redis, state)
    if ctx is None:
        raise AuthError("OAuth state inválido o expirado")

    settings = get_settings()
    redirect_uri = f"{settings.app_base_url}/auth/google/callback"

    client = GoogleCalendarClient(settings)
    token_resp = await client.exchange_code(code, redirect_uri)

    # Obtener email de la cuenta Google conectada
    google_email = await client.get_user_email(token_resp.access_token)

    await calendar_service.save_integration(
        db_no_tenant,
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        token_response=token_resp,
        google_email=google_email,
    )
    return RedirectResponse(url="/settings/integrations?connected=google", status_code=302)
```

---

## Estructura de ficheros nueva

```
app/
  models/calendar_integration.py
  schemas/calendar.py
  core/
    google_calendar_client.py
    oauth_state.py
    crypto.py                    # si no existe helper de cifrado aún
  services/calendar_service.py
  routes/web/
    integrations.py              # nuevo (o ampliar settings.py)
  config.py                      # añadir google_oauth_client_id / secret / scopes
  core/middleware.py             # añadir /auth/google/callback a PUBLIC_PATHS

templates/
  pages/settings/
    integrations.html
  components/
    integration_google_calendar.html

migrations/versions/
  p17_calendar_01_add_calendar_integrations.py

tests/
  unit/
    test_oauth_state.py
    test_calendar_service.py
    test_google_calendar_client.py
  integration/
    test_calendar_integration.py
```

---

## Verificación manual

1. `infisical run -- uv run alembic upgrade head` → verificar tabla `calendar_integrations` creada.
2. `infisical run -- uv run uvicorn app.main:app --reload`.
3. Abrir `/settings/integrations` → ver card «Google Calendar» en estado desconectado.
4. Clic en «Conectar con Google» → redirige a `accounts.google.com`.
5. Autorizar con cuenta de prueba → vuelve a `/settings/integrations?connected=google`.
6. Verificar que el card muestra el email de la cuenta Google y estado «Activo».
7. En psql: `SELECT google_email, status, token_expires_at FROM calendar_integrations;` → ver fila con email y tokens cifrados (bytea, no texto plano).
8. Clic en «Desconectar» → confirmar → card vuelve a estado desconectado.
9. Verificar en psql que `status = 'revoked'` o la fila fue eliminada.
10. Verificar logs del servidor: eventos `calendar.oauth.success` y `calendar.token.revoked`.

---

## Criterios de aceptación

- El flujo OAuth completo funciona: connect → callback → tokens guardados → disconnect → tokens revocados.
- Los tokens en BD están cifrados (`access_token_enc` y `refresh_token_enc` son `bytea`, nunca `text`).
- `calendar_service.list_upcoming_events` devuelve eventos reales del calendario conectado.
- Estado CSRF: reutilizar un state ya consumido devuelve error (no permite replay).
- RLS: tenant A no puede ver ni usar la integración de tenant B.
- El email de la cuenta Google vinculada se muestra en la UI.
- `mypy --strict` y `ruff check` pasan.
- Tests unitarios e integración pasan con `uv run pytest`.

---

## Comandos útiles

```bash
# Migrar
infisical run -- uv run alembic upgrade head

# Tests
infisical run -- uv run pytest tests/unit/test_calendar_service.py \
  tests/unit/test_oauth_state.py \
  tests/integration/test_calendar_integration.py -v

# Calidad
uv run ruff check . && uv run ruff format --check . && uv run mypy app

# Inspeccionar integraciones en BD
docker exec saas-postgres psql -U saas -d saas -c \
  "SELECT id, google_email, status, token_expires_at FROM calendar_integrations;"
```

---

## Lo que NO toca este paso

- Módulo RAG (módulo 2): embeddings, WhatsApp, chunks.
- Tool de calendario para el chat (`calendar` family en `ToolRegistry`) → Paso 18.
- Jobs ARQ de sincronización periódica de eventos → Paso 18.
- Creación automática de eventos desde facturas (recordatorios de vencimiento) → Paso 18.
- Soporte para múltiples calendarios del mismo usuario (selección de calendario) → Paso 18.
- Google Meet / Google Tasks → fuera de scope.

---

## Posibles problemas

- **Sin `refresh_token` en el callback**: Google solo devuelve `refresh_token` en la primera autorización o con `prompt=consent`. La URL de auth incluye `access_type=offline&prompt=consent` para garantizarlo.
- **Token expirado entre requests**: `ensure_fresh_token` refresca proactivamente si expira en <5 min. Si el refresco falla (token revocado por el usuario en su cuenta Google), marcar `status=error` y notificar en UI.
- **`/auth/google/callback` bloqueado por `AuthMiddleware`**: ese path debe estar en `PUBLIC_PATHS`; la identidad del usuario se recupera del state de Redis (no del JWT), ya que el flujo viene de Google, no del navegador autenticado.
- **Cuenta Google no verificada en modo "Testing"**: añadir el email como "Test user" en Google Cloud Console → OAuth consent screen.
- **`ENCRYPTION_KEY` vacío**: si no está configurado en Infisical, `Fernet` fallará al arrancar. Verificar con `infisical run -- python -c "from app.config import get_settings; print(bool(get_settings().encryption_key.get_secret_value()))"`.

---

## Siguiente paso

**Paso 18** — Usar la integración de calendario: tool `calendar` en `ToolRegistry` para el chat documental (leer eventos como contexto), job ARQ de sincronización periódica, y creación automática de recordatorios de vencimiento de facturas.
