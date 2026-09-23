# Decision_Log

Fecha base: 2026-08-04

## D001 - No reiniciar la aplicacion

Decision: continuar sobre el repositorio actual.

Motivo:

- Hay arquitectura real en capas.
- Hay migraciones, RLS, auth, LLM, SADM, workers y tests.
- Reescribir perderia seguridad ya conseguida y generaria regresiones.

Consecuencia:

- Se crea documentacion V2.
- Los pasos nuevos parten del estado actual.
- Se refactoriza por modulos, no por reescritura total.

## D002 - Documentacion_V2 gobierna el backlog nuevo

Decision: `Documentacion_V2` sustituye como guia operativa a los checklists antiguos.

Motivo:

- La documentacion anterior mezcla historico, pendientes reales y decisiones descartadas.
- Algunos pasos antiguos ya no reflejan el codigo.

Consecuencia:

- `Documentacion/` queda como referencia historica.
- Solo se consulta si un paso V2 lo indica.

## D003 - No implementar switcher multi-org

Decision: no habra selector de organizacion en UI por ahora.

Motivo:

- Producto actual: un cliente equivale a una organizacion.
- Reduce riesgo de confusion tenant y bugs de sesion.

Consecuencia:

- Aislamiento multi-tenant se prueba con usuarios distintos por organizacion.
- No se anade `OrganizationSwitcher`.
- Excepcion acotada (2026-08-06): si la sesion Clerk no tiene org activa pero el
  usuario tiene **exactamente una** membership, `clerk-auth.js` llama a
  `setActive({ organization })` automaticamente. 0 orgs → `/onboarding`; >1 orgs
  → `/onboarding` (sin selector). No es un switcher; solo desbloquea el caso 1:1.

## D004 - SADM por organizacion administrativa Clerk

Decision: SADM se valida por:

- tenant con `clerk_org_id == ADMIN_CLERK_ORG_ID`,
- membership activa,
- rol `admin`,
- allowlist opcional `SUPERADMIN_CLERK_USER_IDS`.

Motivo:

- Evita que cualquier usuario de la org administrativa tenga privilegios.
- No requiere columna global `users.is_superadmin`.

Consecuencia:

- Rutas `/sadm/*` usan `SuperAdmin`.
- Acceso cross-tenant queda restringido a esa dependency.

## D005 - Identidades solo via Clerk Dashboard

Decision (cerrada 2026-08-07, Opcion B de `Paso05`): usuarios y organizaciones se gestionan **unicamente** desde Clerk. La app no ofrece alta/baja/provision de identidades desde SADM ni otras UIs de plataforma.

Motivo:

- Clerk ya resuelve invites, altas, bajas, roles de org y credenciales.
- Crear contrasenas o orgs desde la app aumenta superficie de riesgo y duplica fuente de verdad.
- SADM debe operar el SaaS (planes, uso, docs), no sustituir el IdP.

Consecuencia:

- Camino unico: Clerk Dashboard -> invitation/membership -> login/webhook -> sync BD.
- SADM: orgs y miembros en **read-only**.
- Retirar de la UI (y no ampliar) cualquier provision SADM residual; no opciones A/C (ocultar en prod o conservar como feature SADM).
- Fuentes: `Paso05_SADM_Consolidacion.md`, `SADM_V2.md` §4.

## D006 - Planes antes que Stripe

Decision: primero entitlements manuales por SADM, despues Stripe.

Motivo:

- Billing sin gates no controla producto ni coste.
- Stripe debe mapear `price_id -> plan_code`, no decidir capacidades en codigo.

Consecuencia:

- `plans` y `plan_entitlements` son P0.
- `/settings/billing` sigue informativo hasta que el catalogo exista.

## D007 - Cuotas por plan, no cuotas globales sueltas

Decision: limites de documentos, reintentos, chat, canales y budgets dependen del plan.

Motivo:

- Evita hardcode comercial.
- Cierra riesgo de DoS economico de forma mantenible.

Consecuencia:

- `rate_limiter.py` debe generalizarse hacia `limit_code`.
- Workers revalidan limites antes de coste LLM.

## D008 - Langfuse solo metadatos

Decision: Langfuse no almacena contenido de cliente.

Motivo:

- GDPR y reduccion de impacto ante brecha.

Consecuencia:

- Usar `app/llm/observability.py`.
- `LANGFUSE_CAPTURE_CONTENT=true` solo en development y datos sinteticos.

## D009 - Chat documental sin SQL libre

Decision: chat sobre documentos internos usa tools tipadas, no SQL agent.

Motivo:

- Reduce prompt injection.
- Reutiliza services bajo RLS.

Consecuencia:

- Tools devuelven proyecciones acotadas.
- No exponer `raw_extraction` al modelo salvo justificacion revisada.

## D010 - Analytics SQL queda para modulo separado

Decision (historica, 2026-08-04): el analista SQL se implementa despues de planes, cuotas y seguridad.

Motivo:

- Mayor riesgo: generacion SQL, fuentes externas, credenciales y coste.

Consecuencia (vigente solo como guardrail si se reabriera):

- Solo conexiones read-only.
- Parser SQL y allowlist de `SELECT`.
- Nunca ejecutar SQL generado por LLM contra la BD principal con permisos de escritura.

**Superseded by D011** (2026-09-23): el modulo no se implementara.

## D011 - Modulo 3 Analytics SQL / BI no se implementa

Decision (cerrada 2026-09-23): **no implementar** el modulo 3 (Analytics SQL read-only / BI sobre BD externa del cliente). Fuera de alcance de producto; no es backlog activo.

Motivo:

- Decision comercial explicita: no se vendra BI/SQL agent sobre fuentes del cliente.
- Mantener `analytics` en plan `total` o Paso08 como "pendiente" prometia capacidad inexistente.

Consecuencia:

- Feature `analytics` fuera de `FEATURE_CODES` y del catalogo (migracion `p66`).
- `Paso08_Analytics_SQL_ReadOnly.md` queda como historico / NO IMPLEMENTAR.
- No crear tablas `data_sources` / `analytics_queries` ni rutas `/analytics`.
- Restos (`TaskType="sql"`, `usage_meter.analytics_queries_count`) documentados como muertos; no reactivar sin nueva decision en este log.
