# TestPM — Pruebas para cerrar el producto mínimo

Fecha: 2026-10-01
Estado: lista viva. Una sección por fila de la tabla «Cierre del producto mínimo» de `Backlog_Priorizado.md`. Las filas ya codificadas tienen sus pruebas completas; las pendientes, solo lo previsto, y se completan al cerrar cada fila (en el mismo commit).

Objetivo: que, al marcar todas las casillas, el producto mínimo esté probado de punta a punta antes de pasar a producción. `PasosParaProduccion.md` sigue siendo la checklist de despliegue y operación; este fichero es el detalle de qué probar de cada bloque.

## Cómo usar este fichero

- **[auto]** = test automático. Se ejecuta con el comando indicado y debe salir todo en verde.
- **[dev]** = prueba manual en local, con la app conectada como `saas_app` (RLS real, `PasosParaProduccion.md` Fase 1.2) y el worker ARQ arrancado.
- **[prod]** = prueba manual tras el primer deploy (Fase 8), con un tenant de prueba. Algunas solo tienen sentido en producción (logs sin datos personales fuera de `development`, IP real detrás de Caddy, emails reales).
- Marcar cada casilla solo con evidencia (salida del comando, captura o fecha).

### Preparación común

1. Suite automática completa (debe estar en verde antes de cualquier prueba manual):

```powershell
infisical run -- uv run ruff check app tests migrations
infisical run -- uv run mypy app
infisical run -- uv run pytest tests/unit tests/integration -q -m "not real_llm"
```

2. Tenant de prueba con un admin, en el plan Básico, asignado desde `/sadm/plans`. Los topes bajos para las pruebas se ponen con **override** en `/sadm/plans/tenants/{id}` (y se quitan al terminar).
3. Consola SQL con el rol propietario (las consultas de este fichero no funcionan con `saas_app`, que no lee algunas tablas):

```powershell
# Dev
docker exec -it saas-postgres psql -U saas -d saas
```

```bash
# Prod (en la VPS, desde el directorio del repo)
infisical run --env=prod -- docker compose -f deploy/docker-compose.prod.yml exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

4. En las consultas, sustituir `<tenant>` por el id del tenant de prueba (sale en la URL de `/sadm/plans/tenants/{id}`).

---

## Fila 0 — Base de cupos mensuales (bloque 1)

**[auto]**

```powershell
infisical run -- uv run pytest tests/unit/test_billing_period.py tests/integration/test_monthly_quota_service.py tests/integration/test_plan_change_service.py tests/integration/test_plan_change_history.py tests/integration/test_plan_admin_routes.py -q
```

Cubre: periodo de mes natural en hora de España, consumo atómico (también con consumos simultáneos), bolsa facturas + tickets, devoluciones que nunca bajan de 0, ampliación solo para el mes en curso, aislamiento por RLS y cambios de plan programados.

**[dev]**

- [ ] En `/sadm/plans/tenants/{id}` aparece la tabla de cupos del mes con consumo, ampliación y tope de cada cupo.
- [ ] Ampliar un cupo (p. ej. facturas +5) → el aviso muestra el extra total y queda en la auditoría:

```sql
SELECT created_at, action, metadata FROM audit_log
WHERE tenant_id = '<tenant>' AND action = 'sadm.quota_extra_added' ORDER BY created_at DESC LIMIT 3;
```

- [ ] Primera asignación de plan a un tenant nuevo → se aplica en el acto.
- [ ] Segunda asignación (otro plan) → queda programada para el día 1 del mes siguiente y la página lo indica. Volver a pedir el plan actual (o pulsar «Anular cambio») → el cambio programado desaparece. Ambas acciones quedan en `audit_log` (`sadm.plan_change_scheduled`, `sadm.plan_change_cancelled`).

**[prod]**

- [ ] El día 1 tras un cambio programado: el tenant ya trabaja con el plan nuevo desde las 00:00 (hora de España) y, tras el cron de la hora (minuto 5), está guardado:

```sql
SELECT id, plan_code, settings -> 'scheduled_plan_change' AS pendiente FROM tenants WHERE id = '<tenant>';
```

---

## Fila 1 — Seguridad P2c 1-3 (`d099e55`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_audit_insert_only.py tests/unit/test_migrations_audit_insert_only.py tests/unit/test_log_redaction.py tests/unit/test_logs_no_personal_data.py tests/unit/test_audit_context.py tests/unit/test_deploy_config.py tests/unit/test_internal_appointment_service.py -q
```

Cubre: `saas_app` sin UPDATE/DELETE en `audit_log` (y las FK de borrado de tenant y usuario siguen funcionando), redacción de tracebacks y de logs de ARQ, guardia contra campos personales en `logger.*`, IP de auditoría solo de `request.client.host` y `--forwarded-allow-ips` limitado a la red interna.

**[dev]**

- [ ] `audit_log` es solo de inserción para la app. Debe devolver `t | f | f`:

```sql
SELECT has_table_privilege('saas_app','audit_log','INSERT'),
       has_table_privilege('saas_app','audit_log','UPDATE'),
       has_table_privilege('saas_app','audit_log','DELETE');
```

- [ ] Crear una cita interna con nombre de cliente → la entrada `scheduling.appointment_created` de `audit_log` no lleva el nombre:

```sql
SELECT metadata FROM audit_log WHERE tenant_id = '<tenant>'
  AND action = 'scheduling.appointment_created' ORDER BY created_at DESC LIMIT 1;
```

**[prod]**

- [ ] Logs sin datos personales: subir una factura, provocar un fallo (un PDF corrupto o una imagen sin texto) y revisar los logs de `api` y `worker`. No debe aparecer el nombre del fichero, importes, proveedores, emails ni teléfonos, y los tracebacks salen como tipo + `fichero:línea:función`, sin mensaje:

```bash
docker compose -f deploy/docker-compose.prod.yml logs --since 15m api worker | grep -iE "filename|@|exception|proveedor|total="
```

- [ ] El worker no registra los argumentos de los jobs: en sus logs, las líneas de inicio de job muestran `<redacted>` en lugar de los argumentos.
- [ ] IP de auditoría real y no falsificable: abrir el detalle de un documento desde el navegador y, por separado, hacer la misma petición con una cabecera falsa (`curl -H "X-Forwarded-For: 1.2.3.4" ...` con la cookie de sesión). En ambas entradas la IP es la pública real, nunca `1.2.3.4` ni `172.30.0.x`:

```sql
SELECT created_at, action, ip FROM audit_log WHERE tenant_id = '<tenant>' ORDER BY created_at DESC LIMIT 5;
```

---

## Fila 1b — Registro de actividad en BD (D029, `95dd274`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/unit/test_activity_capture.py tests/unit/test_activity_buffer.py tests/unit/test_activity_middleware.py tests/unit/test_activity_jobs.py tests/unit/test_config_activity_log.py tests/integration/test_activity_log_db.py -q
```

Cubre: lista permitida por clave y por valor (sin nombres, emails, ficheros, rutas reales ni mensajes de excepción), filas `request` / `job` / `event` / `error`, `X-Request-ID`, rutas excluidas, volcado en bloque con filas mezcladas, permisos de `saas_app`, purga con mínimo de 7 días y bloqueo de retención 0 en producción.

**[dev]**

- [ ] Con retención 0 en producción la app no arranca y explica por qué. Debe acabar en un `ValidationError` con «ACTIVITY_LOG_RETENTION_DAYS=0», «RGPD» y «recomendado: 90» (los valores se pasan en el código para que no los pise Infisical `dev`):

```powershell
infisical run -- uv run python -c "from app.config import Settings; Settings(app_env='production', activity_log_retention_days=0, clerk_jwks_url='', webhook_allow_unsigned=False, langfuse_capture_content=False)"
```

**[prod]**

- [ ] Tras navegar un rato y subir un documento, hay filas recientes de peticiones (`api`) y de jobs (`worker`):

```sql
SELECT kind, source, count(*), max(occurred_at) FROM activity_log
WHERE occurred_at > now() - interval '1 hour' GROUP BY kind, source;
```

- [ ] `saas_app` solo inserta. Debe devolver `t | f | f | f`:

```sql
SELECT has_table_privilege('saas_app','activity_log','INSERT'),
       has_table_privilege('saas_app','activity_log','SELECT'),
       has_table_privilege('saas_app','activity_log','UPDATE'),
       has_table_privilege('saas_app','activity_log','DELETE');
```

- [ ] Cada respuesta lleva `X-Request-ID` y con él se sigue la petición y los jobs que lanzó:

```bash
curl -s -o /dev/null -D - https://<dominio>/login | grep -i x-request-id
```

```sql
SELECT occurred_at, kind, source, name, status_code, outcome, location, data
FROM activity_log WHERE request_id = '<uuid>' OR parent_request_id = '<uuid>' ORDER BY occurred_at;
```

- [ ] No hay datos personales en `data` (debe salir 0):

```sql
SELECT count(*) FROM activity_log WHERE data::text LIKE '%@%';
```

- [ ] La purga existe y el cron la ejecuta a diario (03:30): al día siguiente del deploy hay una fila `job` de `purge_activity_log`:

```sql
SELECT occurred_at, outcome, data FROM activity_log WHERE kind = 'job' AND name = 'purge_activity_log'
ORDER BY occurred_at DESC LIMIT 3;
```

---

## Fila 2 — Facturas y tickets (bloque 2, `adb8eed`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_document_quota.py tests/unit/test_document_quota_units.py tests/unit/test_document_type_confirm.py tests/unit/test_document_upload_routing.py tests/unit/test_document_delete_service.py tests/integration/test_invoice_upload.py tests/integration/test_plan_quota_documents.py -q
```

Cubre: reserva al encolar y bolsa compartida, pendiente al 100 %, duplicados por hash (también de un fallido y de un oculto), devolución al fallar, al abandonar por atasco y al borrar en el mes, presupuesto de IA agotado, procesado de pendientes del más antiguo al más nuevo, avisos del 80 % y del primer pendiente, encolado después del commit y cambio de tipo factura ↔ ticket.

**[dev]** (tenant de prueba con override `invoices_per_month` = 2 y `tickets_per_month` = 0)

- [ ] Subir el mismo fichero dos veces → la segunda vez sale «ya está subido como factura (subido el dd/mm/aaaa). No se ha vuelto a procesar ni consume cupo» y no aparece una fila nueva.
- [ ] Subir 3 facturas distintas → las 2 primeras se procesan; la tercera aparece como «Pendiente de cupo» y la subida muestra el aviso de pendiente.
- [ ] Ampliar facturas en 1 desde `/sadm/plans/tenants/{id}` → en unos segundos la pendiente pasa a procesarse (recargar el panel: la fila pendiente no hace polling).
- [ ] Borrar una factura procesada este mes → el consumo del mes baja en 1.
- [ ] El consumo cuadra con los documentos reservados (las dos columnas iguales):

```sql
SELECT
  (SELECT COALESCE(SUM(used), 0) FROM quota_usage
    WHERE tenant_id = '<tenant>' AND period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date
      AND code IN ('invoices_per_month', 'tickets_per_month')) AS consumo,
  (SELECT count(*) FROM invoices WHERE tenant_id = '<tenant>'
      AND quota_period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date)
  + (SELECT count(*) FROM tickets WHERE tenant_id = '<tenant>'
      AND quota_period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date) AS reservados;
```

- [ ] Cambio de tipo (fallo corregido en este bloque): subir como factura un ticket claro → aparece «confirmar tipo» → «Cambiar a Ticket» → la fila pasa a ticket y se procesa, sin error.
- [ ] Presupuesto de IA agotado (override `llm_budget_eur_month` = 0,01 y una extracción) → la siguiente factura queda «Pendiente: se ha agotado el presupuesto de IA del mes» y no falla. Al quitar el override, el cron de la hora siguiente (minuto 10) la procesa.
- [ ] Procesado excepcional desde `/sadm/documents` de un documento rechazado por páginas → se procesa y no cambia el consumo del mes.
- [ ] «Mi cuenta» (`/settings/profile`) ya no muestra «Documentos procesados al día».

**[prod]** (con SMTP configurado)

- [ ] Con el cupo al 80 % llega al admin el email «Aviso: has usado el 80 % de las facturas y tickets de este mes», y con el primer pendiente, «Has agotado las facturas y tickets de este mes». Solo uno de cada al mes (repetir subidas no manda más).
- [ ] El cron de pendientes corre cada hora: hay filas `job` de `process_quota_pending`:

```sql
SELECT occurred_at, outcome, data FROM activity_log WHERE kind = 'job' AND name = 'process_quota_pending'
ORDER BY occurred_at DESC LIMIT 5;
```

- [ ] El día 1 los pendientes del mes anterior se procesan solos en la primera hora.

---

## Fila 3 — Reintentos (bloque 3, `adb8eed`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_document_quota.py -k retry -q
infisical run -- uv run pytest tests/unit/test_document_processing_service.py tests/unit/test_document_quota_units.py -q
```

Cubre: reintento del mes y por documento, «Revisión manual» tras 3, reintentos gratis para fallos que no causa el usuario, rechazo sin cupo de documentos sin gastar nada, reintento de un documento atascado y bloqueo de los rechazos por límites.

**[dev]**

- [ ] Con un documento que falla siempre (p. ej. una imagen sin texto subida como factura), «Reintentar» 3 veces → la fila muestra «Revisión manual» y ya no ofrece «Reintentar».
- [ ] Con override `document_retries_per_month` = 0, «Reintentar» responde «Has agotado los reintentos de procesado de este mes. Se renuevan el 1 de ...».
- [ ] Con el cupo de facturas lleno (override), reintentar una factura fallida responde «No se puede reintentar: has usado todas las facturas y tickets de este mes» y no cambia el contador del documento:

```sql
SELECT id, status, error_code, manual_retry_count FROM invoices WHERE tenant_id = '<tenant>'
ORDER BY updated_at DESC LIMIT 5;
```

- [ ] Un reintento de un fallo del proveedor no cuenta (simulado): poner a mano `error_code = 'provider_overload'` en una factura fallida, reintentar y comprobar que `manual_retry_count` no sube.

---

## Fila 4 — Chat (bloque 4, `b69e936`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_plan_quota_chat.py tests/unit/test_chat_service.py tests/unit/test_llm_retry.py tests/unit/test_chat_tools.py tests/integration/test_chat_flow.py -q
```

Cubre: una pregunta por turno hasta el tope, respuesta fija al 100 % con renovación y contacto del admin, devolución si el proveedor falla, el corte por presupuesto no consume, la ampliación del SADM reabre el chat y el límite de ritmo por minuto y por hora (por usuario y tenant).

**[dev]** (tenant de prueba con override `chat_questions_per_month` = 2)

- [ ] 2 preguntas se responden; la tercera recibe «Has alcanzado las preguntas de este mes. Se renuevan el 1 de ...» con el teléfono y el email del admin, y no se añade coste en `llm_calls`.
- [ ] Ampliar el cupo del chat en 1 desde `/sadm/plans/tenants/{id}` → la siguiente pregunta se responde.
- [ ] Más de 10 preguntas en un minuto → «Estás enviando preguntas muy seguidas...» y el mensaje no se guarda.
- [ ] Con el presupuesto de IA al 90 % (override bajo de `llm_budget_eur_month`), el chat responde con el mensaje de contacto de D019 y el consumo de preguntas no sube.
- [ ] El consumo cuadra con las preguntas respondidas:

```sql
SELECT used, extra FROM quota_usage WHERE tenant_id = '<tenant>' AND code = 'chat_questions_per_month'
  AND period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date;
```

- [ ] «Mi cuenta» ya no muestra «Mensajes de chat al día».

**[prod]**

- [ ] `CHAT_DAILY_MESSAGE_LIMIT` y `CHAT_USER_DAILY_MESSAGE_LIMIT` no están en Infisical `prod`.

---

## Fila 5 — Contratos (bloque 5, `99132d3`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_contract_quota.py tests/unit/test_contract_quota_units.py tests/integration/test_sadm_plan_routes.py tests/integration/test_monthly_quota_service.py tests/unit/test_document_upload_routing.py tests/unit/test_document_jobs_llm_failure.py tests/unit/test_deploy_config.py -q
```

Cubre: tramos de 1/2/3 altas por páginas y su configuración (`CONTRACT_UPLOAD_PAGE_TIERS`, valores mal formados no arrancan); carga inicial contada en el mes del alta y fecha de renovación al terminar la ventana; bolsa mensual y `quota_pending` al tope; rechazo por páginas, por duplicado (también de un sustituido) y por archivo de activos lleno, sin R2; vencidos, sustituidos y fallidos no ocupan hueco; devolución exacta de altas al fallar y salida de pendientes; contratos y facturas pendientes no se bloquean entre sí; borrar un procesado no devuelve altas; presupuesto de IA agotado → pendiente; ampliación del SADM solo en la bolsa en uso; avisos `contracts_warning` / `contracts_exhausted`; reintento con hueco y nueva reserva; sustituir y reactivar (con hueco) auditados y sus rutas; el chat excluye sustituidos salvo `incluir_sustituidos`; el job usa el máximo de páginas del plan, timeout de 600 s y 660 s antes de darlo por interrumpido.

**[dev]** — tenant de prueba fuera de la carga inicial y con overrides bajos en `/sadm/plans/tenants/{id}` (`contracts_active_max` = 2, `contract_uploads_per_month` = 3). Para sacarlo de la carga inicial:

```sql
UPDATE tenants SET created_at = now() - interval '1 year' WHERE id = '<tenant>';
```

- [ ] Subir un contrato de 31-60 páginas → consume 2 altas (`upload_units` = 2). Uno de 1 página → 3 de 3. El siguiente queda «Pendiente de cupo» con «has usado todas las altas de contratos disponibles» y sin polling.
- [ ] Con 2 contratos vigentes, subir otro → «Has llegado al máximo de 2 contratos vigentes de tu plan. Si es una renovación, marca primero el contrato anterior como sustituido.», sin fila nueva.
- [ ] Desplegar el detalle de un contrato procesado → «Marcar como sustituido» (pide confirmación) → la fila muestra «Sustituido» y ya se puede subir otro contrato.
- [ ] Con el archivo lleno, «Volver a vigente» en el sustituido → «No se puede volver a marcar como vigente...». Con hueco, vuelve a vigente.
- [ ] Chat: «¿qué contratos tengo con <parte>?» no lista el sustituido; «¿y los contratos anteriores?» sí.
- [ ] Volver a subir el mismo fichero (también si está sustituido) → «ya está subido como contrato».
- [ ] PDF de más de 100 páginas → «tu plan admite contratos de hasta 100 páginas», sin fila nueva ni objeto en R2.
- [ ] Ampliar «Altas de contratos al mes» en 1 desde SADM → en unos segundos el pendiente pasa a procesarse (recargar el panel).
- [ ] **Medir** la duración de un contrato real de 80-100 páginas (Backlog P3-9): anotarla aquí. Si se acerca a 600 s, avisar antes de producción.

```sql
SELECT name, duration_ms, outcome FROM activity_log
WHERE kind = 'job' AND name = 'process_contract' AND tenant_id = '<tenant>'
ORDER BY created_at DESC LIMIT 5;
```

- [ ] Carga inicial: con un tenant recién creado (sin el `UPDATE` anterior), `/sadm/plans/tenants/{id}` muestra «En carga inicial de contratos hasta el ...»; ampliar «Altas de contratos al mes» se rechaza. En el tenant fuera de la carga inicial, ampliar «Altas de contratos en la carga inicial» → «terminó el ...».
- [ ] Altas, reservas y auditoría cuadran (las altas usadas del mes = suma de `upload_units` de los contratos con `quota_period` del mes):

```sql
SELECT code, period, used, extra FROM quota_usage
WHERE tenant_id = '<tenant>' AND code LIKE 'contract_uploads%';

SELECT lifecycle, status, quota_code, quota_period, upload_units, page_count, fecha_fin
FROM contracts WHERE tenant_id = '<tenant>' ORDER BY created_at;

SELECT action, created_at FROM audit_log
WHERE tenant_id = '<tenant>' AND action IN ('contract.replaced', 'contract.reactivated')
ORDER BY created_at DESC;
```

**[prod]**

- [ ] `alembic current` = `p82_contract_quota_01` (o posterior).
- [ ] `CONTRACT_UPLOAD_PAGE_TIERS` no está en Infisical `prod` salvo que se quieran otros tramos (por defecto `30,60`).
- [ ] Subir un contrato de prueba de varias páginas → se procesa; la fila muestra el detalle y «Marcar como sustituido».

## Fila 6 — Histórico (bloque 6, `432d7e6`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_document_history.py tests/unit/test_document_history_units.py tests/integration/test_document_query.py tests/unit/test_document_chat_tools.py tests/unit/test_chat_tool_security.py -q
```

Cubre: corte por meses completos (cambios de año incluidos); valores del catálogo (12 / 36 / sin límite) y override de 0 rechazado; sin el límite en el catálogo se aplican 12 meses; facturas y tickets antiguos ocultos en el panel, búsqueda, agregación y contrapartes del chat, y `get_document` responde «no encontrado»; sin fecha de emisión cuenta la de subida; subir de plan vuelve a mostrar lo oculto; contratos vigentes siempre visibles, vencidos y sustituidos por fecha de fin o `replaced_at`; `replaced_at` se rellena al sustituir y se borra al reactivar; el duplicado de un documento oculto se rechaza explicando el histórico.

**[dev]** — tenant Básico con una factura y un ticket procesados. Para envejecer la factura:

```sql
UPDATE invoices SET fecha = now() - interval '14 months' WHERE id = '<factura>';
```

- [ ] La factura desaparece de `/documents` al recargar; el ticket reciente sigue.
- [ ] En el chat, «¿cuánto me facturó <proveedor de esa factura>?» no la cuenta y no la cita.
- [ ] Volver a subir el mismo fichero → «ya está subido como factura ... Queda fuera de los 12 meses de histórico de tu plan, por eso no lo ves.»
- [ ] Override `history_months` = 36 en `/sadm/plans/tenants/{id}` → la factura vuelve a verse en el panel y en el chat. Quitar el override.
- [ ] Override `history_months` = 0 → el SADM lo rechaza («history_months must be at least 1»).
- [ ] Contratos: uno vigente con fecha de inicio de hace años se ve; uno vencido hace más de 12 meses no:

```sql
UPDATE contracts SET fecha_fin = now() - interval '14 months' WHERE id = '<contrato>';
```

- [ ] «Mi cuenta» muestra «Meses de histórico de facturas y tickets: 12» (Premium: «Ilimitado»).
- [ ] Pendiente de decisión (D017, punto 9): una factura de hace más de 12 meses subida hoy se procesa, consume cupo y queda oculta sin aviso. Revisar cuando se decida.

**[prod]**

- [ ] `alembic current` = `p83_history_months_01` (o posterior) y el catálogo tiene el límite:

```sql
SELECT p.code, pe.limit_value FROM plan_entitlements pe JOIN plans p ON p.id = pe.plan_id
WHERE pe.kind = 'limit' AND pe.code = 'history_months' ORDER BY p.code;
```

## Fila 7 — Consumo en «Mi cuenta» (bloque 7, `17e0975`)

**[auto]**

```powershell
infisical run -- uv run pytest tests/integration/test_quota_status.py tests/unit/test_quota_status_units.py tests/unit/test_settings_profile_plan.py tests/unit/test_plan_usage_and_channel_slots.py tests/integration/test_chat_web.py tests/integration/test_sadm_plan_routes.py -q
```

Cubre: niveles y porcentaje (80 % aviso, 100 % agotado, tope 0 = no incluido); «X de Y» de la bolsa de facturas y tickets con desglose y ampliación del SADM; altas de contratos en carga inicial o mensual; contratos vigentes; avisos de `/documents` al 80 % y al 100 % y con el archivo de contratos lleno; `/chat` solo al 100 %; un fallo al calcular los avisos no hace caer la página; uso de IA en % y sin euros; marca «≥ 80 %» / «100 %» del SADM; colores de las barras.

**[dev]** — tenant de prueba con overrides bajos en `/sadm/plans/tenants/{id}`: `invoices_per_month` = 5, `tickets_per_month` = 0, `chat_questions_per_month` = 2.

- [ ] «Mi cuenta» muestra «Consumo del mes» con facturas y tickets, reintentos, preguntas al chat, altas de contratos (con «carga inicial» si el tenant es nuevo) y contratos vigentes, cada uno con «se renueva el …». El uso de IA sale en % y en ninguna parte de la página aparece «€».
- [ ] Subir 4 facturas → `/documents` muestra el aviso ámbar «Has usado 4 de 5 facturas y tickets…» sin recargar. La 5.ª → aviso rojo «Has agotado…»; la 6.ª queda «Pendiente de cupo».
- [ ] Ampliar el cupo en 5 desde el SADM → al recargar, «Mi cuenta» muestra el tope 10 y el aviso pasa a ámbar o desaparece.
- [ ] Hacer 2 preguntas en el chat → al recargar `/chat`, aviso rojo «Has usado las 2 preguntas de este mes…». Con 1 de 2 no hay aviso.
- [ ] `/sadm/plans` marca el tenant con «100 %» (o «≥ 80 %»); un tenant sin consumo no lleva marca.
- [ ] Con override `contracts_active_max` = 1 y un contrato vigente, `/documents` muestra «Tienes 1 contratos vigentes, el máximo de tu plan…».

```sql
SELECT code, period, used, extra FROM quota_usage
WHERE tenant_id = '<tenant>' AND period = date_trunc('month', now() AT TIME ZONE 'Europe/Madrid')::date
ORDER BY code;
```

**[prod]**

- [ ] «Mi cuenta» del tenant piloto muestra el consumo del mes sin euros y la lista de `/sadm/plans` carga sin errores con todos los tenants (una consulta por tenant; revisar el tiempo de carga si hay muchos).

## Fila 8 — Cierre del código

Pendiente. Previsto: suite completa en verde en CI, smoke en dev con `saas_app` (`PasosParaProduccion.md` Fase 1.2: login, subir documento, chat, conocimiento, citas y `/sadm` sin errores de permisos ni de RLS) y PR #1 fusionado en `main` sin la etiqueta `eval-regression-accepted`.

## Fila 9 — Ops de despliegue

Se prueba con `PasosParaProduccion.md` Fases 2-13 (no se duplica aquí). Antes de la firma Go/No-Go (Fase 13), todas las casillas **[prod]** de este fichero deben estar marcadas.
