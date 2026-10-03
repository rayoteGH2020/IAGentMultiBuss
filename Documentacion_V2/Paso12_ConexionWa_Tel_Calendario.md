# Paso12 - Citas por WhatsApp y Telegram sobre el calendario del centro

Estado: **pendiente** (definido el 2026-09-30; decisiones en D030). **Fuera del producto mínimo**: WhatsApp y Telegram no entran en el soft launch (`Backlog_Priorizado.md`). Rama sugerida: `paso/12-channel-appointments`.

Objetivo: que el asistente de canales (WhatsApp y Telegram) consulte servicios y profesionales, busque huecos y cree, cambie, cancele y consulte citas **sobre el módulo interno de citas** (`appointments`, horario del centro, profesionales, servicios). Google Calendar deja de intervenir en los canales.

## 1. Contexto verificado en el código (2026-09-30)

| Pieza | Estado |
|---|---|
| Módulo interno de citas | Hecho. Tablas `business_hours`, `schedule_exceptions`, `services`, `professionals`, `professional_specialties`, `professional_working_hours` y `appointments` (migración `p30_internal_scheduling_01`, RLS `ENABLE` + `FORCE`). Servicios `internal_appointment_service`, `appointment_slot_service`, `professional_service`, `service_catalog_service`, `business_hours_service`. |
| Solapes | La restricción `ex_appointments_no_overlap` (GiST) impide dos citas activas del mismo profesional a la vez, **solo si `professional_id` no es nulo**. El servicio no traduce la violación a un error de dominio. |
| Flujo de canales | WhatsApp y Telegram comparten el mismo camino: webhook → `enqueue_channel_message` → `channel_jobs.process_channel_message` → `channel_chat_service.answer_for_channel`. Solo cambia el envío de la respuesta. |
| Tools del canal | `build_channel_registry()` (`app/llm/tools/knowledge_tools.py`) registra las tools de conocimiento y las de `calendar_tools.py`, que llaman a `calendar_service` → **Google Calendar**. El módulo interno no se usa. |
| Prompt | `channel_external_v1.txt` pide inicio y fin de la cita al estilo de Google Calendar. No conoce servicios, profesionales ni huecos. |
| Identidad del cliente final | `customer_identifier`: teléfono E.164 en WhatsApp (verificado por Meta); `chat_id` en Telegram (no incluye teléfono). |
| Origen de las citas | `appointments.source` solo toma el valor `"manual"`. |

**Problemas que corrige este paso:**

1. **Producto.** Avanzado y Premium venden «citas + WhatsApp/Telegram» (D012), pero el canal reserva en Google Calendar, que está fuera de la oferta.
2. **Privacidad (bloqueante).** `list_appointments` del canal devuelve todas las citas próximas del tenant, y `cancel_appointment` acepta cualquier `event_id`. Un cliente final podría ver o cancelar citas de otros clientes (en clínicas, posibles datos de salud).
3. **Escalado por confianza.** La confianza del turno sale solo de las citas de `search_knowledge` (`answer_for_channel`). Un turno de reserva sin consulta de conocimiento tiene confianza 0, por debajo del umbral (0,5 por defecto), y `channel_jobs` sustituye la respuesta por «no tengo información sobre eso» y envía un email de escalado al admin. Tal como está, ninguna gestión de citas llegaría al cliente.
4. **Caché semántica.** `channel_response_cache` puede guardar una respuesta de un turno con datos de citas y servírsela a otro cliente. Hoy lo evita solo que esos turnos no suelen tener citas de conocimiento.

## 2. Dependencias

- Paso01 (webhooks firmados y deduplicados) y Paso03 (gates por plan): hechos.
- Módulo interno de citas (p30): hecho.
- No depende de los cupos mensuales ni del cobro.

## 3. Decisiones

Cerradas el 2026-09-30 y registradas en `Decision_Log.md` como **D030**.

| # | Decisión | Resultado |
|---|---|---|
| 1 | Google Calendar fuera de los canales | Se retira `calendar_tools` del canal. El código de Google Calendar de la app (`/calendar`, voz) no se toca (D012: se conserva sin evolucionar). |
| 2 | Identidad del cliente final | Clave de propiedad = `(channel, channel_customer_id)`, tomada **siempre del webhook**, nunca de argumentos del modelo. |
| 3 | Citas creadas en la app para el mismo cliente | **WhatsApp:** el cliente ve, cambia y cancela también las citas cuyo teléfono normalizado coincide con su número (Meta lo verifica); p. ej. puede preguntar por su próxima cita aunque la diera el centro por teléfono y cambiarla. **Telegram:** solo las creadas desde ese chat, porque el teléfono lo escribe el cliente y no está verificado. |
| 4 | Estado de la cita creada por canal | `scheduled`. El centro la pasa a `confirmed` desde la app si trabaja así. |
| 5 | Antelación mínima para cambiar o cancelar desde el canal | Parámetro por tenant `channel_min_notice_hours`, **24 horas por defecto**, editable por el admin. Dentro de ese plazo el asistente no cambia ni cancela y da el teléfono del negocio. No afecta a la app ni a la creación de citas. |
| 6 | Tenant con canal pero sin `appointments` (p. ej. override) | No se registran las tools de citas. El asistente responde que las citas se gestionan por teléfono. |

## 4. Requisitos funcionales

El cliente final, desde WhatsApp o Telegram, puede:

1. **Consultar servicios**: nombre y duración de los servicios activos. Precios y condiciones siguen saliendo del conocimiento (`search_knowledge`).
2. **Consultar profesionales**: nombre visible de los profesionales reservables y qué servicios hacen (especialidades). Nunca email, usuario ni datos internos.
3. **Buscar huecos**: para un servicio, opcionalmente con un profesional y a partir de una fecha. Devuelve como máximo 5 huecos reales, calculados por `appointment_slot_service.find_next_available_slots`. Respeta el horario del centro, las excepciones, el horario del profesional, la granularidad, el margen entre citas y el horizonte de búsqueda del tenant.
4. **Reservar**: con servicio, profesional, hora de inicio (uno de los huecos ofrecidos) y nombre. En Telegram también pide el teléfono; en WhatsApp se usa el del remitente. Antes de crear, el asistente resume la cita y pide confirmación explícita.
5. **Ver sus citas** («¿cuándo es mi próxima cita?»): solo las futuras y no canceladas que le pertenecen (decisión 3). En WhatsApp incluye las que el centro creó en la app con su número.
6. **Cambiar una cita suya**: a otro hueco válido, con confirmación explícita y con al menos `channel_min_notice_hours` de antelación sobre la hora actual de la cita.
7. **Cancelar una cita suya**: con confirmación explícita y la misma antelación mínima.

Reglas:

- Todas las validaciones de disponibilidad, solapes y horario se hacen **en código** (`internal_appointment_service`). El modelo solo propone.
- Una cita creada por canal siempre lleva `professional_id` y `service_id`. Así la restricción GiST de solapes aplica también a las carreras entre dos clientes.
- Si al crear o cambiar el hueco ya no está libre (validación o violación de `ex_appointments_no_overlap`), la tool devuelve `slot_taken` y el asistente ofrece huecos nuevos.
- Las fechas y horas se muestran e interpretan en la zona horaria del tenant (`TenantSchedulingSettings.timezone`), no en la del servidor.
- Las citas del canal aparecen en `/appointments` como cualquier otra, con su origen visible (WhatsApp / Telegram).

## 5. Diseño técnico

### 5.1 Modelo de datos (migración `p78_channel_appointments_01`)

Añadir a `appointments`:

| Columna | Tipo | Uso |
|---|---|---|
| `channel` | `VARCHAR(32) NULL` | `whatsapp` \| `telegram` \| `NULL` (citas de la app) |
| `channel_customer_id` | `VARCHAR(255) NULL` | `customer_identifier` del webhook |
| `client_phone_normalized` | `VARCHAR(20) NULL` | E.164 de `client_phone` (decisión 3); `NULL` si no se puede normalizar |

- Índices `(tenant_id, channel, channel_customer_id, start_at)` y `(tenant_id, client_phone_normalized, start_at)`.
- Backfill de `client_phone_normalized` desde `client_phone` de las citas existentes (lo que no se pueda normalizar queda `NULL` y no será visible por WhatsApp).
- `CHECK (channel IS NULL) = (channel_customer_id IS NULL)`.
- `source` pasa a admitir `whatsapp` y `telegram`, además de `manual`.
- RLS: la tabla ya la tiene. Revisar a mano la migración antes del commit (AGENTS §11).

### 5.2 Servicio (`internal_appointment_service` + helper)

- `create_appointment`, `update_appointment` y `cancel_appointment` aceptan `source`, `channel` y `channel_customer_id` (con `created_by_user_id=None`), sin cambiar su comportamiento para la app.
- Nueva función `list_customer_appointments(db, tenant_id, *, channel, channel_customer_id, phone_normalized=None)`: futuras y no canceladas del cliente, ordenadas por inicio. Propiedad: `(channel, channel_customer_id)` coincide, **o** (solo WhatsApp) `client_phone_normalized` = número del remitente. En Telegram `phone_normalized` va siempre `None`.
- Nueva función `get_customer_appointment(...)`: devuelve la cita solo si pertenece al cliente con la misma regla; si no, `NotFoundError` (mismo error para «no existe» y «no es tuya», para no revelar citas ajenas).
- Traducir la violación de `ex_appointments_no_overlap` (SQLSTATE `23P01`) a un error de dominio de `app.core.errors`. También beneficia a la app.
- Normalizador de teléfono a E.164 en `app/core/`, con España (+34) como país por defecto para números sin prefijo. Se aplica al crear y editar citas desde cualquier origen (rellena `client_phone_normalized`). Usa la dependencia `phonenumbers` (aprobada el 2026-09-30, D030), que cubre también números extranjeros.
- Antelación mínima (decisión 5): `channel_min_notice_hours` en `tenant.settings["scheduling"]` (`TenantSchedulingSettingsRead` / `Update`, `DEFAULT_SCHEDULING_SETTINGS` = 24, rango 0-168). Las funciones de cambio y cancelación **para canal** comprueban que la cita empieza en más de ese plazo y, si no, lanzan un error de dominio que la tool traduce a `min_notice`. Las rutas de la app no pasan por esta comprobación.
- Límite anti-abuso: máximo de citas futuras activas por cliente de canal (`CHANNEL_MAX_ACTIVE_APPOINTMENTS_PER_CUSTOMER`, por defecto 3). Al superarlo, la tool devuelve `too_many_appointments` y el asistente da el teléfono del negocio.

### 5.3 Tools del canal (`app/llm/tools/scheduling_tools.py`)

Nueva familia `ToolFamily.scheduling`, exclusiva de canales.

| Tool | Argumentos del modelo | Llama a |
|---|---|---|
| `list_services` | — | `service_catalog_service` (activos) |
| `list_professionals` | `service_id?` | `professional_service.list_bookable_professionals` + especialidades |
| `find_available_slots` | `service_id`, `professional_id?`, `after?` (fecha local) | `appointment_slot_service.find_next_available_slots` (`count` ≤ 5) |
| `create_appointment` | `service_id`, `professional_id`, `start_at`, `client_name`, `client_phone` (solo Telegram) | `internal_appointment_service.create_appointment` |
| `list_my_appointments` | — | `list_customer_appointments` |
| `reschedule_my_appointment` | `appointment_id`, `start_at`, `professional_id?` | `get_customer_appointment` + `update_appointment` |
| `cancel_my_appointment` | `appointment_id`, `reason?` | `get_customer_appointment` + `cancel_appointment` |

Reglas de las tools:

- **La identidad nunca viene del modelo.** `ToolContext` gana los campos opcionales `channel` y `channel_customer_id`, que rellena `answer_for_channel` desde el job. Si faltan, las tools de citas devuelven error sin tocar la BD.
- En WhatsApp, `client_phone` se toma del contexto y se ignora el argumento.
- Argumentos Pydantic con `extra="forbid"`. Los `start_at` sin zona se interpretan en la zona del tenant.
- Los resultados solo incluyen lo necesario: id de la cita, servicio, profesional (nombre visible), inicio, fin y estado. Nunca datos de otros clientes ni `notes` internas.
- Errores con código estable (`slot_taken`, `not_found`, `past_day`, `too_many_appointments`, `min_notice`, `scheduling_unavailable`) y **sin `str(exc)`** en `data` (hoy `calendar_tools` lo devuelve al modelo).

### 5.4 Registry y servicio de canal

- `build_channel_registry(*, scheduling_enabled: bool)`: familias `knowledge` y, si `scheduling_enabled`, `scheduling`. Sin `calendar`.
- `scheduling_enabled` = el tenant tiene `appointments` (`entitlement_service`).
- `answer_for_channel` construye el `ToolContext` con canal e identidad.
- **Caché semántica:** no guardar en `channel_response_cache` ningún turno en el que se haya ejecutado una tool de `scheduling` (`ToolLoopResult.tool_calls_executed`).
- **Confianza y escalado:** un turno en el que se ejecutó con éxito alguna tool de `scheduling` no se escala por falta de citas de conocimiento. `ChannelResponse` indica que el turno fue de citas y `channel_jobs` envía la respuesta sin aplicar el umbral. Los turnos solo de conocimiento siguen igual. Si todas las tools de citas del turno fallaron, se aplica el umbral como hoy.
- Borrar `app/llm/tools/calendar_tools.py` y `tests/unit/test_calendar_tools.py` si, tras el cambio, no quedan usos (hoy solo lo usa el canal; `ToolFamily.calendar` sigue para el chat de la app con `calendar_google`).

### 5.5 Prompt `channel_external_v2.txt`

- Nuevo fichero versionado. `_CHANNEL_PROMPT_VERSION = "channel_external_v2"`. `v1` se conserva para trazabilidad.
- Variable `scheduling_enabled`: sin citas, el asistente no ofrece reservar y da el teléfono del negocio.
- Flujo guiado: servicio → (profesional opcional) → huecos → datos → resumen → confirmación explícita → acción.
- Nunca inventar huecos ni confirmar una cita sin el resultado `ok` de la tool.
- No revelar datos de otras citas ni de otros clientes.
- **Caché implícita de Gemini** (spec §4.6): la fecha y hora actuales van con el mensaje nuevo, no en el system prompt (hoy `current_datetime` va en el system prompt y rompe esa regla).

### 5.6 Interfaz

- `/appointments`: distintivo de origen (WhatsApp / Telegram) en la cita. Sin lógica de negocio en la plantilla: la etiqueta sale de un filtro o del contexto.
- `/settings/business-hours`: campo «Antelación mínima para cambiar o cancelar por WhatsApp/Telegram (horas)» junto a los ajustes de agenda existentes (zona horaria, granularidad, margen). Solo admin y co_admin; el cambio se audita como el resto de ajustes de agenda.

## 6. Seguridad, RGPD y auditoría

- **Aislamiento por cliente:** toda lectura o mutación de citas desde el canal filtra por la identidad del contexto. Tests obligatorios de cliente A contra cliente B y de tenant A contra tenant B.
- **Auditoría (AGENTS §7):** crear, cambiar y cancelar ya se auditan en el servicio; añadir a `metadata` `source` y `channel`, con `user_id=None`. `list_my_appointments` recupera contenido: auditar como consulta (`scheduling.channel_appointments_listed`) con número de citas, sin contenido.
- **Logs:** el código nuevo no registra teléfono, `chat_id`, nombre ni texto del cliente (Backlog P2c-2).
- **Langfuse:** solo metadatos, vía `app/llm/observability.py`. Los argumentos de las tools (nombre, teléfono) no se envían.
- **Datos mínimos:** nombre, teléfono, día, hora, servicio y profesional. Nada de síntomas ni motivo de consulta en `notes` desde el canal.
- **Anti-abuso:** se mantiene `channel_messages_per_hour` y se añade el límite de citas activas por cliente (5.2).

## 7. Tareas

### 7.1 Datos y servicio

- [x] Registrar D030 en `Decision_Log.md` (2026-09-30).
- [x] Decidir el normalizador: `phonenumbers` (2026-09-30).
- [ ] Añadir `phonenumbers` con `uv add phonenumbers` (actualiza `pyproject.toml` y `uv.lock`).
- [ ] Migración `p78_channel_appointments_01` (columnas, índices, check, backfill) revisada a mano.
- [ ] Modelo `Appointment` y `AppointmentRead` con los campos nuevos.
- [ ] Normalizador E.164 y relleno de `client_phone_normalized` al crear y editar desde cualquier origen.
- [ ] `internal_appointment_service`: origen de canal, `list_customer_appointments`, `get_customer_appointment`, antelación mínima para canal, límite de citas activas y traducción de `23P01`.
- [ ] `channel_min_notice_hours` en los ajustes de agenda (schema, default 24, formulario de `/settings/business-hours`).
- [ ] `CHANNEL_MAX_ACTIVE_APPOINTMENTS_PER_CUSTOMER` en `app/config.py` y en `docs/environment-variables.md`.

### 7.2 Capa LLM

- [ ] `ToolFamily.scheduling` y `ToolContext.channel` / `channel_customer_id`.
- [ ] `app/llm/tools/scheduling_tools.py` con las 7 tools.
- [ ] `build_channel_registry(scheduling_enabled=...)` sin `calendar_tools`.
- [ ] `channel_external_v2.txt` y cambio de versión.
- [ ] `answer_for_channel`: contexto con identidad, entitlement `appointments`, exclusión de caché y marca de «turno de citas».
- [ ] `channel_jobs`: no escalar los turnos de citas por falta de conocimiento.
- [ ] Retirar `calendar_tools.py` y su test si no quedan usos.

### 7.3 Interfaz

- [ ] Distintivo de origen en `/appointments`.

### 7.4 Documentación

Enlaces y estado «pendiente» ya alineados el 2026-09-30 (README, Backlog P2-6, D030, spec §4.6, Paso07, Arquitectura_V2, Planes_Entitlements, Seguridad_V2, PasosParaProduccion). Al cerrar el paso:

- [ ] `especificacion-planes-y-cuotas.md` §4.6: pasar las citas del canal de «Pendiente (Paso12)» a «Diseño actual».
- [ ] `Arquitectura_V2.md` §5: fila «Canales WA/TG» con citas internas implementadas.
- [ ] `Planes_Entitlements.md` y `PLAN_META` de `entitlement_codes.py`: Avanzado = «... + WhatsApp/Telegram (conocimiento y citas)».
- [ ] `Backlog_Priorizado.md` P2-6 a **Hecho** y `Paso07` sin la nota de Google Calendar en canales.

## 8. Tests

**Unitarios** (`tests/unit/`):

- `test_scheduling_tools.py`: cada tool con servicios mockeados; la identidad sale del contexto y no de los argumentos; en WhatsApp se ignora `client_phone`; sin identidad → error sin tocar BD; errores sin `str(exc)`; zona horaria del tenant.
- `test_channel_chat_service.py`: con `appointments` se registran las tools de citas y sin él no; nunca hay tools `calendar`; un turno con tools de citas no se guarda en caché y se marca como turno de citas.
- `test_channel_jobs.py`: un turno de citas con confianza 0 se envía al cliente sin escalado ni email; un turno solo de conocimiento por debajo del umbral se sigue escalando.
- `test_internal_appointment_service.py`: origen de canal, límite de citas activas, `23P01` → error de dominio, `get_customer_appointment` de otro cliente → `NotFoundError`, antelación mínima (a 23 h se rechaza, a 25 h se permite, con 0 siempre se permite; la app no la aplica), `client_phone_normalized` al crear y editar.
- `test_phone_normalization.py`: formatos españoles (`600123456`, `600 12 34 56`, `+34600123456`, `0034600123456`), extranjeros con prefijo y valores no normalizables → `None`.
- `test_scheduling_schemas.py`: `channel_min_notice_hours` por defecto 24 y fuera de rango rechazado.
- `test_channel_prompt_v2.py`: el prompt renderiza con y sin `scheduling_enabled` y no lleva la fecha en el system prompt.

**Integración** (`tests/integration/`, Postgres real):

- `test_channel_appointments.py`:
  - Flujo completo: buscar hueco → crear → listar → cambiar → cancelar, con `source`, `channel` y auditoría correctos.
  - Cliente B no ve, no cambia y no cancela citas del cliente A (mismo tenant).
  - Tenant B no ve citas del tenant A (RLS).
  - Dos reservas concurrentes del mismo hueco: una OK y otra `slot_taken`.
  - Decisión 3: una cita creada en la app con el teléfono `600 123 456` es visible y modificable por WhatsApp desde `+34600123456`, y no por Telegram.
  - Decisión 5: cambiar o cancelar una cita que empieza en menos de 24 h devuelve `min_notice` y no toca la BD.
  - Migración: backfill de `client_phone_normalized` sobre citas existentes.
- `test_whatsapp_webhook.py` y `test_telegram_webhook.py`: sin regresiones.

**Eval** (`app/evals/`): dataset corto de conversaciones de reserva, cambio y cancelación que mida que el modelo confirma antes de actuar y no inventa huecos. Se ejecuta con LLM real (`real_llm`), fuera de la suite por defecto.

```powershell
infisical run -- uv run pytest tests/unit/test_scheduling_tools.py tests/unit/test_channel_chat_service.py tests/unit/test_channel_jobs.py tests/unit/test_internal_appointment_service.py tests/unit/test_channel_prompt_v2.py tests/unit/test_phone_normalization.py tests/unit/test_scheduling_schemas.py -q
infisical run -- uv run pytest tests/integration/test_channel_appointments.py tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py tests/integration/test_scheduling_crud_flow.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## 9. Validación manual (Telegram primero, WhatsApp después)

Requisitos: tenant de prueba en plan `advanced`, con horario del centro, al menos un servicio y un profesional reservable con ese servicio (en `/settings/business-hours`, `/settings/services` y `/settings/professionals`).

1. **Migración aplicada.** Debe mostrar `p78_channel_appointments_01` como `head`.
   ```powershell
   infisical run -- uv run alembic upgrade head
   infisical run -- uv run alembic current
   ```
2. **Bot de Telegram conectado.** Integración activa con webhook y secreto, según `Paso01_Seguridad_Residual.md` §5. Para desarrollo local hace falta una URL pública hacia la API (túnel).
3. **Reserva.** Desde Telegram: «Quiero una cita para <servicio>». El bot debe ofrecer huecos reales, pedir nombre y teléfono, resumir y pedir confirmación. Tras confirmar, la cita aparece en `/appointments` con origen Telegram y sin solapar otra.
4. **Comprobación en BD.** Última cita de canal con su origen:
   ```powershell
   psql "<url del superusuario>" -c "SELECT id, source, channel, status, start_at FROM appointments WHERE source IN ('whatsapp','telegram') ORDER BY created_at DESC LIMIT 5;"
   ```
5. **Aislamiento.** Desde otra cuenta de Telegram: «¿Qué citas tengo?». No debe ver la cita del paso 3. Pedir cancelar esa cita por su hora: debe responder que no encuentra ninguna.
6. **Cambio y cancelación.** Desde la cuenta del paso 3: cambiar la cita a otro hueco y luego cancelarla. En `/appointments`: la hora cambia y después aparece cancelada.
7. **Auditoría.** Deben aparecer `scheduling.appointment_created`, `scheduling.appointment_updated`, `scheduling.appointment_cancelled` y `scheduling.channel_appointments_listed` con `user_id` nulo y `channel` en `metadata`:
   ```powershell
   psql "<url del superusuario>" -c "SELECT action, user_id, metadata->>'channel' AS channel, created_at FROM audit_log WHERE action LIKE 'scheduling.%' ORDER BY created_at DESC LIMIT 10;"
   ```
8. **Antelación mínima.** En `/settings/business-hours` debe aparecer el campo con valor 24. Crear en `/appointments` una cita para dentro de unas 3 horas con el teléfono de la cuenta de prueba de WhatsApp (o, en Telegram, reservarla desde el bot). Pedir al bot que la cambie: debe negarse y dar el teléfono del negocio. Bajar el valor a 0, repetir y comprobar que ahora sí la cambia. Dejarlo en 24 al terminar.
9. **Sin `appointments`.** Con un override que quite `appointments` al tenant (`/sadm/plans`), pedir una cita: el bot no ofrece reservar y da el teléfono del negocio. Retirar el override al terminar.
10. **Caché.** Tras las pruebas, no debe haber respuestas con datos de citas en la caché:
   ```powershell
   psql "<url del superusuario>" -c "SELECT question_text, left(answer_text, 80) FROM channel_response_cache ORDER BY created_at DESC LIMIT 10;"
   ```
11. **WhatsApp.** Repetir 3-8 por WhatsApp cuando el canal esté en alcance (número y webhook reales, `Paso01` §4). Comprobar que no pide teléfono y usa el del remitente.
12. **Cita del centro vista por WhatsApp.** Crear en `/appointments`, para dentro de más de 24 h, una cita con el teléfono de la cuenta de WhatsApp escrito sin prefijo (p. ej. `600 123 456`). Por WhatsApp: «¿Cuándo es mi próxima cita?» debe devolverla, y el bot debe poder cambiarla. Desde Telegram no debe aparecer. En BD, la cita tiene `client_phone_normalized` en E.164:
    ```powershell
    psql "<url del superusuario>" -c "SELECT client_phone, client_phone_normalized, source, channel FROM appointments ORDER BY created_at DESC LIMIT 5;"
    ```

## 10. Criterios de aceptación

- [ ] Ningún camino del canal llama a Google Calendar.
- [ ] Un cliente final no puede ver, cambiar ni cancelar citas de otro cliente ni de otro tenant (tests de integración verdes).
- [ ] Las citas del canal respetan horario del centro, excepciones, horario del profesional y solapes, también con reservas concurrentes.
- [ ] Sin `appointments`, el canal no expone tools de citas.
- [ ] Ningún turno con datos de citas entra en la caché semántica.
- [ ] Las respuestas de citas llegan al cliente sin escalado por falta de conocimiento.
- [ ] Auditoría, logs y Langfuse cumplen AGENTS §7 y §8.
- [ ] Suite, `ruff` y `mypy --strict` en verde; validación manual con Telegram hecha.

## 11. Fuera de alcance

Cubierto por la especificación (§4.6) y por el Backlog; no se hace en este paso:

- Recordatorios de cita (`reminders_per_month`, plantillas de Meta).
- Cupos `assistant_messages_per_month` y `end_customer_messages_per_day`.
- Corte por presupuesto de IA en canales (Backlog P2b-17).
- Límites de contenido clínico y derivación a una persona.
- Alta integrada de WhatsApp Business por negocio.
