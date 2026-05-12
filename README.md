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
infisical run -- uv run uvicorn app.main:app --reload
```

*(Tras el Paso 03; hasta entonces la app puede no existir aún. Secretos vía Infisical, ver `Agents.md` §2.)*

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · Jinja2 + HTMX + Alpine.js + Tailwind 4 · Postgres + pgvector · Redis · Cloudflare R2 · Anthropic + Google Gen AI · Clerk · Langfuse.
