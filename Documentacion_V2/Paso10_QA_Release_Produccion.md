# Paso10 - QA, release y produccion

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

## Criterios de aceptacion

- [ ] Tests automaticos verdes o excepciones documentadas.
- [ ] QA manual critica completada.
- [ ] Langfuse RGPD revisado.
- [ ] Sin secretos en repo.
- [ ] Coste LLM controlado por plan/cuota.
- [ ] Release documentado con fecha, commit y migracion HEAD.
