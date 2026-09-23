# Paso10 - QA, release y produccion

Estado: **bloqueado por ops** (2026-09-23). Soft-launch No-Go hasta Infisical staging/prod y checklist `PasosParaProduccion.md`.

Objetivo: cerrar una version publicable con pruebas automaticas, manuales y operativas.

## Dependencias

- Pasos P0 cerrados o aceptados explicitamente.

## Checklist automatico

```powershell
infisical run -- uv run alembic upgrade head
infisical run -- uv run pytest tests/unit -q
infisical run -- uv run pytest tests/integration -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
infisical run -- uv run python -m app.evals.runners.extraction
infisical run -- uv run python -m app.evals.runners.knowledge_retrieval
infisical run -- uv run python -m app.evals.runners.knowledge_qa
infisical run -- uv run python -m app.evals.runners.chat_documents
```

E2E si hay credenciales:

```powershell
infisical run -- uv run pytest tests/e2e -q
```

## QA manual minima

### Infra

- [ ] API arranca.
- [ ] Worker ARQ arranca.
- [ ] Redis OK.
- [ ] Postgres OK.
- [ ] R2/MinIO OK.
- [ ] Langfuse OK.

### Auth y tenant

- [ ] Login admin tenant.
- [ ] Login member tenant.
- [ ] Tenant A no ve Tenant B.
- [ ] CSRF bloquea mutacion sin token.
- [ ] SADM solo accede con usuario permitido.

### Documentos

- [ ] Upload factura.
- [ ] Upload ticket.
- [ ] Documento invalido falla claro.
- [ ] Retry/dismiss.
- [ ] Multi-IVA visible.
- [ ] R2 no expone objetos publicos indebidamente.

### Knowledge y chat

- [ ] Upload knowledge.
- [ ] Indexacion worker.
- [ ] Chat responde con citas.
- [ ] Hide thread.
- [ ] Langfuse sin contenido.

### Canales y calendario

- [ ] Google OAuth.
- [ ] Voz -> evento.
- [ ] WhatsApp real si credenciales disponibles.
- [ ] Telegram real si credenciales disponibles.

### Planes

- [ ] Sidebar cambia por plan.
- [ ] Feature denegada por URL directa.
- [ ] Cuota bloquea antes de coste.
- [ ] SADM cambia plan.

## Produccion

Variables Infisical:

- [ ] `APP_ENV=production`.
- [ ] `SECURITY_HTTPS_REDIRECT=true` o equivalente por proxy.
- [ ] `SECURITY_HSTS_ENABLED=true`.
- [ ] `WEBHOOK_ALLOW_UNSIGNED=false`.
- [ ] `LANGFUSE_CAPTURE_CONTENT=false`.
- [ ] `LLM_RETRY_TRANSIENT_ERRORS=true`.
- [ ] `ADMIN_CLERK_ORG_ID` configurado.
- [ ] `SUPERADMIN_CLERK_USER_IDS` configurado si se usa allowlist.
- [ ] Secrets R2/Clerk/LLM/SMTP/Google/WA/TG en Infisical.

## Rollback

- [ ] Backup BD antes de migraciones destructivas.
- [ ] Migraciones revisadas manualmente.
- [ ] Feature flags/kill-switch para modulos caros.
- [ ] Worker puede detenerse sin perder jobs criticos.

## Decision de release (2026-09-22)

**No-Go.** No hay despliegue a produccion. Infisical `prod` y `staging` tienen 0 secretos, el slug `production` no existe, y la QA manual de `Paso07` no esta hecha con una sesion real.

| Dato | Valor |
|------|--------|
| Fecha | 2026-09-22 |
| Commit en `origin/RamaCursor01` | `7bc1aea` |
| Alembic local | `p64_plans_entitlements_01` (head) |
| Entorno Infisical usado en esta comprobacion | `dev` |
| Responsable del No-Go | pendiente de tu firma; el agente no puede aceptar el riesgo residual |

Smoke solo en local (`APP_ENV=development`, `http://127.0.0.1:8000`):

- [x] `GET /health` 200
- [x] `GET /health/db` 200
- [x] `GET /health/redis` 200
- [x] `GET /docs` 200 en development. En `APP_ENV=production` el codigo deja `docs_url`, `redoc_url` y `openapi_url` a `None` (`app/main.py`).

Sigue abierto para un Go:

- [ ] Secretos propios en Infisical `prod` (no copiar `dev`). Ver `PasosParaProduccion.md` §3.
- [ ] Smoke en un staging con esos secretos: login, un documento, un plan, un canal.
- [ ] Backup de Postgres probado (comando abajo) antes de `alembic upgrade`.
- [ ] QA manual critica de este fichero.
- [ ] Firma tuya de los gaps que aceptes.

### Backup y rollback

El rollback de `p64` hace `DROP TABLE plans`. Con datos de planes, no uses `alembic downgrade` como vuelta atras. Restaura el backup.

Local (contenedor `saas-postgres`, base `saas`):

```powershell
docker exec saas-postgres pg_dump -U saas -Fc -d saas -f /tmp/saas.dump
docker cp saas-postgres:/tmp/saas.dump .\saas.dump
```

Restore local (destructivo para esa base; solo en una copia):

```powershell
docker cp .\saas.dump saas-postgres:/tmp/saas.dump
docker exec saas-postgres pg_restore -U saas -d saas --clean --if-exists /tmp/saas.dump
```

En el Postgres de prod, el mismo `pg_dump -Fc` contra el host real, guardado fuera del servidor de aplicacion, antes de migrar. Comprueba el restore en una instancia vacia al menos una vez.

Kill-switch de coste, sin redeploy de codigo: en Infisical, `ENTITLEMENTS_DISABLED_FEATURES` con los codigos de feature a apagar (CSV) y reinicia API y worker. Parada dura: detener el proceso `arq app.jobs.settings.WorkerSettings` y, si hace falta, rotar las API keys LLM en el proveedor.

Arranque previsto cuando exista el host:

```powershell
infisical run --env=prod -- uv run alembic upgrade head
infisical run --env=prod -- uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
infisical run --env=prod -- uv run arq app.jobs.settings.WorkerSettings
```

## Criterios de aceptacion

- [ ] Tests automaticos verdes o excepciones documentadas en el commit que se despliegue. Suites parciales de Fase A, C y D estan verdes; no se ha repetido `tests/unit` + `tests/integration` enteros en este paso.
- [ ] QA manual critica completada.
- [x] Langfuse RGPD en codigo: `LANGFUSE_CAPTURE_CONTENT` rechazado fuera de development. La instancia de prod no existe todavia.
- [x] Sin secretos en repo (detect-secrets en `7bc1aea`).
- [x] Coste LLM controlado por plan/cuota en codigo (Pasos 02–04).
- [x] Release documentado: fecha 2026-09-22, commit `7bc1aea`, migracion `p64_plans_entitlements_01`, decision **No-Go**.
