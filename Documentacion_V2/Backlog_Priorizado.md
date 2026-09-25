# Backlog_Priorizado

Fecha actualizacion: 2026-09-25
Estado: alineado con codigo en `RamaCursor01` (planes D012 `p67` + Stripe `p65`).

Leyenda: **Hecho** = en codigo y tests. **Ops** = falta accion humana / entorno. **Pendiente** = producto no implementado.

## P0 - Seguridad y control de coste

| # | Item | Estado |
| --- | --- | --- |
| 1 | Rotar y sanear secretos de documentacion historica | **Ops** (repo saneado; rotacion en proveedores pendiente) |
| 2 | Sync roles/memberships Clerk | **Hecho** (+ QA retry real en Dashboard: ops) |
| 3 | Dedupe anti-replay webhooks | **Hecho** (Clerk/WA/TG/Stripe) |
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
| 3 | `/settings/billing` plan/uso + Stripe | **Hecho** en codigo (Paso09); Price IDs + Infisical: **Ops** |
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
| 5 | Tools del chat: filtrar por fecha de vencimiento (`fecha_fin`) en contratos/polizas | **Pendiente** (`chat_documents_v2` doc_031 agota iteraciones) |
| 6 | `transcription` y `translate` siguen en `gemini-2.5-flash` (riesgo de retirada) | **Pendiente** (medir y migrar) |
| 7 | Eval de tickets con mas casos (hoy 3, todos fotos buenas) | **Pendiente** (anadir tickets arrugados / baja calidad) |
| 8 | Evals escriben en BD `saas` (tenant "Invoice extraction eval") | **Pendiente** (BD dedicada o `saas_test`) |
| 9 | CI de evals: fallar si una metrica baja >5 % frente a `main` (Agents.md §9) | **Pendiente** (hoy gating por umbral absoluto) |
| 10 | Dev `DATABASE_URL` con superusuario `saas`: la UI de dev no pasa por RLS (prod usa `saas_app`, NOBYPASSRLS) | **Ops** |
| 11 | Objetos huerfanos en R2/MinIO de tenants borrados en dev | **Ops** (limpieza puntual) |

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

## P3 - Nuevos modulos

| # | Item | Estado |
| --- | --- | --- |
| 1 | Analytics SQL read-only | **No implementar** (D011 / Paso08 archivado; no se vende BI) |
| 2 | Stripe billing | **Hecho** en codigo (Paso09 / F1); operativa Stripe Dashboard: **Ops** |
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

1. Ops: Infisical staging/prod, rotacion credenciales, QA manual Paso07.
2. Ops Stripe: Price IDs + webhook + claves Infisical.
3. Soft-launch Paso10 cuando staging este vivo.

No roadmap: Paso08 Analytics / modulo 3 (D011).
