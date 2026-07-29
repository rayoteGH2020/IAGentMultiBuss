# Arquitectura general del proyecto

> **Fuente única de verdad** para diseño del sistema: visión, stack, modelo de datos (incl. esquema lógico detallado), comportamiento por módulo, capa LLM, seguridad, despliegue, bootstrap de referencia, workflow, roadmap orientativo y enlaces a documentación externa.
>
> **Convenciones de código y reglas para el asistente:** `Agents.md` (raíz; en algunos entornos `AGENTS.md` / `CLAUDE.md` como enlace al mismo contenido). **Pasos de implementación:** `PasoXX.md`. **Uso de Cursor/Claude:** `instrucciones-asistente.md`.

---

## 1. Visión del producto

SaaS modular orientado a **pymes y negocios familiares** (gestorías, peluquerías, clínicas, talleres mecánicos, panaderías, tiendas de barrio, despachos profesionales). El producto **no se vende como "IA"**, sino como herramienta que **ahorra tiempo, reduce errores y evita perder clientes**.

### Módulos

1. **Extracción y conciliación administrativa** — usuario sube PDFs, emails u otros documentos de texto así como fotos o tickets; el sistema extrae datos estructurados (fecha, proveedor, CIF, importes) y los exporta a CSV o ERP.
   - **1.5 · Consulta documental** — chat conversacional sobre los datos ya extraídos por el módulo 1 (facturas y, en el futuro, otros documentos del propio producto). Permite preguntar en lenguaje natural por proveedor, CIF/NIF, rango de fechas, importes o agregaciones, sin abandonar la app y sin requerir conocimientos SQL. Usa **tool-calling tipado** (no SQL libre): distinto del RAG sobre conocimiento (módulo 2) y del SQL agent sobre BDs externas del cliente (módulo 3).
2. **Agente RAG conversacional** — chatbot por WhatsApp, Telegram o web, alimentado con la base de conocimiento de la pyme.
3. **Analista de datos conversacional** — chat donde el dueño pregunta en lenguaje natural sobre su propio negocio y recibe respuesta con gráfico.

### Principios de producto

- **Fricción cero**: integrar donde la pyme ya está (WhatsApp/Telegram, email, Excel).
- **ROI explícito**: vender en horas ahorradas y clientes no perdidos.
- **Privacidad y GDPR**: aislamiento real por tenant, datos en UE; **no** usar datos del cliente para entrenar modelos de terceros.
- **Modular**: cada cliente activa solo los módulos que paga.

---

## 2. Vista global del sistema

```
┌──────────────────────────────────────────────────────────────────┐
│                      CLOUDFLARE (CDN + WAF)                      │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                  HETZNER VPS (Frankfurt) · Coolify               │
│                                                                  │
│  ┌──────────────────────┐    ┌──────────────────────┐            │
│  │   FastAPI app        │    │   ARQ workers        │            │
│  │   Gunicorn+Uvicorn   │    │   Background jobs    │            │
│  │   - HTMX endpoints   │    │   - Procesado lote   │            │
│  │   - JSON API         │    │   - Indexación RAG   │            │
│  │   - Webhooks         │    │   - Evals nightly    │            │
│  └──────────┬───────────┘    └──────────┬───────────┘            │
│             │                            │                        │
│             └─────────────┬──────────────┘                        │
│                           │                                       │
│                  Redis (cache, colas, ratelimit)                  │
│                                                                   │
│                  Langfuse self-hosted (LLM tracing)               │
└──────────────────────────────────────────────────────────────────┘
        │                         │                       │
        ▼                         ▼                       ▼
┌─────────────────┐    ┌────────────────────┐   ┌──────────────────┐
│  Neon Postgres  │    │  Cloudflare R2     │   │   APIs externas  │
│  + pgvector     │    │  (archivos cliente)│   │   - Anthropic    │
│  + RLS          │    │  Cifrado en reposo │   │   - Google AI    │
│                 │    │  EU region         │   │   - Voyage       │
└─────────────────┘    └────────────────────┘   │   - Clerk        │
                                                 │   - WhatsApp Biz │
                                                 └──────────────────┘
```

Una sola máquina, una sola aplicación. Servicios externos solo para lo que no aporta valor diferencial montar uno mismo (BD, storage, auth, LLMs).

---

## 3. Stack tecnológico

### Backend
- **Python 3.12+**
- **FastAPI** como framework web
- **SQLAlchemy 2.0** async como ORM
- **Alembic** para migraciones
- **Pydantic v2** + `pydantic-settings` para validación y configuración
- **Instructor** para structured output de LLMs
- **ARQ** para background jobs (sobre Redis)
- **httpx** como cliente HTTP async

### Frontend
- **Jinja2** como templating
- **HTMX 2.x** para interactividad sin SPA
- **Alpine.js 3.x** para estado puramente cliente
- **Tailwind CSS 4.x** (CLI standalone, sin ecosistema Node)
- **Basecoat UI** o componentes propios estilo shadcn

### Datos
- **PostgreSQL 16+** con extensión **pgvector**
- Hosting: **Neon** (branching tipo Git) o Supabase
- **Redis 7+** para cache, colas y rate limiting
- **Cloudflare R2** (región EU) para archivos

### IA
- **Anthropic** (Claude Sonnet/Haiku) y **Google** (Gemini Flash/Pro) como proveedores LLM
- **Voyage** o **OpenAI** para embeddings
- Cliente propio en `app/llm/client.py` con router de modelos
- **Langfuse** self-hosted para observabilidad

### Autenticación
- **Clerk** con Organizations (multi-tenant B2B)
- Validación JWT en backend contra JWKS de Clerk

### Infraestructura
- **Hetzner Cloud** (Frankfurt o Helsinki, EU)
- **Coolify** como orquestador
- **Cloudflare** delante (CDN + WAF)
- **Docker** + Docker Compose
- **GitHub Actions** para CI/CD

### Tooling
- `uv` como gestor de paquetes
- `ruff` para lint y formato
- `mypy` modo estricto
- `pytest` + `pytest-asyncio` + Playwright
- **`pre-commit`** en el repo (hooks locales): típicamente `ruff` (check + format), `mypy`, `detect-secrets`, `end-of-file-fixer`; configuración en `.pre-commit-config.yaml`. CI en GitHub Actions es la fuente de verdad para merges a `main`.

### Comandos frecuentes en desarrollo

- `uv run uvicorn app.main:app --reload` — servidor de desarrollo.
- `./scripts/tailwind_watch.sh` — Tailwind en watch (cuando exista el script).
- `uv run arq app.jobs.settings.WorkerSettings` — worker ARQ (ajustar path al `WorkerSettings` real del proyecto).
- `uv run alembic upgrade head` / `uv run alembic revision --autogenerate -m "msg"` — migraciones.
- `uv run pytest` — tests; `uv run pytest app/evals` — evals LLM cuando existan.
- `uv run ruff check . && uv run ruff format .` y `uv run mypy app` — calidad antes de PR.

---

## 4. Estructura del repositorio

```
.
├── app/
│   ├── main.py                 # FastAPI entry point
│   ├── config.py               # Settings
│   ├── deps.py                 # Dependencies de FastAPI
│   │
│   ├── core/                   # Infraestructura transversal
│   │   ├── db.py
│   │   ├── security.py
│   │   ├── storage.py
│   │   ├── cache.py
│   │   ├── rate_limit.py
│   │   ├── templating.py
│   │   └── errors.py
│   │
│   ├── models/                 # SQLAlchemy ORM
│   ├── schemas/                # Pydantic schemas
│   ├── llm/                    # Capa de IA
│   │   ├── client.py
│   │   ├── prompts/            # Prompts versionados (.txt)
│   │   ├── extraction.py
│   │   ├── embeddings.py
│   │   ├── rag.py
│   │   ├── sql_agent.py
│   │   ├── guardrails.py
│   │   └── tracing.py
│   ├── services/               # Lógica de negocio
│   ├── routes/
│   │   ├── web/                # Endpoints HTML
│   │   └── api/                # Endpoints JSON
│   ├── templates/              # Jinja2
│   ├── static/                 # CSS, JS, imágenes
│   ├── jobs/                   # ARQ workers
│   └── evals/                  # Datasets + runners
│
├── migrations/                 # Alembic
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.worker
│   └── docker-compose.yml
├── scripts/
├── .github/workflows/
├── pyproject.toml
├── alembic.ini
├── .env.example
└── Agents.md
```

> El fichero de reglas del asistente en la raíz puede aparecer como `AGENTS.md` o `CLAUDE.md` según enlace simbólico; el contenido debe ser el mismo que `Agents.md`.

---

## 5. Modelo de datos

Todas las tablas con datos de cliente tienen `tenant_id` (UUID, FK a `tenants`) con índice y **Row-Level Security activado**.

### Tablas principales

#### Identidad
- `tenants` — organizaciones. Cada pyme es un tenant.
- `users` — usuarios físicos.
- `memberships` — relación user ↔ tenant con `role` (admin | member | viewer).

#### Módulo 1 — Extracción
- `invoices` — facturas extraídas con campos estructurados (`fecha`, `proveedor`, `cif_nif`, `base_imponible`, `iva_percent`, `iva_amount`, `total`, `currency`, `raw_extraction` jsonb, `confidence`).
- `invoice_lines` — líneas de detalle por factura.

#### Módulo 1.5 — Consulta documental
- `chat_threads` — conversaciones de consulta documental (una por hilo abierto por usuario).
- `chat_messages` — mensajes individuales (`user` / `assistant` / `tool`), incluyendo tool calls y resultados serializados.
- Reutiliza tablas existentes en modo lectura: `invoices`, `invoice_lines`. La consulta se ejecuta vía tools tipadas (no SQL libre); las queries reales contra `invoices` viven en `services/invoice_service.py` y aplican RLS automáticamente.
- Requiere extensiones Postgres adicionales: `pg_trgm` (búsqueda LIKE eficiente sobre proveedor) y `unaccent` (tolerancia a tildes), además de las ya presentes (`uuid-ossp`, `pgcrypto`, `vector`).

#### Módulo 2 — RAG
- `documents` — fuentes RAG (PDFs, URLs, FAQs).
- `chunks` — fragmentos con `embedding vector(1536)` y `ts_vector` para búsqueda híbrida.
- `conversations` — conversaciones por canal (web, WhatsApp, Telegram).
- `messages` — mensajes individuales.

#### Módulo 3 — Analytics
- `data_sources` — conexiones a BDs del cliente (cifradas con pgcrypto).
- `analytics_queries` — historial de preguntas con SQL generado.

#### Transversal
- `llm_calls` — observabilidad de cada llamada a LLM (modelo, tokens, coste, latencia).
- `audit_log` — toda acción sobre datos del cliente.
- `usage_meter` — consumo mensual por tenant (para billing).

### Esquema relacional detallado (pseudo-DDL)

Notación orientativa para diseño y revisiones; el **DDL ejecutable** y los índices exactos viven en **migraciones Alembic** (`migrations/`). Mantener ambos alineados al evolucionar modelos.

```sql
-- Identidad y multi-tenancy
tenants (
  id uuid pk,
  clerk_org_id text unique,
  name text,
  plan text,                       -- free | starter | pro
  settings jsonb default '{}',
  monthly_budget_eur numeric,
  created_at timestamptz,
  updated_at timestamptz
)

users (
  id uuid pk,
  clerk_user_id text unique,
  email text unique,
  name text,
  created_at timestamptz
)

memberships (
  id uuid pk,
  user_id uuid fk -> users,
  tenant_id uuid fk -> tenants,
  role text,                       -- admin | member | viewer
  created_at timestamptz,
  unique (user_id, tenant_id)
)

-- Módulo 1: Extracción
invoices (
  id uuid pk,
  tenant_id uuid fk -> tenants,
  status text,                     -- pending | processing | ready | failed | reviewed
  source_file_key text,            -- ruta R2
  source_mime text,
  fecha date,
  proveedor text,
  cif_nif text,
  base_imponible numeric(12,2),
  iva_percent numeric(5,2),
  iva_amount numeric(12,2),
  total numeric(12,2),
  currency text default 'EUR',
  raw_extraction jsonb,
  confidence numeric(3,2),         -- 0..1
  reviewed_by uuid fk -> users null,
  reviewed_at timestamptz null,
  llm_call_id uuid fk -> llm_calls null,
  created_at timestamptz,
  updated_at timestamptz
)

invoice_lines (
  id uuid pk,
  invoice_id uuid fk -> invoices,
  tenant_id uuid,
  descripcion text,
  cantidad numeric,
  precio_unitario numeric,
  total numeric,
  position int
)

-- Módulo 1.5: Consulta documental
chat_threads (
  id uuid pk,
  tenant_id uuid fk -> tenants,    -- CASCADE
  user_id uuid fk -> users null,   -- SET NULL al borrar usuario
  title text,                      -- generado a partir del primer mensaje o editable
  created_at timestamptz,
  updated_at timestamptz
)

chat_messages (
  id uuid pk,
  thread_id uuid fk -> chat_threads,
  tenant_id uuid fk -> tenants,    -- redundante con thread, necesario para RLS directa
  role text,                       -- user | assistant | tool
  content text null,               -- texto del mensaje (null para algunos roles tool)
  tool_call jsonb null,            -- {name, arguments} cuando assistant invoca una tool
  tool_result jsonb null,          -- resultado serializado de la tool (rol = tool)
  llm_call_id uuid fk -> llm_calls null,  -- para correlar coste / traza
  tokens_in int default 0,
  tokens_out int default 0,
  cost_eur numeric default 0,
  created_at timestamptz
)
-- RLS obligatorio en ambas tablas (tenant_id = current_setting('app.current_tenant')::uuid).
-- Índices: (tenant_id, user_id, updated_at DESC) en chat_threads;
--          (tenant_id, thread_id, created_at) en chat_messages.

-- Módulo 2: RAG
documents (
  id uuid pk,
  tenant_id uuid fk,
  kind text,                       -- contract | terms | schedule | services | policy | faq | manual | other
  name text,
  original_filename text,
  source_file_key text,            -- clave R2 del texto fuente (siempre presente)
  source_mime text,
  faq_content text null,           -- pares Q/A serializados en formato P:/R: (Paso 21 B); null si no es FAQ
  status text,                     -- pending | indexing | ready | failed
  chunk_count int default 0,
  error_message text null,
  file_size_bytes int default 0,
  uploaded_by uuid fk -> users null,
  ingested_at timestamptz null,
  created_at timestamptz,
  updated_at timestamptz
)

chunks (
  id uuid pk,
  tenant_id uuid fk,
  document_id uuid fk -> documents,
  content text,
  context text,                    -- contextual retrieval
  embedding vector(1536),
  ts_vector tsvector,              -- columna generada
  metadata jsonb default '{}',
  position int,
  created_at timestamptz
)
-- Índice HNSW sobre embedding, GIN sobre ts_vector (definir en migración)

conversations (
  id uuid pk,
  tenant_id uuid fk -> tenants ON DELETE CASCADE,
  channel text,                      -- whatsapp | telegram
  external_id text null,             -- reservado para uso futuro
  customer_identifier text null,     -- teléfono E.164 (WA) o chat_id (Telegram)
  started_at timestamptz default now(),
  closed_at timestamptz null
)
-- RLS: tenant_isolation (tenant_id = current_setting('app.current_tenant'))
-- Índice compuesto: (tenant_id, channel, customer_identifier) para lookup de conversación activa

channel_messages (
  id uuid pk,
  conversation_id uuid fk -> conversations ON DELETE CASCADE,
  tenant_id uuid fk -> tenants ON DELETE CASCADE,   -- redundante con conversation, necesario para RLS directa
  role text,                         -- user | assistant
  content text,
  metadata jsonb default '{}',       -- citations, confidence, tools_used por turno
  llm_call_id uuid null,             -- correlación de coste / traza Langfuse
  created_at timestamptz default now()
)
-- RLS: tenant_isolation
-- Índice compuesto: (conversation_id, created_at) para cargar historial cronológico
-- NOTA: tabla nombrada channel_messages (no messages) para evitar colisión
--       con el módulo messages del RAG (módulo 2) si se implementa en futuro

channel_integrations (
  id uuid pk,
  tenant_id uuid fk -> tenants ON DELETE CASCADE,
  channel text,                      -- whatsapp | telegram
  phone_number_id text null,         -- WhatsApp: Phone Number ID de Meta (lookup de webhook)
  display_name text null,            -- etiqueta visible: "+34 612…" o "@MyBot"
  api_token_enc bytea null,          -- access_token (WA) / bot_token (TG) cifrado con ENCRYPTION_KEY (Fernet)
  webhook_secret_enc bytea null,     -- secret_token para X-Telegram-Bot-Api-Secret-Token, cifrado
  status text default 'active',      -- active | revoked
  confidence_threshold float default 0.5,  -- umbral de confianza para auto-responder
  created_at timestamptz,
  updated_at timestamptz,
  unique (tenant_id, channel)
)
-- RLS: tenant_isolation + webhook_select (permite SELECT cross-tenant en webhook handlers
--      usando set_config('app.webhook_lookup','true',true); flag local a la transacción)
-- UNIQUE parcial en phone_number_id WHERE active + whatsapp + NOT NULL
-- (evita enrutar webhooks al tenant equivocado; lookup fail-closed si ambigüedad legada)

-- Módulo 3: Analytics
data_sources (
  id uuid pk,
  tenant_id uuid fk,
  type text,                        -- postgres | mysql | csv_upload | shopify
  name text,
  connection_encrypted bytea,
  schema_cache jsonb null,
  last_synced_at timestamptz null
)

analytics_queries (
  id uuid pk,
  tenant_id uuid fk,
  user_id uuid fk,
  question text,
  sql_generated text,
  result_summary text,
  result_data jsonb null,
  executed_at timestamptz,
  llm_call_id uuid fk
)

-- Transversal
llm_calls (
  id uuid pk,
  tenant_id uuid fk,
  task text,
  model text,
  prompt_version text,
  input_tokens int,
  output_tokens int,
  cost_eur numeric(10,6),
  latency_ms int,
  status text,                      -- ok | error
  error text null,
  langfuse_trace_id text null,
  created_at timestamptz
)

audit_log (
  id uuid pk,
  tenant_id uuid fk,
  user_id uuid fk null,
  action text,
  resource_type text,
  resource_id uuid null,
  ip text,
  user_agent text,
  metadata jsonb,
  created_at timestamptz
)

usage_meter (
  tenant_id uuid fk,
  period date,
  invoices_count int default 0,
  rag_messages_count int default 0,
  analytics_queries_count int default 0,
  llm_cost_eur numeric default 0,
  pk (tenant_id, period)
)
```

### Multi-tenancy con RLS

```sql
ALTER TABLE <tabla> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <tabla>
  USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
```

En cada request, después de validar el JWT, se ejecuta `SET LOCAL app.current_tenant = '<uuid>'` en la sesión Postgres. Esto protege incluso si alguien olvida un `WHERE tenant_id = ?` en una query.

---

## 6. Comportamiento y límites por módulo

Especificación de dominio (flujos, decisiones técnicas y guardrails). La implementación paso a paso sigue los ficheros `PasoXX.md`.

### Módulo 1 — Extracción de facturas

**Flujo de usuario (resumen):** lista en `/documents`, subida multipart → R2 → job ARQ por archivo → polling HTMX → filas editables inline → export CSV cuando exista la feature.

**Decisiones técnicas:**

- Sin pipeline OCR dedicado: **entrada multimodal directa al LLM** salvo decisión explícita futura.
- Modelos por defecto alineados con la §8 (router): extracción principalmente `gemini-2.5-flash`; escalado a `gemini-2.5-pro` si flash no completa; fallback `claude-haiku-4-5` cuando aplique.
- **Structured output** con Instructor sobre un schema tipo `Factura` / líneas (ver `app/schemas/`); reintentos acotados en cliente Instructor.
- **Concurrencia:** hasta **5** extracciones en curso por tenant (semáforo); el resto en cola.
- Cada llamada facturable queda en **`llm_calls`** para coste por tenant.

**Límites de recursos y procesado excepcional:**

- **Tope por documento:** 3 páginas de PDF, 40 Mpx de área y 20.000 px de lado en imágenes (`DOCUMENT_MAX_*`, ver `docs/environment-variables.md`). Un fichero pequeño en bytes puede expandirse a gigabytes al decodificarse, así que la comprobación ocurre en `app/core/media_limits.py` **antes** de decodificar y es *fail-closed*: si no se puede medir, se rechaza.
- **Dónde se aplica:** en la subida (`document_upload_service`) y de nuevo en el worker. El original **sí** se sube a R2 aunque se rechace, para que el superadmin pueda revisarlo.
- **La decodificación y el parseo corren en `asyncio.to_thread`**: son CPU-bound y bloquearían el event loop del worker ARQ, congelando el resto de jobs del proceso.
- **Motivo estructurado:** el rechazo se guarda en `error_code` (`invoices`, `tickets`, `document_processing_attempts`). Los códigos de límite no son reintentables: la UI oculta el botón de reintento y remite al administrador, porque reintentar el mismo fichero daría el mismo resultado.
- **Override del superadmin:** en `/sadm/documents` puede abrir el original (URL prefirmada), ver páginas reales y estimación de tiempo y coste, y autorizar el procesado saltándose los límites de negocio —nunca `DOCUMENT_OVERRIDE_MAX_PDF_PAGES`, que protege al worker—. Al autorizar se registra un `ProcessingCharge` con el coste estimado, que el worker liquida con el coste real de la llamada LLM. No es un documento fiscal: es el apunte interno para una repercusión mensual futura.
- **Consumo por tenant:** `/sadm/usage` agrega documentos, llamadas, tokens y coste del periodo sobre `llm_calls`, con una definición única en `app/services/usage_service.py`.

**Métricas objetivo (evals):** accuracy de campos críticos (CIF, total, fecha) ≥95%; validez JSON ≥99%; latencia p50 menor de 8 s / p95 menor de 20 s por factura; coste orientativo p50 menor de 0,005 € por factura (revisar con datos reales).

**Schema Instructor (referencia; implementación en `app/schemas/`):**

```python
from datetime import date

from pydantic import BaseModel, Field


class LineaFactura(BaseModel):
    descripcion: str
    cantidad: float = Field(gt=0)
    precio_unitario: float = Field(ge=0)
    total: float = Field(ge=0)


class Factura(BaseModel):
    fecha: date = Field(description="Fecha de emisión de la factura")
    proveedor: str = Field(description="Razón social del emisor")
    cif_nif: str = Field(description="CIF o NIF del emisor", pattern=r"^[A-Z0-9]{8,10}$")
    base_imponible: float = Field(ge=0)
    iva_percent: float = Field(ge=0, le=100)
    iva_amount: float = Field(ge=0)
    total: float = Field(ge=0)
    lineas: list[LineaFactura] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, description="Confianza del modelo en la extracción")
```

### Módulo 1.5 — Consulta documental

**Propósito:** chat conversacional sobre los documentos ya procesados por el módulo 1 (facturas en MVP; ampliable a otros documentos estructurados en el futuro). El usuario pregunta en lenguaje natural y el modelo responde citando los registros relevantes.

**Diferencia con módulos vecinos:**

- No es **RAG** (módulo 2): no hay chunking ni embeddings sobre texto libre; los datos consultados son tablas estructuradas.
- No es **SQL agent** (módulo 3): no se genera SQL libre; el LLM solo invoca un conjunto cerrado de **tools tipadas**. Las BD consultadas son las **internas del producto**, no `data_sources` externos del cliente.

**Flujo de usuario:** `/chat` → composer + sidebar con `chat_threads` del usuario → al enviar mensaje, se ejecuta un loop de tool-calling sobre la capa LLM → cada paso (llamada al modelo y ejecución de tool) se persiste como `chat_message`; la respuesta del modelo se stream con **SSE** (`hx-ext="sse"`).

**Tools mínimas (MVP):**

- `search_invoices(filters)` — busca facturas del tenant con filtros tipados (`proveedor_query`, `cif_nif`, `fecha_from`, `fecha_to`, `total_min`, `total_max`, `status[]`, etc.); devuelve `Page[InvoiceRead]`.
- `get_invoice(id)` — detalle de una factura concreta (incluye líneas).
- `aggregate_invoices(filters, group_by)` — agregaciones `SUM(total)`, `COUNT(*)`, opcional `GROUP BY proveedor_normalized`.
- `list_providers(query?)` — autocompletado / listado de proveedores únicos del tenant.

**Decisiones técnicas:**

- Modelo por defecto: `claude-sonnet-4-6` (task `chat` del router LLM en §8). Override por entorno con `LLM_MODEL_CHAT`.
- Loop de tool-calling con tope **`max_iters = 6`**; si se agotan sin respuesta final, devolver mensaje de error al usuario.
- Cada iteración (LLM call + tool exec) deja registro en `llm_calls` y span anidado en Langfuse.
- Memoria de contexto: últimos **N=20** mensajes del thread (configurable). Para historiales largos, considerar resumen vía modelo `classify` (no en MVP).
- Reuso de `app/core/text_normalization.py` (`strip_accents` + `lower`) para tolerancia a tildes/mayúsculas en `proveedor`. Mismo helper que el comparador de evals.

**Guardrails obligatorios:**

- **Tools tipadas + RLS:** cada tool valida sus argumentos con Pydantic; las queries internas se ejecutan bajo `set_tenant_context(db, tenant_id)`. Imposible cruzar tenants aunque el LLM lo intente en sus `arguments`.
- **Solo lectura en MVP:** ninguna tool puede modificar facturas (marcar como revisada, editar, borrar). Si en el futuro se exponen tools mutables, requerirán confirmación explícita del usuario en UI.
- **Output del LLM saneado:** no devolver `raw_extraction` completo de `invoices` a las tools (es JSON pesado y puede contener PII). Las tools devuelven proyecciones acotadas (`InvoiceRead`).
- **Anti-prompt-injection:** límite de longitud por mensaje (4 KB); el system prompt avisa al modelo de ignorar instrucciones embebidas en datos extraídos.
- **Rate-limit:** Token bucket por `(tenant_id, user_id)` en Redis. Default conservador en MVP (p. ej. 60 mensajes/día por usuario, configurable por tenant).
- **Audit log:** cada mensaje del usuario y cada tool ejecutada se registra en `audit_log` con `tenant_id`, `user_id`, `thread_id`, `tool_name`, `cost_eur`.

**Métricas objetivo (evals `chat_invoices_v1`):**

- `tool_selection_accuracy ≥ 0.90`: el LLM escoge la tool correcta para la intención del usuario.
- `answer_grounded_in_data ≥ 0.95`: la respuesta solo usa datos devueltos por tools (sin alucinación).
- `latency_p50 < 6 s`, `p95 < 15 s` por turno (incluyendo todas las iteraciones del loop).
- `cost_per_turn` orientativo `< 0.01 €` con `claude-sonnet-4-6` y memoria de 20 mensajes.

**Dependencias con otras secciones:**

- §5 (Modelo de datos): tablas `chat_threads`, `chat_messages` y extensiones Postgres `pg_trgm`, `unaccent`.
- §7 (Frontend): SSE ya soportado por HTMX (`hx-ext="sse"`), descrito en este documento para módulos 2 y 3; aplica igual aquí.
- §8 (Capa LLM): tarea `chat` ya prevista en `TaskType`; añadir helper `run_tool_loop()` en `app/llm/client.py`.
- §9 (Seguridad): RLS, audit log, rate-limit. Sin requisitos nuevos transversales, solo aplicación.

### Módulo 2 — RAG conversacional

**Ingesta:** subida en `/knowledge` → job `index_document`: texto → chunking (~500–800 tokens, solape ~100) → opcional **contextual retrieval** (enriquecer chunk con una línea de contexto vía LLM barato) → embeddings → `chunks`.

**Consulta web:** `/chat`, mensajes persistidos; respuesta con **SSE** (stream); recuperación **híbrida** (denso + BM25, fusión tipo RRF, p.ej. k=60) → opcional rerank → top-k chunks → LLM con política de citas en system prompt.

**Consulta WhatsApp (y análogo Telegram):** webhook JSON (p.ej. `POST` bajo `routes/api/`) → identificar **tenant** (p.ej. número de destino / configuración de integración) y **usuario externo** por origen → buscar o crear `conversation` → misma tubería RAG que en web → respuesta por API del proveedor. Si la **confianza** queda por debajo del umbral o hay **escalado a humano**, notificar al negocio por el canal configurado.

**Decisiones técnicas:** embeddings por defecto `voyage-3-lite`; modelo de respuesta `claude-sonnet-4-6` (calidad) o `gemini-2.5-flash` (coste / planes inferiores).

### Módulo 3 — Analista conversacional

**Flujo:** alta de `data_source` (BD solo lectura o CSV) → introspección de esquema cacheada → chat en `/analytics` con tool use conceptual: `query_sql`, `get_schema`, generación de salida tabular y **gráfico vía plantilla** (p.ej. Chart.js servido por Jinja).

**Guardrails obligatorios:**

- Conexión a datos del cliente **siempre read-only**.
- SQL generado solo **`SELECT`**; validación con parser (p.ej. `sqlparse`) — sin DML/DDL, sin subconsultas que escriban, sin funciones peligrosas.
- **Timeout 10s** por consulta y **máximo 1000 filas** devueltas.
- La regla de aplicación «no ejecutar SQL de escritura del LLM» se mantiene; aquí el motor solo consulta.

---

## 7. Patrón frontend: HTMX + Jinja

### Modelo mental

El frontend **no es una SPA**. Es ASP.NET MVC con superpoderes:
- El servidor renderiza HTML.
- HTMX intercepta clicks/submits y hace AJAX.
- El servidor devuelve HTML (página completa o fragmento).
- HTMX intercambia trozos del DOM.

### Patrón página/fragmento

Cada endpoint web responde de dos formas según el header `HX-Request`:
- **Sin HX-Request** (visita directa, F5, deep link): página completa con layout.
- **Con HX-Request** (navegación interna HTMX): fragmento HTML.

Helper único en `app/core/templating.py`:

```python
def render(request, full: str, partial: str, ctx: dict) -> HTMLResponse:
    template = partial if request.headers.get("HX-Request") else full
    return templates.TemplateResponse(template, {"request": request, **ctx})
```

### Reparto de responsabilidades cliente

- **HTMX**: cualquier interacción que requiere ida y vuelta al servidor (formularios, búsquedas, ediciones, navegación).
- **Alpine.js**: estado puramente cliente (mostrar/ocultar dropdown, tabs, toggles UI).
- **JavaScript vanilla escrito a mano**: prohibido salvo casos justificados.

### Streaming (SSE)

Para chat con respuestas LLM en tiempo real (módulos 2 y 3): Server-Sent Events. HTMX consume nativamente con `hx-ext="sse"`.

---

## 8. Capa LLM

### Punto de entrada único

Toda llamada a modelo (**completado y embeddings**) pasa por `app/llm/client.py`. Los SDKs de Anthropic/Google **no** se usan directamente desde `services` ni `routes`.

### Cliente unificado

```python
# app/llm/client.py (contrato conceptual)
from typing import Literal
from pydantic import BaseModel

TaskType = Literal["extraction", "chat", "sql", "classify", "embedding"]

class LLMClient:
    async def complete[T: BaseModel](
        self,
        task: TaskType,
        messages: list[dict],
        response_model: type[T] | None = None,
        tools: list[dict] | None = None,
        tenant_id: UUID,
        stream: bool = False,
        model_override: str | None = None,
    ) -> T | str | AsyncIterator[str]: ...

    async def embed(self, texts: list[str], tenant_id: UUID) -> list[list[float]]: ...
```

### Router de modelos por defecto

| Tarea | Modelo |
|---|---|
| `extraction` | `gemini-2.5-flash` |
| `classify` | `claude-haiku-4-5-20251001` |
| `chat` | `claude-sonnet-4-6` |
| `sql` | `claude-sonnet-4-6` |
| `embedding` | `voyage-3-lite` |

Equivalente en código para referencia:

```python
DEFAULT_MODELS = {
    "extraction": "gemini-2.5-flash",
    "classify":   "claude-haiku-4-5-20251001",
    "chat":       "claude-sonnet-4-6",
    "sql":        "claude-sonnet-4-6",
    "embedding":  "voyage-3-lite",
}
```

### Prompts versionados

- En `app/llm/prompts/<nombre>_vN.txt`.
- Cargados con `load_prompt(name)` que cachea.
- Cambio de prompt = nueva versión (nuevo fichero), nunca editar in-place.

### Observabilidad

Cada llamada se persiste en **`llm_calls`** y se envía a **Langfuse** con, como mínimo: `tenant_id`, `user_id` cuando aplique, `task`, `model`, `prompt_version`, tokens, coste estimado, latencia.

**Regla dura (RGPD): a Langfuse no viaja contenido de cliente.** Ni documentos, ni texto extraído, ni mensajes de chat, ni consultas de búsqueda, ni respuestas del modelo. El contenido vive en Postgres (con RLS) y R2; la traza se correlaciona con él por `llm_calls.langfuse_trace_id`. Langfuse es un tercero potencialmente hosteado fuera de la infraestructura de datos, así que se trata como sistema de telemetría, no como almacén.

Lo que sí se envía, vía `app/llm/observability.py` (punto único; ningún módulo debe construir payloads de traza por su cuenta):

| Señal | Contenido |
| --- | --- |
| `input` | `messages`, `roles`, `text_chars`, `media_parts` (tipo), `media_bytes` |
| `output` | `schema`, `fields_present` / `fields_missing`, `list_sizes`, `confidence`; para texto libre solo `chars` |
| `metadata` | `tenant_id`, `prompt_version`, `provider`, `latency_ms`, `status`, `tool_calls` |
| `usage_details` / `cost_details` | tokens de entrada/salida y coste en EUR |
| `status_message` | solo el **tipo** de excepción (`ValidationError`, `APITimeoutError`, …) |

El mensaje de error completo puede arrastrar la respuesta cruda del modelo —y con ella el documento—, por eso se queda en `llm_calls.error`. `source_filename` tampoco sale: se persiste solo en `llm_calls`.

Con eso se sigue pudiendo evaluar: coste y latencia por modelo/tenant, tasa de error por tipo, distribución de `confidence` y campos que el modelo deja vacíos. Para depurar un prompt concreto se usa el trace_id contra la BD, o `LANGFUSE_CAPTURE_CONTENT=true`, que captura el payload íntegro y que `Settings` **rechaza** si `APP_ENV` no es `development` (usar solo con datos sintéticos).

### Guardrails

- **Entrada:** límites de longitud, tipos MIME permitidos, comprobación de magic bytes donde corresponda.
- **Salida:** validación con Pydantic / Instructor para respuestas estructuradas.
- **Prompt injection:** heurísticas en entrada + system prompts defensivos.
- **PII:** si el flujo lo requiere, **anonimización o bloqueo** antes de enviar texto del cliente a APIs externas (p.ej. **Microsoft Presidio** u otra capa equivalente acordada).
- **SQL:** el texto SQL generado por el LLM para el analista (módulo 3) solo se ejecuta contra conexiones **read-only** y con las restricciones de la §6; nunca contra la BD principal de la aplicación con permisos de escritura.

### Decisión: cliente propio, no LangChain

LangChain y LlamaIndex se evitan como columna vertebral por: APIs inestables, abstracciones opacas, complejidad innecesaria para casos lineales. Se usan helpers puntuales (ej. `langchain_text_splitters`) solo si justificado.

---

## 9. Seguridad y multi-tenancy

### Validación de tenant por request

1. Extraer JWT de cookie Clerk o `Authorization: Bearer`.
2. Validar firma contra JWKS de Clerk (cache 1h).
3. Extraer `org_id` y `user_id`.
4. Resolver `tenant` y `user` locales (insert si no existen).
5. Verificar `membership` activa.
6. Setear `request.state.tenant`, `request.state.user`.
7. Setear `app.current_tenant` en sesión Postgres.

### Dependencies FastAPI (patrón)

```python
from fastapi import HTTPException, Request

# Tenant, User, Membership: modelos ORM del proyecto.


async def current_tenant(request: Request) -> Tenant:
    if not request.state.tenant:
        raise HTTPException(401)
    return request.state.tenant


async def current_user(request: Request) -> User:
    if not request.state.user:
        raise HTTPException(401)
    return request.state.user


def require_role(*roles: str):
    async def _dep(request: Request) -> Membership:
        m = request.state.membership
        if m.role not in roles:
            raise HTTPException(403)
        return m

    return _dep
```

### Cifrado de campos sensibles

- Conexiones a BD del cliente (módulo 3) cifradas con `pgcrypto`.
- Tokens de integraciones (WhatsApp Business) cifrados.

### Headers de seguridad

`Strict-Transport-Security`, `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`.

### Rate limiting

Sliding window con Redis. Por tenant y por endpoint sensible.

### Audit log

Decorador o middleware loguea toda acción sobre datos del cliente.

### GDPR

- Endpoint de exportación total de datos (`/account/export` → ZIP con JSON + ficheros).
- Endpoint de borrado en cascada (`/account/delete`).
- Política de retención configurable por tenant (job nightly aplica).
- Página `/legal/subprocessors` con lista actualizada de proveedores.

---

## 10. Procesado asíncrono

### ARQ workers

Tipos de jobs:
- `process_invoice` — extracción individual (módulo 1).
- `index_document` — chunking + embedding + insert (módulo 2).
- `run_evals` — nightly, ejecuta eval set y publica métricas.
- `cleanup_old_files` — aplica política de retención GDPR.
- `generate_monthly_report` — informe de uso al cliente.

### Patrón polling con HTMX

HTTP request encola job → devuelve fragmento con `hx-trigger="every 2s"` apuntando a `/jobs/{id}/status` → endpoint devuelve progreso o resultado final → cuando termina, devuelve resultado y el polling se detiene solo (no hay elemento con trigger).

---

## 11. Observabilidad

- **Langfuse self-hosted** — trazas LLM por tenant.
- **structlog** — logs estructurados de la app.
- **Sentry** o **GlitchTip** — errores en producción.
- **Healthchecks** — `/health`, `/health/db`, `/health/redis`.
- **Dashboard interno** `/admin/metrics` con métricas clave (facturas/día, coste LLM/día, MAU, error rate).

---

## 12. Despliegue

### Fase inicial (0–50 clientes)

- 1 VPS Hetzner CX31 (Frankfurt o Helsinki) — 13€/mes.
- Coolify gestiona Docker Compose con: app, worker, redis, langfuse, postgres-langfuse.
- Postgres principal en **Neon** (free tier o ~25€/mes).
- Redis local en la VPS.
- R2 en Cloudflare con bucket por entorno.
- Cloudflare delante con SSL, WAF y caching de estáticos.

### Coste mensual estimado

| Concepto | Coste |
|---|---|
| Hetzner CX31 | 13€ |
| Neon Postgres | 25€ |
| Cloudflare R2 | 5€ |
| Clerk (B2B) | 25€ |
| LLMs | 80–150€ |
| Dominio + email | 5€ |
| **Total infra** | **~150–250€** |

Con 50 clientes a 50€/mes (2.500€ MRR), margen bruto ~90%.

### Fase crecimiento (50–500 clientes)

- VPS más grande o segunda VPS detrás de load balancer.
- Workers ARQ en VPS dedicada.
- Postgres con read replica.
- **Mantener monolito**. No Kubernetes hasta justificarlo concretamente.

---

## 13. Decisiones arquitectónicas (Decision Log)

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| FastAPI + Python | Next.js + TypeScript | Dev solo viniendo de C#/MVC que prefiere Python; ecosistema LLM más rico |
| HTMX + Jinja | React/SPA | Modelo mental MVC, sin estado cliente, menos complejidad |
| Cliente LLM propio | LangChain / LlamaIndex | APIs inestables, capas opacas, overkill para casos lineales |
| pgvector | Pinecone / Qdrant | Ya tenemos Postgres, suficiente hasta escala alta, GDPR más simple |
| Clerk | Auth propio | Ahorra 3-4 semanas; Organizations cubre B2B multi-tenant nativo |
| Cloudflare R2 | AWS S3 | Sin egress fees, más barato, EU region |
| Hetzner + Coolify | AWS / GCP | 10-20x más barato, sobra para 0-500 clientes, GDPR EU |
| Multimodal LLM directo | OCR + LLM separado | Mejor calidad en layouts variados, menos código |
| Monolito modular | Microservicios | Dev solo, despliegue simple |
| RLS Postgres | Filtrado solo en app | Defensa en profundidad |

---

## 14. Lo que NO hay (a propósito)

Cosas que verás en plantillas "modernas" pero que para esta fase son ruido y complejidad innecesaria:

- Microservicios
- Kubernetes
- Kafka / RabbitMQ
- GraphQL
- Pinecone / Qdrant / Weaviate
- LangChain / LangGraph como base
- Service mesh
- Feature flags as a service
- BFF separado
- Edge functions multi-región

Cuando alguno se necesite por un dolor concreto, se añade. Empezar con ellos bloquea.

---

## 15. Bootstrap y entorno local

- **Comandos iniciales** (`uv init`, `uv add`, Tailwind standalone, descarga HTMX/Alpine, `alembic init`): **`Paso01.md`** — guía paso a paso del arranque del repo.
- **Variables de entorno:** plantilla en **`.env.example`** en la raíz; mantenerla alineada con `app/config.py` / `Settings`.
- **Docker Compose de desarrollo** (`docker/docker-compose.yml`): **postgres** (pgvector), **redis** (ARQ), **langfuse-db**, stack **Langfuse v3** (`langfuse-web`, `langfuse-worker`, ClickHouse, MinIO interno, Redis interno de Langfuse). **MinIO opcional** aparte como sustituto local de R2 (`R2_ENDPOINT_URL`). Arranque: `docker compose -f docker/docker-compose.yml up -d`.

---

## 16. Tests, integración y evals

- **Unit:** lógica pura en `services/` y `llm/` con mocks.
- **Integration:** Postgres real (testcontainers o BD de test), cubriendo routes + services + acceso a datos.
- **E2E:** Playwright sobre flujos HTMX críticos (login, subida de factura, lectura de resultado).
- **Evals LLM:** datasets en `app/evals/datasets/`, runners en `app/evals/runners/`; en CI sobre cambios en `app/llm/` o `app/services/`; regresión de métricas respecto a `main` según umbrales definidos en CI (p. ej. caída mayor del 5 % respecto a la línea base → PR en revisión).

---

## 17. Roadmap orientativo (referencia de planificación)

Checklist histórico de alto nivel; el trabajo real se prioriza con **`PasoXX.md`** y el backlog.

### Semana 1 — Scaffolding y auth

- [ ] Estructura de carpetas creada
- [ ] `pyproject.toml` con dependencias
- [ ] `docker-compose` para Postgres + Redis + Langfuse
- [ ] FastAPI arranca con `/health` y `/health/db`
- [ ] Tailwind compilando, HTMX/Alpine servidos como estáticos
- [ ] `base.html` + `layouts/dashboard.html` con sidebar de ejemplo
- [ ] Modelos `Tenant`, `User`, `Membership` + migración inicial
- [ ] RLS activado en Postgres
- [ ] Integración Clerk: login, callback, validación JWT
- [ ] Middleware de tenant + `SET LOCAL app.current_tenant`
- [ ] Página `/dashboard` protegida saluda al usuario por nombre
- [ ] CI con tests + ruff + mypy pasando

### Semana 2 — Módulo 1 end-to-end con una factura

- [ ] Modelo `Invoice`, `InvoiceLine`, migración
- [ ] Schema Pydantic `Factura` con Instructor
- [ ] `app/llm/client.py` con router básico (Anthropic + Google)
- [ ] `app/llm/extraction.py` con prompt v1
- [ ] Integración R2 (`boto3`) con URLs prefirmadas
- [ ] Endpoint `/documents` (lista vacía si no hay)
- [ ] Endpoint `/documents/upload` (dropzone HTMX + Alpine)
- [ ] Worker ARQ `process_invoice`
- [ ] Endpoint `/jobs/{id}/status` con polling HTMX
- [ ] Fila de factura procesada en tabla
- [ ] `llm_calls` poblado correctamente
- [ ] Langfuse mostrando trazas
- [ ] Eval set de 20 facturas con ground truth + runner pytest

### Semana 3 — Pulido módulo 1

- [ ] Subida en lote con procesado paralelo (semáforo 5)
- [ ] Edición inline de celdas (HTMX por celda)
- [ ] Exportar CSV
- [ ] Manejo de errores en UI (toasts, alertas)
- [ ] Estados de carga (skeletons, spinners)
- [ ] Búsqueda en lista de facturas
- [ ] Filtros (fecha, proveedor, estado)
- [ ] Paginación o infinite scroll
- [ ] Auditoría: subida y modificación en `audit_log`
- [ ] Eval set ampliado a 50 facturas, métricas estables
- [ ] Despliegue en Hetzner con Coolify, dominio con SSL
- [ ] Primer cliente piloto subiendo facturas reales

### Después (no en las tres primeras semanas)

- Módulo 1.5 (Consulta documental sobre facturas) — ~2 semanas; ver fases detalladas en `PendienteImplementar.md`.
- Módulo 2 (RAG) — varias semanas adicionales.
- Módulo 3 (analista SQL) — varias semanas adicionales.
- Integración WhatsApp Business.
- Billing con Stripe.
- Panel de administración interna.

---

## 18. Referencias externas (documentación)

- **HTMX:** https://htmx.org/docs/ — https://htmx.org/examples/
- **Alpine.js:** https://alpinejs.dev/
- **FastAPI:** https://fastapi.tiangolo.com/
- **SQLAlchemy 2.0:** https://docs.sqlalchemy.org/en/20/
- **Instructor:** https://python.useinstructor.com/
- **Pydantic v2:** https://docs.pydantic.dev/
- **ARQ:** https://arq-docs.helpmanual.io/
- **Tailwind CSS:** https://tailwindcss.com/
- **Basecoat UI:** https://basecoatui.com/
- **Anthropic API:** https://docs.anthropic.com/
- **Google Gen AI (Gemini):** https://ai.google.dev/gemini-api/docs
- **Clerk:** https://clerk.com/docs
- **Langfuse:** https://langfuse.com/docs
- **Coolify:** https://coolify.io/docs
