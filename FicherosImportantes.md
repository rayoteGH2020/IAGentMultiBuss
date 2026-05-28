# Ficheros Importantes del Proyecto

> Referencia rápida de los ficheros clave del proyecto, organizados por área funcional.
> Para arquitectura global ver `arquitectura.md`. Para reglas operativas ver `CLAUDE.md`.

---

## 1. Validación de credenciales y autenticación

### `app/core/security.py`
Comunicación directa con Clerk para validar tokens y obtener datos de usuario/organización.

| Método | Descripción |
|--------|-------------|
| `verify_clerk_jwt(token)` | Descarga JWKS de Clerk (cache 1h), valida firma y expiración del JWT. Devuelve el payload de claims. |
| `fetch_clerk_user(user_id)` | GET `/v1/users/{user_id}` a la API de Clerk. Devuelve nombre, email e imagen del usuario. |
| `fetch_clerk_org(org_id)` | GET `/v1/organizations/{org_id}` a la API de Clerk. Devuelve nombre y slug de la organización. |

---

### `app/core/middleware.py`
Middleware que se ejecuta en **cada request** y resuelve el contexto de autenticación.

| Clase / Método | Descripción |
|----------------|-------------|
| `AuthMiddleware` | `BaseHTTPMiddleware` que extrae el JWT de cookie o header `Authorization`, invoca `try_resolve_clerk_session()` y setea `request.state.tenant`, `request.state.user`, `request.state.membership`. |
| `try_resolve_clerk_session(request, db)` | Orquesta: verifica JWT → resuelve tenant y user locales → verifica membership activa → setea `app.current_tenant` en la sesión Postgres (activa RLS). |

---

### `app/services/auth_service.py`
Lógica de sincronización entre Clerk y la BD local.

| Método | Descripción |
|--------|-------------|
| `resolve_user(db, clerk_user_id, claims)` | Obtiene o crea un `User` local a partir del `clerk_user_id` del JWT. Actualiza email/nombre si cambiaron. |
| `resolve_tenant(db, clerk_org_id, claims)` | Obtiene o crea un `Tenant` local a partir del `clerk_org_id`. Sincroniza nombre y plan. |
| `ensure_membership(db, user_id, tenant_id, role)` | Crea la fila `Membership` si no existe; actualiza el rol si cambió en Clerk. |
| `org_id_from_claims(claims)` | Extrae el `org_id` del payload del JWT. Devuelve `None` si el usuario no tiene organización activa. |
| `org_role_from_claims(claims)` | Extrae el rol de la organización (`admin`/`member`/`viewer`) del JWT. |

---

### `app/deps.py`
Dependencias FastAPI reutilizables inyectadas en los endpoints.

| Dependencia | Descripción |
|-------------|-------------|
| `CurrentUser` | Extrae `request.state.user`; lanza 401 si no autenticado. |
| `CurrentTenant` | Extrae `request.state.tenant`; lanza 401 si no resuelto. |
| `require_role(*roles)` | Factoria de dependencia que verifica que `membership.role` está en la lista. Lanza 403 si no. |
| `get_db()` | Abre `AsyncSession` con contexto de tenant ya seteado (RLS activo). |
| `get_db_no_tenant()` | Abre `AsyncSession` sin contexto de tenant (para webhooks públicos y callbacks OAuth). |
| `RedisDep` | Inyecta el cliente Redis del singleton. |

---

### `app/core/crypto.py`
Cifrado simétrico de tokens sensibles (OAuth, API keys de canales externos).

| Método | Descripción |
|--------|-------------|
| `encrypt_token(plaintext)` | Cifra una cadena con Fernet (AES-128-CBC + HMAC-SHA256) usando `ENCRYPTION_KEY` de Infisical. Devuelve `bytes`. |
| `decrypt_token(ciphertext)` | Descifra `bytes` Fernet. Lanza `ValidationError` si la clave es incorrecta o el token fue manipulado. |

---

### `app/core/oauth_state.py`
Prevención de ataques CSRF en flujos OAuth.

| Método | Descripción |
|--------|-------------|
| `generate_state(redis, user_id, tenant_id)` | Genera un nonce aleatorio, lo persiste en Redis con TTL de 10 min junto al contexto del usuario. Devuelve el nonce. |
| `consume_state(redis, state)` | Lee y elimina atómicamente el estado de Redis. Devuelve `{"user_id", "tenant_id"}` o `None` si expiró o fue reutilizado. |

---

### `app/routes/web/auth.py`
Endpoints HTML del flujo de autenticación.

| Endpoint | Descripción |
|----------|-------------|
| `GET /login` | Página de login con widget Clerk. |
| `GET /signup` | Página de registro con widget Clerk. |
| `GET /onboarding` | Página para usuario autenticado que aún no tiene organización en Clerk. |
| `POST /logout` | Inicia el flujo de cierre de sesión. |
| `GET /logout-done` | Limpia cookies de sesión en el servidor y redirige a `/login`. |

---

### `app/routes/api/webhooks.py`
Webhook de Clerk para sincronización de eventos en tiempo real.

| Endpoint | Descripción |
|----------|-------------|
| `POST /api/webhooks/clerk` | Recibe eventos `user.created`, `user.updated`, `user.deleted`, `organization.created`, `organization.deleted`. Verifica firma Svix. Sincroniza BD local. |

---

## 2. Ingesta de documentos (facturas y tickets — Módulo 1)

### `app/services/document_upload_service.py`
Punto de entrada para la subida de cualquier documento del módulo 1.

| Método | Descripción |
|--------|-------------|
| `ingest_uploaded_document(file, doc_type, tenant, user, db)` | Valida MIME, clasifica el tipo de documento y enruta a `invoice_service` o `ticket_service`. Devuelve el ID del documento creado. |

---

### `app/services/invoice_service.py`
Toda la lógica de negocio sobre facturas.

| Método | Descripción |
|--------|-------------|
| `create_invoice_from_upload(bytes, filename, mime, tenant, user, db)` | Genera clave R2, sube bytes, inserta `Invoice` en estado `processing`, encola job ARQ. Devuelve el `Invoice` creado. |
| `apply_extraction_result(invoice_id, factura, llm_call_id, db)` | Persiste el resultado estructurado del LLM sobre la `Invoice` + crea `InvoiceLine`s. Cambia estado a `ready`. |
| `mark_failed(invoice_id, error_msg, db)` | Cambia estado a `failed` y guarda el mensaje de error en `raw_extraction`. |
| `list_invoices(tenant_id, filters, db)` | Lista facturas con filtros opcionales por status/proveedor/fecha. Paginado. |
| `get_invoice(invoice_id, tenant_id, db)` | Obtiene factura con eager-load de líneas y la llamada LLM asociada. |
| `search_invoices(filters, tenant_id, db)` | Búsqueda tipada con `proveedor_query`, `cif_nif`, rangos de fecha e importe. Usado por el chat. |
| `aggregate_invoices(filters, group_by, metric, tenant_id, db)` | Ejecuta `COUNT(*)` o `SUM(total)` con agrupación opcional por `proveedor_normalized`. |
| `list_providers(query, tenant_id, db)` | Lista proveedores distintos del tenant; soporta búsqueda parcial con `unaccent`. |

---

### `app/services/ticket_service.py`
Misma estructura que `invoice_service` para tickets/recibos.

| Método | Descripción |
|--------|-------------|
| `create_ticket_from_upload(...)` | Análogo a `create_invoice_from_upload` para tickets. |
| `apply_extraction_result(ticket_id, ticket_recibo, llm_call_id, db)` | Persiste `TicketRecibo` sobre el modelo `Ticket`. |
| `search_tickets(filters, tenant_id, db)` | Búsqueda tipada de tickets. |
| `aggregate_tickets(filters, group_by, metric, tenant_id, db)` | Agregaciones sobre tickets. |
| `list_comercios(query, tenant_id, db)` | Lista comercios distintos del tenant. |

---

### `app/llm/extraction.py`
Capa LLM dedicada a la extracción estructurada de documentos.

| Función | Descripción |
|---------|-------------|
| `extract_invoice(client, bytes, mime, tenant_id, db)` | Envía el fichero al LLM (Gemini Flash por defecto), usa Instructor con schema `Factura` para obtener datos estructurados. Registra en `llm_calls`. |
| `extract_ticket(client, bytes, mime, tenant_id, db)` | Igual que `extract_invoice` pero con schema `TicketRecibo`. |
| `extract_text_from_image(client, bytes, mime, tenant_id, db)` | OCR vía LLM para imágenes; devuelve texto plano para indexación RAG. |

---

### `app/jobs/invoice_jobs.py`
Worker ARQ para procesamiento asíncrono de facturas.

| Función | Descripción |
|---------|-------------|
| `process_invoice(ctx, invoice_id, tenant_id)` | Descarga el fichero de R2, llama a `extract_invoice()`, aplica resultado con `apply_extraction_result()`. En caso de error llama a `mark_failed()`. Gestiona el semáforo de concurrencia por tenant (máx. 5). |

---

### `app/jobs/ticket_jobs.py`
Análogo a `invoice_jobs` para tickets.

| Función | Descripción |
|---------|-------------|
| `process_ticket(ctx, ticket_id, tenant_id)` | Mismo flujo que `process_invoice` pero para tickets. |

---

### `app/schemas/invoice.py`
Schemas Pydantic para structured output del LLM.

| Clase | Descripción |
|-------|-------------|
| `LineaFactura` | Línea de factura: `descripcion`, `cantidad`, `precio_unitario`, `total`. Validaciones `gt=0`. |
| `Factura` | Schema de extracción completo: `fecha`, `proveedor`, `cif_nif` (con regex), importes, `lineas`, `confidence` (0–1). |

---

### `app/schemas/ticket.py`

| Clase | Descripción |
|-------|-------------|
| `TicketRecibo` | Schema de extracción de ticket: `fecha`, `comercio`, `numero_ticket`, `forma_pago`, `total`, `confidence`. |

---

### `app/routes/web/documents.py`
Endpoints HTML del módulo 1.

| Endpoint | Descripción |
|----------|-------------|
| `GET /documents` | Lista de facturas y tickets con filtros y paginación. |
| `POST /documents/upload` | Recibe `UploadFile`, valida tipo, llama a `document_upload_service`. Devuelve fila con polling HTMX. |
| `GET /documents/{id}` | Detalle de documento. |
| `GET /jobs/{job_id}/status` | Polling HTMX: devuelve fragmento con estado del job. |

---

## 3. Ingesta de conocimiento (RAG — Módulo 2)

### `app/services/knowledge_document_service.py`
CRUD y ciclo de vida de documentos de la base de conocimiento.

| Método | Descripción |
|--------|-------------|
| `create_from_upload(file, kind, tenant, user, db)` | Valida MIME y tamaño, sube a R2, inserta `KnowledgeDocument` en estado `pending`, encola `index_knowledge_document`. |
| `list_documents(tenant_id, filters, db)` | Lista documentos con filtros por `kind` y `status`. Incluye `chunk_count`. |
| `get_document(document_id, tenant_id, db)` | Detalle con URL presignada de descarga de R2. |
| `delete_document(document_id, tenant_id, db)` | Borra `KnowledgeChunk`s en cascada, elimina objeto de R2, borra `KnowledgeDocument`. |
| `request_reindex(document_id, tenant_id, db)` | Borra chunks existentes, cambia estado a `pending`, re-encola indexación. |
| `mark_indexing(document_id, db)` | Avanza estado a `indexing` al inicio del pipeline. |
| `apply_index_result(document_id, chunk_count, db)` | Marca como `ready` y actualiza `chunk_count` + `ingested_at`. |
| `mark_failed(document_id, error_msg, db)` | Marca como `failed` con mensaje de error. |

---

### `app/services/knowledge_index_service.py`
Pipeline completo de indexación de un documento de conocimiento.

| Método | Descripción |
|--------|-------------|
| `run_index_pipeline(document_id, tenant_id, db, storage, llm_client)` | Orquesta: descarga de R2 → extracción de texto → chunking → embeddings con Voyage → inserción de `KnowledgeChunk`s en Postgres (con `embedding` y `ts_vector`). |

---

### `app/services/knowledge_search_service.py`
Motor de búsqueda híbrida para el RAG.

| Método | Descripción |
|--------|-------------|
| `search(query, tenant_id, filters, db, llm_client)` | Ejecuta búsqueda **dense** (HNSW pgvector con embedding de la query) y **sparse** (BM25 con `ts_vector`) en paralelo con `asyncio.gather`. Fusiona con **Reciprocal Rank Fusion (RRF)**. Devuelve `KnowledgeSearchResult` con lista de `KnowledgeChunkRef` ordenados por score. |
| `get_chunk_by_id(chunk_id, tenant_id, db)` | Recupera un `KnowledgeChunk` concreto por UUID (para tool `get_knowledge_chunk`). |
| `list_ready_documents(tenant_id, db)` | Lista documentos en estado `ready` con metadata básica (sin chunks). Usado por tool `list_knowledge_sources`. |

---

### `app/jobs/knowledge_jobs.py`
Worker ARQ para indexación asíncrona.

| Función | Descripción |
|---------|-------------|
| `index_knowledge_document(ctx, document_id, tenant_id)` | Llama a `knowledge_index_service.run_index_pipeline()`. Gestiona el semáforo de concurrencia. En error llama a `mark_failed()`. |

---

### `app/core/text_chunking.py`
Particionado de texto en fragmentos para indexación.

| Función | Descripción |
|---------|-------------|
| `chunk_text(text, chunk_size, overlap)` | Divide texto en chunks por párrafos con solape configurable. Respeta límite `max_chunks` por documento. |

---

### `app/core/document_text.py`
Extracción de texto plano desde distintos formatos.

| Función | Descripción |
|---------|-------------|
| `extract_knowledge_text(bytes, mime_type)` | Extrae texto de PDF (via `pdfminer`), TXT o Markdown. Devuelve texto plano limpio para el pipeline de chunking. |

---

### `app/llm/embeddings.py`
Cliente de embeddings para Voyage AI.

| Clase / Método | Descripción |
|----------------|-------------|
| `VoyageEmbedder` | Wrapper de la API de Voyage. |
| `embed(texts, input_type)` | Envía lista de textos a Voyage (`voyage-3-lite`, 512 dimensiones). Devuelve `list[list[float]]`. `input_type` es `"query"` o `"document"`. |

---

### `app/schemas/knowledge.py`

| Clase | Descripción |
|-------|-------------|
| `KnowledgeDocumentFilters` | Filtros para listar documentos: `kind`, `status`. |
| `KnowledgeDocumentRead` | Proyección del documento: `id`, `name`, `kind`, `status`, `chunk_count`, `ingested_at`. Sin `embedding`. |

---

### `app/schemas/knowledge_search.py`

| Clase | Descripción |
|-------|-------------|
| `KnowledgeChunkRef` | Referencia a un chunk con: `id`, `document_id`, `document_name`, `kind`, `position`, `content`, `context`, `score`. Sin campo `embedding`. |
| `KnowledgeSearchFilters` | Filtros de búsqueda: `kind[]`, `document_ids[]`, `top_k`, `min_score`. |
| `KnowledgeSearchResult` | Resultado completo: `chunks: list[KnowledgeChunkRef]`, `query`, `latency_ms`. |
| `KnowledgeSourceRef` | Referencia ligera a documento listo: `id`, `name`, `kind`, `chunk_count`. |

---

### `app/routes/web/knowledge.py`
Endpoints HTML del módulo 2.

| Endpoint | Descripción |
|----------|-------------|
| `GET /knowledge` | Lista de documentos de conocimiento con estado de indexación. |
| `POST /knowledge/upload` | Recibe fichero (PDF/TXT/MD), llama a `create_from_upload`, devuelve fila con polling. |
| `DELETE /knowledge/{id}` | Borra documento y chunks. Confirmación con `hx-confirm`. |
| `POST /knowledge/{id}/reindex` | Dispara reindexación del documento. |

---

## 4. Chat y tool-calling (Módulos 1.5 y 2)

### `app/llm/client.py`
Punto de entrada único para todas las llamadas LLM del sistema.

| Clase / Método | Descripción |
|----------------|-------------|
| `LLMClient` | Gestiona clientes de Anthropic y Google con un único punto de acceso. |
| `complete(task, messages, response_model, tools, tenant_id, stream, model_override)` | Selecciona modelo según `task` (ver tabla `DEFAULT_MODELS`), llama al proveedor, registra en `llm_calls` y traza en Langfuse. Si `response_model` se especifica, usa Instructor. |
| `run_tool_loop(task, messages, tools, tenant_id, max_iters)` | Loop de tool-calling: LLM → detecta tool_call → ejecuta → añade resultado → repite hasta respuesta final o `max_iters`. |
| `embed(texts, tenant_id)` | Delega en `VoyageEmbedder`, registra en `llm_calls`. |
| `get_llm_client()` | Singleton perezoso; reutiliza conexiones HTTP de los SDKs. |

**Tabla de modelos por defecto (`DEFAULT_MODELS`):**

| Tarea | Modelo |
|-------|--------|
| `extraction` | `gemini-2.5-flash` |
| `classify` | `claude-haiku-4-5-20251001` |
| `chat` | `claude-sonnet-4-6` |
| `sql` | `claude-sonnet-4-6` |
| `embedding` | `voyage-3-lite` |

---

### `app/llm/chat_loop.py`
Loop de tool-calling independiente del transporte (web, canal externo).

| Función | Descripción |
|---------|-------------|
| `run_tool_loop(llm_client, registry, messages, system_prompt, tenant_id, max_iters)` | Itera: llama al LLM → si devuelve `tool_use` ejecuta la tool en `registry` → inserta resultado → continúa. Devuelve el mensaje final del asistente + `citations_buffer` acumulado. |

---

### `app/llm/tools/registry.py`
Registro de tools disponibles para el chat.

| Clase | Descripción |
|-------|-------------|
| `ToolRegistry` | Diccionario mutable de `{name: ToolDefinition}`. Se construye según el modo (documental, conocimiento, unificado). |
| `ToolDefinition` | Nombre, schema JSON de parámetros, función ejecutora `async def execute(ctx, **args)`. |
| `ToolContext` | Contexto de ejecución: `db`, `tenant_id`, `user_id`, `thread_id`. Pasado a todas las tools. |
| `ToolResult` | Resultado de ejecución: `ok: bool`, `data: dict`, `citations: list[ToolCitation]`, `error: str | None`. |

---

### `app/llm/tools/document_chat.py`
Tools de consulta documental (módulo 1.5). Solo lectura, solo datos internos del negocio.

| Función | Descripción |
|---------|-------------|
| `execute_search_documents(ctx, filters)` | Delega en `document_query_service.search_documents()`. Devuelve `Page[DocumentRead]`. |
| `execute_get_document(ctx, doc_id, doc_type)` | Obtiene detalle de factura o ticket por ID. |
| `execute_aggregate_documents(ctx, filters, group_by, metric)` | Ejecuta agregación tipada sobre facturas/tickets. |
| `execute_list_doc_types(ctx)` | Lista tipos de documento activos del catálogo. |
| `execute_list_document_parties(ctx, query, doc_type)` | Lista proveedores (facturas) o comercios (tickets) distintos. |
| `build_document_chat_registry()` | Construye el `ToolRegistry` con las 5 tools documentales. |

---

### `app/llm/tools/knowledge_tools.py`
Tools de consulta de base de conocimiento (módulo 2). Usadas tanto en chat web como en canales externos.

| Función | Descripción |
|---------|-------------|
| `execute_search_knowledge(ctx, query, filters)` | Delega en `knowledge_search_service.search()`. Devuelve `KnowledgeSearchResult`. |
| `execute_get_knowledge_chunk(ctx, chunk_id)` | Recupera un chunk concreto por UUID. |
| `execute_list_knowledge_sources(ctx)` | Lista documentos `ready` disponibles para el tenant. |
| `register_knowledge_tools(registry)` | Añade las 3 tools de conocimiento a un `ToolRegistry` existente. |

---

### `app/services/chat_tool_runner.py`
Orquestador del chat desde las rutas web.

| Función | Descripción |
|---------|-------------|
| `get_chat_registry(tenant_id, settings)` | Construye el `ToolRegistry` combinado: siempre incluye tools documentales; añade tools de conocimiento si `knowledge_tools_enabled=True`. |
| `execute_tool(registry, tool_name, args, ctx)` | Despacha la llamada a la función ejecutora correcta del registry. Gestiona errores de tool y los convierte en `ToolResult` con `ok=False`. |

---

### `app/routes/web/chat.py`
Endpoints SSE y persistencia del chat.

| Endpoint | Descripción |
|----------|-------------|
| `GET /chat` | Lista de hilos del usuario + composer. |
| `POST /chat/threads` | Crea nuevo `ChatThread`. |
| `GET /chat/{thread_id}` | Vista de un hilo con sus mensajes. |
| `POST /chat/{thread_id}/message` | Recibe mensaje del usuario, ejecuta `run_tool_loop`, devuelve respuesta final con citas como SSE (`text/event-stream`). |

---

## 5. Modelos ORM

### `app/models/user.py`

| Campo clave | Descripción |
|-------------|-------------|
| `clerk_user_id` | ID único de Clerk; unique constraint. |
| `email`, `name` | Sincronizados desde Clerk. |

---

### `app/models/tenant.py`

| Campo clave | Descripción |
|-------------|-------------|
| `clerk_org_id` | ID de organización de Clerk; unique constraint. |
| `name`, `plan` | Nombre y plan de suscripción (`free`/`starter`/`pro`). |
| `settings` | JSONB de configuración por tenant (feature flags, umbrales). |

---

### `app/models/membership.py`

| Campo clave | Descripción |
|-------------|-------------|
| `user_id`, `tenant_id` | FK a `users` y `tenants`. Unique constraint por par. |
| `role` | `admin` / `member` / `viewer`. |

---

### `app/models/invoice.py`

| Campo clave | Descripción |
|-------------|-------------|
| `status` | `pending → processing → ready / failed / reviewed`. |
| `source_file_key` | Ruta del objeto en R2. |
| `fecha`, `proveedor`, `cif_nif`, `total` | Datos extraídos por el LLM. |
| `raw_extraction` | JSONB con el resultado bruto del LLM. |
| `confidence` | Confianza del modelo en la extracción (0–1). |
| `llm_call_id` | FK a `llm_calls` para trazabilidad. |

---

### `app/models/ticket.py`
Análogo a `Invoice` para tickets/recibos de comercio.

| Campo clave | Descripción |
|-------------|-------------|
| `comercio`, `numero_ticket`, `forma_pago`, `total` | Datos del recibo. |
| `status`, `source_file_key`, `confidence`, `llm_call_id` | Igual que en `Invoice`. |

---

### `app/models/knowledge.py`

| Modelo | Campos clave | Descripción |
|--------|-------------|-------------|
| `KnowledgeDocument` | `name`, `kind`, `status`, `source_file_key`, `chunk_count` | Documento fuente del RAG. |
| `KnowledgeChunk` | `content`, `context`, `embedding vector(512)`, `ts_vector`, `position` | Fragmento indexable con embedding de Voyage y vector de texto para BM25. |

---

### `app/models/llm_call.py`

| Campo clave | Descripción |
|-------------|-------------|
| `task` | Tipo de tarea: `extraction`, `chat`, `classify`, `embedding`. |
| `model`, `provider` | Modelo y proveedor usado. |
| `input_tokens`, `output_tokens`, `cost_eur` | Consumo y coste de la llamada. |
| `latency_ms`, `status`, `error` | Métricas de rendimiento y errores. |
| `langfuse_trace_id` | ID de traza en Langfuse para debug. |

---

### `app/models/chat.py`

| Modelo | Campos clave | Descripción |
|--------|-------------|-------------|
| `ChatThread` | `tenant_id`, `user_id`, `title` | Hilo de conversación del módulo 1.5/2. |
| `ChatMessage` | `thread_id`, `role`, `content`, `tool_call`, `tool_result`, `citations` | Mensaje individual; JSONB para tool calls y citas. |

---

### `app/models/calendar_integration.py`

| Campo clave | Descripción |
|-------------|-------------|
| `provider` | Proveedor OAuth: `google`. |
| `status` | `active` / `revoked`. |
| `access_token_enc`, `refresh_token_enc` | Tokens cifrados con `encrypt_token()`. |
| `google_email` | Email de la cuenta Google vinculada. |

---

### `app/models/audit_log.py`

| Campo clave | Descripción |
|-------------|-------------|
| `action` | Acción realizada: `invoice.upload`, `knowledge.search`, `channel.message_sent`, etc. |
| `resource_type`, `resource_id` | Tipo e ID del recurso afectado. |
| `tenant_id`, `user_id` | Contexto de quién realizó la acción. |
| `metadata` | JSONB con datos adicionales (cost_eur, citations_count, etc.). |

---

## 6. Infraestructura transversal

### `app/core/db.py`
Gestión del pool de conexiones y contexto de tenant (RLS).

| Función | Descripción |
|---------|-------------|
| `get_engine()` | `AsyncEngine` singleton con `QueuePool`; configurado con `pool_pre_ping=True`. |
| `get_sessionmaker()` | `async_sessionmaker` con `expire_on_commit=False` para evitar lazy-loads fuera de sesión. |
| `set_tenant_context(db, tenant_id)` | Ejecuta `SET LOCAL app.current_tenant = '{uuid}'` en la sesión Postgres. Activa RLS. |
| `clear_tenant_context(db)` | Limpia la variable de sesión (para tests y jobs globales). |
| `session_scope()` | Context manager async con commit automático y rollback en excepción. Para workers ARQ. |
| `session_factory_for_worker(tenant_id)` | Abre sesión con RLS seteado para workers ARQ que procesan tareas por tenant. |

---

### `app/core/storage.py`
Cliente S3-compatible para Cloudflare R2.

| Método | Descripción |
|--------|-------------|
| `upload_bytes(key, data, content_type)` | Sube bytes a R2. Lanza `ExternalServiceError` si falla. |
| `download_bytes(key)` | Descarga objeto completo en memoria. |
| `delete(key)` | Elimina objeto del bucket. |
| `presigned_url_get(key, expires_in)` | URL firmada GET para descarga directa desde el navegador (sin pasar por la app). |
| `exists(key)` | `HEAD` al objeto; devuelve `bool`. |
| `get_storage()` | Singleton del cliente `Storage`. |

---

### `app/core/errors.py`
Jerarquía de excepciones de dominio y sus handlers HTTP.

| Excepción | Código HTTP | Descripción |
|-----------|-------------|-------------|
| `NotFoundError` | 404 | Recurso no encontrado. |
| `ValidationError` | 422 | Datos de entrada inválidos. |
| `AuthError` | 401 | Sin autenticación válida. |
| `ForbiddenError` | 403 | Sin permisos suficientes. |
| `RateLimitError` | 429 | Límite de peticiones superado. |
| `ExternalServiceError` | 502 | Fallo en LLM, R2 o Clerk. |
| `LLMCompleteError` | 502 | Fallo específico de LLM con `llm_call_id` para trazabilidad. |
| `register_error_handlers(app)` | — | Registra todos los handlers en la app FastAPI. |

---

### `app/core/templating.py`
Helper para el patrón página/fragmento de HTMX.

| Función | Descripción |
|---------|-------------|
| `render(request, full, partial, ctx)` | Si `HX-Request` header presente: renderiza `partial`. Si no (visita directa/F5): renderiza `full`. Garantiza que las URLs funcionen con deep-link. |

---

### `app/core/rate_limiter.py`
Control de tasas de peticiones por tenant.

| Función | Descripción |
|---------|-------------|
| `check_rate_limit(redis, key, max_requests, window_seconds)` | Implementa sliding window con Redis. Lanza `RateLimitError` si se supera el límite. Usado en upload de documentos y mensajes de chat. |

---

### `app/core/keys.py`
Generación de claves R2 consistentes y namespaced.

| Función | Descripción |
|---------|-------------|
| `invoice_key(tenant_id, invoice_id, filename)` | Genera ruta `invoices/{tenant_id}/{invoice_id}/{filename}`. |
| `ticket_key(tenant_id, ticket_id, filename)` | Genera ruta `tickets/{tenant_id}/{ticket_id}/{filename}`. |
| `document_key(tenant_id, document_id, filename)` | Genera ruta `knowledge/{tenant_id}/{document_id}/{filename}`. |

---

## 7. Configuración

### `app/config.py`
Pydantic `BaseSettings` con `env_file=None` (variables solo desde entorno/Infisical).

| Sección | Variables clave |
|---------|----------------|
| **App** | `app_base_url`, `debug`, `timezone` |
| **Database** | `postgres_url` (AsyncPG DSN) |
| **Storage R2** | `r2_endpoint_url`, `r2_access_key_id`, `r2_secret_access_key`, `r2_bucket` |
| **Auth Clerk** | `clerk_secret_key`, `clerk_jwks_url`, `clerk_webhook_secret` |
| **LLM** | `anthropic_api_key`, `google_api_key`, `voyage_api_key` |
| **Observabilidad** | `langfuse_secret_key`, `langfuse_public_key`, `langfuse_host` |
| **Knowledge RAG** | `knowledge_tools_enabled`, `knowledge_chat_max_citations`, `knowledge_chat_min_score_threshold` |
| **Google Calendar** | `google_oauth_client_id`, `google_oauth_client_secret`, `google_calendar_scopes` |
| **Crypto** | `encryption_key` (Fernet, usado por `crypto.py`) |
| `get_settings()` | Singleton `@lru_cache`. Llamar siempre a esta función, nunca instanciar `Settings()` directamente. |

---

### `app/main.py`
Entry point de la aplicación FastAPI.

| Elemento | Descripción |
|----------|-------------|
| `create_app()` | Crea la instancia FastAPI, registra routers, monta estáticos, añade `AuthMiddleware`, registra `register_error_handlers()`. |
| `lifespan` | Context manager async: inicializa Redis, verifica BD, inicia Langfuse al arranque; cierra conexiones al apagado limpio. |
| Routers registrados | `web/auth`, `web/home`, `web/documents`, `web/knowledge`, `web/chat`, `web/settings`, `web/integrations`, `web/jobs`, `api/health`, `api/webhooks`, `api/metrics`. |

---

## 8. Jobs ARQ

### `app/jobs/settings.py`
Configuración del worker ARQ.

| Elemento | Descripción |
|----------|-------------|
| `WorkerSettings` | Define las funciones de job registradas, Redis pool, timeout por job, concurrencia máxima. |
| `on_startup`, `on_shutdown` | Inicialización y limpieza de recursos del worker (DB pool, LLM clients, Storage). |

---

### `app/jobs/queue.py`
Fachada para encolar jobs desde la aplicación web.

| Función | Descripción |
|---------|-------------|
| `get_arq_pool()` | Singleton perezoso del pool ARQ sobre Redis. |
| `enqueue_invoice_processing(invoice_id, tenant_id)` | Encola `process_invoice` con los IDs necesarios. |
| `enqueue_ticket_processing(ticket_id, tenant_id)` | Encola `process_ticket`. |
| `enqueue_knowledge_indexing(document_id, tenant_id)` | Encola `index_knowledge_document`. |

---

### `app/jobs/invoice_slots.py`

| Función | Descripción |
|---------|-------------|
| `tenant_invoice_extraction_slot(redis, tenant_id)` | Context manager async. Implementa semáforo con Redis: máx. 5 extracciones simultáneas por tenant. El resto espera en cola. |

---

## 9. Observabilidad y auditoría

### `app/services/audit_service.py`
Registro inmutable (append-only) de todas las acciones relevantes.

| Función | Descripción |
|---------|-------------|
| `log_action(db, tenant_id, user_id, action, resource_type, resource_id, metadata, ctx)` | Inserta fila en `audit_log`. `ctx` aporta IP y User-Agent. |
| `log_chat_user_message(db, ...)` | Audita envío de mensaje en chat: acción `chat.message_sent`. |
| `log_chat_tool_executed(db, ...)` | Audita ejecución de tool: acción `chat.tool_executed` con `tool_name` y `cost_eur`. |
| `log_knowledge_chat_search(db, ...)` | Audita búsqueda de conocimiento: acción `knowledge.chat_search` con `citations_count`. |

---

### `app/llm/tracing.py`
Integración con Langfuse para observabilidad de llamadas LLM.

| Función | Descripción |
|---------|-------------|
| `get_langfuse()` | Singleton del cliente Langfuse. Si `LANGFUSE_SECRET_KEY` no está configurada, devuelve cliente noop. |
| `create_trace(name, tenant_id, user_id, metadata)` | Crea una traza padre en Langfuse para una operación completa (ej. un turno de chat). |
| `create_span(trace, name, input, metadata)` | Crea un span anidado (ej. cada llamada al LLM dentro del loop). |

---

### `app/llm/pricing.py`

| Función | Descripción |
|---------|-------------|
| `compute_cost_eur(model, input_tokens, output_tokens)` | Calcula coste en EUR usando la tabla de precios por modelo. Devuelve `Decimal`. |

---

## 10. Evals (calidad LLM)

### `app/evals/runners/extraction.py`
Eval de extracción de facturas/tickets.

| Función | Descripción |
|---------|-------------|
| `run_extraction_eval(dataset_path)` | Ejecuta extracción sobre el dataset y compara con ground truth. Métricas: accuracy por campo (`cif_nif`, `total`, `fecha`), `validity_rate`, `latency_p50`, `cost_p50`. |

---

### `app/evals/runners/knowledge_retrieval.py`
Eval del motor de búsqueda híbrida.

| Función | Descripción |
|---------|-------------|
| `run_retrieval_eval(dataset_path)` | Por cada pregunta del dataset busca en el índice y verifica si el chunk correcto aparece en top-5. Métrica: `recall@5`. Objetivo ≥ 0.75. |

---

### `app/evals/runners/knowledge_qa.py`
Eval end-to-end de Q&A con RAG + LLM.

| Función | Descripción |
|---------|-------------|
| `run_qa_eval(dataset_path)` | Invoca el pipeline RAG completo con LLM. Métricas: `retrieval_recall@5`, `answer_grounded`, `citation_present`, `latency_p50`, `cost_per_question_eur`. Objetivos: retrieval ≥ 0.80, grounded ≥ 0.85. |
