# Arquitectura_V2

Fecha: 2026-09-14
Estado: **arquitectura vigente del monolito** (el codigo es la fuente de verdad).
HEAD migraciones: `p64_plans_entitlements_01`.

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
- Catalogo de planes + gates + cuotas/budgets (Pasos 02–04).
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
| UI | Jinja2 + HTMX 2.x + Alpine 3.x + Tailwind 4 (CLI standalone). Sin React/Vue/Svelte |
| BD | PostgreSQL 16+ con pgvector, pgcrypto |
| Storage | R2 via boto3 |
| LLM | SDKs Anthropic/Google/Voyage via cliente propio. No LangChain/LlamaIndex como columna |
| Auth | Clerk Organizations |
| Secretos | Infisical (`env_file=None` en Settings). Sin `.env` en el repo |
| Observabilidad | `llm_calls`, Langfuse metadata-only, structlog |
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

No hay rutas de analytics SQL (D011 — no se implementara). Webhooks Stripe: montados (`/api/webhooks/stripe`).

## 5. Modulos de producto

| Modulo | Estado | Notas |
| --- | --- | --- |
| Identidad y tenants | Implementado | Clerk + memberships + RLS. Sync membership via webhooks. Sin switcher multi-org (D003). |
| Documentos | Implementado | Facturas, tickets, contratos, seguros. Tipo verificado, quality gate, multi-IVA, `processing_charges` (Paso06). |
| Chat documental | Implementado | Tools tipadas, citas verificadas, cuota user+tenant, anti-exfil (Paso07). |
| Knowledge/RAG | Implementado | Upload/OCR/FAQ, retrieval hibrido, embeddings Voyage, aislamiento tenant (Paso07). |
| Canales WA/TG | Implementado | Integraciones, jobs, firma, body limit, dedupe Redis, gates. QA manual prod pendiente. |
| Calendario Google/voz | Implementado | OAuth cifrado, voz → evento. Gates `calendar_*`. QA manual pendiente. |
| Citas internas | Implementado | Scheduling multi-profesional, API find-slots, gates `appointments`. |
| SADM | Implementado | Orgs/miembros RO; usage; docs rechazados; chat traces/usage; **planes** assign/override. Identidades solo Clerk (D005). |
| Planes/entitlements | Implementado | D012: `basic`/`advanced`/`premium`; gates; cuotas duros; calendar_* no publicados. |
| Analytics SQL | **No implementar** (D011) | Feature retirada del catalogo. Paso08 archivado. Sin rutas ni tablas. |
| Billing Stripe | Implementado | Checkout + portal + webhook firmado; `assign_tenant_plan`; `tenant_plan_changes`; `billing_status`. Requiere Price IDs e Infisical. |

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
- Calendario/citas: `calendar_integrations`, `appointments`, `professionals`, `professional_specialties`, `professional_working_hours`, `business_hours`, `scheduling_services`, `schedule_exceptions`.
- Observabilidad/coste: `llm_calls`, `audit_log`, `usage_meter`.
- Planes: `plans`, `plan_entitlements`, `tenant_plan_changes`.
- Tenants billing: `stripe_customer_id`, `stripe_subscription_id`, `billing_status`.

### 6.3 Deuda de esquema documentada

- Analytics (D011): **no** crear `data_sources` / `analytics_queries`. Columna historica `usage_meter.analytics_queries_count` sin uso de producto.
- Stripe Price IDs: rellenar `plans.stripe_price_id` por entorno (Paso09 codigo listo).

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
- Audit log de acciones sobre datos de cliente.
- Langfuse sin contenido de cliente (D008).
- Controles de tamano, MIME, paginas y pixeles (`media_limits`) antes de OCR/LLM.
- Kill-switch global: `ENTITLEMENTS_DISABLED_FEATURES`.

Residual operativo (no bloquea el modelo de capas, si el go-live): checklists abiertas en Paso00/01/07/10 y `PasosParaProduccion.md` (QA manual, rotacion de secretos historicos, firma Go/No-Go).

## 8. Auth y multi-tenant

1. Middleware extrae/valida sesion Clerk.
2. Resuelve `user`, `tenant`, `membership` locales.
3. Setea `request.state.*` y `app.current_tenant` (RLS).
4. Dependencias: `CurrentUser`, `CurrentTenant`, `RequireAdmin`, `SuperAdmin`, `require_feature` / `require_any_feature`.
5. Denegacion de plan → `PlanRequiredError` (HTML o JSON segun ruta).

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
- cuotas (`plan_quota_service`) y budget LLM mensual.

Planes seed: `basic`, `medium`, `high`, `total`. Alias legacy `free` → `basic`.

SADM: `/sadm/plans` asigna `plan_code` y overrides; ver `SADM_V2.md`.

## 10. SADM

Consola operativa del SaaS, no IdP (D005).

Alcance actual:

- Dashboard.
- Organizaciones y miembros (read-only).
- Planes: listado, assign, override entitlements.
- Documentos rechazados / autorizacion de override de limites.
- Uso / cargos por tenant.
- Trazas y uso de chat (metadatos).

Prohibido: crear usuarios/orgs/contrasenas desde la app.

## 11. LLM

Punto unico: `app/llm/client.py`.

Tareas (`TaskType`): `extraction`, `classify`, `chat`, `embedding`, `transcription`, `translate`. `sql` tipado pero **muerto** (D011 — sin modulo Analytics).

Defaults (`DEFAULT_MODELS`): extraction/chat Gemini Flash; classify Haiku; embedding `voyage-3-lite`; transcription Gemini audio. Entrada `sql` tipada pero sin producto (D011).

Reglas:

- Prompts versionados en `app/llm/prompts/`.
- Structured output (Instructor) cuando aplique.
- Tools tipadas con Pydantic (`app/llm/tools/`).
- Sin SQL libre para chat documental.
- Sin Analytics SQL sobre BD externa (D011 — no se implementa).
- Observabilidad: `llm_calls` + Langfuse metadata-only (`app/llm/observability.py`).
- Coste: `app/llm/pricing.py` + enforcement de budget de plan.

## 12. Jobs ARQ

Registro en `app/jobs/settings.py`:

| Job | Rol |
| --- | --- |
| `process_invoice` | Extraccion facturas |
| `process_ticket` | Extraccion tickets |
| `process_contract` | Extraccion contratos |
| `process_insurance` | Extraccion seguros |
| `index_knowledge_document` | Chunking + embeddings (timeout 600s) |
| `process_channel_message` | Respuesta canales WA/TG (timeout 120s) |

Cada worker revalida feature de plan y puede devolver `skipped` / `plan_required` sin gastar LLM.

## 13. Frontend

No SPA.

- Server-rendered HTML con Jinja2.
- HTMX para interacciones servidor (patron pagina/fragmento via `render()`).
- Alpine solo para estado local pequeno.
- Tailwind CLI standalone (`static/css/input.css` + `@theme`).
- No logica de negocio en templates.
- No JS manual para negocio.
- No JSON desde `routes/web` para pintar UI.

## 14. Observabilidad y costes

- `llm_calls`: cada llamada LLM (tokens, coste, latencia, status).
- `usage_meter`: contadores agregados. `analytics_queries_count` es columna historica sin producto (D011).
- `processing_charges`: cargos/estimaciones de procesamiento documental.
- `audit_log`: acciones sensibles.
- Langfuse: trazas metadata-only; nunca documentos, mensajes ni respuestas crudas.

## 15. Tests y evals

- Unit: `tests/unit/`.
- Integration: `tests/integration/` (Postgres real; marker `integration`; excluir `real_llm` en CI local tipico).
- E2E: Playwright.
- Evals: `app/evals/` (extraccion, chat documental, knowledge).
- Politica: todo cambio de codigo crea/actualiza tests y se ejecuta antes de dar por cerrado.

Nota: `-m "integration and not real_llm"` sobre `tests/unit` deselecciona casi todos los unitarios. Ejecutar unit e integration por separado.

## 16. Despliegue

Fase actual:

- Monolito modular (API + worker ARQ).
- Hetzner/Coolify o equivalente simple.
- Postgres, Redis, R2, Clerk, Langfuse self-hosted.
- Secretos solo Infisical entorno `prod`.

No introducir Kubernetes, microservicios ni GraphQL hasta dolor medible.

Checklist go-live: `PasosParaProduccion.md`.

## 17. Orden estrategico restante

Codigo de Pasos 02–07 y 09 (Stripe) esta en el repo. Lo que queda:

1. Ops: residual Paso00/01 (Infisical staging/prod, rotacion credenciales), QA manual Paso07, soft-launch Paso10.
2. Operativa Stripe: Price IDs + claves Infisical + webhook Dashboard.
3. Deuda documental menor: mantener este fichero y el backlog alineados tras cada cierre.

**No roadmap:** Analytics SQL / modulo 3 (D011).

## 18. Decisiones cerradas

Ver `Decision_Log.md` (D001–D011): continuidad del repo, gobernanza Documentacion_V2, sin switcher multi-org, SADM por org admin, identidades solo Clerk, planes antes que Stripe, cuotas por plan, Langfuse metadata-only, **Analytics SQL no se implementa (D011)**, etc.

## 19. Docs V2 a no usar como snapshot de codigo sin revisar

Estos ficheros pueden seguir siendo utiles como diseno, pero **algunas secciones estan desfasadas** respecto al HEAD actual:

- `Planes_Entitlements.md` / `SADM_V2.md`: si cambias matriz comercial, actualiza codigo + estos docs juntos.
- `PasosParaProduccion.md`: el estado de checkboxes ops puede ir detras del codigo; prioriza Infisical y QA.
- Preferir `Backlog_Priorizado.md` y cabeceras `Estado:` de cada `PasoXX.md` como fuente de "hecho vs pendiente".

Ante duda: este `Arquitectura_V2.md` + codigo + paso activo.
