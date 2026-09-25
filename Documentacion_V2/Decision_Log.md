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

## D012 - Catalogo Basico / Avanzado / Premium (limites duros)

Decision (cerrada 2026-09-23): sustituir `basic|medium|high|total` por tres ofertas publicadas:

- **Basico** (`basic`): documentos + chat documental + knowledge + chat knowledge.
- **Avanzado** (`advanced`): Basico + citas internas (BBDD saas) + WhatsApp/Telegram.
- **Premium** (`premium`): mismas features que Avanzado; limites mayores.

Motivo:

- Simplificar venta (2 escalones de producto + 1 de capacidad).
- No publicitar Calendar Google / voz (codigo se conserva; sin evolucionar).
- Limites **duros** en los tres: superar Avanzado implica Premium; superar Premium implica custom/SADM.

Consecuencia:

- Migracion `p67_plans_basic_adv_prem_01` (remap tenants + reseed entitlements).
- Alias legacy: `medium`→`basic`, `high`→`advanced`, `total`→`premium`.
- Actualizar Stripe Price IDs a los tres codigos nuevos.

## D013 - Despliegue con Docker Compose + Caddy (no Coolify)

Decision (cerrada 2026-09-24): produccion en una VPS con `deploy/docker-compose.prod.yml` + Caddy (TLS automatico), arrancado siempre con `infisical run`.

Motivo:

- Una sola app y un solo servidor: el panel/multi-app de Coolify no aporta.
- Coolify guarda variables en su BD: segunda fuente de secretos fuera de Infisical (Agents.md §2).
- Menor superficie de ataque: sin panel web con control root de Docker expuesto.
- Infraestructura versionada y revisable en el repo, con tests (`tests/unit/test_deploy_config.py`).

Consecuencia:

- Backups, deploy y rollback por scripts (`deploy/scripts/`), guia en `Paso11_Despliegue_VPS.md`.
- Unico secreto fuera de Infisical: Machine Identity de la VPS en `/etc/iagent/infisical-identity.conf` (600).
- La app conecta como `saas_app` (NOBYPASSRLS); migraciones con `MIGRATIONS_DATABASE_URL` (propietario).
- Redis de prod con `noeviction` (cola ARQ y anti-replay no se expulsan).

## D014 - Extraccion con gemini-3.8-flash, thinking bajo y resolucion por defecto

Decision (cerrada 2026-09-25): la tarea `extraction` (facturas, tickets, contratos, polizas y OCR de knowledge) usa `gemini-3.8-flash` con `thinking_level=low` y `media_resolution` por defecto. Chat, transcripcion y traduccion no cambian (sin medir).

Motivo (medido 2026-09-25, scripts desechables sobre `invoices_v1` + 17 documentos de muestra):

| Tipo | Casos | 2.5-flash + thinking dinamico | 3.8-flash thinking low |
| --- | --- | --- | --- |
| Facturas | 20 | p50 15,6 s / 96,7 % | p50 2,8 s / 98,3 % |
| Contratos | 6 | p50 7,0 s / 94,4 % | p50 2,0 s / 100 % |
| Tickets | 3 | p50 3,9 s / 100 % | p50 1,6 s / 100 % |
| Polizas | 8 | p50 5,8 s / 100 % | p50 1,8 s / 100 % |

- El thinking dinamico causaba la latencia (eval CI rojo: p50 16,2 s / p95 50,7 s) sin mejorar la extraccion.
- `gemini-2.5-flash` sin thinking cumple, pero queda al limite (95,8 % en facturas) y la familia 2.x se esta retirando (2.0-flash y 2.5-flash-lite ya devuelven 404).
- Coste por factura ~$0,0033 hoy / ~$0,0066 desde 2027-01-01 (tarifa estandar $1,50 / $7,50); ~2,5-5x mas que 2.5-flash sin thinking, pero por debajo del coste real anterior con thinking (~$0,0087). Gemini 3 tokeniza la entrada 1,5-2,7x mas que 2.5 por documento.

Descartado:

- `media_resolution=low`: misma precision y 33-63 % menos tokens de entrada, pero solo 13-35 % de ahorro total (el coste lo domina la salida) y requiere saltarse Instructor (1.15.1 no propaga el parametro), contrario a `Agents.md` §1. Revisar si Instructor lo soporta.
- `gemini-2.5-flash-lite`: retirado (404).

Consecuencia:

- `DEFAULT_MODELS["extraction"] = "gemini-3.8-flash"`; `_google_thinking_config` baja el thinking por familia (3.x Flash `thinking_level=low`, 2.5 Flash `thinking_budget=0`, Pro sin tocar).
- `_extract_token_usage` suma `thoughts_token_count` a output: Google lo factura como salida y antes no se contaba (coste de extraccion infravalorado ~7x).
- `pricing.py` registra ya la tarifa 2027 de 3.8-flash: sobreestima el coste hasta 2026-12-31 para que el budget del plan no se quede corto.
- Infisical: `LLM_MODEL_EXTRACTION` debe eliminarse o valer `gemini-3.8-flash` en cada entorno (el override tiene prioridad sobre el default).
- Deuda: schemas ambiguos detectados en la medicion (contrato `fecha_inicio` firma/inicio e `importe` periodico/total; poliza `tipo_seguro` texto libre) y evals permanentes de tickets/contratos/polizas.
