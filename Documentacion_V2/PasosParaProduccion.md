# PasosParaProduccion

Fecha: 2026-08-05 · Actualizado: 2026-09-23
Estado: checklist operativa go-live. Control comercial (`Paso02`–`Paso04`) **cerrado en codigo**; bloqueadores restantes son ops (secretos, Infisical prod, QA, deploy).
Fuente: consolidado de `Documentacion_V2` (`Paso00`–`Paso10`, `Seguridad_V2`, `SADM_V2`, `Arquitectura_V2`, `Planes_Entitlements`, `Backlog_Priorizado`) y `docs/environment-variables.md`.

Usar este fichero como guia de go-live. El detalle de cada control vive en su `PasoXX_*.md`; aqui solo el orden de trabajo y lo que hay que comprobar.

## 0. Antes de empezar

Reglas:

- Fuente de verdad: `Documentacion_V2/`. Si un doc antiguo contradice V2, gana V2.
- Secretos solo en Infisical. Nunca `.env` en el repo ni valores reales en Markdown/chats.
- No abrir produccion con `WEBHOOK_ALLOW_UNSIGNED=true` ni `LANGFUSE_CAPTURE_CONTENT=true`.
- Arquitectura de despliegue prevista: monolito (API + worker ARQ), Postgres, Redis, R2, Clerk, Langfuse self-hosted (p. ej. Hetzner/Coolify). Sin Kubernetes/microservicios.

Decide el alcance del primer go-live:

| Alcance | Implicacion |
| --- | --- |
| Soft launch / tenants piloto | Puedes aceptar gaps documentados si seguridad P0 critica esta cerrada. |
| Produccion comercial | P0 seguridad ops + planes/gates/cuotas ya en codigo; falta Infisical prod, QA y Stripe operativa. |

Marca cada casilla solo cuando haya evidencia (comando, captura, ticket o fecha).

---

## 1. Bloqueadores P0 (no saltar)

Segun `Backlog_Priorizado.md` y `Paso01` / `Paso10`: no considerar produccion "cerrada" si estos puntos siguen abiertos sin aceptacion explicita por escrito.

### 1.1 Seguridad residual (`Paso01`, `Seguridad_V2`)

- [ ] Secretos de documentacion historica rotados en proveedores e Infisical (no basta con borrar texto).
- [ ] `detect-secrets` / busqueda sin secretos reales en repo.
- [ ] Sync Clerk membership: webhooks `organizationMembership.created|updated|deleted` activos y probados.
- [ ] JWT audience/`azp`: `CLERK_JWT_AZP_ALLOWLIST` y/o `CLERK_JWT_AUDIENCE_ALLOWLIST` rellenados en Infisical **prod** (obligatorio con `CLERK_JWKS_URL` + `APP_ENV=production`).
- [ ] Webhooks: limite de body antes de parsear + dedupe anti-replay (WhatsApp message id, Telegram update id, Clerk/Stripe cuando aplique).
- [ ] OCR/imagenes knowledge: limites de `media_limits` antes de OCR/LLM.
- [ ] `LANGFUSE_CAPTURE_CONTENT=false` en prod (Settings falla si esta a true fuera de development).

### 1.2 Control comercial / coste (`Paso02`–`Paso04`)

**Codigo cerrado** (2026-09-23): catalogo, gates y cuotas estan en el repo (`p64`). No hace falta excepcion por "salir sin planes".

- [x] Catalogo de planes en BD (`Paso02`).
- [x] Gates por feature en rutas, sidebar, workers y webhooks (`Paso03`).
- [x] Cuotas, budgets y circuit breakers (`Paso04`).
- [x] Kill-switch documentado: `ENTITLEMENTS_DISABLED_FEATURES` + parar worker ARQ / rotar claves LLM.

Ops pendiente (no bloquea el modelo de planes en codigo):

- [ ] Secretos e Infisical `prod`/`staging` (ver §3).
- [ ] Price IDs Stripe en `plans.stripe_price_id` si cobro self-serve (Paso09).

### 1.3 Decision Go / No-Go

- [ ] Lista de gaps abiertos revisada (seguridad, coste, producto).
- [ ] Fecha, responsable y firma de aceptacion de riesgos residuales.

---

## 2. Saneamiento y auditoria previa

Detalle: `Paso00_Auditoria_Base.md`.

```powershell
git status --short
git ls-files Documentacion
git grep -n "TOKEN\|PASSWORD\|SECRET\|API_KEY\|Bearer"
infisical run --env=prod -- uv run detect-secrets scan --all-files
infisical run --env=prod -- uv run alembic heads
infisical run --env=prod -- uv run alembic current
```

- [x] No hay secretos reales activos en docs versionados (detect-secrets en commit de producto; dumps locales fuera de git).
- [ ] Credenciales expuestas historicamente invalidadas en Clerk, LLM, R2, Postgres, Redis, Langfuse, WA/TG, etc.
- [x] `Documentacion/` eliminada del repo (`eae1c00`); guia vigente = `Documentacion_V2/`.
- [x] HEAD de migraciones conocido: codigo planes `p64_plans_entitlements_01`; Stripe `p65_stripe_billing_01` (aplicar en cada entorno).

---

## 3. Infisical entorno `prod` (variables)

Referencia completa: `docs/environment-variables.md`. Valores solo en Infisical.

### 3.1 App y seguridad HTTP

- [ ] `APP_ENV=production`
- [ ] `APP_SECRET_KEY` aleatorio largo (rotar invalida sesiones)
- [ ] `APP_BASE_URL=https://<dominio-publico>`
- [ ] `LOG_LEVEL=INFO` (o superior)
- [ ] `SECURITY_ALLOWED_HOSTS` = dominio(s) reales (CSV/JSON), sin `*`
- [ ] `SECURITY_HTTPS_REDIRECT=true` (o TLS terminado en proxy con redirect equivalente)
- [ ] `SECURITY_HSTS_ENABLED=true`
- [ ] `WEBHOOK_ALLOW_UNSIGNED=false`
- [ ] `ENCRYPTION_KEY` (Fernet/AES-256 en base64) generada para prod; no reutilizar la de CI/dev

### 3.2 Datos y colas

- [ ] `DATABASE_URL` Postgres prod (`postgresql+asyncpg://…`), backups activos
- [ ] `REDIS_URL` Redis prod (ARQ, cache, rate limits)
- [ ] Extensiones Postgres: `vector`, `pgcrypto`, `uuid-ossp` (u otras que usen las migraciones)

### 3.3 Storage R2

- [ ] `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- [ ] `R2_BUCKET` dedicado a prod (no el de dev)
- [ ] `R2_ENDPOINT_URL` vacio/null en prod (no MinIO)
- [ ] `R2_PUBLIC_URL` solo si aplica; objetos de cliente no publicos indebidamente
- [ ] `R2_REGION=auto`

### 3.4 Clerk

- [ ] `CLERK_SECRET_KEY` / `CLERK_PUBLISHABLE_KEY` de la instancia **prod**
- [ ] `CLERK_JWKS_URL` de esa instancia
- [ ] `CLERK_WEBHOOK_SECRET` del endpoint prod
- [ ] `CLERK_JWT_AZP_ALLOWLIST` y/o `CLERK_JWT_AUDIENCE_ALLOWLIST` (ver `Paso01` §3 tareas manuales)
- [ ] `ADMIN_CLERK_ORG_ID` = org SADM
- [ ] `SUPERADMIN_CLERK_USER_IDS` allowlist si se usa defensa en profundidad

### 3.5 LLM y observabilidad

- [ ] `GOOGLE_API_KEY` / `ANTHROPIC_API_KEY` / `VOYAGE_API_KEY` segun modelos activos
- [ ] Overrides `LLM_MODEL_*` solo si estan medidos
- [ ] `LANGFUSE_HOST` = instancia self-hosted prod
- [ ] `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` del proyecto prod
- [ ] `LANGFUSE_CAPTURE_CONTENT=false`
- [ ] `LLM_RETRY_TRANSIENT_ERRORS=true` (recomendado en `Paso10`)

### 3.6 Integraciones opcionales segun alcance

- [ ] SMTP: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, TLS
- [ ] Google Calendar: OAuth client id/secret y redirect URIs de prod
- [ ] WhatsApp: `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`
- [ ] Telegram: secretos de bot/webhook por tenant cifrados; verify en prod
- [ ] `METRICS_TOKEN` si se usa `/metrics/...`

### 3.7 Comprobacion de arranque Settings

```powershell
infisical run --env=prod -- uv run python -c "from app.config import get_settings; s=get_settings(); print(s.app_env, bool(s.clerk_jwks_url), s.langfuse_capture_content)"
```

- [ ] Arranca sin `ValidationError`
- [ ] Imprime `production`, JWKS configurado, `LANGFUSE_CAPTURE_CONTENT` falso

---

## 4. Clerk Dashboard (prod)

Detalle sync: `Paso01` §2; SADM: `SADM_V2.md`.

- [ ] Aplicacion/instancia Clerk de produccion (no reutilizar keys de development)
- [ ] Dominios / redirect URLs = `APP_BASE_URL`
- [ ] Webhook endpoint `https://<dominio>/api/webhooks/clerk` con Svix secret en Infisical
- [ ] Eventos suscritos al menos:
  - `user.created` (si se usa)
  - `organization.created` (si se usa)
  - `organizationMembership.created`
  - `organizationMembership.updated`
  - `organizationMembership.deleted`
- [ ] Probar delivery 200 tras remove/downgrade de membership
- [ ] Org SADM creada; usuarios admin en allowlist si aplica
- [ ] Usuarios y orgs: solo Clerk Dashboard; sin provision desde SADM/app (`Decision_Log` D005 / `SADM_V2` / `Paso05`)

---

## 5. Infraestructura y procesos

Segun `Arquitectura_V2` §10 y `Paso10`.

### 5.1 Servicios

- [ ] Postgres reachable, backups automaticos y restore probado al menos una vez
- [ ] Redis reachable
- [ ] Cloudflare R2 bucket + politicas (sin listado publico indebido)
- [ ] TLS en el dominio (Let's Encrypt / proxy Coolify/Caddy/nginx)
- [ ] Firewall: solo 443 (y SSH acotado) hacia la VPS

### 5.2 Procesos a mantener vivos

- [ ] API: `uvicorn app.main:app` (o equivalente bajo process manager)
- [ ] Worker: `arq app.jobs.settings.WorkerSettings`
- [ ] Reinicio automatico ante crash (systemd/Coolify/Docker restart)
- [ ] Logs centralizados o al menos rotacion (structlog → stdout)

### 5.3 Healthchecks

- [ ] `GET /health` OK
- [ ] `GET /health/db` OK
- [ ] `GET /health/redis` OK
- [ ] Docs OpenAPI deshabilitados o no publicos en production (`/docs` no expuesto)

---

## 6. Migraciones y despliegue de codigo

- [ ] Backup BD inmediatamente antes de migrar
- [ ] Revisar a mano el diff de migraciones pendientes (`alembic history` / archivos en `migrations/versions/`)
- [ ] `infisical run --env=prod -- uv run alembic upgrade head`
- [ ] `alembic current` = head documentado en el release
- [ ] Desplegar commit etiquetado (tag o SHA)
- [ ] Reiniciar API + worker con las mismas variables Infisical prod
- [ ] Smoke: login + una ruta critica

Comandos tipicos de calidad pre-deploy (staging o CI verde primero):

```powershell
infisical run --env=prod -- uv run alembic upgrade head
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
infisical run -- uv run pytest tests/unit -q
infisical run -- uv run pytest tests/integration -q
```

Evals (coste API; ejecutar en ventana controlada):

```powershell
infisical run -- uv run python -m app.evals.runners.extraction
infisical run -- uv run python -m app.evals.runners.knowledge_retrieval
infisical run -- uv run python -m app.evals.runners.knowledge_qa
infisical run -- uv run python -m app.evals.runners.chat_documents
```

E2E si hay credenciales:

```powershell
infisical run -- uv run pytest tests/e2e -q
```

- [ ] CI en `main` verde o excepciones documentadas
- [ ] Tests automaticos verdes o gaps aceptados por escrito

---

## 7. QA manual minima en produccion (o staging espejo)

Detalle: `Paso10_QA_Release_Produccion.md`. Preferible completar en staging identico y repetir smoke en prod.

### 7.1 Auth y tenant

- [ ] Login admin tenant
- [ ] Login member/viewer (matriz de menu: Chat/Citas vs Documentos/Ajustes)
- [ ] Tenant A no ve datos de Tenant B (RLS)
- [ ] CSRF bloquea mutacion sin token
- [ ] Usuario eliminado de org en Clerk → sin acceso (webhook + membership `is_active=false`)
- [ ] Downgrade admin → member → pierde rutas admin
- [ ] SADM: solo org + rol admin (+ allowlist); member de org SADM no entra a `/sadm`

### 7.2 Documentos

- [ ] Upload factura / ticket
- [ ] Documento invalido falla con mensaje claro
- [ ] Retry / dismiss
- [ ] Worker procesa cola
- [ ] Objetos en R2 no publicos indebidamente

### 7.3 Knowledge y chat

- [ ] Upload knowledge + indexacion worker
- [ ] Chat con citas
- [ ] Hide thread
- [ ] En Langfuse: solo metadatos (sin prompts/documentos/mensajes)

### 7.4 Canales y calendario (si entran en el alcance)

- [ ] Google OAuth callback con URL de prod
- [ ] Voz → evento (si feature activa)
- [ ] WhatsApp / Telegram con firma real (sin unsigned)

### 7.5 Planes (codigo `Paso02`–`Paso04` cerrado; verificar en staging/prod)

- [ ] Sidebar segun plan
- [ ] URL directa a feature denegada
- [ ] Cuota bloquea antes de coste LLM
- [ ] SADM puede asignar/cambiar plan (`/sadm/plans`)
- [ ] Billing Stripe checkout/portal (si self-serve; Price IDs + Infisical)

---

## 8. Seguridad operativa (verificacion)

Checklist condensada de `Seguridad_V2.md`:

- [ ] JWT validado contra JWKS + azp/aud
- [ ] Membership activa y rol sincronizado
- [ ] `set_tenant_context` + RLS FORCE en tablas tenant
- [ ] CSRF en mutaciones web
- [ ] Webhooks firmados, body limitado, dedupe
- [ ] Rate limit en endpoints sensibles
- [ ] Tokens OAuth/API cifrados con `ENCRYPTION_KEY`
- [ ] Audit log en acciones sobre datos de cliente y acciones SADM sensibles
- [ ] Limites MIME/bytes/paginas/pixeles antes de procesar archivos
- [ ] Sin secretos nuevos en el commit de release

```powershell
infisical run -- uv run pytest tests/unit/test_llm_observability.py tests/unit/test_clerk_jwt_audience.py tests/unit/test_superadmin_permissions.py -q
```

---

## 9. SADM en produccion

- [ ] `ADMIN_CLERK_ORG_ID` correcto
- [ ] Allowlist de usuarios si se usa
- [ ] Dashboard `/sadm` accesible solo para SuperAdmin
- [ ] No exponer secretos, tokens ni contenido bruto de Langfuse en UI
- [ ] Acciones sensibles (plan, override, procesado excepcional, ver original) dejan audit log
- [ ] Provision create-org/user desde app deshabilitada u oculta en prod (`Paso05` opcion B recomendada)

---

## 10. Observabilidad y coste

- [ ] Langfuse prod recibe trazas metadata-only
- [ ] Tabla `llm_calls` escribe en prod
- [ ] `usage_meter` / usage SADM visible para tenants piloto
- [ ] Alertas o revision manual de coste las primeras 48–72 h
- [ ] Kill-switch documentado: parar worker ARQ y/o rotar API keys LLM

---

## 11. Rollback y contingencia

- [ ] Procedimiento de restore de backup BD probado
- [ ] Commit/tag anterior conocido para redeploy
- [ ] Migraciones destructivas evitadas o con plan down documentado
- [ ] Worker se puede detener sin corromper jobs (reencolables)
- [ ] Contacto on-call / canal de incidencias

---

## 12. Documentar el release

Antes de dar por cerrado el go-live:

- [ ] Fecha y hora UTC/Europe
- [ ] Commit SHA / tag
- [ ] `alembic current` (revision)
- [ ] Entorno Infisical usado (`prod`)
- [ ] Lista de gaps aceptados y fecha de cierre prevista
- [ ] Resultado de smoke QA (OK / parcial)
- [ ] Enlace a este checklist cumplimentado (issue, wiki o copia fechada)

Plantilla sugerida:

```text
Release: YYYY-MM-DD
Commit: <sha>
Alembic: <revision>
Infisical: prod
Gaps aceptados: <lista o "ninguno">
Smoke: OK | parcial
Responsable: <nombre>
```

---

## 13. Orden cronologico recomendado el dia D

1. Congelar codigo (tag) y CI verde.
2. Backup BD.
3. Revisar Infisical `prod` (seccion 3) y Clerk webhooks (seccion 4).
4. Migrar (`alembic upgrade head`).
5. Desplegar API + worker.
6. Healthchecks.
7. Smoke auth + un documento + Langfuse metadata.
8. Activar webhooks externos (Clerk, WA/TG si aplica) apuntando a URL prod.
9. Vigilar logs/coste 1–2 h.
10. Rellenar seccion 12 (documento de release).

---

## 14. Referencias rapidas

| Tema | Documento |
| --- | --- |
| Reglas asistentes / secretos | `Documentacion_V2/AGENTS.md` |
| Arquitectura y deploy | `Documentacion_V2/Arquitectura_V2.md` |
| Seguridad | `Documentacion_V2/Seguridad_V2.md` |
| SADM | `Documentacion_V2/SADM_V2.md` |
| Planes | `Documentacion_V2/Planes_Entitlements.md` |
| Auditoria previa | `Documentacion_V2/Paso00_Auditoria_Base.md` |
| Seguridad residual | `Documentacion_V2/Paso01_Seguridad_Residual.md` |
| QA release | `Documentacion_V2/Paso10_QA_Release_Produccion.md` |
| Variables | `docs/environment-variables.md` |
| Backlog P0 | `Documentacion_V2/Backlog_Priorizado.md` |

Si un paso de este fichero contradice un `PasoXX` mas reciente, actualizar este documento o el paso y dejar constancia en `Decision_Log.md`.
