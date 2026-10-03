# Especificación: planes, cuotas de uso y control de coste

> **Para el asistente de código (Cursor / Claude Code).**
> La base de esta especificación es lo que **ya está implementado** y documentado en `Planes_Entitlements.md`:
> - catálogo `basic` | `advanced` | `premium`;
> - `entitlement_service` + `require_feature`;
> - `plan_quota_service` (Redis);
> - panel SADM `/sadm/plans`;
> - `tenant_plan_changes`;
> - seed en `app/core/entitlement_codes.py` y migración `p67`.
>
> Este documento **amplía y corrige** ese sistema. No crees un catálogo, un servicio de cuotas ni tablas de planes en paralelo.
> **Estado (2026-09-30):** el análisis de brechas (secciones 5 y 10) se hizo en las revisiones de la especificación y quedó recogido en `Decision_Log.md` (D017–D027) y en la tabla «Cierre del producto mínimo» de `Backlog_Priorizado.md`, que es el plan de implementación vigente. No se generó `docs/analisis-planes-y-cuotas.md`. Cada bloque presenta su diseño antes de escribir código.

---

## 0. Reglas para el asistente

1. **Reutiliza lo existente.**
   - Los límites nuevos son **códigos de límite** en el seed de entitlements.
   - Las funciones nuevas son **códigos de feature**.
   - Las comprobaciones pasan por `entitlement_service`, `require_feature` y `plan_quota_service`.
   - Solo propón una tabla o un servicio nuevo si el análisis demuestra que lo existente no lo cubre, y justifícalo.
2. **Analiza el código real antes de proponer.** Cita ficheros y líneas. Si algo no existe, dilo; no lo supongas.
3. **Respeta el stack y las convenciones:**
   - FastAPI + Jinja2 + HTMX + Alpine.js + Tailwind (sin Node).
   - PostgreSQL + pgvector, Redis, Cloudflare R2.
   - Clerk multi-tenant.
   - Cliente LLM propio (Anthropic + Google) con Langfuse e Instructor. Sin LangChain.
   - Prompts versionados en `.txt`.
4. **Los límites y precios no se escriben en el código.** Van en el seed de entitlements y se pueden sobrescribir por tenant desde SADM.
5. Trabaja en **pasos pequeños y numerados**, cada uno desplegable y con sus pruebas.
6. **Prioridad: producto mínimo.** Lo marcado como **[Fase posterior]** se especifica ahora para que el diseño lo tenga en cuenta, pero no se implementa todavía.

---

## 1. Decisiones de partida

| Tema | Decisión | Origen |
|---|---|---|
| Arquitectura | La de `Planes_Entitlements.md`. **Hay que verificar** que cubre todo lo de este documento (sección 5) | Planes_Entitlements |
| Qué incluye cada plan | El catálogo de `Planes_Entitlements.md` (D012), **más el analista en Premium** **[Post producto mínimo, D018]** | Planes_Entitlements + esta especificación |
| Analista de datos | **Se implementará solo en Premium, después del producto mínimo (D018).** Mientras tanto **D011 sigue vigente**: sin feature `analytics` en el catálogo ni rutas de la tarea `sql`. Al retomarlo, registrar una decisión nueva que sustituya a D011 | Esta especificación + D018 |
| WhatsApp y Telegram | Incluidos en Avanzado y Premium. No existen en Básico. **No hay complemento aparte** | Planes_Entitlements |
| Tipo de límite | **Mensuales y comerciales**, con avisos al 80 % y 100 %. Sustituyen a los diarios como referencia de producto | Esta especificación |
| Control de coste | `llm_budget_eur_month` como **tope duro** por tenant, **con opción de ampliación** (override en SADM ahora; packs en fase posterior). Importes recalibrados (sección 3) | Planes_Entitlements + esta especificación |
| Usuarios | 3 / 9 / 20 | Esta especificación |
| Precios | 22 / 49 / 99 € al mes sin IVA. **No están cerrados**: pueden subir | Esta especificación |
| Cambio de plan | Solo desde SADM con `assign_tenant_plan` (D016) | Planes_Entitlements |
| Cobro | **Pendiente de decidir.** Stripe está retirado | Planes_Entitlements |
| Google Calendar y voz | Fuera de la implementación por ahora (D012) | Planes_Entitlements |
| Modelos de IA | Los de `DEFAULT_MODELS` en `app/llm/client.py` (sección 8). Los costes se calculan con las tarifas de `app/llm/pricing.py` y las medias reales de `llm_calls` | Código actual |
| Contratos | Modelo de contratos activos + altas al mes + hash + páginas + borrado diferido (sección 4.3) | Esta especificación |
| Packs de ampliación | Se especifican ahora y se implementan **[Fase posterior]** | Esta especificación |

---

## 2. Planes

### 2.1 Funcionalidades (feature codes)

| Feature code | Básico | Avanzado | Premium | Notas |
|---|:---:|:---:|:---:|---|
| `documents` | Sí | Sí | Sí | Facturas y tickets (extracción, conciliación, exportación) y contratos |
| `documents_chat` | Sí | Sí | Sí | Preguntas sobre los documentos y contratos del dueño (con herramientas sobre los datos extraídos, sin búsqueda vectorial) |
| `knowledge` | Sí | Sí | Sí | Conocimiento del negocio: preguntas frecuentes, servicios, tarifas, horarios, formas de pago |
| `knowledge_chat` | Sí | Sí | Sí | Chat interno del dueño sobre ese conocimiento |
| `appointments` | No | Sí | Sí | Citas: crear, modificar, cancelar y consultar |
| `channel_whatsapp` | No | Sí | Sí | Asistente para clientes finales |
| `channel_telegram` | No | Sí | Sí | Mismo motor; útil para pilotos y pruebas |
| `analytics` | No | No | **Sí** **[Post producto mínimo, D018]** | Analista de datos conversacional. Hasta retomarlo no entra en el catálogo (D011 vigente) |
| `calendar_google`, `calendar_voice` | No | No | No | Fuera de oferta (D012); solo override SADM |

> **A verificar en el análisis:** si los contratos encajan en `documents` / `documents_chat`, o si hace falta un feature code propio (`contracts`).

### 2.2 Condiciones comerciales

Precios sin IVA (21 %). Pago anual = 10 mensualidades (2 meses gratis).

| | Básico | Avanzado | Premium |
|---|---|---|---|
| Precio mensual | 22 € | 49 € | 99 € |
| Precio anual | 220 € | 490 € | 990 € |
| Usuarios | 3 | 9 | 20 |
| Histórico visible | 12 meses | 3 años | Ilimitado |
| Exportación CSV / Excel **[Post producto mínimo]** | Sí | Sí | Sí |
| Avisos de contratos **[Post producto mínimo]** | Vencimiento | + plazo de baja + resumen de condiciones | + comparativa entre renovaciones |
| Soporte **[Post producto mínimo]** | Email | Email prioritario | Teléfono / WhatsApp + puesta en marcha guiada |

**Fuera del producto mínimo (2026-09-30):**
- **Envío de datos fuera de la app a gestorías** (envío automático, integración con software contable): no se ofrece ni se implementa por ahora.
- **Exportación CSV / Excel:** descarga por el propio usuario, auditada como exportación. Backlog P3-5.
- **Avisos de contratos:** todos, incluido el de vencimiento. Requieren extraer renovación automática, preaviso y fecha límite de baja (hoy no se extraen) y un feature code propio para el plazo de baja. Backlog P3-6.
- **Niveles de soporte:** condición comercial y de operación, sin feature code. Backlog P3-7.

---

## 3. Límites

### 3.1 Límites comerciales mensuales (los que ve el cliente)

En el catálogo desde `p77` (D027), aún sin aplicar: facturas, tickets y contratos. `members_max` aplicado (D022). Sin crear todavía: `history_months` (bloque 6), `assistant_messages_per_month` y `reminders_per_month` (canales, fuera del producto mínimo).

| Limit code | Unidad | Básico | Avanzado | Premium |
|---|---|---:|---:|---:|
| `invoices_per_month` | facturas | 40 | 150 | 400 |
| `tickets_per_month` | tickets | 30 | 80 | 200 |
| `contracts_active_max` | contratos activos | 15 | 40 | 100 (se vende como «sin límite práctico») |
| `contract_uploads_per_month` | altas y renovaciones | 5 | 10 | 30 |
| `contract_uploads_first_period` | altas en el primer periodo | 15 | 40 | 100 |
| `contract_max_pages` | páginas por fichero | 100 | 100 | 100 |
| `assistant_messages_per_month` | mensajes enviados por el asistente | 0 | 800 | 2.500 |
| `reminders_per_month` | recordatorios de cita | 0 | 200 | 600 |
| `members_max` | usuarios de cualquier rol, admin incluido (D022) | 3 | 9 | 20 |
| `history_months` | meses visibles (facturas y tickets; contratos por vigencia, D017) | 12 | 36 | sin límite |

### 3.2 Control de coste y protección técnica (el cliente no los ve)

| Limit code | Unidad | Básico | Avanzado | Premium | Notas |
|---|---|---:|---:|---:|---|
| `llm_budget_eur_month` | € de IA al mes | **6** | **15** | **30** | **Tope duro.** Recalibrado: antes era 30/100/250, por encima incluso del precio del Básico. Equivale a un 27-31 % del precio. Con los modelos y costes reales, el peor caso (todos los límites y topes técnicos al 100 %, reintentos incluidos) es de unos 1,75 / 8,40 / 41 €: el presupuesto lo cubre 3,4 y 1,8 veces en Básico y Avanzado. **En Premium el tope es el que limita** (sobre todo por el tope del analista y de los chats); con un uso normal (unos 12 €) no se alcanza. Sin el analista (post producto mínimo, D018), el peor caso de Premium baja a unos 23 € y el presupuesto lo cubre. Ver hoja «Coste por cliente» del Excel |
| `end_customer_messages_per_day` | mensajes por cliente final | 0 | 30 | 30 | Anti-abuso. Sustituye a `channel_messages_per_hour` |
| `document_retries_per_month` | reintentos manuales | 40 | 150 | 400 | **D027: sustituye a `document_retries_per_day` (20/80/300),** que permitía unos 600 reintentos al mes en Básico (≈4,7 € de IA). Además, **máximo 3 reintentos manuales por documento** |
| `chat_questions_per_month` | preguntas | 400 | 1.500 | 4.000 | **D023.** Un solo cupo mensual para el chat de la app (documentos y conocimiento son el mismo chat y una pregunta puede usar herramientas de ambos). Suma de los antiguos `documents_chat_questions_per_month` (250/1.000/2.500) y `knowledge_chat_questions_per_month` (150/500/1.500). Sin límite comercial; tope técnico de unas 5 veces el uso previsto (80/300/800). Sirve para que el chat no consuma el presupuesto de IA que necesita la extracción |
| `analytics_questions_per_month` | preguntas | 0 | 0 | 500 | **[Post producto mínimo, D018]** Uso previsto: 200. A ≈0,036 € por pregunta, 500 preguntas son ≈18 € |

**Límites diarios actuales.** `documents_per_day`, `knowledge_uploads_per_day` y `chat_messages_per_day` **dejan de ser la referencia**; los sustituyen los mensuales.
- En el análisis, indica si conviene conservar alguno solo como freno a ráfagas (por ejemplo, con un valor de 3 veces el mensual dividido entre 30).
- **Chat (D023, hecho 2026-10-01):** sin topes diarios. `chat_messages_per_day` del plan, el tope diario de plataforma (`CHAT_DAILY_MESSAGE_LIMIT`) y el diario por usuario (`CHAT_USER_DAILY_MESSAGE_LIMIT`) se han sustituido por `chat_questions_per_month` + un límite de ritmo por usuario (10 preguntas por minuto y 60 por hora, configurable) contra scripts o cuentas comprometidas.
- `voice_notes_per_hour` y `channel_external_slots` se quedan como están.

**Documentos de conocimiento (`knowledge_docs_max`, `knowledge_uploads_per_day`).**
- Pendiente de decidir: ver preguntas abiertas.
- Propuesta: aplicar el mismo modelo que a los contratos, con activos + altas al mes + hash + páginas.

---

## 4. Reglas de negocio

### 4.1 Periodo de cómputo
- **Mes natural en hora de España** (del 1 al último día) para todos los contadores mensuales y el presupuesto de IA (D027, `app/core/billing_period.py`). Si el alta no es el día 1, la factura se prorratea fuera de la app, pero las cuotas son las del mes completo.
- Los contadores mensuales persisten en PostgreSQL (tabla `quota_usage`, `monthly_quota_service`, D027). Los diarios y de ritmo que quedan siguen en Redis.

### 4.2 Facturas y tickets (bolsa compensable)
- En la interfaz se muestran dos límites, pero **se controla el total**: `facturas + tickets <= invoices_per_month + tickets_per_month (+ saldo de packs, en la fase posterior)`.
- **Cuándo se consume el cupo comercial:** cuando la extracción termina bien. Si falla, o el usuario lo descarta como duplicado o ilegible, no consume o se devuelve.
- **Los fallos sí cuentan en el presupuesto de IA:**
  - Toda llamada al LLM, con éxito o fallida (incluidos los reintentos), se registra en `llm_calls` con los tokens **reales** que devuelve la API y suma en `llm_budget_eur_month`.
  - Los errores de la API sin tokens procesados cuentan 0 €. Una respuesta cortada a medias cuenta los tokens ya procesados.
  - Así el gasto de IA por cliente nunca supera su presupuesto, aunque falle todo.
- **Reintentos limitados:**
  - Como máximo **2 reintentos automáticos** por documento: hasta **3 llamadas de extracción** al LLM por procesado. Los hace Instructor cuando la respuesta no cumple el schema (`LLM_EXTRACTION_MAX_RETRIES`, por defecto 2 y con tope 2 en `app/config.py`). Cada reintento es una llamada completa y cuenta en el presupuesto.
  - **El worker no repite la extracción.** ARQ solo vuelve a ejecutar un job si se difiere por el semáforo de concurrencia (antes de llamar al LLM, sin coste) o si el worker se reinicia a mitad. En ese segundo caso, si la ejecución anterior ya había llamado al LLM, no se vuelve a extraer: el documento pasa a fallido con `processing_interrupted` y el usuario puede reintentarlo a mano (`app/jobs/extraction_guard.py`).
  - Los reintentos por **errores HTTP transitorios** del proveedor (429/5xx, `LLM_RETRY_TRANSIENT_ERRORS`, desactivado por defecto) no son reintentos de extracción: el proveedor no procesa tokens y cuestan 0 €.
  - Si tras los reintentos sigue fallando, el documento pasa a `failed` y **no se vuelve a intentar solo**.
  - Los reintentos que lance el usuario los limita `document_retries_per_month` (40 / 150 / 400) y un **máximo de 3 por documento** (D027). Pasado ese máximo, el documento se queda en `failed` con los reintentos agotados: no se ofrece «Reintentar» y la interfaz lo muestra como «Revisión manual». No es un estado propio en la BD; se deduce del contador de reintentos manuales del documento.
  - **Motivo:** con el límite diario anterior (20 al día en Básico) cabían unos 600 reintentos al mes, unos 4,7 € de IA. Eso cabe en el tope de 6 €, pero agotaría el presupuesto y bloquearía los chats del cliente.
- **Clasificación previa:** si la regla automática no reconoce el tipo de documento, se clasifica con `claude-haiku-4-5` (tarea `classify`, ≈0,002 € por llamada) antes de extraer. Cuenta en el presupuesto de IA, no en el cupo comercial. Registrar en `llm_calls` qué porcentaje de documentos la necesita.
- **Filtros antes de llamar al LLM (coste cero):**
  - **Hash SHA-256 del fichero:** si en el mismo tenant ya existe un documento con ese hash, no se reprocesa; se avisa de que es un duplicado y no consume cupo.
  - **Validación básica:** formato admitido, tamaño mínimo de imagen, fichero no corrupto y límite de páginas en los PDF (configurable). Si no pasa, se rechaza sin llamar al LLM.
- **Margen de seguridad (referencia):**
  - Una extracción fallida cuesta lo mismo que una correcta: unos 0,005 € por factura y 0,003 € por ticket (medido con `gemini-3.8-flash`), más unos 0,002 € si también hubo que clasificarla con Haiku.
  - Con el presupuesto de IA gastado entero, cada plan sigue dando beneficio: unos 10,77 / 16,37 / 20,62 € por cliente al mes.
  - Sin tope, harían falta unos 3.100 / 5.400 / 7.300 fallos al mes para perder el margen.
- **Al 80 %:** aviso en la app y por email.
- **Al 100 %:** la subida no se bloquea. El documento queda pendiente de cupo (`status = quota_pending`); se procesa en el siguiente periodo o tras una ampliación desde SADM (packs en la fase posterior).

### 4.3 Contratos
- **Dos límites que se aplican a la vez:**
  - `contracts_active_max`: el tamaño de su archivo de contratos.
  - `contract_uploads_per_month`: **cada subida consume un alta, también las renovaciones.** Borrar **no devuelve** el alta.
- **Carga inicial:** desde el alta hasta el final del primer mes completo (alta 28/10 → hasta 30/11; alta el día 1 → ese mes), el límite de altas es `contract_uploads_first_period` (igual al de activos) (D027).
- **Renovación:** consume un alta pero **no ocupa un hueco de contrato activo**. El anterior pasa a sustituido (`lifecycle = replaced`). **Producto mínimo (D027):** botón manual «Marcar como sustituido» en el contrato anterior; el enlace automático con `replaces_contract_id` queda para después.
- **Por páginas:**
  - Hasta 30 páginas = 1 alta; de 31 a 60 = 2; de 61 a 100 = 3.
  - Más de `contract_max_pages` = se rechaza con un mensaje claro.
  - Los tramos son configurables.
- **No se reprocesa el mismo fichero:**
  - Se guarda el SHA-256.
  - Si en **el mismo tenant** existe un contrato con ese hash (activo, sustituido o borrado hace menos de 30 días), se reutilizan la extracción y los embeddings. **No se llama al LLM ni se consume alta.** En el producto mínimo, sin borrado diferido, solo cuenta el contrato que sigue existiendo (activo o sustituido).
- **Borrado diferido [Post producto mínimo, Backlog P3-8]:** mientras tanto se mantiene el borrado inmediato (borrar no devuelve el alta).
  - Al borrar se rellena `deleted_at` (no hay estado «borrado»); queda oculto y fuera de las búsquedas.
  - A los 30 días se purga (embeddings, datos y fichero en R2).
- **Extracción al subir (Instructor, `contract_extraction_v2`, D024):** parte contraria, fecha de firma, inicio de vigencia, vencimiento, cuota sin IVA (`importe_periodico`) con su `periodicidad`, importe total, `importe_anual` (calculado) e `iva_incluido`. Renovación automática, preaviso en días y **fecha límite de baja** se añaden con los avisos de contratos **[Post producto mínimo]** (Backlog P3-6).
- **Avisos según el plan** (sección 2.2), con un trabajo programado diario. **[Post producto mínimo]** (Backlog P3-6).
- **Cupo:** al 80 % y al 100 % de las altas, aviso. Al 100 %, el contrato queda pendiente de cupo (`status = quota_pending`).
- **Estado actual (hecho 2026-10-03, bloque 5, `p82_contract_quota_01`):** altas reservadas al encolar y devueltas si el contrato no termina bien; borrar uno procesado no las devuelve. Carga inicial contada en el mes del alta durante toda la ventana. Tramos `CONTRACT_UPLOAD_PAGE_TIERS` (`30,60`) y máximo `contract_max_pages` rechazado en la subida. Archivo de activos lleno = rechazo con «marca primero el contrato anterior como sustituido»; activo = vigente, no fallido y no vencido. Duplicado por hash = rechazo (sin reutilizar la extracción). Botones «Marcar como sustituido» / «Volver a vigente» (auditados) y el chat excluye los sustituidos salvo que se pidan. Emails al 80 % y con el primer pendiente; avisos en la app con el bloque 7. La ampliación del SADM de la carga inicial solo vale dentro de su ventana; agotada, no se pasa al cupo mensual: se pide ampliación (la carga inicial de clientes nuevos será asistida). Detalles en D027.

- **Estados (convención):** dos ejes separados. `status` = procesado del fichero por el LLM (`pending`, `processing`, `ready`, `failed`, `reviewed`, y `quota_pending` nuevo). `lifecycle` = estado del documento para el usuario (`active`, `replaced`, `archived`); el borrado es `deleted_at`. Implementado en contratos (`p82`): `active` y `replaced`; `archived` y `deleted_at` llegan con el borrado diferido (P3-8). Los valores van en inglés; la UI los muestra en español con el filtro `status_label` (`app/core/status_labels.py`).

### 4.4 Chat documental y chat de conocimiento (dueño, en la app)
- Cuenta **cada pregunta con su respuesta** en `chat_questions_per_month`, un único cupo mensual del tenant para los dos chats (D023).
- **Al 100 %:** mensaje fijo sin llamar al modelo («Has alcanzado las preguntas de este mes; se renuevan el día X»), con el contacto del admin, como el corte por presupuesto (D019). Se amplía con override del SADM. Sin avisos al 80 %: es un tope técnico, no comercial.
- **Límite de ritmo por usuario** (≈10 por minuto o 60 por hora) en lugar de los topes diarios: no lo alcanza una persona y frena en minutos un script o una cuenta comprometida.
- El presupuesto de IA (§4.8, D019) sigue siendo el tope duro final.
- **Estado actual (hecho 2026-10-01, bloque 4, `p81_chat_quota_01`):** cupo mensual con `monthly_quota_service`; la pregunta se cuenta al empezar el turno y se devuelve si el proveedor falla; el corte por presupuesto no la cuenta. Límite de ritmo por usuario y organización (10 por minuto, 60 por hora). Los topes diarios están retirados. Detalles en D023.
- **Historial (decidido 2026-09-30, se acepta el comportamiento actual):** se reenvían los últimos 20 mensajes (`chat_history_message_limit`), incluidos los mensajes `tool` con su resultado completo. Una pregunta con herramientas ocupa ≈4 mensajes, así que son ≈4-5 turnos.
  - Los resultados de las herramientas se guardan en `chat_messages.tool_result`: los usan las trazas de chat del SADM y permiten las preguntas de continuación («¿y la segunda?»). Entran en el borrado y el export RGPD de los chats.
  - Motivo: el coste ya está acotado (máx. observado ≈0,0045 € por pregunta; las 400 preguntas del Básico (D023) ≈1,8 € en el peor caso frente a 6 € de presupuesto) y quitar los resultados arriesga las continuaciones sin un ahorro relevante.
  - Descartado: reenviar solo 4 turnos sin resultados de herramientas y la compactación del historial (sería otra llamada al LLM, con coste y prompt propios).
  - Revisar con uso real (Backlog P2b-21): si el historial pesa mucho en hilos largos, recortar el tamaño de los resultados antiguos en lugar de quitarlos.
- **Modelo:** `gemini-3.5-flash-lite` (tarea `chat`), con herramientas (`run_tool_loop`, desde `chat_service.py`). Coste medido: ≈0,002 € por pregunta (≈2 llamadas por las herramientas; máximo observado ≈0,0045 €).
- **Reformulación:** solo si el chat busca **antes** de llamar al modelo. Con `run_tool_loop` es el propio modelo quien formula la búsqueda al llamar a la herramienta, así que la reformulación previa sobra. **A verificar en el análisis** cómo se construye hoy la búsqueda.
- **Sin prompt caching explícito.**
  - La parte fija de estos chats son las instrucciones y la definición de las herramientas. Los resultados de búsqueda cambian en cada pregunta.
  - Gemini aplica su propia caché implícita cuando el inicio del prompt se repite; no hay que marcar nada. Solo hay que mantener **el orden fijo** (instrucciones y herramientas primero, idénticas en cada llamada; historial y pregunta al final) para no impedirla.
  - Tokens de caché en `llm_calls`: **aplazado** (Backlog P2b-20). No afecta al presupuesto, que ya se cobra con los tokens reales.

### 4.5 Analista de datos (Premium) **[Post producto mínimo, D018]**
- **Estado:** no se desarrolla en el producto mínimo. D011 sigue vigente hasta que se retome; entonces, decisión nueva que la sustituya. Lo que sigue es el diseño para ese momento.
- Feature `analytics` y dependencia `require_feature("analytics")`. Se oculta en los demás planes.
- **Modelo:** tarea `sql` → `claude-sonnet-4-6` (reservada y sin uso desde D011). Cada llamada se registra en `llm_calls` con `task="sql"`, que solo usa el analista: no hace falta columna `feature`.
- **Coste estimado:** ≈0,036 € por pregunta (8.000 / 1.000 tokens, con la tarifa de Sonnet de `pricing.py`: 2,80 / 14,00 € por millón, equivalente a 3 $ / 15 $). **A verificar:** medir con los primeros usos.
- **Prompt caching:** la descripción del esquema de datos es fija y puede pasar del mínimo que exige Anthropic para cachear. Si lo pasa, marcar `cache_control` al final del esquema; comprobar el mínimo de `claude-sonnet-4-6` en la documentación de Anthropic.
- **Solo lectura:** consultas contra una vista o esquema con permisos de solo lectura, filtrado por `tenant_id`. Validar el SQL generado antes de ejecutarlo (sin DML ni DDL, con límite de filas y tiempo).

### 4.6 Asistente para clientes finales (WhatsApp / Telegram, Avanzado y Premium)
- **Qué hace:** responde con el conocimiento del negocio y gestiona citas (crear, modificar, cancelar, consultar).
- **Diseño actual (verificado):**
  - El conocimiento no va en el prompt: el asistente lo consulta con `run_tool_loop` y las herramientas de `build_channel_registry` (`search_knowledge`, búsqueda semántica sobre los fragmentos indexados de los documentos de conocimiento con `voyage-3-lite`).
  - Reenvía los últimos 10 mensajes de la conversación, solo texto.
  - La caché semántica de respuestas guarda una respuesta solo si su confianza supera `channel_cache_min_confidence`; la confianza sale de la puntuación de las citas de `search_knowledge` (`channel_chat_service.py`).
- **Modelo:** `gemini-3.5-flash-lite` (tarea `chat`, `channel_chat_service.py`), el mismo que el chat de la app. Los embeddings de `voyage-3-lite` se usan también como caché de respuestas de los canales. Coste estimado: ≈0,003 € por mensaje (2 llamadas de ≈4.000 / 150 tokens + 1 embedding). **Sin medir todavía:** tomar la media real de `llm_calls` en cuanto haya tráfico.
- **Prompt caching:**
  - Gemini aplica caché implícita cuando el inicio del prompt se repite; no hace falta marcar nada. Para no impedirla: **nada variable antes del historial** (ni fecha, ni hora, ni nombre del cliente final, ni identificadores de petición); la fecha y la disponibilidad van con el mensaje nuevo.
  - **A verificar** en la documentación de Google: tamaño mínimo para que se aplique la caché implícita con `gemini-3.5-flash-lite` y descuento que aplica. Tokens de caché en `llm_calls`: aplazado (Backlog P2b-20).
  - El Excel **no cuenta ningún descuento por caché**: si existe, el coste real será menor.
- **Posible optimización futura, no comprometida (Backlog P2b-22): conocimiento en el prompt.**
  - Idea: meter el conocimiento del negocio entero en el prompt en lugar de buscarlo con la herramienta. Ahorra la llamada de ida y vuelta a `search_knowledge` y aprovecha mejor la caché implícita, a cambio de 2.000-5.000 tokens más por llamada. Ahorro estimado ≈30-40 % por mensaje sobre ≈0,003 €, sin medir.
  - Es un **rediseño** de algo que funciona. Condiciones antes de hacerlo:
    - Medición real en `llm_calls` que lo justifique.
    - Decidir de dónde sale el «perfil del negocio»: hoy el conocimiento son documentos subidos troceados en fragmentos, sin perfil estructurado ni límite de tamaño. Crear una entidad de perfil o concatenar fragmentos (con búsqueda como respaldo si pasa de ≈8.000 tokens, lo que deja dos caminos que mantener).
    - Redefinir la confianza de la caché semántica, que hoy depende de las citas de `search_knowledge`: sin citas la caché no se llenaría.
  - Si se hace: orden fijo del prompt (herramientas de citas → instrucciones `.txt` versionado → conocimiento → historial → mensaje nuevo) y conocimiento serializado de forma determinista (orden estable, sin espacios variables, versión serializada con hash que solo se regenera cuando el dueño lo edita).
  - Si alguna vez se enruta el asistente a un modelo de Anthropic, aplican sus reglas (marca `cache_control` explícita y mínimo de tokens por modelo).
- **Citas con herramientas (tool use):** el modelo propone; la **disponibilidad, los solapamientos y el horario se validan en el código**.
  - **Estado (verificado 2026-09-30):** el módulo de citas internas existe (horario del centro, excepciones, profesionales con especialidades, servicios, `appointments` con restricción GiST contra solapes por profesional), pero el canal **no lo usa**: sus tools de citas van a Google Calendar (fuera de oferta, D012) y no filtran por cliente final.
  - **Diseño acordado (D030, `Paso12_ConexionWa_Tel_Calendario.md`):** tools sobre el módulo interno (servicios, profesionales, huecos, crear, ver, cambiar y cancelar); identidad del cliente final tomada del webhook; en WhatsApp el cliente gestiona también las citas creadas en la app con su número; antelación mínima por tenant para cambiar o cancelar (24 h por defecto); los turnos con citas no entran en la caché semántica.
- **Cuotas:**
  - Se consume 1 de `assistant_messages_per_month` por mensaje enviado y 1 de `reminders_per_month` por recordatorio.
  - **Al 80 %:** aviso al dueño.
  - **Al 100 %:** mensaje fijo con el teléfono del negocio, sin llamar al modelo.
- **Anti-abuso:**
  - `end_customer_messages_per_day` por número de cliente final (Redis).
  - Si la pregunta no trata sobre el negocio, se responde con una frase fija.
- **Meta:**
  - Cada negocio tiene **su propio número y su cuenta de WhatsApp Business**, dados de alta con el alta integrada de Meta para proveedores tecnológicos. **Meta factura al negocio.**
  - Se guardan `waba_id`, `phone_number_id` y la referencia al token (cifrado).
  - Tarifas desde el 1/10/2026: se cobran también las respuestas, unos 0,0166 € por mensaje en España. **Confirmar con la tarifa oficial de Meta.**
- **Recordatorios:** plantillas de utilidad aprobadas por Meta, enviadas con un trabajo programado (por ejemplo, 24 horas antes de la cita).
- **Derivación a una persona:** si el cliente lo pide o el asistente no sabe responder, se avisa al dueño y se pausa el bot en esa conversación.
- **Límites en el contenido:** nunca diagnostica, valora síntomas ni da consejos clínicos o técnicos. Ante urgencias o síntomas, da una respuesta fija con el teléfono del negocio. Es imprescindible para clínicas y fisioterapia.
- **Datos personales:** se guarda solo lo necesario para la cita (nombre, teléfono, día y hora). En centros sanitarios, la cita puede ser dato de salud: se necesita contrato de encargado del tratamiento y los datos alojados en la UE.

### 4.7 Usuarios e histórico
- `members_max` (D022): máximo de miembros activos de cualquier rol, admin incluido (3 / 9 / 20). Se comprueba en la app al solicitar o registrar un alta (`plan_quota_service.ensure_member_capacity`). Premium va dirigido a negocios de 10 o más miembros; ese mínimo es comercial, no se aplica.
- Si un tenant ya supera el máximo (cambio de catálogo o bajada de plan), conserva sus miembros y solo se bloquean las altas nuevas. `/settings/members` muestra «X de Y» y un aviso al llegar al máximo o superarlo. El SADM puede fijar otro máximo por tenant con un override.
- **Clerk** tiene su propio máximo de miembros por organización (hoy 5): debe ser ≥ 20 o las invitaciones de Avanzado y Premium fallarán a partir del 6.º miembro (Backlog P2b-23).
- **`history_months` (D017):** oculta facturas y tickets con fecha de emisión anterior al límite. Los contratos activos o pendientes de vencer son siempre visibles, buscables en el chat y avisables; tras vencer o ser sustituidos, se aplica el mismo límite contado desde su fecha de fin.
- Las pólizas seguirán la regla de los contratos cuando se resuelva su encaje en los planes (aparcado).
- El histórico más antiguo que `history_months` **no se borra**, solo se oculta. Si el cliente sube de plan, vuelve a verse.
- Aún no implementado (`history_months` no existe en el código, 2026-09-30): implementarlo ya con esta regla.

### 4.8 Control de coste (`llm_budget_eur_month`)
- Cada llamada al LLM (también la clasificación con Haiku y los embeddings de Voyage) se registra en `llm_calls` con `tenant_id`, `task`, `prompt_version`, tokens y coste (**verificado**). No hay columna `feature`: la función se identifica con `task` + `prompt_version` (el asistente de canales usa `channel_external_v1`; el chat de la app, el prompt de `chat_prompts.py`; el analista, `task="sql"`). El chat de documentos y el de conocimiento son un único chat (`PROMPT_UNIFIED`) y una misma pregunta puede usar herramientas de ambos, así que no se separan por llamada. Los topes de preguntas de §3.2 son contadores de cuota y no dependen de `llm_calls`. Tokens de caché: aplazado (Backlog P2b-20). `plan_quota_service` acumula el gasto del periodo.
- **Umbrales (D019, implementado):**
  - **80 %:** email al admin del tenant (una vez al mes). Alerta en SADM dentro de la app: pendiente (Backlog P2b-18).
  - **90 %:** el chat de la app responde con mensaje fijo sin llamar al modelo (contacto del admin, D020) y email al SADM (una vez al mes).
  - **100 %: tope duro** (`ensure_llm_budget`). Se bloquean las funciones que llaman al LLM y aparece un banner en todas las páginas del panel:
    - Facturas y tickets pasan a pendientes (`status = quota_pending`, motivo `llm_budget`) y se procesan solos al renovarse el presupuesto (hecho, bloque 2, 2026-10-01). Los contratos también (hecho, bloque 5, 2026-10-03), y devuelven sus altas. Las pólizas siguen fallando con un mensaje reintentable (aparcadas).
    - El asistente de canales responderá con el mensaje fijo (Backlog P2b-17, fuera del producto mínimo).
    - Nada se pierde.
- **Ampliación:**
  - **Ahora:** override del SADM en `entitlements_override`, que es **permanente** (hay que retirarlo a mano). La ampliación solo para el mes en curso (`quota_usage.extra`, D027) cubre los cupos, no el presupuesto de IA: pendiente (Backlog P2b-27).
  - **[Fase posterior]:** packs de ampliación (sección 4.9).
- Con un uso normal no debería saltar nunca. Si salta a menudo en un cliente, es señal de abuso o de que necesita otro plan.

### 4.9 Packs de ampliación [Fase posterior]
- **Pack de documentos:** 50 documentos por 5 € sin IVA.
- **Orden de consumo:** primero el cupo del plan y después los packs, del más antiguo al más nuevo.
- **No caducan a fin de mes;** el saldo se acumula. Tienen una **validez de 12 meses** desde la compra, con aviso 30 días antes si queda saldo.
- Si el cliente cambia de plan, conserva el saldo.
- Si compra packs 3 meses seguidos, la app le sugiere el plan siguiente (nunca se le cambia automáticamente).
- **No hay packs de contratos.** El presupuesto de IA se amplía en proporción al pack.
- Mientras no haya cobro, un pack solo puede asignarlo SADM.
- **Ejemplo (Básico, 70 al mes):**
  - Octubre, sube 90: gasta 70 del plan y 20 de un pack de 50. Le quedan 30.
  - Noviembre, sube 60: no toca el pack. Le siguen quedando 30.
  - Diciembre, sube 85: gasta 70 del plan y 15 del pack. Le quedan 15.

### 4.10 Cambio de plan y cobro
- Solo desde SADM con `assign_tenant_plan` (D016). Ningún rol del tenant puede cambiar de plan.
- **Cobro: pendiente de decidir.** No implementar nada de cobro en esta fase. Todo lo que dependa del cobro (packs, periodo de facturación) queda parametrizado.

### 4.11 Fuera de la implementación
- Google Calendar y voz (D012).
- Integración con software contable (Premium).
- Comparativa entre renovaciones de contratos (Premium): solo dejar preparado el modelo de datos.

---

## 5. Análisis de brechas (Fase 1)

Para cada punto, indica si **ya está cubierto**, **cubierto en parte** o **no cubierto** por lo implementado, con ficheros y líneas:

1. **Seed de entitlements** (`app/core/entitlement_codes.py`, migración `p67`): ¿se pueden añadir los límites de la sección 3 (y, al retomar el analista, el feature `analytics`, D018) solo con el seed? ¿Qué pasa con los tenants existentes y con los alias legacy?
2. **`plan_quota_service`:**
   - ¿Soporta periodos mensuales, además de diarios?
   - ¿Tiene un consumo atómico y devoluciones?
   - ¿Permite una bolsa compartida entre dos límites (facturas + tickets)?
   - ¿Persiste en PostgreSQL o solo en Redis?
   - ¿Tiene avisos al 80 %?
3. **Presupuesto de LLM:** `llm_calls` ya guarda tokens y coste por llamada (con las tarifas de `pricing.py`). Guarda `tenant_id`; la función sale de `task` + `prompt_version` (ver §4.8); tokens de caché aplazados (Backlog P2b-20). ¿Se registran también las llamadas fallidas, la clasificación y los embeddings? ¿Hay un override por tenant en SADM?
4. **Documentos:** ¿dónde está el punto de «extracción correcta» para consumir cuota? ¿Existe un estado equivalente a `quota_pending`?
5. **Contratos:** ¿existen como entidad propia o son documentos genéricos? Campos, estados, hash, número de páginas, borrado lógico.
6. **Knowledge:** estructura actual y tamaño típico. *Respondido:* búsqueda semántica con la herramienta `search_knowledge`, tanto en el chat de la app como en los canales (ver §4.6).
7. **Appointments y canales:** qué está implementado (tablas, herramientas del modelo, validación de disponibilidad, recordatorios, plantillas de Meta, derivación a una persona).
8. **Historial de los chats:** *Respondido:* chat de la app, 20 mensajes con los resultados de las herramientas; canales, 10 mensajes solo de texto (ver §4.4).
9. **Tareas programadas:** planificador existente para avisos de contratos, recordatorios, reinicio de periodos y purgas.
10. **Usuarios e histórico:** cómo se aplica hoy `members_max` y si existe algún filtro por antigüedad.
11. **SADM:** qué overrides permite ya (límites, features, presupuesto) y si registra quién y cuándo.
12. **Analista:** si existe algo de `analytics` en el código conservado (D011) que sea reutilizable.

---

## 6. Modelo de datos (orientativo; solo lo que falte)

> Antes de crear nada, comprueba si ya existe un equivalente. Adapta los nombres a las convenciones del proyecto.

```sql
-- Contadores mensuales persistentes (si plan_quota_service solo usa Redis)
usage_events (                     -- solo inserciones: auditoría y devoluciones
  id, tenant_id, limit_code TEXT, quantity INT DEFAULT 1,
  source TEXT DEFAULT 'plan',      -- 'plan' | 'pack:<id>' | 'override:<id>'
  source_id UUID NULL, period_start DATE, created_at TIMESTAMPTZ, refunded BOOL DEFAULT false
)
usage_counters ( tenant_id, period_start, limit_code, used INT,
                 PRIMARY KEY (tenant_id, period_start, limit_code) )

-- Coste real del LLM: llm_calls ya tiene tenant_id, task, prompt_version, model,
-- input_tokens, output_tokens, cost_eur NUMERIC(10,6) y langfuse_trace_id.
-- Sin columna feature (ver §4.8). Aplazado (Backlog P2b-20):
llm_calls + ( cached_input_tokens INT )

-- Contratos (añadir a la entidad existente)
contracts + (
  -- status (ya existe) = procesado del fichero: pending | processing | ready | failed | reviewed
  --   + quota_pending (nuevo, también en facturas y tickets)
  lifecycle TEXT DEFAULT 'active', -- estado para el usuario: 'active' | 'replaced' | 'archived'
  replaces_contract_id FK NULL,
  file_sha256 TEXT,                -- índice (tenant_id, file_sha256)
  page_count INT, upload_units INT,
  deleted_at TIMESTAMPTZ NULL,    -- borrado diferido: no nulo = borrado
  provider, contract_type, start_date, end_date,
  auto_renewal BOOL, notice_days INT, cancel_deadline DATE,
  amount_cents INT, periodicity TEXT
)

-- [Fase posterior] Packs
credit_packs ( id, tenant_id, kind TEXT,   -- 'documents'
               quantity INT, remaining INT, purchased_at TIMESTAMPTZ,
               expires_at TIMESTAMPTZ,      -- purchased_at + 12 meses
               price_cents INT, granted_by TEXT )  -- 'sadm' | 'cobro'
```

Para el asistente y las citas (`messaging_channels`, `end_customers`, `assistant_conversations`, `assistant_messages`, `services`, `appointments`, `business_hours`, `reminders`): **usa lo que ya exista** por `appointments` y los canales. Añade solo lo que falte para las reglas de la sección 4.6, por ejemplo la restricción `EXCLUDE USING gist` contra solapamientos de citas si el negocio no admite citas simultáneas.

---

## 7. Puntos de control (ampliar `plan_quota_service`)

- `check(tenant_id, limit_code, qty)` devuelve el uso, el límite, el porcentaje y si está permitido.
- `consume(tenant_id, limit_code, qty, source_id)` es **atómico**: `UPDATE ... SET used = used + :qty WHERE used + :qty <= :limit RETURNING used`, o `INCR` con Lua en Redis y persistencia. Se aplica en una sola transacción:
  1. Descontar del cupo del plan.
  2. **[Fase posterior]** Si no hay hueco, descontar de packs vigentes (`FOR UPDATE SKIP LOCKED`, del más antiguo al más nuevo).
  3. Si no queda nada, el elemento pasa a `quota_pending`.
  4. Guardar `source` en `usage_events` para poder devolver la unidad al sitio correcto.
- `refund(event_id)`.
- **Bolsa compensable:** un límite virtual `documents_per_month = invoices_per_month + tickets_per_month`.
- **Presupuesto:** `consume_budget(tenant_id, cost_eur)` después de cada llamada, y `check_budget()` antes de las llamadas caras.
- **Interfaz:** un componente Jinja con barras de consumo y banners al 80 % y al 100 % (HTMX).

---

## 8. Parámetros de referencia (hoja de costes)

**Modelos por tarea** (`DEFAULT_MODELS` en `app/llm/client.py`, entorno dev; no hay modelos por tenant):

| Tarea | Modelo | Uso | Tarifa (€ / millón, entrada / salida) |
|---|---|---|---|
| `extraction` | `gemini-3.8-flash` (razonamiento bajo) | Facturas, tickets, contratos, pólizas y texto de imágenes del conocimiento | 1,38 / 6,90 |
| `classify` | `claude-haiku-4-5` | Tipo de documento cuando la regla automática no basta | 0,90 / 4,50 |
| `chat` | `gemini-3.5-flash-lite` | Chat de la app y asistente de WhatsApp/Telegram | 0,28 / 2,30 |
| `embedding` | `voyage-3-lite` (512 dimensiones) | Conocimiento (indexar y buscar) y caché de respuestas de los canales | 0,018 / — |
| `sql` | `claude-sonnet-4-6` | Analista (Premium). Reservada, sin uso | 2,80 / 14,00 |
| `transcription`, `translate` | `gemini-2.5-flash` | Voz para calendario (fuera de oferta, D012) y traducción (sin uso) | Fuera de este cálculo |

**Coste por operación** (tokens medios reales de `llm_calls` en dev; base limpiada el 28/09/2026, muestra pequeña):

| Operación | Muestras | Tokens entrada / salida | Coste |
|---|---:|---|---:|
| Extraer una factura | 1 | 2.634 / 227 | ≈0,0052 € |
| Extraer un ticket | 4 | 1.507 / 121 | ≈0,0029 € |
| Extraer un contrato o póliza corto | 1 + 1 | ≈1.700 / ≈200 | ≈0,0037 € |
| Extraer un contrato de ≈10 páginas | — | 15.000 / 800 (**estimado**) | ≈0,026 € |
| Clasificar el tipo de documento (Haiku) | 27 | 732-2.165 / 63-142 | 0,0009-0,0026 € |
| Pregunta de chat (≈2 llamadas) | 8 | 3.286 / 96 por llamada | ≈0,002 € (máx. ≈0,0045 €) |
| Mensaje del asistente (≈2 llamadas) | — | 4.000 / 150 por llamada (**estimado**) | ≈0,003 € |
| Embedding (búsqueda o indexado) | 5 | 1.034 | ≈0,00002 € |
| Pregunta al analista | — | 8.000 / 1.000 (**estimado**) | ≈0,036 € |

- La salida de Gemini ya incluye los tokens de razonamiento (`_extract_token_usage` en `client.py`).
- **Pendiente de medir:** contratos de 30-100 páginas, asistente de canales y analista. Propuesta: ejecutar los evals de extracción (facturas, tickets, contratos, pólizas) y `chat_documents_v2` contra `saas_test` para tener decenas de muestras por tipo (los documentos de los evals son sintéticos y pueden ser más cortos que los reales).
- **Precio de Gemini 3.x Flash:** tuvo precio introductorio hasta el 31/12/2026. Comprobar que `pricing.py` usa la tarifa que se pagará en 2027.

## 9. Producto mínimo: orden sugerido

1. Seed: nuevos límites y valores de `members_max` (hecho, D022) y `llm_budget_eur_month` (hecho, D026). `analytics` en Premium se añade al retomar el analista (D018).
2. ~~Registro de coste en `llm_calls` y presupuesto con aviso al 80 % y tope al 100 % + override en SADM.~~ Hecho (P2b-14, D019, D026). Falta la ampliación mensual del presupuesto (Backlog P2b-27).
3. `plan_quota_service` mensual: consumo atómico, devoluciones, persistencia y bolsa compensable. **Base hecha (D027):** `monthly_quota_service` + tabla `quota_usage`; conectado a facturas, tickets y reintentos (paso 4), a las preguntas del chat (D023, bloque 4) y a las altas de contratos (paso 5).
4. ~~Cuotas en facturas y tickets (`quota_pending`).~~ Hecho (bloques 2 y 3, 2026-10-01, `p80_document_quota_01`): reserva al encolar y devolución si no termina bien, `quota_pending` con su job, hash SHA-256, avisos por email al 80 % y al primer pendiente, y reintentos al mes con máximo 3 por documento. Detalles en D027.
5. ~~Contratos: estados, renovación, altas al mes con carga inicial, páginas, hash y extracción.~~ Hecho (bloque 5, 2026-10-03, `p82_contract_quota_01`). Fuera: borrado diferido y purga (Backlog P3-8) y renovación enlazada automática. Detalles en D027.
6. ~~Historial de los chats (sección 4.4).~~ Sin cambios de código: se acepta el comportamiento actual (§4.4, 2026-09-30). Medición pendiente en Backlog P2b-21.
7. Usuarios (hecho, D022) e histórico (`history_months`, bloque 6).
8. Interfaz de consumo y avisos.
9. Trabajos programados: reinicio de periodos y purga de contratos borrados (los avisos de contratos, post producto mínimo).
10. Asistente: cuotas de mensajes y recordatorios, anti-abuso, corte por presupuesto (P2b-17), límites en el contenido y derivación a una persona (sobre lo existente en `appointments` y los canales). Requisito previo: conectar las citas del canal al módulo interno (`Paso12`, D030). **[Post producto mínimo]**: WhatsApp/Telegram quedan fuera del soft launch. El conocimiento en el prompt es una optimización no comprometida (P2b-22).
11. Analista de datos (Premium). **[Post producto mínimo, D018]**
12. **[Fase posterior]** Packs, cobro, exportación CSV / Excel, avisos de contratos y niveles de soporte. La integración contable y el envío a gestorías quedan fuera por ahora.

---

## 10. Entregable de la Fase 1: informe de brechas

Crea `docs/analisis-planes-y-cuotas.md` con:

1. **Resumen** de lo que ya existe: entitlements, cuotas, SADM, `llm_calls`, `appointments`, canales, knowledge.
2. **Tabla de brechas:** una fila por cada regla de las secciones 3 y 4 → cubierta / en parte / no cubierta → fichero → cambio propuesto.
3. **Cambios en el seed** y su efecto en los tenants existentes, incluidos los alias legacy.
4. **Cambios por fichero:** nuevos y modificados, con una línea cada uno.
5. **Migraciones:** en orden, indicando cuáles rellenan datos existentes.
6. **Riesgos:** concurrencia, coherencia Redis/PostgreSQL, seguridad (tokens de Meta, webhooks, SQL del analista), datos de salud.
7. **Plan de implementación:** los pasos de la sección 9, con ficheros, pruebas y tamaño (S/M/L).
8. **Preguntas abiertas** (sección 11) y las que surjan del análisis.

---

## 11. Preguntas abiertas

- **Precios definitivos:** 22 / 49 / 99 € están en revisión y pueden subir.
- **Método de cobro** (D016).
- **Documentos de conocimiento:** ¿aplicar el mismo modelo que a los contratos? ¿Con qué límites?
- **Registrar la decisión nueva** que sustituya a D011 **al retomar el analista** (D018).
- **Premium:** con todos los topes al 100 % el coste de IA (≈41 €) supera el presupuesto de 30 €. ¿Se deja que el tope limite, se sube el presupuesto o se bajan los topes del analista y los chats? Durante el producto mínimo, sin analista (D018), el peor caso es de unos 23 € y no hay problema; decidir al retomarlo.
- ~~**`document_retries_per_month`:** confirmar que sustituye al límite diario, con máximo 3 reintentos por documento.~~ **Resuelto (D027):** sí, 40 / 150 / 400 al mes y máximo 3 por documento.
- ~~**Citas simultáneas:** ¿se admiten (varios profesionales o gabinetes)? ¿Hace falta el concepto de «profesional» o «recurso» en `appointments`?~~ **Resuelto en el código (p30):** existe `professionals` (con especialidades y horario propio); se admiten citas simultáneas de profesionales distintos y la restricción `ex_appointments_no_overlap` impide solapes del mismo profesional. No hay concepto de gabinete o sala.
