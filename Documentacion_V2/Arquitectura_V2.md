# Arquitectura_V2

Fecha: 2026-09-14 · Actualizado: 2026-10-01
Estado: **arquitectura vigente del monolito** (el codigo es la fuente de verdad).
HEAD migraciones: `p82_contract_quota_01`.

Si un doc antiguo o un backlog desfasado contradice este fichero o el codigo, gana el codigo + `Documentacion_V2` (ver `Decision_Log.md` D001–D002).

## 1. Vision

SaaS modular para pymes. No se vende como "IA", sino como ahorro operativo:

- procesar documentos,
- responder preguntas de clientes,
- consultar conocimiento interno,
- gestionar citas y calendario,
- (futuro) analizar datos con lenguaje natural,
- controlar costes y acceso por plan.

## 2. Decision de continuidad

El codigo actual no se descarta (D001). La base contiene:

- FastAPI con rutas web y API.
- SQLAlchemy 2.0 async + Alembic con RLS (`FORCE ROW LEVEL SECURITY`).
- Clerk Organizations (auth, memberships, webhooks).
- SADM (`/sadm/*`) con planes, uso, docs y trazas.
- Catalogo de planes + gates + cuotas/budgets (Pasos 02–04) + base de cupos mensuales (D027).
- ARQ workers sobre Redis.
- Cloudflare R2 (storage).
- Capa LLM propia (`app/llm/client.py`), prompts versionados, Langfuse metadata-only.
- Tests unitarios, integracion y e2e; CI y evals.

La V2 corrige la documentacion y fija el orden de evolucion restante.

## 3. Stack obligatorio

| Capa | Tecnologia |
| --- | --- |
| Runtime | Python 3.12+, FastAPI, uvicorn |
| ORM / migraciones | SQLAlchemy 2.0 async, Alembic |
| Validacion | Pydantic v2, Instructor |
| Jobs | ARQ (Redis). No Celery |
| HTTP | httpx async |
| UI | Jinja2 + HTMX 2.x + Alpine 3.x + Tailwind 3.4 (CLI standalone, D028). Sin React/Vue/Svelte |
| BD | PostgreSQL 16+ con pgvector, pgcrypto |
| Storage | R2 via boto3 |
| LLM | SDKs Anthropic/Google/Voyage via cliente propio. No LangChain/LlamaIndex como columna |
| Auth | Clerk Organizations |
| Secretos | Infisical (`env_file=None` en Settings). Sin `.env` en el repo |
| Observabilidad | `llm_calls`, Langfuse metadata-only, structlog sin datos personales (`app/core/log_redaction.py`, `Seguridad_V2.md` §8b), registro de actividad en BD `activity_log` con `X-Request-ID` (`app/core/activity/`, D029) |
| Tests | pytest, Playwright; evals en `app/evals/` |

Entry points:

- API/web: `app.main:app` (`create_app()`).
- Worker: `uv run arq app.jobs.settings.WorkerSettings`.

## 4. Arquitectura logica

```text
Browser / HTMX / Alpine
        |
FastAPI routes (web HTML | api JSON)
        |
services
        |
models + core + llm + jobs
        |
Postgres/RLS + Redis + R2 + LLM providers + Clerk
```

Capas (regla absoluta: `routes/` no importa `models/` directamente):

- `routes/web`: HTML, `render()`, HTMX, CSRF.
- `routes/api`: JSON, webhooks, integraciones, health/metrics.
- `services`: negocio, permisos de dominio, orquestacion.
- `models`: tablas SQLAlchemy.
- `schemas`: Pydantic DTOs.
- `core`: DB/RLS, auth, security, storage, cache, rate limit, crypto, entitlements codes, media_limits.
- `llm`: cliente, prompts, tools, observabilidad, pricing.
- `jobs`: ARQ workers (revalidan feature/cuota antes de coste LLM).
- `evals`: datasets y runners.

### 4.1 Rutas montadas (resumen)

**Web:** auth, home, documents, knowledge, jobs, chat, calendar, calendar_voice, settings, settings_scheduling, appointments, integrations, admin channel integrations, SADM (dashboard, organizations, plans, documents, usage, chat_traces, chat_usage), demo. Redirect legacy `/invoices` → `/documents`.

**API:** health, metrics, scheduling (`/api/v1/scheduling`), webhooks Clerk, WhatsApp, Telegram.

No hay rutas de analytics SQL (D011 — no se implementara). Sin integracion de pagos (D016: Stripe retirado).

## 5. Modulos de producto

| Modulo | Estado | Notas |
| --- | --- | --- |
| Identidad y tenants | Implementado | Clerk + memberships + RLS. Sync membership via webhooks. Sin switcher multi-org (D003). |
| Documentos | Implementado | Facturas, tickets, contratos, seguros. Tipo verificado, quality gate, multi-IVA, `processing_charges` (Paso06). |
| Chat documental | Implementado | Tools tipadas, citas verificadas, cuota user+tenant, anti-exfil (Paso07). |
| Knowledge/RAG | Implementado | Upload/OCR/FAQ, retrieval hibrido, embeddings Voyage, aislamiento tenant (Paso07). |
| Canales WA/TG | Implementado (citas: pendiente) | Integraciones, jobs, firma, body limit, dedupe Redis, gates, conocimiento. Las citas del canal usan hoy Google Calendar y no filtran por cliente final; pasan al modulo de citas internas en Paso12 (D030). QA manual prod pendiente. |
| Calendario Google/voz | Implementado | OAuth cifrado, voz → evento. Gates `calendar_*`. QA manual pendiente. |
| Citas internas | Implementado | Scheduling multi-profesional (horario del centro, excepciones, profesionales con especialidades y horario propio, servicios), sin solapes por profesional (GiST), API find-slots, gates `appointments`. Solo desde la app; conexion con WhatsApp/Telegram en Paso12 (D030). |
| SADM | Implementado | Orgs/miembros RO; usage; docs rechazados; chat traces/usage; **planes** assign/override. Identidades solo Clerk (D005). |
| Planes/entitlements | Implementado | D012: `basic`/`advanced`/`premium`; gates; cuotas duros; calendar_* no publicados. Cupos mensuales en catalogo y `quota_usage` (D027); se aplican por bloques (Backlog, cierre del producto minimo). |
| Analytics SQL | **No implementar** (D011) | Feature retirada del catalogo. Paso08 archivado. Sin rutas ni tablas. |
| Cobro de planes | **Pendiente de decision** (D016) | Stripe retirado. Plan asignado solo por SADM (`assign_tenant_plan` + historial `tenant_plan_changes`). `/settings/billing` solo lectura. |

## 6. Datos

### 6.1 Principios

- Toda tabla con datos de cliente lleva `tenant_id`.
- Toda tabla con `tenant_id` tiene RLS con `FORCE ROW LEVEL SECURITY`.
- Services incluyen filtro por `tenant_id` como defensa adicional.
- Datos globales sin tenant: `doc_types`, `plans`, `plan_entitlements`.
- Datos cross-tenant SADM: solo con `SuperAdmin` + `get_db_no_tenant` y politicas explicitas.
- Secretos de integracion: cifrados (Fernet); no en claro en BD.

### 6.2 Tablas por dominio (codigo actual)

- Identidad: `tenants` (`plan` legacy + `plan_code`), `users`, `memberships`.
- Documentos: `invoices`, `invoice_lines`, `tickets`, `contracts`, `insurances`, `document_processing_attempts`, `processing_charges`, `doc_types`.
- Knowledge: `knowledge_documents`, `knowledge_chunks`.
- Chat: `chat_threads`, `chat_messages`.
- Canales: `channel_integrations`, `conversations`, `channel_messages`, `channel_response_cache`.
- Calendario/citas: `calendar_integrations`, `appointments`, `professionals`, `professional_specialties`, `professional_working_hours`, `business_hours`, `services`, `schedule_exceptions`.
- Observabilidad/coste: `llm_calls`, `audit_log`, `usage_meter`.
- Planes: `plans`, `plan_entitlements`, `tenant_plan_changes`, `quota_usage` (cupos mensuales por tenant y mes, D027).

### 6.3 Deuda de esquema documentada

- Analytics (D011): **no** crear `data_sources` / `analytics_queries`. Columna historica `usage_meter.analytics_queries_count` sin uso de producto.
- Stripe (D016): columnas eliminadas en `p68_drop_stripe_billing_01`; sin deuda pendiente.

## 7. Seguridad base

La seguridad no es un paso final; es transversal. Ver `Seguridad_V2.md`.

Obligatorio en runtime:

- Clerk JWT validado contra JWKS; allowlists `azp` / audience en produccion.
- Membership activa y rol sincronizado (webhooks Clerk).
- Tenant context en Postgres antes de queries tenant-scoped.
- RLS + tests de aislamiento.
- CSRF para mutaciones web.
- Webhooks firmados; body limit + dedupe anti-replay.
- Rate limit / cuotas por plan en endpoints y workers sensibles.
- Cifrado de tokens OAuth y API de canales.
- Audit log de mutaciones y accesos a datos de cliente; no en listados ni polling (detalle en `AGENTS.md` §7).
- Langfuse sin contenido de cliente (D008).
- Controles de tamano, MIME, paginas y pixeles (`media_limits`) antes de OCR/LLM.
- Kill-switch global: `ENTITLEMENTS_DISABLED_FEATURES`.

Residual operativo (no bloquea el modelo de capas, si el go-live): checklists abiertas en Paso00/01/07/10 y `PasosParaProduccion.md` (QA manual, rotacion de secretos historicos, firma Go/No-Go).

## 8. Auth y multi-tenant

1. Middleware extrae/valida sesion Clerk.
2. Resuelve `user`, `tenant`, `membership` locales.
3. Setea `request.state.*` y `app.current_tenant` (RLS).
4. Dependencias: `CurrentUser`, `CurrentTenant`, `RequireManager` (admin o co_admin), `SuperAdmin`, `require_feature` / `require_any_feature`.
5. Denegacion de plan → `PlanRequiredError` (HTML o JSON segun ruta).

Roles de organizacion (Clerk `org:<rol>`, reglas en `app/core/permissions.py`):

| Rol | Acceso | Solicitar baja de miembros |
| --- | --- | --- |
| `admin` | Dueno del negocio, unico por tenant (un segundo admin de Clerk se sincroniza como `co_admin`, salvo en la org SADM). Todo el tenant | Cualquiera salvo a si mismo |
| `co_admin` | Igual que admin | Cualquiera salvo a si mismo y al admin |
| `member` / `viewer` | Chat y citas segun permisos | No |

Ningun rol de tenant asigna ni cambia el plan: lo hace solo el SADM (Ajustes > Facturacion es de solo lectura).

La baja se solicita al SADM por email con fecha de baja efectiva; la membership sigue activa hasta que el SADM la elimina en Clerk (webhook `organizationMembership.deleted`). La solicitud queda guardada en `memberships.removal_requested_at` / `removal_effective_date` (`p69`): la fila muestra la papelera bloqueada con ambas fechas y no admite otra solicitud mientras este pendiente. Se limpia si la membership se reactiva. Regla de negocio: el SADM **no puede rechazar** una baja solicitada; debe ejecutarla en la fecha efectiva (no existe "anular solicitud").

Corte automatico (RGPD): desde las 00:00 de la fecha efectiva (zona de la app) el middleware desactiva la membership en la primera peticion y el cron ARQ `expire_member_removals` (cada 15 min + al arrancar el worker) desactiva al resto; ambos registran `membership.removal_executed` en `audit_log`. No depende de que el SADM actue en Clerk. Solo `organizationMembership.created` reactiva una membership; `updated` nunca devuelve el acceso. Mientras la baja esta pendiente el miembro no es editable (`member_locked_by_removal`).

El alta tambien se solicita al SADM por email desde `/settings/members` ("Nuevo miembro": nombre, apellidos, alias, email, fecha de alta y rol `co_admin`/`member`). La app no crea nada en Clerk ni en BD; rechaza antes de enviar si el email ya es miembro o el plan no tiene plazas (`members_max`). Anti-reenvio 24 h por tenant+email.

SADM (D004): org `ADMIN_CLERK_ORG_ID` + membership admin + allowlist opcional `SUPERADMIN_CLERK_USER_IDS`. Sin columna `users.is_superadmin`.

## 9. Planes y entitlements

Capa transversal implementada. No basta con ocultar el sidebar.

Fuente de nombres: `app/core/entitlement_codes.py` + `Planes_Entitlements.md` (el diseno; el codigo manda si divergen).

Resolucion:

- `entitlement_service.resolve_entitlements(db, tenant)` → catalogo BD + override en `tenants.settings` + kill-switch.
- Cache por request en `request.state.entitlements`.
- Fail-closed si el plan falta o esta inactivo.

Gates obligatorios en:

- rutas web/API,
- sidebar (`nav_items_for_access`),
- services cuando aplica,
- workers ARQ,
- webhooks de canal,
- cuotas (`plan_quota_service`; mensuales en `monthly_quota_service`, D027) y budget LLM mensual.

Planes seed: `basic`, `advanced`, `premium` (D012). Alias legacy: `free`/`medium` → `basic`, `high` → `advanced`, `total` → `premium`.

Periodo: mes natural en hora de Espana para cupos y presupuesto (D027, `app/core/billing_period.py`).

SADM: `/sadm/plans` asigna `plan_code` y overrides; la primera asignacion es inmediata y las siguientes se programan para el dia 1 del mes siguiente (`plan_change_service` + cron `apply_scheduled_plan_changes`). Ampliacion de un cupo solo para el mes en curso (`quota_usage.extra`, auditada). Ver `SADM_V2.md`.

## 10. SADM

Consola operativa del SaaS, no IdP (D005).

Alcance actual:

- Dashboard.
- Organizaciones y miembros (read-only).
- Planes: listado, assign (cambios programados al mes siguiente), override entitlements, cupos del mes y ampliacion mensual (D027).
- Documentos rechazados / autorizacion de override de limites.
- Uso / cargos por tenant.
- Trazas y uso de chat (metadatos).

Prohibido: crear usuarios/orgs/contrasenas desde la app.

## 11. LLM

Punto unico: `app/llm/client.py`.

Tareas (`TaskType`): `extraction`, `classify`, `chat`, `embedding`, `transcription`, `translate`. `sql` tipado pero **muerto** (D011 — sin modulo Analytics).

Defaults (`DEFAULT_MODELS`): extraction `gemini-3.8-flash` con thinking bajo (D014); chat `gemini-3.5-flash-lite` con thinking por defecto (D015); classify Haiku; embedding `voyage-3-lite`; transcription Gemini audio. Entrada `sql` tipada pero sin producto (D011).

Reglas:

- Prompts versionados en `app/llm/prompts/`.
- Structured output (Instructor) cuando aplique.
- Tools tipadas con Pydantic (`app/llm/tools/`).
- Sin SQL libre para chat documental.
- Sin Analytics SQL sobre BD externa (D011 — no se implementa).
- Observabilidad: `llm_calls` + Langfuse metadata-only (`app/llm/observability.py`).
- Coste: `app/llm/pricing.py` + enforcement de budget de plan. Los tokens de thinking de Gemini cuentan como output (D014).
- Thinking: tareas sin beneficio de razonamiento (hoy `extraction`) lo bajan al minimo via `_google_thinking_config` (D014).

## 12. Jobs ARQ

Registro en `app/jobs/settings.py`:

| Job | Rol |
| --- | --- |
| `process_invoice` | Extraccion facturas |
| `process_ticket` | Extraccion tickets |
| `process_contract` | Extraccion contratos (timeout 600s: hasta 100 paginas) |
| `process_insurance` | Extraccion seguros |
| `index_knowledge_document` | Chunking + embeddings (timeout 600s) |
| `process_channel_message` | Respuesta canales WA/TG (timeout 120s) |
| `send_llm_budget_alert` | Emails de presupuesto de IA (80 % admin, 90 % SADM, corte del chat; D019) |
| `send_llm_provider_billing_alert` | Email al SADM si un proveedor LLM rechaza por saldo (402; D025) |
| `process_quota_pending` | Facturas y tickets pendientes de cupo o de presupuesto de IA: los encola cuando hay hueco (tras una ampliacion del SADM, una reserva devuelta o el cron) |
| `send_documents_quota_alert` | Emails al admin: 80 % de la bolsa facturas + tickets y primer documento pendiente del mes |

Todos los jobs se registran envueltos con `tracked_job` (`activity_log`, D029).

Crons: `expire_member_removals` (cada 15 min + al arrancar; bajas con fecha efectiva vencida), `apply_scheduled_plan_changes` (cambios de plan programados, D027), `process_quota_pending` (cada hora en el minuto 10 + al arrancar; renovacion del cupo el dia 1) y `purge_activity_log` (diario, 03:30; D029).

Cupo de facturas y tickets (D027, bloques 2 y 3): `document_quota_service` reserva una unidad al encolar y la devuelve si el documento no termina bien (`mark_failed`, extraccion inservible, abandono por atasco, presupuesto de IA agotado o borrado en el mes en curso). Sin hueco, el documento queda en `quota_pending`.

Contratos (D027, bloque 5): el mismo servicio reserva las altas del contrato (1 a 3 segun sus paginas) en la bolsa de carga inicial o en la mensual; `contract_quota_service` decide la bolsa, los tramos y el hueco en el archivo de activos (`contracts_active_max`), y gestiona "Marcar como sustituido" / "Volver a vigente" (`contracts.lifecycle`).

Cada worker de procesado revalida feature de plan y puede devolver `skipped` / `plan_required` sin gastar LLM.

## 13. Frontend

No SPA.

- Server-rendered HTML con Jinja2.
- HTMX para interacciones servidor (patron pagina/fragmento via `render()`).
- Alpine solo para estado local pequeno.
- Tailwind 3.4 CLI standalone (`static/css/input.css` con `@tailwind` + `tailwind.config.js`; misma version en `bin/tailwindcss.exe` y en el `Dockerfile`). Migracion a v4 (`@theme`) despues del producto minimo (D028).
- No logica de negocio en templates.
- No JS manual para negocio.
- No JSON desde `routes/web` para pintar UI.

## 14. Observabilidad y costes

- `llm_calls`: cada llamada LLM (tokens, coste, latencia, status).
- `usage_meter`: contadores agregados y gasto de IA del mes. `analytics_queries_count` es columna historica sin producto (D011).
- `quota_usage`: cupos mensuales consumidos y ampliaciones del SADM por tenant y mes (D027).
- `processing_charges`: cargos/estimaciones de procesamiento documental.
- `audit_log`: acciones sensibles.
- Langfuse: trazas metadata-only; nunca documentos, mensajes ni respuestas crudas.

## 15. Tests y evals

- Unit: `tests/unit/`.
- Integration: `tests/integration/` (Postgres real; marker `integration`; excluir `real_llm` en CI local tipico).
- BD de tests: **siempre `saas_test`** (`tests/db_target.py` reescribe `DATABASE_URL` y `RLS_TEST_DATABASE_URL` en `tests/conftest.py`). La app usa `saas`. Crear/migrar tras cada migracion nueva: `infisical run -- bash scripts/test_db_setup.sh`.
- BD de evals: **tambien `saas_test`** (`app/evals/eval_db.py`: cada runner y seed de evals llama a `use_eval_database()` antes de tocar BD; los modos `--validate-only` no usan BD). Asi el tenant de evals, sus `llm_calls` y los documentos sembrados no ensucian `saas`. Evals (tenant fijo `EVAL_TENANT_ID`) y tests (tenants aleatorios) no se pisan. Se migra con el mismo `scripts/test_db_setup.sh`. CI (`evals.yml`) levanta Postgres con `saas_test`.
- E2E: Playwright.
- Evals: `app/evals/` (extraccion de facturas, tickets, contratos y polizas; chat documental `chat_documents_v2` sobre datos sembrados; knowledge). CI (`.github/workflows/evals.yml`) ejecuta extraccion y chat documental (este x2) con umbrales de `app/evals/thresholds.py` y `targets` del dataset, y despues `app/evals/compare_baseline.py`: falla si una metrica baja >5 % relativo frente a `app/evals/baselines.json` de `origin/main` (AGENTS.md §9). Excepcion: etiqueta `eval-regression-accepted` en el PR + re-run. Actualizar la baseline: `infisical run -- uv run python -m app.evals.compare_baseline --since <epoch> --update` tras ejecutar las evals, y commitear el diff.
- Politica: todo cambio de codigo crea/actualiza tests y se ejecuta antes de dar por cerrado.

Nota: `-m "integration and not real_llm"` sobre `tests/unit` deselecciona casi todos los unitarios. Ejecutar unit e integration por separado.

## 16. Despliegue

Fase actual:

- Monolito modular (API + worker ARQ), una imagen (`Dockerfile`).
- VPS (p. ej. Hetzner) con Docker Compose + Caddy (D013); sin Coolify.
- Postgres (pgvector) y Redis en la misma VPS, sin puertos publicados; R2, Clerk; Langfuse self-hosted aplazable.
- Secretos solo Infisical entorno `prod` (Machine Identity de la VPS).
- Guia: `Paso11_Despliegue_VPS.md`.

No introducir Kubernetes, microservicios ni GraphQL hasta dolor medible.

Checklist go-live: `PasosParaProduccion.md`.

## 17. Orden estrategico restante

Codigo de Pasos 02–07 esta en el repo (Paso09 Stripe retirado, D016). Lo que queda, en este orden:

1. Cierre del producto minimo: tabla "Cierre del producto minimo" de `Backlog_Priorizado.md` (fuente unica: seguridad P2c 1-3, bloques 2-7 de cupos, cierre del codigo y ops de despliegue de `PasosParaProduccion.md`).
2. Decidir el metodo de cobro de los planes antes de la produccion comercial (Backlog P3-2, D016).
3. Deuda documental menor: mantener este fichero y el backlog alineados tras cada cierre.

**No roadmap:** Analytics SQL / modulo 3 (D011). El analista de Premium sobre datos del tenant queda para despues del producto minimo (D018).

## 18. Decisiones cerradas

Ver `Decision_Log.md` (D001–D027): continuidad del repo, gobernanza Documentacion_V2, sin switcher multi-org, SADM por org admin, identidades solo Clerk, planes antes que Stripe, cuotas por plan, Langfuse metadata-only, **Analytics SQL no se implementa (D011)**, **plan solo por SADM y Stripe retirado (D016)**, presupuesto de IA y cortes (D019, D026), miembros 3/9/20 (D022), chat con cupo mensual (D023), cupos mensuales y cambios de plan programados (D027), etc.

## 19. Docs V2 a no usar como snapshot de codigo sin revisar

Estos ficheros pueden seguir siendo utiles como diseno, pero **algunas secciones estan desfasadas** respecto al HEAD actual:

- `Planes_Entitlements.md` / `SADM_V2.md`: si cambias matriz comercial, actualiza codigo + estos docs juntos.
- `PasosParaProduccion.md`: el estado de checkboxes ops puede ir detras del codigo; prioriza Infisical y QA.
- Preferir `Backlog_Priorizado.md` y cabeceras `Estado:` de cada `PasoXX.md` como fuente de "hecho vs pendiente".

Ante duda: este `Arquitectura_V2.md` + codigo + paso activo.
