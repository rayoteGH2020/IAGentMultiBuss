# Paso08 - Analytics SQL read-only

Estado: **NO IMPLEMENTAR** (D011, 2026-09-23).

Decision de producto: el modulo 3 (analista SQL / BI sobre BD externa del cliente)
**no se implementara** ahora ni como roadmap activo. Motivo: no se va a vender esa
capacidad; mantener el paso como "pendiente" o la feature `analytics` en plan
`total` prometia un producto inexistente.

Consecuencias en codigo (ya aplicadas):

- Feature `analytics` retirada de `FEATURE_CODES` y del catalogo / plan `total`
  (`p66_drop_analytics_entitlement_01`).
- Sin rutas `/analytics`, sin modelos `data_sources` / `analytics_queries`.
- Restos tipados (`TaskType="sql"`, columna `usage_meter.analytics_queries_count`)
  quedan comentados como reservados muertos; no reactivar sin Decision_Log nueva.

Este documento se conserva solo como **historico de diseno**. No abrir tareas ni
PRs contra este paso.

---

## Diseno original (archivado; no ejecutar)

Objetivo (historico): implementar el analista conversacional solo cuando la
plataforma ya tenga gates, cuotas y guardrails.

### Alcance previsto (no aplicar)

Modulo 3:

- alta de data sources externas,
- credenciales cifradas,
- introspeccion de schema,
- chat analytics,
- generacion SQL controlada,
- ejecucion read-only,
- graficos server-rendered o via Chart.js controlado.

### Fuera de alcance (sigue vigente como guardrail si algun dia se reabriera)

- Ejecutar SQL contra la BD principal de la app.
- Conexiones con permisos de escritura.
- DDL/DML.
- SQL multi-statement.
- Acceso cross-tenant.
- Copiar datasets de cliente a logs o Langfuse.

### Checklist (anulada)

- [x] ~~Feature `analytics` requerida~~ → **retirada** (D011).
- [x] Documentado como no implementar en Decision_Log, backlog y planes.

Ver: `Decision_Log.md` D011, `Planes_Entitlements.md`, `Backlog_Priorizado.md`.
