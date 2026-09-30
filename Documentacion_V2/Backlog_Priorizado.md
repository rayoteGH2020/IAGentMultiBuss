# Backlog_Priorizado

Fecha actualizacion: 2026-09-29
Estado: alineado con codigo en `RamaCursor01` (planes D012 `p67`; Stripe retirado D016 `p68`).

Leyenda: **Hecho** = en codigo y tests. **Ops** = falta accion humana / entorno. **Pendiente** = producto no implementado.

## P0 - Seguridad y control de coste

| # | Item | Estado |
| --- | --- | --- |
| 1 | Rotar y sanear secretos de documentacion historica | **Ops** (repo saneado; rotacion en proveedores pendiente) |
| 2 | Sync roles/memberships Clerk | **Hecho** (+ QA retry real en Dashboard: ops) |
| 3 | Dedupe anti-replay webhooks | **Hecho** (Clerk/WA/TG) |
| 4 | Limite de body webhooks | **Hecho** |
| 5 | OCR knowledge + `media_limits` | **Hecho** |
| 6 | Catalogo de planes | **Hecho** (D012: `basic`/`advanced`/`premium`, `p67`) |
| 7 | Gates por plan | **Hecho** (Paso03) |
| 8 | Cuotas y budgets por plan | **Hecho** (Paso04) |

## P1 - Plataforma y producto base

| # | Item | Estado |
| --- | --- | --- |
| 1 | Consolidar SADM V2 | **Hecho** (Paso05) |
| 2 | Asignar planes desde SADM | **Hecho** (`/sadm/plans`) |
| 3 | `/settings/billing` plan/uso (solo lectura; plan lo asigna SADM, D016) | **Hecho** |
| 4 | UX documentos procesando/rechazados | **Hecho** (Paso06) |
| 5 | Verificacion de tipo antes de extraccion | **Hecho** (Paso06) |
| 6 | UI multi-IVA | **Hecho** (Paso06) |
| 7 | QA real Clerk/R2/Langfuse/Calendar/WA/TG | **Ops** (Paso07 QA manual) |

## P2 - IA y canales

| # | Item | Estado |
| --- | --- | --- |
| 1 | Chat documental y citations | **Hecho** (Paso07 codigo) |
| 2 | QA y evals de RAG | **Hecho** en suite; regresion continua |
| 3 | Cache semantica canales + invalidacion | **Hecho** |
| 4 | Alertas de coste por turnos chat/tools | Parcial (`usage_meter` / SADM); alertas push no |
| 5 | Prompts contratos/seguros | **Hecho** base (Paso06) |

## P2b - Modelos LLM, evals y deuda de extraccion (2026-09-25)

Contexto: D014 (extraccion `gemini-3.8-flash`), D015 (chat `gemini-3.5-flash-lite`), evals permanentes de tickets/contratos/polizas y eval de chat documental `chat_documents_v2`.

| # | Item | Estado |
| --- | --- | --- |
| 1 | Infisical prod: fijar o dejar sin definir `LLM_MODEL_EXTRACTION` / `LLM_MODEL_CHAT` (deben coincidir con dev) | **Ops** (cuando exista entorno prod) |
| 2 | `GOOGLE_API_KEY` dedicada a CI (hoy comparte cuota con dev) | **Ops** |
| 3 | Contratos: separar `fecha_inicio` (firma vs inicio de vigencia) e `importe` (periodico vs total + periodicidad) | **Pendiente** (schema + prompt v2 + migracion) |
| 4 | Polizas: `tipo_seguro` como enum cerrado en vez de texto libre | **Pendiente** (schema + prompt v2 + migracion de datos) |
| 5 | Tools del chat: filtrar por fecha de vencimiento (`fecha_fin`) en contratos/polizas | **Hecho** 2026-09-28 (`fecha_fin_from` / `fecha_fin_to`; `chat_documents_v2` 32/32 x2) |
| 6 | `transcription` y `translate` siguen en `gemini-2.5-flash` (riesgo de retirada) | **Pendiente, no urgente** (medir y migrar; antes consultar fecha de retirada del modelo) |
| 7 | Eval de tickets con mas casos (hoy 3, todos fotos buenas) | **Pendiente** (anadir tickets arrugados / baja calidad) |
| 8 | Evals escriben en BD `saas` (tenant "Invoice extraction eval") | **Hecho** 2026-09-28 (evals en `saas_test`, compartida con pytest: `app/evals/eval_db.py`; CI igual). Datos antiguos del tenant de evals borrados de `saas` el 2026-09-28 (tenant + cascade: 1.113 `llm_calls`, 808 `audit_log`, 353 `chat_threads`, documentos sembrados; + usuario `@eval.local`) |
| 9 | CI de evals: fallar si una metrica baja >5 % frente a `main` (Agents.md §9) | **Hecho** 2026-09-28 (`app/evals/baselines.json` + `compare_baseline`; chat x2 con media; excepcion con etiqueta `eval-regression-accepted`) |
| 10 | Dev `DATABASE_URL` con superusuario `saas`: la UI de dev no pasa por RLS (prod usa `saas_app`, NOBYPASSRLS) | **Ops** |
| 11 | Objetos huerfanos en R2/MinIO de tenants borrados en dev | **Ops** (limpieza puntual) |
| 12 | Tools del chat: `group_by` month/year agrupa contratos/polizas por `fecha_inicio`; falta agrupar por vencimiento (`fecha_fin`) | **Pendiente** (continuacion del item 5 + caso nuevo en `chat_documents_v2`) |
| 14 | Coste de reintentos de Instructor con Gemini: Instructor 1.15 solo suma el uso de los reintentos para OpenAI y Anthropic (`instructor/utils/core.py::update_total_usage`); con `from_genai` en `llm_calls` queda solo el ultimo intento. Una extraccion con 2 reintentos por schema invalido cuenta ~1/3 de su coste en el presupuesto del plan. Sumar el uso de cada intento (hooks de Instructor) | **Hecho** 2026-09-29: `_AttemptUsage` en `client.py` suma cada intento via hook `completion:response`; las llamadas fallidas con tokens se registran con su coste y descuentan del presupuesto (antes 0 EUR y sin descontar); el chat (`chat_loop.py`) ahora tambien suma su coste a `usage_meter` (antes no lo hacia) |
| 16 | Chat de la app frente al presupuesto de IA (D019) | **Hecho** 2026-09-29: al 90 % responde con mensaje fijo sin LLM (telefono y email del admin del tenant, `users`, D020) y avisa al admin (1 cada 24 h, max. 3/mes); email al admin al cruzar el 80 % (1/mes) con cualquier gasto de IA. Ampliado: email al SADM al 90 % (1/mes, con nombre y apellido del admin desde Clerk y su telefono de `users`) y banner configurable al 100 % en todas las paginas (`LLM_BUDGET_EXHAUSTED_NOTICE`) |
| 17 | Asistente de canales (WhatsApp/Telegram, Avanzado/Premium) frente al presupuesto: hoy no comprueba `llm_budget_eur_month` antes de llamar al LLM. Aplicar el mismo corte que el chat (D019) con mensaje fijo al cliente final sin llamar al modelo, siguiendo el patron del anti-abuso de `channel_jobs.py`. Decidir si el umbral es el mismo 90 % | **Pendiente** (no afecta a Basico) |
| 18 | Interfaz de consumo y avisos (spec §7 y paso 8 de §9): barras de consumo y banners al 80 % / 100 % en la app, alerta en SADM al 80 %, y aviso de cupo para documentos (`pendiente_cupo`). Hoy hay el email al admin del 80 %, el email al SADM del 90 %, el banner del 100 % (D019) y el consumo en `/settings/profile` | **Pendiente** (resto) |
| 19 | Ops: que el admin de cada tenant rellene su telefono en `/settings/members` > editar su ficha (D020, `users.phone`). Sin telefono el mensaje de corte del chat sale solo con el email del admin (queda log `llm_budget.contact_incomplete`) | **Ops** (ficha hecha 2026-09-29) |
| 15 | Semaforo de extraccion por tenant: cada `Retry` consume un intento de ARQ (`max_tries=2`); con varios workers un job diferido 2 veces se descartaria y el documento quedaria en processing hasta el barrido de huerfanos. Hoy no ocurre (1 worker, `max_jobs` = limite por tenant = 5). Resolver antes de escalar workers (p. ej. `max_tries` propio para extraccion, ya seguro por `extraction_guard`) | **Pendiente** (latente) |
| 20 | Tokens de cache en `llm_calls` (`cached_input_tokens`): leer el campo de cache de la respuesta de Gemini/Anthropic, columna + migracion. Solo para medir el ahorro de la cache implicita (canales, chat) y afinar la hoja de costes; el presupuesto ya se cobra con tokens reales. Sin columna `feature`: la funcion sale de `task` + `prompt_version` (spec §4.8) | **Pendiente, no urgente** (medicion, fuera del producto minimo) |
| 21 | Historial del chat de la app (spec §4.4): se reenvian 20 mensajes con los resultados de las tools. Con uso real, medir en `llm_calls` los tokens de entrada de la primera pregunta de cada hilo frente a las siguientes; si el historial pesa mucho en hilos largos, recortar el tamano de los `tool_result` antiguos al reenviarlos (no quitarlos). Antes de tocar el historial, anadir a `chat_documents_v2` casos de varias preguntas seguidas (hoy los 32 son de una sola pregunta) | **Pendiente, no urgente** (medir con trafico real) |
| 13 | `.gitattributes` solo fija LF en ficheros de deploy; el resto depende de `core.autocrlf` de cada maquina | **Pendiente, higiene** (`* text=auto eol=lf` + `git add --renormalize .`, commit `chore:`) |

Orden acordado para la deuda de producto (2026-09-28, aplazado): **12 -> 3 -> 4**, despues 6. El item 3 cambia campos usados por panel, chat y evals: presentar diseno de campos antes de implementar.

### Detalle de la deuda de producto (items 3, 4 y 5)

Problema comun: campos del schema que no dicen exactamente que dato guardar. El modelo decide en cada extraccion y todo lo que se construye encima (filtros, sumas, alertas, respuestas del chat) sale incoherente. Detectado al medir D014 y con `chat_documents_v2`.

**3a. Contratos - `fecha_inicio`.** El schema pide "fecha de inicio **o** firma". En el contrato de ascensores (firma 02/09/2026, inicio 01/10/2026) el modelo devuelve una u otra segun la ejecucion. Consecuencia: "contratos que empiezan este mes" o la antiguedad de un contrato fallan de forma intermitente.

**3b. Contratos - `importe`.** El schema pide "importe, canon o valor economico". En la practica se guardan magnitudes distintas:

| Contrato | Valor guardado | Que es |
| --- | --- | --- |
| Plaza de garaje | 114,95 EUR | Cuota **mensual** con IVA |
| Vivienda | 13.800 EUR | Renta **anual** |
| Consultoria | 19.200 EUR | **Total** del contrato (6 meses) |

Sumar "importe de mis contratos" (chat, listados) mezcla mensual, anual y total: el resultado no significa nada. Igual con "cuanto pago al mes en contratos".

Solucion 3a/3b: separar campos (`fecha_firma` / `fecha_inicio`; `importe_periodico` + `periodicidad` / `importe_total`), prompt `contract_extraction_v2` y migracion de datos.

**4. Polizas - `tipo_seguro`.** Texto libre: la misma poliza de hogar sale como "hogar", "Multirriesgo Hogar" u "Hogar Integral"; la de coche como "auto", "automoviles" o "Automoviles". Filtrar "mis seguros de coche" o agrupar gasto por tipo pierde polizas segun como se escribio cada una.

Solucion: enum cerrado (hogar, auto, vida, salud, decesos, accidentes, rc, multirriesgo_comercio, otros), prompt `insurance_extraction_v2` y migracion que reclasifique lo ya extraido.

**5. Tools del chat sin filtro por vencimiento.** `search_documents` / `aggregate_documents` filtran contratos y polizas por `fecha_inicio`, no por `fecha_fin`. En `chat_documents_v2` doc_031 ("cuantas polizas vencen en octubre de 2027?", respuesta correcta 7) el chat respondio "No se ha encontrado ninguna poliza": respuesta falsa dada con seguridad, que hoy recibiria un cliente real. Solucion: filtros `fecha_fin_from` / `fecha_fin_to` en las tools y servicios de contratos/polizas. **Prioridad mas alta de los tres.**

*Resuelto 2026-09-28:* filtros `fecha_fin_from` / `fecha_fin_to` en `DocumentSearchFilters`, en los args de `search_documents` / `aggregate_documents` y en los servicios de contratos y polizas; `fecha_from` / `fecha_to` documentan que en contratos/polizas filtran por inicio. La pista va solo en la descripcion de los parametros: anadirla tambien a la descripcion de las tools hizo que el modelo dejara de usar `aggregate_documents` en doc_013 (sumaba a mano y fallaba, 2/2 ejecuciones). Pendiente relacionado: `group_by` month/year sigue agrupando por `fecha_inicio`.

## P2c - Deuda de seguridad (revision 2026-09-29, aplazada)

Contexto: revision externa de 6 puntos. Hechos y en `RamaCursor01`: metrics `hmac.compare_digest`, dedupe Telegram, `REVOKE TRUNCATE` (`p71`), errores LLM crudos fuera de logs (`0c42075`), auditoria de knowledge y accesos SADM (`cd614d0`), alcance de `audit_log` en `AGENTS.md` §7 (`502eafe`), trazas de chat SADM solo del tenant propio + auditoria de detalle de cita (`5379502`, `p72`). Aplazado el resto para cerrar el producto minimo; retomar antes de produccion comercial y en este orden.

| # | Item | Estado |
| --- | --- | --- |
| 1 | `audit_log` solo insercion: `REVOKE UPDATE, DELETE ON audit_log FROM saas_app` (concedido en `p16`). Los tests que borran filas de `audit_log` (p. ej. `_cleanup` en `test_document_override.py`) pasaran a usar el rol propietario. ~1 h | **Pendiente** |
| 2 | Datos personales en logs: `customer_identifier` (`channel_jobs.py`), nombres de fichero en subida (`documents.py`), comercio/total en `worker.ticket.done`, ramas `except Exception` que loguean `str(exc)` en workers/knowledge, destinatario/asunto en debug de `email.py`, `client_name` en metadata de `scheduling.appointment_created` (legible por SADM via `audit_log`). Sustituir por hash HMAC / contadores / tipos. 2-3 h | **Pendiente** |
| 3 | IP de auditoria: hoy no falsificable (Caddy es el borde sin `trusted_proxies`). Unificar en `request.client.host` (quitar parseo manual de `X-Forwarded-For` en `audit_context.py` y `documents.py`, y las 5 copias de `_audit_request_context`), restringir `--forwarded-allow-ips=*` a la red interna y test en `test_deploy_config.py`. ~30-45 min | **Pendiente** (robustez) |
| 4 | Al activar Cloudflare delante: `trusted_proxies` con rangos de Cloudflare en Caddy y firewall solo desde Cloudflare; si no, todas las IPs auditadas seran de Cloudflare | **Ops** (bloqueante al activar Cloudflare) |
| 5 | `/metrics`: restriccion de red en proxy/infra ademas del token (hoy Caddy responde 404 a `/metrics`) | **Ops** |
| 6 | CSP sin `unsafe-eval` / `unsafe-inline`: migrar a `@alpinejs/csp` (~156 usos de Alpine) + nonces para ~10 scripts inline. 1-2 dias | **Pendiente** (backlog) |

`p71` y `p72` aplicadas en dev y `saas_test` (2026-09-29). Pendiente en staging/prod cuando existan (`infisical run -- uv run alembic upgrade head`; comprobar con `alembic current` que muestra `p72_drop_sadm_chat_read_01 (head)`).

## P3 - Nuevos modulos

| # | Item | Estado |
| --- | --- | --- |
| 1 | Analytics SQL read-only | **No implementar** (D011 / Paso08 archivado; no se vende BI sobre BD externa del cliente) |
| 1b | Analista de datos Premium sobre datos del tenant (`especificacion-planes-y-cuotas.md` §4.5) | **Post producto minimo** (D018). Al retomarlo, nueva decision que sustituya a D011 |
| 2 | **Metodo de cobro de los planes** (2026-09-28) | **Pendiente de decision** (D016). Stripe retirado. Decidir: proveedor (Stripe, Redsys, GoCardless/SEPA...) o cobro fuera de la app (transferencia/factura manual); quien cambia el plan ante impago; si el cobro debe reflejarse en la app. Nueva decision en `Decision_Log.md` antes de implementar |
| 3 | Resenas/marketing | **Pendiente** (no priorizado) |
| 4 | MCP/tooling externo | **Pendiente** (no priorizado) |

## No implementar ahora

- Reescritura total.
- Switcher multi-org.
- Microservicios.
- Kubernetes.
- GraphQL.
- React/Vue/Svelte.
- LangChain/LlamaIndex como base.

## Orden recomendado restante

1. Ops: Infisical staging/prod, rotacion credenciales, QA manual Paso07. Borrar `STRIPE_*` de Infisical (D016).
2. Decidir metodo de cobro de los planes (P3-2) antes de la produccion comercial.
3. Soft-launch Paso10 cuando staging este vivo.
4. Deuda de seguridad P2c (1 -> 2 -> 3) antes de la produccion comercial; P2c-4 al activar Cloudflare.

No roadmap: Paso08 Analytics / modulo 3 (D011).
