# Variables de entorno (Infisical)

Los valores reales (secretos, URLs con credenciales) se guardan en **Infisical**; la app arranca con `infisical run -- ...` (ver `Agents.md` §2). **No** usar ficheros `.env` en el repositorio.

> **Por qué Infisical y no `.env`:** los ficheros `.env` suelen acabar en el repo por accidente (`.gitignore` mal configurado, editors que los crean automáticamente). Infisical inyecta los valores directamente en el entorno del proceso en tiempo de ejecución, sin tocar disco, y los gestiona con control de acceso, auditoría y rotación centralizada.

Nombres en **MAYÚSCULAS**: `pydantic-settings` lee las variables del entorno del proceso (`os.environ`) y las mapea a los campos de `app/config.py` de forma case-insensitive.

## Variables por grupo

### App general

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `APP_ENV` | Sí | Controla comportamiento condicional (`is_dev`): logs más verbosos, saltar validaciones de Clerk, etc. Valores: `development`, `staging`, `production`. |
| `APP_SECRET_KEY` | Sí | Firma tokens internos y cookies de sesión. Debe ser un valor aleatorio largo; si rota, las sesiones activas se invalidan. |
| `APP_BASE_URL` | No | URL canónica usada para construir enlaces absolutos (webhooks, emails). En dev `http://localhost:8000`. |
| `LOG_LEVEL` | No | Filtra la verbosidad de structlog. `DEBUG` en desarrollo, `INFO` en producción. |

### Base de datos y caché

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `DATABASE_URL` | Sí | Conexión async a Postgres (`postgresql+asyncpg://…`). SQLAlchemy usa el prefijo `asyncpg` para saber qué driver cargar. |
| `REDIS_URL` | Sí | Redis para colas ARQ, caché y semáforos de concurrencia por tenant. |

### Almacenamiento (Cloudflare R2)

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `R2_ACCOUNT_ID` | En prod | ID de cuenta Cloudflare; forma parte del endpoint S3-compatible. |
| `R2_ACCESS_KEY_ID` | En prod | Clave de acceso del token de API de R2. |
| `R2_SECRET_ACCESS_KEY` | En prod | Secreto del token; equivalente a `AWS_SECRET_ACCESS_KEY`. |
| `R2_BUCKET` | No | Nombre del bucket. Default `saas-files`; puede variar por entorno (dev/staging/prod). |
| `R2_PUBLIC_URL` | En prod | URL pública del bucket para servir ficheros; vacía en dev (se usan URLs prefirmadas). |
| `R2_REGION` | No | Siempre `auto` para R2 (no usa regiones AWS). Cambiar este valor rompe la autenticación con Cloudflare. |
| `R2_ENDPOINT_URL` | Solo dev | Si se define, boto3 apunta a MinIO local en lugar de R2. `None` en producción. |
| `STORAGE_PRESIGNED_TTL_SECONDS` | No | Vida de las URLs prefirmadas (default 3600 s = 1 h). |

### Autenticación (Clerk)

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `CLERK_SECRET_KEY` | En prod | Usado por el backend para validar tokens y llamar a la API de Clerk. |
| `CLERK_PUBLISHABLE_KEY` | En prod | Clave pública usada en el frontend para inicializar el widget de Clerk. |
| `CLERK_JWKS_URL` | En prod | URL del endpoint JWKS de Clerk desde el que el backend descarga las claves públicas para verificar JWTs. Se cachea 1 h. |
| `CLERK_WEBHOOK_SECRET` | En prod | Secreto para verificar la firma de los webhooks de Clerk (eventos de usuario/organización). |
| `CLERK_JWT_AZP_ALLOWLIST` | En staging/prod si hay JWKS | CSV de valores `azp` aceptados del session JWT. Si no está vacío, el claim es obligatorio. Evita aceptar tokens de otro cliente/entorno. |
| `CLERK_JWT_AUDIENCE_ALLOWLIST` | En staging/prod si hay JWKS (alternativa o complemento a azp) | CSV de valores `aud` aceptados. Misma política fail-closed que azp. Al menos una de las dos allowlists debe definirse fuera de development cuando `CLERK_JWKS_URL` está configurada. |

### Proveedores LLM

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `ANTHROPIC_API_KEY` | En prod | Acceso a Claude (chat, clasificación, SQL). Obligatoria si se usan modelos Anthropic. |
| `GOOGLE_API_KEY` | En prod | Acceso a Gemini (extracción de facturas). Obligatoria para el módulo 1. |
| `VOYAGE_API_KEY` | En prod | Acceso a Voyage para embeddings (módulo 2 RAG). Puede omitirse si el módulo 2 no está activo. |
| `LLM_MODEL_EXTRACTION` | No | Override del modelo de extracción. Si no se define, `LLMClient` usa `gemini-3.8-flash` (arquitectura.md §8). En modelos Gemini Flash la extracción baja el thinking al mínimo (`app/llm/client.py`, `_google_thinking_config`). |
| `LLM_MODEL_CHAT` | No | Override del modelo de chat. Default `gemini-3.5-flash-lite` (`GOOGLE_API_KEY`, D015). Debe coincidir en todos los entornos. Alternativa: `claude-sonnet-4-6` con `ANTHROPIC_API_KEY`. |
| `LLM_MODEL_CLASSIFY` | No | Override del modelo de clasificación. Default `claude-haiku-4-5-20251001`. |
| `LLM_MODEL_SQL` | No | Override del modelo SQL. Default `claude-sonnet-4-6`. |
| `LLM_BUDGET_WARN_RATIO` | No | Fracción del presupuesto mensual de IA a partir de la cual se envía un email al admin del tenant (una vez por mes). Default `0.8` (D019). |
| `CHAT_BUDGET_CUTOFF_RATIO` | No | Fracción del presupuesto a partir de la cual el chat de la app deja de llamar al LLM y responde con el mensaje fijo de contacto (teléfono y email del admin del tenant, tabla `users`, D020). Default `0.9` (D019). |
| `CHAT_CUTOFF_NOTIFY_INTERVAL_SECONDS` | No | Intervalo mínimo entre avisos al admin tras el corte del chat. Default `86400` (24 h). |
| `CHAT_CUTOFF_NOTIFY_MAX_PER_MONTH` | No | Máximo de avisos al admin por el corte del chat en un mes. Default `3`. Al cruzar `CHAT_BUDGET_CUTOFF_RATIO` también se avisa al SADM (`EMAIL_SADM`) una vez al mes, con nombre y apellido del admin (Clerk) y su email y teléfono (tabla `users`, D020). |
| `CHAT_RATE_LIMIT_PER_MINUTE` | No | Preguntas al chat por usuario y organización en un minuto (default `10`, D023). Freno contra scripts o cuentas comprometidas, igual en todos los planes; el cupo comercial es `chat_questions_per_month` del plan. `0` = sin límite. Sustituye a `CHAT_DAILY_MESSAGE_LIMIT` y `CHAT_USER_DAILY_MESSAGE_LIMIT`, retiradas (bloque 4): si siguen en Infisical se ignoran. |
| `CHAT_RATE_LIMIT_PER_HOUR` | No | Igual que la anterior, por hora (default `60`). |
| `LLM_BUDGET_EXHAUSTED_NOTICE` | No | Texto del banner que ven todos los usuarios del tenant en todas las páginas del panel cuando el gasto de IA del mes llega al 100 % del presupuesto. Tiene un texto por defecto en `app/config.py`. |
| `EMAIL_SADM` | No | Email del superadmin para avisos de plataforma: usuarios sin organizacion, presupuesto de IA de un tenant al 90 % y **proveedor de IA sin saldo o con la facturacion bloqueada (HTTP 402)**, este ultimo como mucho una vez cada 6 h por proveedor. Vacio = no se envian (queda un warning en logs). |
| `LLM_EXTRACTION_MAX_RETRIES` | No | Reintentos automáticos de Instructor por extracción de documento cuando la respuesta no cumple el schema. Default `2` (hasta 3 llamadas); admite `0`-`2`, con tope 2 por regla de producto (`especificacion-planes-y-cuotas.md` §4.2). El worker no repite la extracción si se reinicia a mitad (`app/jobs/extraction_guard.py`). |

### Observabilidad (Langfuse)

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `LANGFUSE_PUBLIC_KEY` | No | Clave pública del proyecto Langfuse; identifica el proyecto en el servidor. |
| `LANGFUSE_SECRET_KEY` | No | Clave secreta para autenticar las trazas enviadas desde la app. |
| `LANGFUSE_HOST` | No | URL del servidor Langfuse. En local apunta al contenedor `langfuse-web` del compose (`http://localhost:3000`); en prod a la instancia self-hosted en la VPS. Si está vacío, las trazas se descartan silenciosamente. |
| `LANGFUSE_CAPTURE_CONTENT` | No | `false` por defecto: a Langfuse solo van metadatos de evaluación (modelo, tokens, coste, latencia, forma del resultado), nunca documentos, mensajes ni consultas (`arquitectura.md` §8). A `true` captura el payload íntegro para depurar prompts; `Settings` lanza `ValidationError` si `APP_ENV` es `staging` o `production`. |
| `ACTIVITY_LOG_ENABLED` | No | `true` por defecto: registra peticiones, jobs, eventos y errores en la tabla `activity_log` (D029), sin datos personales salvo `user_id`. `false` lo apaga sin redeploy (incidente); los tests lo fuerzan a `false`. |
| `ACTIVITY_LOG_RETENTION_DAYS` | No | Días que se conservan las filas de `activity_log` (90 por defecto). `0` = no purgar, solo fuera de producción: con `APP_ENV=production` la app no arranca (RGPD). Valores entre 1 y 6 se rechazan; la función de purga en BD exige también un mínimo de 7. |
| `AUDIT_LOG_RETENTION_DAYS` | No | Días que se conservan las entradas de `audit_log` (730 = 2 años por defecto, P2c-7). `0` = no purgar, solo fuera de producción: con `APP_ENV=production` la app no arranca (RGPD). Valores entre 1 y 364 se rechazan; la función de purga en BD exige también un mínimo de 365. Purga diaria a las 03:45. |
| `AUDIT_PSEUDONYM_KEY` | **Sí en producción** | Clave de los seudónimos HMAC de la metadata de `audit_log` (emails, nombres de fichero y de profesionales, P2c-7). Mínimo 32 caracteres y distinta de `APP_SECRET_KEY`. **No se rota nunca**: con otra clave los seudónimos antiguos ya no se pueden recalcular y `scripts/audit_lookup.py` deja de encontrar el historial anterior. Fuera de producción, si falta, se deriva de `APP_SECRET_KEY`. |

> **Langfuse v3 (dev local):** el compose levanta `langfuse-web`, `langfuse-worker`, ClickHouse, MinIO y Redis propios de Langfuse. Tras migrar desde v2, borra `docker/data/langfuse-db/` en dev si hay errores de esquema, entra en la UI, copia las API keys del proyecto `mi-saas-dev` a Infisical y reinicia el worker ARQ (`get_langfuse()` cachea las claves al arrancar).

### Seguridad y métricas

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `ENCRYPTION_KEY` | En prod | Clave AES-256 (32 bytes en base64) para cifrar campos sensibles en BD (conexiones de clientes, tokens OAuth). |
| `METRICS_TOKEN` | No | Bearer token para el endpoint `GET /metrics/module1`. Autenticación máquina-a-máquina (CI, dashboards internos); no es auth de usuario. |
| `WEBHOOK_ALLOW_UNSIGNED` | No | Default `false`. Si `true` **y** `APP_ENV=development`, permite procesar webhooks de WhatsApp/Telegram sin verificación criptográfica (solo dev local). Prohibido en staging/production (la app no arranca). |
| `WEBHOOK_MAX_BODY_BYTES` | No | Tope de bytes del body **antes** de parsear JSON (default `262144` = 256 KiB). Aplica a WhatsApp, Telegram y Clerk. |
| `WEBHOOK_DEDUPE_TTL_SECONDS` | No | TTL de la clave Redis `SET NX` anti-replay (default `86400`). Claves `webhook:dedupe:<provider>:<event_id>`. |
| `WHATSAPP_APP_SECRET` | En prod | Secreto de la app Meta para validar `X-Hub-Signature-256` en POST `/api/webhooks/whatsapp`. Obligatorio en staging/production antes de guardar integraciones WhatsApp. |
| `WHATSAPP_VERIFY_TOKEN` | En prod | Token arbitrario que Meta devuelve en la verificación GET del webhook. |

### Límites de procesado documental

Todos tienen default en `app/config.py`; se sobreescriben por entorno solo si hay una razón medida. Un PDF o una imagen pequeños en bytes pueden expandirse a gigabytes al decodificarse (*decompression bomb*), así que estos topes se validan **antes** de decodificar y son *fail-closed*: si no se pueden verificar, el documento se rechaza.

| Variable | Default | Por qué existe |
|----------|:-------:|----------------|
| `DOCUMENT_MAX_PDF_PAGES` | `3` | Páginas admitidas por documento de negocio (factura, ticket, póliza). Los contratos usan `contract_max_pages` del plan (100), con `DOCUMENT_OVERRIDE_MAX_PDF_PAGES` como techo. Todas se envían al LLM: subirlo multiplica coste y latencia por documento. |
| `CONTRACT_UPLOAD_PAGE_TIERS` | `30,60` | Tramos de altas por contrato según sus páginas (D027): hasta 30 páginas consume 1 alta, de 31 a 60 consume 2 y más de 60 consume 3. Umbrales crecientes en CSV o JSON; un valor mal formado impide arrancar. |
| `DOCUMENT_MAX_IMAGE_PIXELS` | `40000000` | Área máxima tras decodificar (~8000 x 5000). Es también el `Image.MAX_IMAGE_PIXELS` con el que Pillow aborta la decodificación. |
| `DOCUMENT_MAX_IMAGE_EDGE_PX` | `20000` | Lado máximo. Descarta imágenes tipo 1 x 500.000 px que pasarían el filtro de área. |
| `DOCUMENT_OVERRIDE_MAX_PDF_PAGES` | `100` | Techo duro del procesado excepcional que autoriza el superadmin. El override salta los límites de negocio, nunca los de supervivencia del worker. |
| `DOCUMENT_ESTIMATED_SECONDS_PER_PAGE` | `15.0` | Base de la estimación de tiempo mostrada antes de autorizar. |
| `DOCUMENT_ESTIMATED_INPUT_TOKENS_PER_PAGE` | `2500` | Base de la estimación de coste (tokens de entrada por página). |
| `DOCUMENT_ESTIMATED_OUTPUT_TOKENS_PER_PAGE` | `900` | Base de la estimación de coste (tokens de salida por página). |
| `DOCUMENT_OVERRIDE_CHARGE_MULTIPLIER` | `1.0` | Multiplicador sobre el coste de proveedor al repercutir un procesado excepcional (`1.0` = a coste, sin margen). |
| `KNOWLEDGE_MAX_PDF_PAGES` | `300` | Los documentos de conocimiento son manuales o libros: el tope es mucho mayor que en facturas, pero existe porque `pypdf` recorre página a página. Al superarlo se rechaza el documento entero, no se indexa un trozo. |
| `KNOWLEDGE_MAX_EXTRACTED_CHARS` | `1300000` | Techo de texto extraído antes de chunkificar (~325k tokens). Al superarlo se trunca con warning. |

Calibrar `DOCUMENT_ESTIMATED_*` con datos reales de la tabla `llm_calls` cuando haya volumen; los valores actuales son una aproximación a partir de extracciones de 1-3 páginas.

## Ejemplos no secretos (dev local, Paso 02)

Solo guía al rellenar Infisical; credenciales de ejemplo del compose local:

- `DATABASE_URL=postgresql+asyncpg://saas:saas@localhost:5432/saas`
- `REDIS_URL=redis://localhost:6379/0`
- `LANGFUSE_HOST=http://localhost:3000`
- `LANGFUSE_PUBLIC_KEY=pk-lf-mi-saas-dev-local` (headless init del compose local v3)
- `LANGFUSE_SECRET_KEY=sk-lf-mi-saas-dev-local`
- `R2_ENDPOINT_URL=http://localhost:9000` (MinIO local)
- `APP_ENV=development`
- `WEBHOOK_ALLOW_UNSIGNED=true` (solo dev local; nunca en staging/production)

## GitHub Actions (CI)

El workflow `.github/workflows/ci.yml` se ejecuta en cada PR y en push a `main`. **No** usa Infisical: inyecta variables de entorno de prueba en el job.

| Job | Qué verifica | Infra |
|-----|--------------|-------|
| `lint-and-typecheck` | `ruff check`, `ruff format --check`, `mypy app` | Ninguna |
| `test` | `alembic upgrade head`, `alembic check`, `pytest tests/unit`, `pytest tests/integration -m "integration and not real_llm"` | Postgres (pgvector) + Redis |

Variables mínimas del job `test` (valores fijos en el YAML, no secretos):

| Variable | Valor CI | Notas |
|----------|----------|-------|
| `APP_SECRET_KEY` | `ci-not-secret` | Solo CI; no usar en prod |
| `DATABASE_URL` | `postgresql+asyncpg://saas:saas@localhost:5432/saas` | Superusuario para migraciones |
| `RLS_TEST_DATABASE_URL` | `postgresql+asyncpg://saas_app:saas@localhost:5432/saas` | Rol RLS para fixtures de tests |
| `REDIS_URL` | `redis://localhost:6379/0` | Servicio Redis del workflow |
| `ENCRYPTION_KEY` | Clave Fernet fija en `ci.yml` | Tests de cifrado / webhooks |

**No** se inyectan claves LLM ni R2 en CI principal. Tests marcados `real_llm` o gateados por `RUN_LLM_TESTS` / `RUN_R2_TESTS` quedan excluidos.

Los **evals LLM** (coste API) siguen en `.github/workflows/evals.yml` con path filter; requieren secrets de repositorio: `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, etc.

Simular CI localmente:

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
# Con Postgres + Redis levantados (docker compose) y migraciones aplicadas:
uv run pytest tests/unit -q
uv run pytest tests/integration -q -m "integration and not real_llm"
```
