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

### Proveedores LLM

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `ANTHROPIC_API_KEY` | En prod | Acceso a Claude (chat, clasificación, SQL). Obligatoria si se usan modelos Anthropic. |
| `GOOGLE_API_KEY` | En prod | Acceso a Gemini (extracción de facturas). Obligatoria para el módulo 1. |
| `VOYAGE_API_KEY` | En prod | Acceso a Voyage para embeddings (módulo 2 RAG). Puede omitirse si el módulo 2 no está activo. |
| `LLM_MODEL_EXTRACTION` | No | Override del modelo de extracción. Si no se define, `LLMClient` usa `gemini-2.5-flash` (arquitectura.md §8). |
| `LLM_MODEL_CHAT` | No | Override del modelo de chat. Default `gemini-2.5-flash` (`GOOGLE_API_KEY`). Alternativa: `claude-sonnet-4-6` con `ANTHROPIC_API_KEY`. |
| `LLM_MODEL_CLASSIFY` | No | Override del modelo de clasificación. Default `claude-haiku-4-5-20251001`. |
| `LLM_MODEL_SQL` | No | Override del modelo SQL. Default `claude-sonnet-4-6`. |

### Observabilidad (Langfuse)

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `LANGFUSE_PUBLIC_KEY` | No | Clave pública del proyecto Langfuse; identifica el proyecto en el servidor. |
| `LANGFUSE_SECRET_KEY` | No | Clave secreta para autenticar las trazas enviadas desde la app. |
| `LANGFUSE_HOST` | No | URL del servidor Langfuse. En local apunta al contenedor `langfuse-web` del compose (`http://localhost:3000`); en prod a la instancia self-hosted en la VPS. Si está vacío, las trazas se descartan silenciosamente. |
| `LANGFUSE_CAPTURE_CONTENT` | No | `false` por defecto: a Langfuse solo van metadatos de evaluación (modelo, tokens, coste, latencia, forma del resultado), nunca documentos, mensajes ni consultas (`arquitectura.md` §8). A `true` captura el payload íntegro para depurar prompts; `Settings` lanza `ValidationError` si `APP_ENV` es `staging` o `production`. |

> **Langfuse v3 (dev local):** el compose levanta `langfuse-web`, `langfuse-worker`, ClickHouse, MinIO y Redis propios de Langfuse. Tras migrar desde v2, borra `docker/data/langfuse-db/` en dev si hay errores de esquema, entra en la UI, copia las API keys del proyecto `mi-saas-dev` a Infisical y reinicia el worker ARQ (`get_langfuse()` cachea las claves al arrancar).

### Seguridad y métricas

| Variable | Obligatoria | Por qué existe |
|----------|:-----------:|----------------|
| `ENCRYPTION_KEY` | En prod | Clave AES-256 (32 bytes en base64) para cifrar campos sensibles en BD (conexiones de clientes, tokens OAuth). |
| `METRICS_TOKEN` | No | Bearer token para el endpoint `GET /metrics/module1`. Autenticación máquina-a-máquina (CI, dashboards internos); no es auth de usuario. |
| `WEBHOOK_ALLOW_UNSIGNED` | No | Default `false`. Si `true` **y** `APP_ENV=development`, permite procesar webhooks de WhatsApp/Telegram sin verificación criptográfica (solo dev local). Prohibido en staging/production (la app no arranca). |
| `WHATSAPP_APP_SECRET` | En prod | Secreto de la app Meta para validar `X-Hub-Signature-256` en POST `/api/webhooks/whatsapp`. Obligatorio en staging/production antes de guardar integraciones WhatsApp. |
| `WHATSAPP_VERIFY_TOKEN` | En prod | Token arbitrario que Meta devuelve en la verificación GET del webhook. |

### Límites de procesado documental

Todos tienen default en `app/config.py`; se sobreescriben por entorno solo si hay una razón medida. Un PDF o una imagen pequeños en bytes pueden expandirse a gigabytes al decodificarse (*decompression bomb*), así que estos topes se validan **antes** de decodificar y son *fail-closed*: si no se pueden verificar, el documento se rechaza.

| Variable | Default | Por qué existe |
|----------|:-------:|----------------|
| `DOCUMENT_MAX_PDF_PAGES` | `3` | Páginas admitidas por documento de negocio (factura, ticket). Todas se envían al LLM: subirlo multiplica coste y latencia por documento. |
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
