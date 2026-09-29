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

## D015 - Chat con gemini-3.5-flash-lite y thinking por defecto

Decision (cerrada 2026-09-25): la tarea `chat` (chat web unificado y canales WhatsApp/Telegram) usa `gemini-3.5-flash-lite` en todos los entornos, con el thinking por defecto del modelo.

Motivo (medido 2026-09-25 con `knowledge_qa_v1 --with-llm`, 22 preguntas, tenant de eval):

| Modelo | Grounded / citas | p50 | $ / 1.000 preguntas |
| --- | --- | --- | --- |
| gemini-2.5-flash (default anterior, prod) | 100 % / 100 % | 2,9 s | 2,34 |
| gemini-3.1-flash-lite (override dev) | 100 % / 100 % | 3,0 s | 1,90 |
| gemini-3.5-flash-lite | 100 % / 100 % | 3,0 s | 2,35 |
| gemini-3.8-flash | 100 % / 100 % | 3,1 s | 5,62 (11,23 desde 2027) |
| gemini-3.8-flash thinking low | 95 % / 91 % | 3,5 s | 6,04 |

- Dev (3.1-flash-lite) y prod (2.5-flash) usaban modelos distintos; dev ademas contabilizaba 0 EUR (sin tarifa).
- 3.5-flash-lite iguala calidad a mismo coste que 2.5-flash y pertenece a la familia 3.x (menor riesgo de retirada).
- 3.8-flash no aporta mejora medible y cuesta 2,4-4,8x.
- Con thinking bajo el modelo se salta tools (respuesta sin consultar la base) o elige la tool equivocada: el chat NO entra en `_LOW_THINKING_TASKS`.

Limitacion: `knowledge_qa_v1` esta saturado (todos al 100 %) y solo cubre preguntas de knowledge. Resuelto el mismo dia con `chat_documents_v2`: 32 preguntas sobre documentos sembrados (`seed_documents_eval`), con respuestas exactas (sumas, recuentos, medias, porcentajes, vencimientos); `gemini-3.5-flash-lite` acierta 31/32 (falla doc_031 por falta de filtro de vencimiento en las tools, backlog P2b-5).

Consecuencia:

- `DEFAULT_MODELS["chat"] = "gemini-3.5-flash-lite"`; tarifa en `pricing.py` ($0,30 / $2,50).
- `chat_loop._gemini_token_usage` suma `thoughts_token_count` a output (mismo fallo que D014 en extraccion).
- Infisical: `LLM_MODEL_CHAT` debe eliminarse o valer `gemini-3.5-flash-lite` en cada entorno.

## D016 - Plan asignado solo por SADM; integracion Stripe retirada

Decision (cerrada 2026-09-28): ningun rol de tenant (`admin`, `co_admin`, `member`, `viewer`) asigna ni cambia el plan; solo el SADM desde `/sadm/plans`. Se elimina toda la integracion con Stripe (Paso09). El metodo de cobro de los planes **queda pendiente de decidir** (Backlog P3-2).

Motivo:

- El plan es una decision comercial del SADM, no autoservicio.
- El webhook de Stripe cambiaba el plan (alta/cambio de suscripcion, downgrade a `basic` al cancelar): segunda via de cambio de plan fuera del SADM.
- Sin metodo de cobro decidido, mantener la integracion era codigo, secretos (`STRIPE_*`) y un endpoint publico sin uso.

Consecuencia:

- Borrados `stripe_billing_service.py`, `/api/webhooks/stripe`, checkout/portal de `/settings/billing` (queda solo lectura: plan y consumo), settings `STRIPE_*` y la dependencia `stripe`.
- Migracion `p68_drop_stripe_billing_01`: elimina `tenants.stripe_customer_id`, `stripe_subscription_id`, `billing_status` y `plans.stripe_price_id` (vacias en dev al retirarlas). Se conserva `tenant_plan_changes` como historial de asignaciones SADM.
- Supersede D006 en lo relativo a Stripe (planes y entitlements siguen vigentes). `Paso09_Billing_Stripe.md` queda como historico.
- Infisical: borrar `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY` de todos los entornos.
- Si se reintroduce un proveedor de pago: nueva decision en este log; el pago nunca asigna plan sin pasar por la regla que se decida.

## D017 - Historico visible (`history_months`): solo documentos puntuales; contratos por vigencia

Decision (cerrada 2026-09-29, pendiente de implementar): el limite `history_months` de `especificacion-planes-y-cuotas.md` (12 / 36 / sin limite) oculta solo **facturas y tickets** con fecha de emision anterior al limite. Los **contratos** activos o pendientes de vencer son siempre visibles, buscables en el chat y avisables; tras vencer o ser sustituidos se aplica el mismo limite contado desde su fecha de fin. Las polizas seguiran la regla de los contratos cuando se corrija su encaje en planes (incongruencia aparcada).

Motivo:

- La especificacion no fija sobre que fecha se cuenta el historico. Contado desde la subida, un contrato de 3 anos quedaria oculto en Basico a los 12 meses: desaparece de la lista y del chat, y el aviso de vencimiento (incluido en Basico, §2.2) no se enviaria o apuntaria a un contrato invisible. Anula el argumento de venta del plan.
- El tamano del archivo de contratos ya lo limita `contracts_active_max`; un segundo limite por antiguedad sobre contratos vigentes es redundante y ambiguo (¿un contrato oculto ocupa hueco?).

Consecuencia:

- Al revisar la especificacion, sustituir §4.7 por: "`history_months` oculta facturas y tickets con fecha de emision anterior al limite. Los contratos activos o pendientes de vencer son siempre visibles; tras vencer o ser sustituidos, se aplica el mismo limite desde su fecha de fin."
- Ni `history_months` ni los avisos de contratos existen aun en el codigo (2026-09-29); implementar ambos ya con esta regla.
- El historico oculto no se borra; si el cliente sube de plan, vuelve a verse (§4.7 sin cambios en ese punto).

## D018 - Analista de datos en Premium: aplazado hasta despues del producto minimo

Decision (cerrada 2026-09-29): el analista de datos conversacional para Premium (`analytics`, §2.1 y §4.5 de `especificacion-planes-y-cuotas.md`) **no se desarrolla ahora**. Queda en el roadmap para despues del producto minimo, junto con el resto de funcionalidades posteriores.

Motivo:

- Prioridad: cerrar el producto minimo (plan Basico).
- El analista no forma parte del Basico ni del Avanzado.

Consecuencia:

- **D011 sigue vigente** mientras no se retome el analista: sin feature `analytics` en el catalogo, sin rutas ni runners de la tarea `sql`. AGENTS.md §7, `arquitectura.md` y `app/llm/client.py` no se tocan.
- Al retomarlo: nueva decision que sustituya a D011. Diferencia de alcance a tener en cuenta: D011 descartaba BI/SQL sobre la **BD externa del cliente**; la especificacion plantea un analista sobre los **datos del propio tenant en la app** (facturas, tickets, contratos), en solo lectura y con RLS.
- Mientras tanto, Premium se ofrece con las mismas funcionalidades que Avanzado y limites mayores (D012). En la especificacion, `analytics` y `analytics_questions_per_month` quedan como fase posterior y no entran en el calculo del presupuesto de Premium.

## D019 - Presupuesto de IA: aviso al 80 % y corte del chat al 90 %

Decision (cerrada 2026-09-29): el presupuesto mensual de IA (`llm_budget_eur_month`) es unico para toda la IA del tenant. Para que el chat no agote lo que necesita la extraccion de documentos:

- **80 %**: al cruzarlo con cualquier gasto de IA, email al admin del tenant (rol `admin` activo; no co_admin), una vez por mes.
- **90 %**: el chat de la app deja de llamar al LLM y responde: "En estos momentos no puedo responderte, ponte en contacto con nosotros y te ayudaremos (telefono - email)". El telefono y el email salen de los metadatos publicos de la organizacion en Clerk (`contact_phone`, `contact_email`), con cache de 1 h. Tras esa respuesta se notifica al admin como maximo una vez cada 24 h y 3 veces por mes, y queda `chat.budget_cutoff` en `audit_log`.
- **100 %**: sin cambios, `ensure_llm_budget` bloquea el resto de la IA.

Motivo:

- Tras P2b-14 el coste del chat cuenta en el presupuesto; sin corte, el chat podia agotarlo y bloquear la extraccion sin bloquearse a si mismo.
- Clerk no tiene telefono ni email de organizacion: los metadatos publicos son un contacto del negocio que no depende de quien sea el admin.

Consecuencia:

- `app/services/llm_budget_alert_service.py`, job ARQ `send_llm_budget_alert` (email fuera de la peticion) y comprobacion en `chat_service._run_assistant_turn`.
- Umbrales y frecuencia configurables: `LLM_BUDGET_WARN_RATIO` (0,8), `CHAT_BUDGET_CUTOFF_RATIO` (0,9), `CHAT_CUTOFF_NOTIFY_INTERVAL_SECONDS` (86400), `CHAT_CUTOFF_NOTIFY_MAX_PER_MONTH` (3).
- Pendiente: mismo corte en el asistente de canales (Backlog P2b-17), interfaz de consumo y avisos (P2b-18) y rellenar los metadatos en Clerk (P2b-19, ops).
