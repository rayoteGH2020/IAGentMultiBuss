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
- ~~Actualizar Stripe Price IDs a los tres codigos nuevos.~~ Sin efecto: Stripe retirado (D016).

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

**Implementada 2026-10-03** (bloque 6, `432d7e6`, migracion `p83_history_months_01`). Detalles aprobados:

1. `history_months` en el catalogo: 12 / 36 / sin limite (NULL). Un override de 0 o negativo se rechaza (ocultaria todo); si el limite faltara en el catalogo se aplican 12.
2. Corte por meses completos: el mes en curso y los N anteriores (12 el 3/10/2026 → desde el 1/10/2025). Se calcula al leer (`document_history_service`).
3. Facturas y tickets: fecha de emision; sin ella (pendientes, fallidas o sin fecha extraida), la de subida.
4. Contratos: vigentes (activos y no vencidos) siempre visibles; vencidos por `fecha_fin`; sustituidos por `fecha_fin` o, sin ella, por `replaced_at` (columna nueva, rellenada con `updated_at` en los ya sustituidos).
5. Polizas: sin cambios (aparcadas).
6. Se aplica al panel `/documents` y a las tools del chat (buscar, agregar, contrapartes y `get_document`, que responde "no encontrado" para un documento oculto).
7. No se aplica al export RGPD, SADM, cupos, job de pendientes ni a acciones por id sin boton visible.
8. Un documento oculto sigue bloqueando el mismo fichero (hash); el aviso explica que queda fuera de los N meses de historico del plan.
9. **Documentos que ya nacen fuera del historico** (decidido e implementado 2026-10-03, `aa8b812`; p. ej. una factura de hace 2 anos subida en Basico): solo se procesa lo que entra en el historico **del plan** (12 / 36 / sin limite). Facturas y tickets; los contratos siguen la regla de vigencia.
   - **En la subida, sin llamada nueva al LLM:** la fecha de emision sale del texto del PDF (regla que solo acepta fechas etiquetadas como emision: "Fecha", "Fecha de factura", "Fecha de emision", "Fecha de expedicion"; ambigua = no decide) o, en fotos y escaneados, de la misma llamada de clasificacion con Haiku que ya se hacia (prompt `classification_v2`, campos `fecha_emision` y `fecha_confianza`; solo cuenta con confianza >= 0,8). Si es anterior al historico, se rechaza como un duplicado: sin R2, cupo ni extraccion.
   - **Red de seguridad:** si la fecha no se pudo leer al subir y la extraccion da una anterior, el documento queda en error definitivo `outside_history` (visible, sin "Reintentar" ni procesado excepcional del SADM), sin guardar sus datos, y se devuelve el cupo. Ese caso si gasta la extraccion.
   - Descartado: una llamada aparte a un modelo barato solo para la fecha. Seria una llamada mas en cada documento, tambien en los recientes, y solo ahorra con los antiguos.
   - Rechazada antes la propuesta de procesarlos, ocultarlos y avisarlo en la fila.

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
- **90 %**: el chat de la app deja de llamar al LLM y responde: "En estos momentos no puedo responderte, ponte en contacto con nosotros y te ayudaremos (telefono - email)". El telefono y el email son los del admin del tenant en `users` (D020; la version inicial los leia de los metadatos de la organizacion en Clerk). Tras esa respuesta se notifica al admin como maximo una vez cada 24 h y 3 veces por mes, y queda `chat.budget_cutoff` en `audit_log`.
- **100 %**: `ensure_llm_budget` bloquea el resto de la IA.

Ampliacion (2026-09-29):

- **90 % → SADM**: al cruzar el umbral de corte con cualquier gasto de IA, email a `EMAIL_SADM` una vez por mes. Asunto "Cuota de uso de IA de uno de los tenant al 90%"; cuerpo con "nombre de la organizacion-id del tenant" y nombre, apellido, email y movil del admin (nombre, apellido y telefono principal desde Clerk; "no disponible" si faltan o Clerk falla). Las notificaciones al tenant siguen yendo solo al admin.
- **100 % → aviso en el panel**: banner en la cabecera de todas las paginas del panel, para todos los usuarios del tenant, con el texto configurable `LLM_BUDGET_EXHAUSTED_NOTICE` (un unico texto para toda la app). El middleware calcula la condicion con cache de 60 s en Redis.

Motivo:

- Tras P2b-14 el coste del chat cuenta en el presupuesto; sin corte, el chat podia agotarlo y bloquear la extraccion sin bloquearse a si mismo.
- Clerk no tiene telefono ni email de organizacion: los metadatos publicos son un contacto del negocio que no depende de quien sea el admin. *(Sustituido por D020: el contacto es el telefono y el email del admin en `users`.)*

Consecuencia:

- `app/services/llm_budget_alert_service.py`, job ARQ `send_llm_budget_alert` (email fuera de la peticion) y comprobacion en `chat_service._run_assistant_turn`.
- Umbrales y frecuencia configurables: `LLM_BUDGET_WARN_RATIO` (0,8), `CHAT_BUDGET_CUTOFF_RATIO` (0,9), `CHAT_CUTOFF_NOTIFY_INTERVAL_SECONDS` (86400), `CHAT_CUTOFF_NOTIFY_MAX_PER_MONTH` (3).
- Pendiente: mismo corte en el asistente de canales (Backlog P2b-17), interfaz de consumo y avisos (P2b-18) y que el admin de cada tenant rellene su telefono en su ficha de miembro (P2b-19, ops; D020).

## D020 - Telefono de los miembros en `users`, editable solo por el admin del tenant

Decision (cerrada 2026-09-29): el telefono de cada miembro se guarda en nuestra tabla `users` (`users.phone`, migracion `p73_users_phone_01`), **no en Clerk**. Se edita en la ficha de miembro (`/settings/members` > editar): solo el `admin` del tenant puede modificarlo; el `co_admin` lo ve en lectura. El contacto que muestra el chat al cortarse por presupuesto (D019) es el telefono y el email del admin del tenant, y el email al SADM al 90 % usa tambien `users.phone` (nombre y apellido siguen leyendose de Clerk).

Motivo:

- Decision del usuario: no guardar el telefono en Clerk (en dev ningun usuario tenia telefono en Clerk y la instancia no lo usa).
- El telefono es un dato de la app, igual que los permisos de citas de la ficha de miembro.

Consecuencia:

- `TenantMemberUpdate.phone` (validado: digitos, espacios y + inicial, 6-20 caracteres; vacio = borrar) solo se aplica si viene en la peticion; `membership_service.update_tenant_member` devuelve 403 si lo envia un rol distinto de `admin`. El `audit_log` registra `phone_set`, nunca el numero.
- `users` es global (un usuario puede pertenecer a varios tenants): el telefono es uno por usuario.
- Se descarta la version anterior de esta decision (formulario en SADM que escribia `contact_phone`/`contact_email` en los metadatos de la organizacion en Clerk); no llego a commitearse.

## D021 - Usuario de Clerk borrado y recreado con el mismo email: cuenta nueva y limpia

Decision (cerrada 2026-09-29): cuando llega un usuario de Clerk sin usuario local y ya hay uno local con el mismo email (sin distinguir mayusculas):

- **Sin id de Clerk** (alta desde la app con invitacion): se vincula al nuevo id, solo si Clerk tiene el email verificado.
- **Con un id de Clerk que ya no existe** (404 en Clerk): el usuario local antiguo se anonimiza (`deleted+<id>@deleted.invalid`, sin nombre ni id de Clerk), sus membresias se desactivan en todos los tenants y se crea un usuario nuevo. No hereda permisos ni historial.
- **Con un id de Clerk que sigue existiendo**: error de autenticacion y log `auth.clerk_email_conflict`; no se toca nada.
- El webhook `user.deleted` aplica la misma anonimizacion (antes solo se registraba en el log).

Motivo:

- Un usuario borrado y recreado en Clerk (caso real en dev, 2026-09-29) no podia entrar: `resolve_user` intentaba crear otro usuario con el mismo email y chocaba con `ix_users_email`. El mismo fallo afectaba al primer login de los usuarios invitados desde la app.
- Vincular por email a una cuenta con historial permitiria heredar permisos (p. ej. co_admin) a quien consiga una cuenta de Clerk con ese email. Un error de red al consultar Clerk nunca se trata como "borrado".

Consecuencia:

- `auth_service.resolve_user`, `detach_deleted_clerk_user`, `handle_clerk_user_deleted` y `security.clerk_user_exists`.
- La fila anonimizada se conserva por las FK (audit_log, documentos). `memberships` tiene RLS por tenant: la desactivacion recorre los tenants fijando el contexto de cada uno (evento raro).

## D022 - Maximo de miembros por plan: 3 / 9 / 20

Decision (cerrada 2026-09-30): `members_max` = **3 / 9 / 20** (Basico / Avanzado / Premium), contando todos los miembros activos de cualquier rol, admin incluido. Premium se dirige a negocios de 10 o mas miembros; ese minimo es comercial y no se aplica. Sustituye a 5 / 15 / 40 (`Planes_Entitlements.md`, seed p67) y a 1 / 3 / 10 (`especificacion-planes-y-cuotas.md`).

Motivo:

- Con 1 usuario en Basico no cabian el co_admin ni la gestoria/asesor, caso tipico de un autonomo. 3 los cubre sin reglas especiales por rol.
- Un solo tope por tenant, igual para todos los roles, evita excepciones (p. ej. lectores que no cuentan) que permitirian saltarse el limite.

Consecuencia:

- Seed `PLAN_LIMITS` y migracion `p74_members_max_01` (solo `plan_entitlements`; los overrides por tenant no se tocan).
- Tenants que ya superan el tope: conservan sus miembros; solo se bloquean las altas nuevas (`ensure_member_capacity`, sin cambios). `/settings/members` muestra "X de Y" y un aviso al llegar o superar el tope (`plan_quota_service.get_member_usage`). Excepciones caso a caso con override del SADM. Mismo criterio cuando el SADM baja de plan a un tenant.
- Clerk limita hoy a 5 miembros por organizacion: hay que subirlo a >= 20 (Backlog P2b-23).

## D023 - Chat de la app: un cupo mensual y limite de ritmo, sin topes diarios

Decision (cerrada 2026-09-30, pendiente de implementar): el chat de la app tiene **un solo cupo mensual** por tenant, `chat_questions_per_month` = **400 / 1.500 / 4.000** (Basico / Avanzado / Premium), que sustituye a `documents_chat_questions_per_month` y `knowledge_chat_questions_per_month` de la especificacion (sus valores sumados). Se eliminan los topes diarios del chat (`chat_messages_per_day` del plan, `CHAT_DAILY_MESSAGE_LIMIT` y `CHAT_USER_DAILY_MESSAGE_LIMIT`) y se sustituyen por un **limite de ritmo por usuario** (≈10 preguntas por minuto o 60 por hora).

- Al 100 % del cupo: mensaje fijo sin llamar al modelo, con la fecha de renovacion y el contacto del admin (como D019). Ampliable con override del SADM. Sin aviso al 80 % (tope tecnico, no comercial).
- El presupuesto de IA (D019) sigue siendo el tope duro final.

Motivo:

- Documentos y conocimiento son un unico chat (`PROMPT_UNIFIED`): una pregunta puede usar herramientas de ambos y antes de responder no se sabe a que cupo cargarla.
- Un tope diario pierde lo no usado y bloquea dias de uso intenso aunque el mes vaya holgado. Los topes diarios fueron una solucion de implementacion (contador Redis con TTL) previa a las cuotas mensuales y al presupuesto, no una decision de producto.
- El limite de ritmo frena scripts o cuentas comprometidas en minutos sin afectar a un uso humano intenso.
- El cupo no hace falta para el coste total (400 preguntas ≈1,8 € frente a 6 € de presupuesto en Basico); evita que el chat consuma el presupuesto que necesita la extraccion.
- El antiguo tope de 100 preguntas/dia en Basico (≈3.000/mes) no protegia nada: con el coste medio (≈0,002 €) el chat solo ya gastaba los 6 € del presupuesto, y el corte al 90 % (D019) llegaba antes que el tope.
- El "uso previsto" del que sale el cupo (80 preguntas/mes en Basico, x5) es una estimacion sin datos y hecha para 1 usuario; con 3 miembros (D022) puede quedarse corto. Se revisa con uso real (Backlog P2b-21); mientras, override del SADM caso a caso.

Consecuencia:

- Se implementa con los cupos mensuales de D027 (`monthly_quota_service`; el limite `chat_questions_per_month` ya esta en `PLAN_LIMITS` desde `p77`) en el bloque 4 del cierre del producto minimo; el limite de ritmo reutiliza el contador Redis de `rate_limiter.py` con ventana corta.
- **Implementada 2026-10-01** (bloque 4, migracion `p81_chat_quota_01`, que retira `chat_messages_per_day` del catalogo; P2b-24 cerrado).

Detalles de implementacion (aprobados 2026-10-01):

1. **La pregunta se cuenta al empezar el turno** (`chat_service._run_assistant_turn`, justo antes de llamar al LLM) **y se devuelve si el proveedor falla** (`ToolLoopResult.failed`). Solo gasta cupo una pregunta con respuesta del modelo: si el usuario envia y no abre el SSE, o el proveedor esta saturado o sin saldo, no cuenta. El corte por presupuesto (D019) va antes y tampoco cuenta.
2. **Limite de ritmo por usuario dentro de cada tenant**: 10 por minuto y 60 por hora, ventanas fijas en Redis (`rate:chat:{tenant}:{user}:...`), igual en todos los planes y configurable (`CHAT_RATE_LIMIT_PER_MINUTE`, `CHAT_RATE_LIMIT_PER_HOUR`; `0` = sin limite). Se comprueba al enviar, antes de guardar el mensaje. Sin limite de todo el tenant: lo cubren el cupo mensual y el presupuesto, y un limite comun bloquearia a los companeros si una cuenta lanza un script.
3. **Sin entrada en `audit_log` para la respuesta del 100 %**: no encaja en `AGENTS.md` §7; queda en `activity_log` (`chat.quota_exhausted_reply`). El corte por presupuesto mantiene `chat.budget_cutoff`.
4. **Retiradas `CHAT_DAILY_MESSAGE_LIMIT` y `CHAT_USER_DAILY_MESSAGE_LIMIT`**: si siguen en Infisical se ignoran; borrarlas de `prod` (Fase 5).

Respuesta al 100 %: "Has alcanzado las preguntas de este mes. Se renuevan el 1 de <mes>. Si lo necesitas antes, contacta con el administrador de tu organizacion (telefono - email)", con el contacto del admin en `users` (D020). Se guarda como respuesta del asistente sin llamar al LLM. El SADM la levanta ampliando `chat_questions_per_month` del mes desde `/sadm/plans/tenants/{id}`.

## D024 - Contratos: importes sin IVA por periodicidad y fecha de firma separada

Decision (cerrada 2026-09-30, implementada): la extraccion de contratos (`contract_extraction_v2`) sustituye el campo unico `importe` por:

- `importe_periodico` + `periodicidad` (`mensual` | `trimestral` | `semestral` | `anual` | `unico`): la cuota que se repite, **sin IVA**.
- `importe_total`: valor total sin IVA, solo si figura expresamente o es un pago unico.
- `importe_anual`: **calculado** por la app (cuota x pagos al ano), no lo devuelve el modelo. Es lo que suma el chat en "¿cuanto pago en contratos?"; no incluye pagos unicos.
- `iva_incluido`: true solo si el contrato da los importes unicamente con IVA y no se puede separar la base.
- `fecha_firma` separada de `fecha_inicio` (inicio de vigencia; si no hay otra, la de firma).

Motivo:

- `importe` guardaba cuota mensual, renta anual o total segun el contrato (garaje 114,95 mensual con IVA, vivienda 13.800 anual, consultoria 19.200 total): sumarlos daba cifras falsas en chat y listados (P2b-3).
- Sin IVA: los clientes son autonomos y pymes, para los que el IVA es deducible; asi se comparan con las facturas.
- Los codigos de `periodicidad` van en espanol, como `doc_type_code` (codigos de dominio); los estados siguen en ingles.

Consecuencia:

- Migracion `p75_contract_amounts_01`: nuevas columnas y check de `periodicidad`; `importe` se elimina sin traducir (valor ambiguo, sin produccion). En dev: `scripts/reextract_contracts.py`.
- Chat: `sum_total` de contratos suma `importe_anual`; filtros de importe usan anual o, si es pago unico, el total. Panel: cuota con su periodicidad ("95,00 € / mes").
- Eval `contracts_v2` (sustituye a `contracts_v1`). *(Corregido 2026-09-30: `main` aun no tiene `app/evals/baselines.json`; el workflow usa entonces la baseline del PR, que ya tiene `contracts_v2`, asi que el cambio de nombre no necesita la etiqueta `eval-regression-accepted`.)*
- Los campos de avisos (renovacion automatica, preaviso, fecha limite de baja) se anadiran como columnas nuevas con P3-6, sin tocar estas.

## D025 - Proveedor de IA sin saldo (402): mensaje propio y aviso al SADM

Decision (cerrada 2026-09-30, implementada): cuando un proveedor LLM rechaza por saldo agotado o facturacion (HTTP 402; Google lo devuelve como `RESOURCE_EXHAUSTED` "prepayment credits are depleted"; Anthropic "credit balance is too low"), la app:

- Lo clasifica como `provider_billing`, distinto de la saturacion (`provider_overload`, 429/503). Antes el `RESOURCE_EXHAUSTED` caia en saturacion y el usuario veia "el servidor de IA tiene muchas solicitudes", que invitaba a reintentar sin sentido.
- Muestra "El servicio de IA no esta disponible en este momento. Ya se ha avisado al administrador; podras volver a intentarlo cuando se restablezca" (documentos y chat).
- Avisa al SADM por email (`EMAIL_SADM`), como mucho una vez cada 6 h por proveedor (Redis NX + job ARQ `send_llm_provider_billing_alert`), sin datos de clientes.
- El documento queda **reintentable**: tras recargar, el usuario lo reintenta sin pasar por el SADM.
- No se reintenta automaticamente (402 no es transitorio).

Motivo: caso real en dev (2026-09-30), creditos de Google AI Studio agotados; con clientes reales nadie se habria enterado.

Consecuencia: `document_processing_errors.provider_error_code` / `provider_error_user_message`, `app/llm/provider_alerts.py`, `app/services/llm_provider_alert_service.py`, `app/jobs/provider_alert_jobs.py`.

## D026 - Presupuesto mensual de IA por plan: 6 / 15 / 30 EUR

Decision (cerrada 2026-09-30, implementada): `llm_budget_eur_month` = **6 / 15 / 30 EUR** (Basico / Avanzado / Premium), segun `especificacion-planes-y-cuotas.md` §3.2 y paso 1 de su §9. Sustituye a 30 / 100 / 250 (seed p67).

Motivo:

- El presupuesto es un **techo**, no el gasto esperado. Debe quedar por encima del uso normal (Basico ≈1,2-1,7 EUR/mes medido en dev, muestra pequena) y por debajo de lo que deja margen con el precio (22 / 49 / 99 EUR sin IVA).
- Con 30 EUR en Basico, un tenant con uso anomalo (script, cuenta comprometida o uso intensivo sostenido dentro de los limites diarios actuales) podia costar en IA mas que el precio del plan.

Consecuencia:

- Seed `PLAN_LIMITS` y migracion `p76_llm_budget_01` (solo `plan_entitlements`; los overrides por tenant no se tocan).
- Al 100 % se bloquea toda llamada al LLM (`ensure_llm_budget`). Desde el bloque 2 (2026-10-01), las facturas y tickets no fallan: quedan en `quota_pending` con motivo `llm_budget` y se procesan solos al renovarse el presupuesto (D027, detalles de los bloques 2 y 3). Contratos y polizas siguen fallando con "Has alcanzado el presupuesto mensual de IA de tu plan" hasta el bloque 5. Avisos y corte del chat sin cambios (D019: 80 % email, 90 % corte del chat).
- Un tenant que ya haya gastado mas del nuevo tope en el mes en curso queda bloqueado hasta el siguiente periodo o hasta un override del SADM en `/sadm/plans`.
- La cifra de uso normal sale de pocas muestras: revisar con trafico real (coste por tenant en `/sadm` y `llm_calls`) y ajustar por override o nuevo seed.

## D027 - Cupos mensuales: periodo, carga inicial, cambios de plan y limites diarios

Decision (cerrada 2026-09-30; bloque 1 implementado): los invitados del soft launch tienen los mismos limites que produccion (spec de planes §3), para que el piloto de datos reales con los que calibrar esos limites. Reglas:

- **Periodo:** mes natural en hora de Espana (del 1 al ultimo dia) para todos los contadores mensuales y para el presupuesto de IA (`usage_meter` pasa de UTC a hora de Espana). Si el alta no es el dia 1, la factura se prorratea fuera de la app (D016), pero las cuotas y limites son los del mes completo.
- **Carga inicial de contratos:** la ventana de `contract_uploads_first_period` va del alta al final del primer mes completo (alta 28/10 → hasta 30/11; alta el dia 1 → ese mes). Evita que un alta a final de mes tenga solo unos dias para su carga inicial.
- **Cambios de plan:** la primera asignacion del SADM es inmediata; las siguientes se programan para el dia 1 del mes siguiente (facturacion por mes completo). Pedir el plan actual anula el cambio pendiente. Subidas urgentes a mitad de mes: ampliacion del cupo del mes desde SADM.
- **Ampliacion del SADM:** por cupo y solo para el mes en curso (`quota_usage.extra`), registrada en `audit_log` (`sadm.quota_extra_added`). Los overrides permanentes siguen en `entitlements_override`.
- **Reintentos:** `document_retries_per_month` = 40 / 150 / 400 y maximo 3 reintentos manuales por documento (sustituye a `document_retries_per_day`).
- **Limites diarios:** se retiran al sustituirlos por los mensuales; solo se conserva un tope de subidas de documentos por dia, alto, como freno contra scripts. No afecta a los limites que la spec no sustituye: knowledge (`knowledge_uploads_per_day`, `knowledge_docs_max`), `channel_messages_per_hour`, `voice_notes_per_hour` y `channel_external_slots` siguen como estan.
- **Borrado diferido y purga de contratos a los 30 dias:** fuera del producto minimo (Backlog P3-8); se mantiene el borrado inmediato.
- **Contratos (bloque 5):** hash SHA-256 contra duplicados dentro del mismo tenant (se reutiliza el del bloque 2; sin LLM ni consumo de alta). Renovacion con boton manual "Marcar como sustituido" en el contrato anterior: libera su hueco de activo, queda en el historico y el chat no lo trata como vigente. La renovacion enlazada automatica (proponer a que contrato renueva) queda fuera del producto minimo y se monta despues sobre ese estado. Motivo: sin ninguna de las dos, un tenant en su maximo de activos no podria subir una renovacion hasta que venciera el contrato anterior, y el chat veria dos contratos vigentes de la misma contraparte (hoy no existe archivar a mano).

Motivo:

- Probar con los limites diarios actuales validaria un modelo que se va a sustituir y los datos del piloto no servirian para calibrar los mensuales.
- Mes natural = mismo periodo para facturacion, cupos y presupuesto; sin periodos distintos por tenant en SADM.

Consecuencia:

- Bloque 1 (hecho): `app/core/billing_period.py`; tabla `quota_usage` (migracion `p77_quota_usage_01`, RLS, `saas_app` sin DELETE ni TRUNCATE) con los limites mensuales en el catalogo; `monthly_quota_service` (consumo atomico por bolsa con `pg_advisory_xact_lock`, bolsa facturas + tickets, devoluciones, ampliacion del mes); `plan_change_service` + cron `apply_scheduled_plan_changes` (el plan nuevo rige por lectura desde las 00:00 del dia 1); SADM `/sadm/plans/tenants/{id}` con cupos del mes, ampliacion y cambio programado.
- Los limites mensuales estan en el catalogo y cada bloque los activa y retira el diario correspondiente: **aplicados** 2 facturas/tickets + `quota_pending` y 3 reintentos (2026-10-01, `p80_document_quota_01`) 4 chat D023 (2026-10-01, `p81_chat_quota_01`) 5 contratos (2026-10-03, `p82_contract_quota_01`), 6 `history_months` (2026-10-03, `p83_history_months_01`) y 7 consumo en "Mi cuenta" (2026-10-03, `17e0975`, sin migracion).

Detalles del bloque 7, consumo y avisos en la app (aprobados 2026-10-03):

1. "Mi cuenta" muestra "X de Y", barra y fecha de renovacion de: facturas y tickets (una barra, la bolsa, con el desglose), reintentos, preguntas al chat, altas de contratos (carga inicial con su fin o mensual) y contratos vigentes. Las ampliaciones del SADM suben el tope. Un cupo a 0 (no incluido) no se muestra.
2. Presupuesto de IA solo en porcentaje ("Uso de IA del mes: 35 %"): los euros dejaban ver el coste interno.
3. Avisos en la pagina donde se consume, para todos los usuarios: `/documents` al 80 % y al 100 % (facturas y tickets, altas de contratos) y con el archivo de contratos vigentes lleno; se refrescan con el fragmento HTMX del panel. `/chat` solo al 100 % (tope tecnico, spec §4.4). Un fallo al calcularlos nunca hace caer la pagina (savepoint + log).
4. Barras con los mismos umbrales que los avisos: ambar desde el 80 %, roja al 100 % (antes 70 % / 90 %).
5. SADM: marca "≥ 80 %" / "100 %" por tenant en `/sadm/plans` (presupuesto de IA o algun cupo mensual; P2b-18).
6. Sin migracion ni cache: se calcula al cargar la pagina (`quota_status_service`).

Detalles de implementacion de los bloques 2 y 3 (aprobados 2026-10-01):

1. **Reserva al encolar y devolucion si no termina bien**, en vez de consumir al terminar: se reserva una unidad de la bolsa facturas + tickets al encolar y se devuelve si el documento falla, se interrumpe, pasa a esperar presupuesto de IA o se borra en el mes en curso. Efecto neto = solo cuenta lo que se extrae bien (spec §4.2), sin pasarse del tope con subidas simultaneas. `quota_period` guarda el mes de la reserva: la devolucion va a ese mes y no se repite.
2. **Borrar devuelve la unidad si es del mes en curso.** Cubre los duplicados que el hash no detecta (otra foto del mismo ticket, el PDF del email y el escaneo) y los ficheros subidos por error. Un documento que no termino bien la devuelve siempre; uno procesado en un mes cerrado, no. El abuso (subir, leer, borrar) lo limita el presupuesto de IA.
3. **Presupuesto de IA agotado = `quota_pending`** (motivo `llm_budget`) en vez de `failed` (spec §4.7). El worker lo comprueba antes de llamar al LLM.
4. **Los fallos que no causa el usuario no gastan reintento**: `processing_interrupted`, `provider_overload` y `provider_billing` (solo los marca el sistema).
5. **Reintentar sin cupo de documentos se rechaza** con un mensaje claro, sin gastar el reintento (no pasa a pendiente).
6. **Duplicados por SHA-256**: bloquea cualquier factura o ticket no oculto con el mismo hash, tambien los fallidos ("usa Reintentar"); si no, borrar y volver a subir saltaria el limite de reintentos. Los ocultados no bloquean. Los documentos anteriores a `p80` no tienen hash (no se recalcula).
7. **El procesado excepcional del SADM no consume cupo**: se cobra aparte (`processing_charges`).
8. **Emails al admin al 80 % y con el primer documento pendiente del mes**, uno por mes de cada tipo (deduplicado en Redis). Los avisos dentro de la app son del bloque 7.

Ademas:

- `process_quota_pending`: cron horario (minuto 10, tras el cambio de plan del dia 1 en el minuto 5) y al arrancar el worker; tambien se lanza tras una ampliacion del SADM de facturas o tickets y al devolverse una reserva del mes si hay pendientes. Procesa del mas antiguo al mas nuevo, con `FOR UPDATE SKIP LOCKED`, y encola despues del commit. No hace nada mientras el presupuesto de IA siga agotado.
- La fila "Pendiente de cupo" no hace polling: se ve procesada al recargar el panel.
- `documents_per_day` se mantiene (50 / 200 / 800) como freno contra scripts y deja de mostrarse en "Mi cuenta". `document_retries_per_day` sale del catalogo (`p80`), pero el codigo sigue siendo valido para no romper overrides antiguos.
- Corregido de paso un fallo previo: confirmar el tipo sugerido (factura ↔ ticket) llamaba a `dismiss_from_panel`, que solo admite documentos fallidos, y el documento original estaba pendiente; el cambio de tipo fallaba. Ahora el original se oculta directamente y el hash pasa al documento nuevo.

Detalles de implementacion del bloque 5, contratos (aprobados 2026-10-02, hecho 2026-10-03, `99132d3`, `p82_contract_quota_01`):

1. **Altas reservadas al encolar y devueltas si el contrato no termina bien** (fallo, extraccion inservible, abandono por atasco, presupuesto de IA agotado), como en el bloque 2. **Borrar un contrato procesado nunca devuelve sus altas** (spec §4.3); uno que no termino, si.
2. **Activo** = `lifecycle` `active`, no fallido, no oculto y no vencido (`fecha_fin` vacia o de hoy en adelante). Un contrato vencido libera su hueco solo. Los pendientes y en proceso tambien ocupan hueco, para no pasarse con subidas simultaneas.
3. **Archivo de activos lleno = rechazo en la subida**, sin R2 ni LLM ni altas, con "Si es una renovacion, marca primero el contrato anterior como sustituido". Las altas, reintentos y reactivaciones se serializan por tenant con un bloqueo transaccional.
4. **Carga inicial:** durante la ventana (`initial_load_window_end`) se consume `contract_uploads_first_period`, contado siempre en el mes del alta aunque la ventana abarque dos meses; sustituye al mensual. Despues, `contract_uploads_per_month`. Los mensajes de pendiente dan como fecha el dia siguiente al fin de la ventana (el dia 1 intermedio no trae altas nuevas).
   - **Ampliacion del SADM (2026-10-03):** la de la carga inicial solo se admite dentro de la ventana y va al mes del alta; la de altas mensuales solo fuera de ella. Cada una se rechaza con un mensaje que indica cual ampliar. Fuera de la ventana, la carga inicial deja de aparecer en los cupos del SADM.
   - **Agotar la carga inicial antes de tiempo (2026-10-03):** no se pasa al cupo mensual. A los clientes nuevos se les ayudara con su carga inicial (la mayor parte de su documentacion historica); si necesitan mas, piden ampliacion al SADM.
5. **Tramos por paginas configurables** (`CONTRACT_UPLOAD_PAGE_TIERS`, por defecto `30,60`): 1 + numero de umbrales que superan sus paginas; una imagen cuenta como 1 pagina. Maximo `contract_max_pages` del plan (100), con `DOCUMENT_OVERRIDE_MAX_PDF_PAGES` como techo; por encima se rechaza en la subida sin R2 ni LLM. Antes de este bloque los contratos usaban el tope de facturas (3 paginas).
6. **Chat:** `search_documents` y `aggregate_documents` excluyen los contratos sustituidos salvo `incluir_sustituidos=true`; `ContractRead` incluye `lifecycle`. Riesgo: cambia la respuesta de las tools de contratos; las evals de chat lo validan en CI.
7. **"Marcar como sustituido" / "Volver a vigente"** en el detalle del contrato procesado, con `hx-confirm`; reactivar comprueba el hueco. Las dos acciones se auditan (`contract.replaced`, `contract.reactivated`). Etiqueta "Sustituido" en la fila.
8. **Duplicado por hash** de un contrato existente (vigente o sustituido) = rechazo; un fallido no oculto apunta a "Reintentar". No se reutiliza la extraccion (la spec lo plantea con borrado diferido, P3-8).

Ademas:

- Sin altas → `quota_pending` (mismo job `process_quota_pending`, que encola contratos). Una bolsa sin hueco no frena a la otra: cada bolsa conserva el orden de llegada.
- Emails al admin `contracts_warning` (80 %) y `contracts_exhausted` (primer pendiente), deduplicados como los de documentos.
- Presupuesto de IA agotado → el contrato pasa a `quota_pending` (motivo `llm_budget`) y devuelve sus altas, como facturas y tickets. Las polizas siguen igual (aparcadas).
- Reintentar un contrato exige hueco de activo y vuelve a reservar sus altas; sin altas se rechaza sin gastar el reintento.
- Job `process_contract` con timeout de 600 s; un contrato no se da por interrumpido hasta 660 s (las facturas, 180 s), para no ofrecer "Reintentar" sobre una extraccion larga en curso. Se mide en dev cuanto tarda un contrato de 80-100 paginas; el procesamiento en batch para la carga inicial queda para despues del producto minimo (Backlog P3-9).
- El procesado excepcional del SADM no reserva altas ni comprueba el archivo de activos.
- Los contratos anteriores a `p82` no tienen hash ni paginas (no se recalculan); cuentan como activos segun la regla del punto 2.

## D028 - Tailwind 3.4 como version vigente; migracion a v4 despues del producto minimo

Decision (cerrada 2026-09-30): la spec (`AGENTS.md` §1, `Arquitectura_V2.md` §3 y §13) decia Tailwind CSS 4, pero el codigo usa **Tailwind 3.4.17** (`@tailwind` en `app/static/css/input.css`, `tailwind.config.js`, `bin/tailwindcss.exe` en dev y `TAILWIND_VERSION=v3.4.17` con checksum en el `Dockerfile`). Se actualiza la spec a 3.4 como desviacion aceptada.

Motivo:

- Migrar a v4 cambia la configuracion (`@theme` en CSS en lugar de `tailwind.config.js`) y obliga a revisar todas las plantillas, sin aportar nada al cierre del producto minimo.
- La version esta fijada y verificada por checksum en dev y en la imagen: no hay deriva entre entornos.

Consecuencia:

- `Agents.md` (y `CLAUDE.md`, enlace simbolico a el), `Documentacion_V2/AGENTS.md` y `Arquitectura_V2.md` dicen Tailwind 3.4.
- Migracion a v4: despues del producto minimo (Backlog P2b-29), con nueva entrada en este log.

## D029 - Registro de actividad en BD (`activity_log`), separado de `audit_log`

Decision (cerrada 2026-09-30, implementada 2026-10-01 con la migracion `p79_activity_log_01`; fila 1b del cierre del producto minimo): una tabla `activity_log` registra por donde pasa la ejecucion de la app para poder corregir y mejorar durante el piloto. Consulta solo por SQL (sin pantalla en el SADM por ahora).

- **Filas** (`kind`): `request` (plantilla de ruta, metodo, estado, duracion, HTMX), `job` (nombre del job ARQ, intento, resultado, duracion), `event` (cada `log.info/warning/error` del codigo, capturado por un procesador de structlog, con modulo, funcion y linea) y `error` (excepcion no controlada: tipo y fichero:linea:funcion del frame mas profundo de `app/`).
- **Identificadores en todas:** `tenant_id`, `user_id` (id interno), `request_id`, `job_id` y `parent_request_id` (propagado al encolar desde `app/jobs/queue.py`, enlaza el job con la peticion que lo lanzo).
- **Sin datos personales ni contenido:** de los datos extra de cada evento solo se guardan claves de una lista permitida (ids, codigos, estados, conteos, duraciones); nunca mensajes de excepcion, nombres, emails, nombres de fichero, importes, IP, parametros de URL ni cuerpos. No se registran DEBUG, `/static`, `/health` ni el polling HTMX de estado.
- **Escritura:** buffer en memoria acotado por proceso (API y worker), volcado en bloque cada ~2 s fuera de la transaccion de la peticion; si falla, la app sigue. No se usa Redis (`noeviction`, 512 MB: una avalancha de eventos podria tumbar la cola ARQ).
- **Seguridad:** RLS por tenant; `saas_app` solo `INSERT`; la purga la hace una funcion `SECURITY DEFINER` que solo borra filas mas antiguas que la retencion, lanzada por un cron ARQ.
- **Retencion:** `ACTIVITY_LOG_RETENTION_DAYS`, 90 por defecto; `0` = no se purga nunca. **En produccion `0` no se admite** (decidido 2026-10-01): la app se niega a arrancar con un mensaje que explica el motivo y como corregirlo, igual que con `LANGFUSE_CAPTURE_CONTENT=true`. En development y staging se permite `0`. Motivo: cada fila lleva `user_id` (dato personal) y guardarlo sin limite choca con la minimizacion del RGPD, impide fijar un plazo en la politica de privacidad y, a 1-3 millones de filas al mes, engorda sin control la BD y los backups.

Motivo:

- Hoy el recorrido del usuario no queda en ningun sitio: `audit_log` excluye a proposito navegacion, listados y polling (AGENTS.md §7), los logs de consola se rotan en dias (Docker 10 MB x 5) y no hay un identificador que una peticion, logs y jobs.
- Separada de `audit_log`: este es forense, sin ruido y solo insercion (P2c-1); mezclar actividad de depuracion rompe ambas reglas.
- Capturar los eventos de structlog reutiliza los ~270 puntos de log que ya existen sin tocarlos.

Consecuencia:

- Migracion nueva (tabla, indices por fecha, tenant y `request_id`, RLS, permisos, funcion de purga), middleware de peticion, hooks `on_job_start`/`after_job_end` de ARQ, procesador de structlog y cron de purga; tests de lista permitida (sin datos personales), RLS y purga.
- Nota en AGENTS.md §7 que distinga auditoria (`audit_log`) de actividad (`activity_log`).
- `user_id` es dato personal: retencion limitada y mencion en la politica de privacidad.
- Volumen estimado: 1-3 millones de filas al mes con 10 usuarios; revisar con uso real.

Detalles de implementacion (aprobados 2026-10-01):

- **RLS con INSERT permisivo:** RLS activado con `tenant_isolation` mas una politica de INSERT abierta, porque el volcado mezcla filas de varios tenants en un mismo INSERT; separarlas por tenant no anadia seguridad real (el tenant lo pone el propio volcador). Lo que protege es que `saas_app` solo tiene `INSERT` (ni `SELECT` ni `UPDATE` ni `DELETE`). Sin `FORCE`: el propietario consulta y purga sin RLS.
- **Sin FK** en `tenant_id` / `user_id`: un tenant borrado entre el buffer y el volcado tumbaria el lote, y cada INSERT comprobaria la FK. La purga por retencion cubre el borrado.
- **`request_id`:** UUID nuevo por peticion (se ignora el `X-Request-ID` del cliente), devuelto en la cabecera `X-Request-ID` y anadido a los logs de consola. En los jobs, `job_id` va tambien a los logs.
- **Errores:** un log con traceback (`log.exception`) se guarda como fila `error`, ademas de las excepciones que escapan de una peticion o un job; casi todas las excepciones de la app se capturan y se registran asi.
- **Jobs:** wrapper `tracked_job` en `WorkerSettings` en lugar de los hooks `on_job_start`/`after_job_end` de ARQ, que no reciben el nombre del job ni sus argumentos. Toma `tenant_id` de la firma del job y el `parent_request_id` de un kwarg que anade `job_parent_kwargs()` al encolar.
- **Purga:** `purge_activity_log(retention_days, batch_size)` exige `retention_days >= 7` en la propia BD, para que un proceso comprometido no pueda borrar lo reciente; el cron (diario, 03:30) la llama por lotes de 50 000.
- **Interruptor:** `ACTIVITY_LOG_ENABLED` (por defecto `true`) lo apaga sin redeploy; los tests lo fuerzan a `false`.
- **Alcance de la exclusion:** `/static`, `/health*`, `/metrics` y las rutas de polling `/jobs/{kind}/{id}/status` no generan fila `request`; los eventos INFO+ que ocurran dentro de ellas si se registran (en la practica, solo `app_error` si el polling llega sin sesion).

## D030 - Citas por WhatsApp y Telegram sobre el modulo interno de citas

Decision (cerrada 2026-09-30, pendiente de implementar; `Paso12_ConexionWa_Tel_Calendario.md`, fuera del producto minimo): el asistente de canales gestiona citas sobre el modulo interno (`appointments`, horario del centro, profesionales, servicios) y deja de usar Google Calendar.

- **Google Calendar fuera de los canales:** se retiran las tools `calendar_tools` del registry del canal. El codigo de Google Calendar de la app (`/calendar`, voz) se conserva sin evolucionar (D012).
- **Identidad del cliente final:** `(channel, customer_identifier)` del webhook (telefono E.164 en WhatsApp, `chat_id` en Telegram), nunca un dato que proponga el modelo. Toda lectura o cambio de citas desde el canal filtra por esa identidad.
- **Citas creadas en la app:** en WhatsApp el cliente ve, cambia y cancela tambien las citas cuyo telefono normalizado (E.164) coincide con su numero, porque Meta lo verifica. En Telegram, solo las creadas desde ese chat (el telefono lo escribe el cliente).
- **Antelacion minima:** cambiar o cancelar desde el canal exige `channel_min_notice_hours` por tenant, **24 h por defecto**, editable por el admin en `/settings/business-hours`. Dentro del plazo, el asistente da el telefono del negocio. No afecta a la app ni a la creacion de citas.
- **Estado inicial:** las citas del canal nacen `scheduled`; el centro las confirma en la app si trabaja asi.
- **Sin `appointments`** (p. ej. override): el canal no expone tools de citas.

Motivo:

- Avanzado y Premium venden citas + WhatsApp/Telegram (D012), pero el canal reservaba en Google Calendar, fuera de oferta.
- Las tools de Google Calendar del canal listaban y cancelaban citas de todo el tenant sin filtrar por cliente: un cliente final podia ver o cancelar citas ajenas.
- Permitir cambiar la proxima cita por WhatsApp aunque la diera el centro por telefono es el uso esperado en clinicas y centros.

Consecuencia:

- Migracion `p78` en `appointments`: `channel`, `channel_customer_id`, `client_phone_normalized` (con backfill) e indices.
- Tools nuevas de la familia `scheduling` sobre `internal_appointment_service` y `appointment_slot_service`; prompt `channel_external_v2`.
- Las respuestas de turnos con tools de citas no entran en la cache semantica del canal.
- Normalizador E.164 (España por defecto) con la dependencia `phonenumbers` (aprobada 2026-09-30, AGENTS §1): cubre tambien numeros extranjeros.

## D031 - Datos personales en la auditoria: seudonimos, retencion de 2 anos y rastreo

Decision (cerrada e implementada 2026-10-03, `5c4eed3`, Backlog P2c-7, migracion `p84_audit_log_retention_01`): `audit_log` es solo insercion (P2c-1) y no tenia purga, asi que los datos personales de su metadata se habrian quedado para siempre sin poder limpiarse. Antes del primer cliente real:

1. **Seudonimos en lugar del dato en claro** (HMAC-SHA256 truncado a 128 bits, `app/core/audit_pseudonym.py`):
   - Miembros (solicitud, alta y baja): `email_ref`.
   - Documentos y knowledge (subida y borrado): `file_ext`, `filename_ref` y `file_sha256` (el hash del contenido prueba que fichero fue, mejor que el nombre).
   - Profesionales: `display_name_ref`. Calendario: `google_email_ref`.
   - Se mantiene el nombre de los servicios del catalogo (no es dato personal).
   - Normalizacion antes del HMAC: emails y ficheros sin distinguir mayusculas; nombres de persona ademas sin tildes ni espacios repetidos.
2. **Rastreo** (`scripts/audit_lookup.py`, `audit_lookup_service`): con el email se encuentra lo que se hizo con la persona (seudonimo en la metadata) y lo que hizo ella (por `user_id`), tambien tras borrar su cuenta (D021 conserva la fila y su `user_id`; la membresia de la entrada lleva a el). Con el nombre: cuentas activas y profesionales; tras un borrado el nombre ya no existe y hace falta el email (efecto buscado del borrado). Ficheros por nombre exacto o SHA-256. El propio rastreo queda en el `audit_log` de cada tenant con resultados (`sadm.audit_lookup`, sin el dato buscado).
3. **Retencion**: `AUDIT_LOG_RETENTION_DAYS` = 730 (2 anos) por defecto, configurable; minimo 365, impuesto tambien por la funcion de BD `purge_audit_log` (`SECURITY DEFINER`, recorre los tenants porque la tabla tiene RLS forzado); `0` (no purgar) prohibido en produccion. Cron diario a las 03:45.
4. **Clave aparte `AUDIT_PSEUDONYM_KEY`**, obligatoria en produccion (32+ caracteres y distinta de `APP_SECRET_KEY`) y **que no se rota**: si cambiara, los seudonimos anteriores ya no se podrian recalcular y se perderia el rastro. `APP_SECRET_KEY` puede rotarse sin afectar a la auditoria. Fuera de produccion, si falta, se deriva de `APP_SECRET_KEY`.

Motivo:

- Guardar emails y nombres de fichero en una tabla inmutable y sin plazo choca con la minimizacion y la limitacion del plazo de conservacion (RGPD). El seudonimo sigue siendo dato personal, pero una fuga no revela nada y el rastreo sigue siendo posible si se conoce el dato.
- 2 anos cubren el periodo en que un cliente podria reclamar sobre un acceso o un borrado. Pendiente de validar con asesoria y de reflejar en la politica de privacidad y en el registro de actividades (probable, no es asesoria juridica).

Consecuencia:

- Guardia en CI: `tests/unit/test_audit_pseudonym.py` falla si una llamada a `log_action` vuelve a meter `email`, `filename`, `display_name`, `google_email` o `phone` en la metadata.
- Las entradas anteriores (solo dev y tests) conservan el dato en claro; el rastreo las encuentra igualmente.
- `AUDIT_PSEUDONYM_KEY` va al gestor de contrasenas junto a `ENCRYPTION_KEY` (`PasosParaProduccion.md` Fase 5.1).
