# AGENTS.md V2

Reglas operativas para cualquier asistente o humano que trabaje desde `Documentacion_V2`.

## 0. Lectura obligatoria

Antes de tocar codigo:

1. `AGENTS.md` de la raiz.
2. `Documentacion_V2/AGENTS.md`.
3. `Documentacion_V2/Arquitectura_V2.md`.
4. `Documentacion_V2/Decision_Log.md`.
5. El `Documentacion_V2/PasoXX_*.md` activo.

Los documentos antiguos de `Documentacion/` son historicos, salvo que un paso V2 los cite como referencia. No usar checklists antiguos como backlog directo.

## 1. Decision de continuidad

No reconstruir la aplicacion desde cero. La base actual se evoluciona por pasos pequenos, testeados y reversibles. Solo se admite reescritura local de un modulo si:

- tiene deuda aislada,
- hay test de comportamiento antes o durante el cambio,
- no rompe contratos de capas,
- el paso activo lo autoriza.

## 2. Stack obligatorio

No sustituir sin aprobacion explicita:

- Python 3.12+.
- FastAPI.
- SQLAlchemy 2.0 async.
- Alembic.
- Pydantic v2 y `pydantic-settings`.
- Instructor para structured output.
- ARQ sobre Redis.
- `httpx` async.
- Jinja2, HTMX y Alpine.js.
- Tailwind CSS 4 CLI standalone.
- PostgreSQL 16+ con pgvector.
- Cloudflare R2 via `boto3`.
- Clerk con Organizations.
- SDKs oficiales de Anthropic, Google y Voyage/OpenAI segun capa LLM.
- Cliente LLM propio en `app/llm/client.py`.
- pytest, pytest-asyncio, Playwright, ruff, mypy estricto, pre-commit.

Prohibido introducir React, Vue, Svelte, LangChain/LlamaIndex como columna vertebral, MongoDB, Pinecone, Qdrant, Celery, GraphQL, Kubernetes o microservicios en esta fase.

## 3. Capas

Patron obligatorio:

```text
routes/ -> services/ -> models/ + llm/ + core/
```

Reglas:

- `app/routes/` no importa directamente `app.models`.
- `routes/web/` devuelve HTML con `render()`.
- `routes/api/` devuelve JSON y usa `tags` y `summary`.
- `services/` no conoce HTTP.
- `llm/` no conoce HTTP ni accede a BD directa salvo contratos ya existentes de tools controladas.
- `models/` no contiene logica de negocio.
- `core/` contiene infraestructura transversal.

## 4. Secretos

- Infisical es la unica via de secretos.
- No crear ni usar `.env`.
- `Settings` debe usar `BaseSettings` con `env_file=None`.
- Variables en mayusculas.
- Nunca escribir secretos reales en Markdown, tests, fixtures, logs ni comentarios.
- Si se detecta un secreto en documentacion antigua, no copiarlo: crear tarea P0 de rotacion.

## 5. Seguridad obligatoria

Toda feature nueva debe revisar:

- Autenticacion Clerk y membership activa.
- RLS para toda tabla con `tenant_id`.
- `set_tenant_context` antes de queries tenant-scoped.
- Defensa en profundidad con `WHERE tenant_id = ...` en services.
- CSRF en mutaciones web.
- Rate limit o cuota si puede generar coste o carga.
- Audit log en acciones sobre datos de cliente.
- Cifrado de tokens y credenciales.
- No exponer `raw_extraction`, tokens, headers de auth ni secretos en UI o trazas.

## 6. LLM e IA

- Toda llamada LLM pasa por `app/llm/client.py` o wrappers existentes en `app/llm/`.
- Prompts en `app/llm/prompts/*_vN.txt`; no prompts largos inline.
- Respuestas estructuradas con Pydantic/Instructor.
- Langfuse solo metadatos mediante `app/llm/observability.py`.
- Prohibido enviar a Langfuse contenido de cliente: documentos, OCR, mensajes, respuestas, chunks, SQL o errores crudos.
- Tools del chat deben ser tipadas, con schemas Pydantic y sin SQL libre.
- El SQL agent futuro solo puede consultar fuentes externas read-only, nunca la BD principal con permisos de escritura.

## 7. SADM

SADM significa SuperAdmin de plataforma.

Reglas:

- Acceso solo con `SuperAdmin`: org Clerk administrativa + rol admin activo + allowlist opcional.
- Las rutas SADM que leen cross-tenant usan `get_db_no_tenant` y mecanismos explicitos de politica `superadmin_select` si la tabla tiene RLS.
- Usuarios y organizaciones: solo Clerk Dashboard. SADM no provisiona identidades; webhook/login sincronizan BD (`Decision_Log` D005 / `Paso05`).
- Toda accion SADM que cambie datos comerciales o de cliente requiere audit log.

## 8. Planes

No introducir `if tenant.plan == "..."` dispersos.

Patron obligatorio:

- `plans` y `plan_entitlements` como catalogo.
- `resolve_entitlements()` como punto unico.
- `require_feature()` en rutas.
- `assert_limit()` o equivalente en services/workers.
- Sidebar filtrado por entitlements.
- Workers y webhooks revalidan features antes de coste LLM.

## 9. Tests

Todo cambio de codigo requiere tests nuevos o actualizados y ejecucion.

Comando preferido:

```powershell
infisical run -- uv run pytest <rutas> -q
```

Si Infisical no es necesario para tests unitarios puros, se puede ejecutar:

```powershell
$env:UV_CACHE_DIR = "D:\AppsIA\IAgentMultiBuss\.uv-cache"
uv run pytest <rutas> -q
```

Documentacion pura no requiere tests de aplicacion, pero debe verificarse con busquedas basicas y revision de enlaces.

## 10. Respuesta esperada

En el chat:

- Espanol.
- Nivel de confianza al inicio: seguro, probable o suposicion.
- Conciso.
- Si algo contradice esta spec, decirlo antes de hacer nada.
- No dar la razon por defecto: priorizar ciberseguridad, mantenibilidad y calidad.
