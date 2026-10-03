# Mi SaaS

SaaS modular para pymes (gestorías, peluquerías, talleres, etc.) con tres módulos:

1. Extracción y conciliación administrativa (facturas, tickets).
2. Agente RAG conversacional (WhatsApp, web).
3. Analista de datos conversacional.

## Documentación

- `arquitectura.md` — Arquitectura del sistema.
- `Agents.md` — Reglas para el asistente de IA.
- `instrucciones-asistente.md` — Cómo usar Cursor / Claude Code.
- `Paso0X.md` — Pasos de construcción.

## Desarrollo local

Ver `Paso01.md` para el bootstrap inicial.

```bash
uv sync

# Terminal 1 — API HTTP
infisical run -- uv run uvicorn app.main:app --reload

# Terminal 2 — worker ARQ (procesamiento de facturas encoladas)
infisical run -- uv run arq app.jobs.settings.WorkerSettings
```

*(Tras el Paso 03; hasta entonces la app puede no existir aún. Secretos vía Infisical, ver `Agents.md` §2.)*

## Evals (módulo 1)

Dataset y runner del Paso 15 para la extracción de facturas.

```bash
# Coloca los PDFs reales en tests/fixtures/invoices/ y rellena ground_truth
# en app/evals/datasets/invoices_v1.json.

infisical run -- uv run python -m app.evals.runners.extraction <tenant_uuid>
# Resumen por stdout + detalle en app/evals/results/invoices_v1_<ts>.json
```

## Métricas internas

Endpoint protegido por `X-Metrics-Token` (variable `METRICS_TOKEN` en Infisical):

```bash
curl -H "X-Metrics-Token: $METRICS_TOKEN" http://localhost:8000/metrics/module1
```

## CI (GitHub Actions)

En cada PR y push a `main` se ejecuta `.github/workflows/ci.yml`:

- **Lint:** ruff + mypy sobre `app/`
- **Tests:** unit + integración (Postgres pgvector + Redis en el runner); excluye evals LLM y tests `real_llm`

Los evals de extracción con API real están en `.github/workflows/evals.yml` (solo si cambian ficheros LLM relevantes).

Detalle de variables de entorno del job de test: `docs/environment-variables.md` § GitHub Actions.

## E2E (Playwright)

Test gated por `RUN_E2E=1`. Necesita una sesión Clerk pre-autenticada en `tests/e2e/auth_state.json`.

```bash
RUN_E2E=1 infisical run -- uv run pytest tests/e2e/test_invoice_upload_flow.py
```

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · Jinja2 + HTMX + Alpine.js + Tailwind 4 · Postgres + pgvector · Redis · Cloudflare R2 · Anthropic + Google Gen AI · Clerk · Langfuse.
