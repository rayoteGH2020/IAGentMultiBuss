# Variables de entorno (Infisical)

Los valores reales (secretos, URLs con credenciales) se guardan en **Infisical**; la app arranca con `infisical run -- ...` (ver `Agents.md` §2). **No** usar ficheros `.env` en el repositorio.

Nombres en **MAYÚSCULAS** (lectura vía `pydantic-settings` desde el entorno del proceso).

| Variable | Uso |
|----------|-----|
| `APP_ENV` | `development` \| `staging` \| `production` |
| `APP_SECRET_KEY` | Secreto de sesión / firmas internas |
| `APP_BASE_URL` | URL pública de la app |
| `LOG_LEVEL` | `DEBUG`, `INFO`, … |
| `DATABASE_URL` | Postgres async (`postgresql+asyncpg://…`) |
| `REDIS_URL` | Redis (`redis://localhost:6379/0`) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL`, `R2_REGION` | Cloudflare R2 |
| `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`, `CLERK_JWKS_URL`, `CLERK_WEBHOOK_SECRET` | Clerk (auth) |
| `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `VOYAGE_API_KEY` | Proveedores LLM |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Langfuse |
| `ENCRYPTION_KEY` | Cifrado de campos sensibles (p. ej. 32 bytes base64) |
| `METRICS_TOKEN` | Token interno para `GET /metrics/module1` (Paso 15). |

## Ejemplos no secretos (dev local, Paso 02)

Solo guía al rellenar Infisical; credenciales de ejemplo del compose local:

- `DATABASE_URL=postgresql+asyncpg://saas:saas@localhost:5432/saas`
- `REDIS_URL=redis://localhost:6379/0`
- `LANGFUSE_HOST=http://localhost:3000`
