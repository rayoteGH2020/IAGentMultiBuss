# Paso08 - Analytics SQL read-only

Objetivo: implementar el analista conversacional solo cuando la plataforma ya tenga gates, cuotas y guardrails.

## Dependencias

- Paso01 completado.
- Paso02, Paso03 y Paso04 completados.
- Paso07 revisado para patrones de chat/tools.

## Alcance

Modulo 3:

- alta de data sources externas,
- credenciales cifradas,
- introspeccion de schema,
- chat analytics,
- generacion SQL controlada,
- ejecucion read-only,
- graficos server-rendered o via Chart.js controlado.

## Fuera de alcance

- Ejecutar SQL contra la BD principal de la app.
- Conexiones con permisos de escritura.
- DDL/DML.
- SQL multi-statement.
- Acceso cross-tenant.
- Copiar datasets de cliente a logs o Langfuse.

## Seguridad SQL

Obligatorio:

- Usuario DB externo read-only.
- Parser SQL.
- Solo `SELECT`.
- Denegar `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `COPY`, `CALL`.
- Denegar funciones peligrosas.
- Timeout 10s.
- Max 1000 filas.
- Parametrizacion donde aplique.
- Schema cacheado y acotado.
- Audit log de query hash y metadata, no contenido sensible.

## Modelo esperado

```text
data_sources
- id
- tenant_id
- name
- kind
- connection_config_enc
- schema_cache
- status
- created_at
- updated_at

analytics_queries
- id
- tenant_id
- user_id
- question_hash
- sql_hash
- result_shape
- chart_spec
- llm_call_id
- created_at
```

## Tests minimos

- [ ] SQL `SELECT` simple permitido.
- [ ] SQL con `DROP` denegado.
- [ ] Multi-statement denegado.
- [ ] Timeout aplicado.
- [ ] Max filas aplicado.
- [ ] Credenciales cifradas.
- [ ] RLS en tablas propias.
- [ ] Langfuse metadata-only.
- [ ] Feature `analytics` requerida.

## Comandos

```powershell
infisical run -- uv run pytest tests/unit/test_analytics_sql_guardrails.py tests/integration/test_analytics_routes.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [ ] No existe ruta para analytics sin plan `total` u override.
- [ ] No se puede ejecutar escritura aunque el LLM lo proponga.
- [ ] No se filtra contenido a Langfuse.
- [ ] El usuario recibe tablas/graficos acotados y comprensibles.
