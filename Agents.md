# AGENTS.md

> Reglas operativas para Cursor, Claude Code y cualquier asistente de IA que trabaje en este proyecto.
> **Arquitectura y dominio del sistema:** `arquitectura.md` (fuente única).
> **Guía de uso del asistente (personas):** `instrucciones-asistente.md`.

---

## 0. Lectura obligatoria al inicio de cada sesión

1. Este fichero (`AGENTS.md`).
2. `arquitectura.md` si la tarea afecta a estructura, stack, seguridad o dominio de módulos / capa LLM (p. ej. §6–§8). Si afecta a **secretos o configuración**, leer también **§2** de este fichero.
3. El `PasoXX.md` correspondiente a la tarea en curso.

Si el asistente empieza a generar código sin haber leído los ficheros relevantes, debe detenerse y leerlos primero.

---

## 1. Stack obligatorio

**No sustituir por equivalentes sin aprobación explícita.**

- **Lenguaje**: Python 3.12+.
- **Framework web**: FastAPI.
- **ORM**: SQLAlchemy 2.0 estilo async (`AsyncSession`, `Mapped[T]`, `mapped_column`).
- **Migraciones**: Alembic.
- **Validación**: Pydantic v2 + `pydantic-settings`.
- **Structured output**: Instructor.
- **Background jobs**: ARQ (sobre Redis). NO Celery.
- **HTTP client**: `httpx` async.
- **Templating**: Jinja2.
- **Interactividad**: HTMX + Alpine.js. NO React, NO Vue, NO Svelte.
- **CSS**: Tailwind CSS 4 (CLI standalone).
- **BD**: PostgreSQL 16+ con pgvector. NO MongoDB, NO Pinecone, NO Qdrant.
- **Storage**: Cloudflare R2 vía `boto3`.
- **LLMs**: SDKs oficiales de Anthropic y Google. Cliente propio en `app/llm/client.py`. NO LangChain ni LlamaIndex como columna vertebral.
- **Auth**: Clerk con Organizations.
- **Tests**: pytest + pytest-asyncio + Playwright.
- **Tooling**: uv (paquetes), ruff (lint+format), mypy estricto. Hooks **`pre-commit`** y alineación con CI: ver `arquitectura.md` §3.
- **Secretos:** Infisical como plataforma de gestión; flujo y prohibiciones en **§2**.

---

## 2. Gestión de secretos y configuración

- **Herramienta:** Infisical (Secret Management Platform).
- **Prohibición estricta:** no crear ni utilizar archivos `.env`. No subir credenciales a GitHub.
- **Flujo de desarrollo:**
  1. Los secretos se inyectan en tiempo de ejecución mediante el CLI de Infisical (`infisical run -- ...`).
  2. En Python, la configuración se gestiona exclusivamente con **Pydantic Settings**.
  3. El modelo de configuración debe heredar de `pydantic_settings.BaseSettings` con `env_file=None`.
- **Nombres de variables:** siempre usar nombres descriptivos en **MAYÚSCULAS** (p. ej. `GEMINI_API_KEY`, `POSTGRES_URL`).

---

## 3. Arquitectura: patrón de capas

```
routes/ → services/ → models/ + llm/ + core/
```

- **`app/routes/`**: validan input HTTP, llaman a `services`, devuelven HTML o JSON. **No tienen lógica de negocio.**
- **`app/services/`**: orquestan lógica de negocio. Llaman a `models`, `llm`, `storage`, `cache`. **No conocen HTTP.**
- **`app/llm/`**: encapsula llamadas a LLMs y prompts. **No conoce HTTP ni BD directa.**
- **`app/models/`**: define tablas y relaciones. **No tiene lógica de negocio.**
- **`app/core/`**: infraestructura transversal (db, security, storage, etc.).

**Regla absoluta**: `routes/` no importa nunca de `models/` directamente. Siempre vía `services/`.

### Sub-división `routes/web/` vs `routes/api/`

- `routes/web/` devuelve **HTML** (TemplateResponse). Aplica patrón página/fragmento (sección 6).
- `routes/api/` devuelve **JSON** (Pydantic models). Para webhooks, integraciones, futura API pública.

---

## 4. Convenciones Python

- **Type hints obligatorios** en toda firma pública. `mypy --strict` debe pasar.
- **Imports absolutos** desde `app.`. NO imports relativos.
- **Async por defecto** en handlers, services, acceso DB/HTTP/LLM.
- **NO `from x import *`**.
- **Strings con comillas dobles** `"texto"`. F-strings para interpolación.
- **Naming**:
  - `snake_case` para funciones y variables.
  - `PascalCase` para clases.
  - `UPPER_SNAKE_CASE` para constantes.
- **Funciones cortas**: si supera 40 líneas, refactorizar.
- **NO `print()`** en código de aplicación. Usar `structlog`.
- **Excepciones**: levantar de `app.core.errors`, nunca strings o tipos genéricos.
- **Docstrings** en funciones públicas no triviales. Estilo Google.

### SQLAlchemy

```python
# Correcto (estilo 2.0)
class Invoice(Base):
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))

# Query async
result = await session.execute(select(Invoice).where(Invoice.tenant_id == tenant.id))
invoices = result.scalars().all()

# PROHIBIDO (estilo legado)
invoices = session.query(Invoice).filter_by(tenant_id=tenant.id).all()
```

### Pydantic

- Pydantic v2 (`model_config = ConfigDict(...)`, NO la API v1).
- Schemas en `app/schemas/` con sufijos `Create`, `Update`, `Read` cuando aplique.
- Para Instructor: usar `Field(description=...)` y validators donde aplique.
- **Settings de aplicación** (`app/config.py` o equivalente): `BaseSettings` con `env_file=None`; variables solo desde entorno (p. ej. inyectadas por Infisical). Ver **§2**.

### Endpoints FastAPI

```python
@router.post("/documents", response_class=HTMLResponse)
async def create_invoice(
    request: Request,
    archivo: UploadFile,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(current_tenant),
    user: User = Depends(current_user),
):
    invoice = await invoice_service.create(archivo, tenant, user, db)
    return render(
        request,
        full="pages/documents/detail.html",
        partial="components/invoice_row.html",
        ctx={"invoice": invoice},
    )
```

- En rutas documentables (en especial `routes/api/`), usar `tags` y `summary` en cada endpoint para OpenAPI.

---

## 5. Convenciones de templates

### Jinja2

- Plantillas extienden `layouts/*.html` o `base.html`.
- Auto-escape activado.
- Fragmentos en `components/` con `{% include %}` o macros.
- NO generar HTML concatenando strings en Python.
- NO lógica de negocio en templates. Solo: iteración, condicionales simples, filtros.
- Filtros Jinja reutilizables: registrar en `app/core/templating.py` al arrancar la app.

### HTMX

```html
<button
  hx-post="/documents/{{ inv.id }}/approve"
  hx-target="#invoice-row-{{ inv.id }}"
  hx-swap="outerHTML"
  hx-confirm="¿Aprobar?"
  hx-indicator="#spinner-{{ inv.id }}">
  Aprobar
</button>
```

- `hx-target` siempre explícito.
- `hx-indicator` en operaciones >300ms.
- `hx-confirm` en operaciones destructivas.

### Alpine.js

- Solo para estado puramente cliente.
- `x-data` mínimo, idealmente inline.
- Si crece >10 líneas, mover a componente registrado.
- Si el estado importa al servidor → HTMX, no Alpine.

---

## 6. Patrón página/fragmento (HTMX)

**Regla**: cada endpoint web devuelve **página completa** o **fragmento** según header `HX-Request`.

Helper en `app/core/templating.py`:

```python
def render(request: Request, full: str, partial: str, ctx: dict) -> HTMLResponse:
    template = partial if request.headers.get("HX-Request") else full
    return templates.TemplateResponse(template, {"request": request, **ctx})
```

Uso obligatorio en todos los endpoints de `routes/web/`. Beneficio: URLs siempre funcionan en hard refresh y deep link.

---

## 7. Multi-tenancy y seguridad

### Cada request

1. Middleware de auth extrae JWT.
2. Valida contra JWKS de Clerk.
3. Resuelve `tenant` y `user` locales.
4. Setea `request.state.tenant` y `request.state.user`.
5. **Setea `app.current_tenant` en sesión Postgres** (esto activa RLS).

### RLS obligatorio

Cada tabla con `tenant_id` debe tener:

```sql
ALTER TABLE <tabla> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <tabla>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

Las queries no necesitan `WHERE tenant_id = ?` explícito (RLS lo aplica), pero **es buena práctica incluirlo igualmente** como defensa en profundidad.

### Cifrado de campos sensibles

- Conexiones a BD del cliente (módulo 3): cifradas con `pgcrypto`.
- Tokens OAuth de integraciones: cifrados.

### Audit log

Toda acción sobre datos del cliente (subir, ver, descargar, modificar, borrar) debe loguearse en `audit_log`.

---

## 8. Capa LLM (referencia)

Toda la especificación arquitectónica de la capa LLM — punto de entrada único en `app/llm/client.py`, métodos `complete` y `embed`, tabla `DEFAULT_MODELS`, prompts versionados, observabilidad (`llm_calls`, Langfuse) y guardrails (validación, PII, SQL solo lectura en analítica) — está en **`arquitectura.md` §8**. Las reglas operativas (no usar SDKs desde `routes`/`services`, no prompts largos inline) siguen aplicando aquí y en el DO/DON'T.

---

## 9. Tests y evals

### Tests

- **Unit** en `tests/unit/`: lógica pura, services con mocks.
- **Integration** en `tests/integration/`: con Postgres real (testcontainers).
- **E2E** en `tests/e2e/`: Playwright sobre HTMX, flujos críticos.

Toda función pública no trivial debe tener test. Coverage objetivo: >70% en `services/`, >90% en `core/`.

### Evals (calidad LLM)

- Datasets en `app/evals/datasets/`.
- Runners en `app/evals/runners/`.
- Métricas concretas por módulo (objetivos en `arquitectura.md` §6).
- CI ejecuta evals si cambia `app/llm/` o `app/services/`.
- Bajada >5% respecto a `main` → PR falla.

---

## 10. Commits y workflow Git

- **Conventional Commits**: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`.
- Imperativo, presente, en inglés: `feat: add invoice batch upload`.
- Una rama por Paso: `paso/03-db-models`.
- PR a `main` solo con CI verde.

---

## 11. DO / DON'T

### DO

- ✅ Devolver fragmentos HTML desde endpoints HTMX.
- ✅ Validar tenant en cada request antes de tocar BD.
- ✅ Loguear toda llamada LLM en `llm_calls` y Langfuse.
- ✅ Usar Pydantic + Instructor para output estructurado.
- ✅ Mantener prompts en ficheros versionados.
- ✅ Type hints estrictos.
- ✅ Async en todo IO.
- ✅ Migraciones revisadas a mano antes de commit.
- ✅ Cifrar credenciales de cliente.
- ✅ Aplicar RLS en toda tabla con `tenant_id`.

### DON'T

- ❌ NO usar LangChain ni LlamaIndex como columna vertebral.
- ❌ NO devolver JSON desde endpoints en `routes/web/`.
- ❌ NO escribir JavaScript a mano para lógica de negocio.
- ❌ NO mezclar lógica en templates Jinja.
- ❌ NO ejecutar SQL generado por LLM con permisos de escritura.
- ❌ NO hardcodear prompts en código Python.
- ❌ NO usar `session.query()` (SQLAlchemy legado).
- ❌ NO confiar en `WHERE tenant_id = ?` sin RLS de respaldo.
- ❌ NO guardar archivos de cliente en disco; siempre R2.
- ❌ NO usar `print`, `time.sleep`, `requests` síncrono.
- ❌ NO crear ni commitear archivos `.env` (secretos vía Infisical; ver **§2**). NO commitear claves sueltas ni `app/static/css/app.css`.
- ❌ NO introducir microservicios, Kubernetes ni GraphQL en esta fase.

---

## 12. Cuando contradiga la spec

Si la petición del usuario contradice este fichero, alertar antes de ejecutar:

> "Lo que me pides contradice [sección X de AGENTS.md / arquitectura.md]. Específicamente: [explicación]. ¿Quieres que (a) lo haga así desviándome de la spec, (b) actualicemos la spec primero, o (c) busquemos una alternativa?"

---

## 13. Cuando no tenga certeza

NO inventar. Decir:

> "No estoy seguro de [X]. Necesito (a) ver la documentación de [librería], (b) verificar con un comando, o (c) que me confirmes [decisión]. ¿Cuál prefieres?"

---

## 14. Estilo de respuesta

- Conciso, sin preámbulos.
- Código completo, no stubs ni placeholders.
- Comentarios solo cuando aporten contexto no obvio.
- En español el chat, en inglés los identificadores de código.
- Si una tarea tiene riesgo arquitectónico, exponer el riesgo antes de implementar.

---

**Versión**: 1.0
