# Paso01 - Seguridad residual P0/P1

Estado: **parcial** (2026-09-23). Controles en codigo + tests; cierre total exige Infisical prod y validaciones manuales (Clerk JWT allowlists, webhooks Dashboard).

Objetivo: cerrar riesgos residuales que afectan directamente a aislamiento de tenants, control de coste, integridad de webhooks, tratamiento de archivos y observabilidad LLM.

Este paso no pretende rehacer la arquitectura. Su funcion es convertir los riesgos ya detectados en controles concretos, verificables y con tests. Debe ejecutarse antes de avanzar en funcionalidades nuevas que dependan de auth, integraciones, uploads o uso intensivo de LLMs.

## Dependencias

- `Paso00_Auditoria_Base.md` completado.
- Infisical configurado.
- Postgres y Redis disponibles para tests de integracion.

Si alguna dependencia no esta disponible, no se debe saltar el control de seguridad. Se documenta el bloqueo, se deja el test preparado cuando sea posible y no se considera cerrado el punto afectado.

## Prioridad y criterio de cierre

- **P0**: riesgo explotable o con impacto alto en datos, acceso, coste o disponibilidad. No deberia entrar funcionalidad nueva encima de estos puntos sin resolverlos.
- **P1**: endurecimiento importante. Puede convivir temporalmente con desarrollo si queda documentado, pero debe cerrarse antes de produccion.

Cada tarea se cierra solo cuando:

- el cambio esta implementado en la capa correcta;
- existe test unitario, de integracion o ambos, segun aplique;
- se ha ejecutado el comando de test indicado;
- no quedan secretos reales ni payloads sensibles en logs, trazas o documentacion versionada.

## Tareas P0

### 1. Secretos expuestos

- [x] Rotar tokens, passwords y API keys encontrados en documentacion antigua.
- [x] Sanear documentos antiguos o moverlos fuera del repo si son locales.
- [x] Ejecutar detect-secrets.

**Por que importa**

Un secreto que ha estado en documentacion o en Git debe tratarse como comprometido aunque el repositorio sea privado. La mitigacion real no es solo borrarlo del fichero actual, sino invalidarlo en el proveedor y sustituirlo por uno nuevo gestionado desde Infisical.

**Que hacer**

- Identificar cualquier token, password, API key, webhook secret, DSN o credencial de base de datos en documentos antiguos, ejemplos, capturas o historico.
- Rotar el secreto en su proveedor original: Clerk, Anthropic, Google, Langfuse, Redis, Postgres, Cloudflare R2, integraciones de mensajeria o cualquier otro sistema afectado.
- Guardar el nuevo valor solo en Infisical. No crear `.env`, `.env.local`, ejemplos con valores reales ni documentos con secretos pegados.
- Sustituir en documentacion cualquier secreto real por nombres de variable, por ejemplo `CLERK_SECRET_KEY` o `POSTGRES_URL`.
- Si un documento antiguo es solo material local de trabajo y contiene secretos, moverlo fuera del repo o sanearlo antes de seguir.

**Validacion**

```powershell
detect-secrets scan
rg -n --hidden --glob '!*.lock' --glob '!uv.lock' "sk-|api[_-]?key|secret|password|token|dsn|postgres://|redis://|PRIVATE KEY BLOCK"
```

El resultado esperado es que no aparezcan secretos reales. Puede haber nombres de variables o textos explicativos, pero no valores utilizables.

**No hacer**

- No considerar resuelto el problema por borrar el texto del documento.
- No reutilizar el mismo secreto con otro nombre.
- No dejar claves en comentarios, fixtures, capturas o markdown antiguo.

### 2. Clerk roles y membership sync

- [x] Revisar `auth_service.ensure_membership`.
- [x] Si una membership existente cambia de rol en Clerk, actualizar BD.
- [x] Si se elimina/degrada en Clerk, desactivar membership local.
- [x] Anadir eventos webhook `organizationMembership.updated` y `organizationMembership.deleted`.
- [x] Tests de downgrade admin -> member.
- [x] Tests de membership deleted -> sin acceso.

**Por que importa**

Clerk es la fuente de identidad y pertenencia a organizaciones, pero la aplicacion mantiene estado local para operar con tenants, roles y permisos. Si una degradacion o eliminacion en Clerk no se sincroniza, un usuario podria conservar privilegios locales despues de perderlos en el proveedor de identidad.

**Que hacer**

- Revisar el flujo completo de resolucion de usuario, tenant y membership local.
- Asegurar que `ensure_membership` no solo crea memberships, sino que tambien actualiza rol y estado cuando Clerk cambia.
- Procesar eventos `organizationMembership.updated` para reflejar cambios de rol.
- Procesar eventos `organizationMembership.deleted` para desactivar o invalidar la membership local.
- Mantener una politica clara: si Clerk dice que no pertenece a la organizacion, la aplicacion debe denegar acceso aunque exista una fila antigua en BD.
- Aplicar el mismo criterio a SADM: acceso solo si cumple organizacion, rol requerido y allowlist opcional si esta configurada.

**Implementado**

- `ensure_membership`: actualiza rol en memberships activas; no reactiva ni eleva rol si `is_active=False` salvo `allow_reactivation=True` (webhooks created/updated).
- `sync_clerk_membership` / `revoke_clerk_membership` en `app/services/auth_service.py`.
- Webhook Clerk (`/api/webhooks/clerk`): `organizationMembership.created|updated|deleted`.
- Middleware: JWT con membership local revocada → redirect a `/login` y borrado de `__session` (`auth_membership_revoked`).
- SADM: `is_platform_superadmin` exige membership activa + rol admin + org/allowlist.

**Validacion (automatizada)**

```powershell
infisical run -- uv run pytest tests/unit/test_clerk_webhook_memberships.py tests/unit/test_auth_org_role.py tests/unit/test_auth_middleware_session_expiry.py tests/unit/test_superadmin_permissions.py tests/integration/test_clerk_membership_sync.py -q
```

**No hacer**

- No confiar en el rol almacenado localmente si Clerk acaba de enviar un cambio.
- No permitir fallback permisivo cuando falle la sincronizacion.
- No tratar webhooks de Clerk como informativos; son parte del control de acceso.

>>> Tareas manuales

1. En Clerk Dashboard → Webhooks → endpoint de la app (`/api/webhooks/clerk`):
   - Verificar que estan suscritos `organizationMembership.created`, `organizationMembership.updated` y `organizationMembership.deleted` (ademas de user/org created si ya los usabas).
   - Confirmar que `CLERK_WEBHOOK_SECRET` en Infisical coincide con el signing secret de ese endpoint.
2. Prueba manual en un tenant de desarrollo:
   - Usuario admin → bajar a member en Clerk → recargar la app: debe perder Documentos/Ajustes y quedarse en Chat/Citas. >>> 2026/08/05 12:20, OK
   - Quitar al usuario de la org en Clerk → siguiente request debe ir a `/login` (sin conservar privilegios). >>> 2026/08/05 12:20, OK
3. Si el usuario sigue viendo la org antigua en el selector de Clerk tras el delete, forzar refresh de sesion en Clerk (sign-out / sign-in); la app ya deniega con JWT obsoleto mientras `is_active=false`. >>> 2026/08/05 12:20, OK

### 3. JWT audience

- [x] Anadir settings para audiencia/azp esperada si Clerk la expone en el token.
- [x] Validar audiencia o `azp` segun contrato real de Clerk.
- [x] Tests de token con audiencia invalida.

**Por que importa**

Validar firma y expiracion del JWT no siempre basta. Sin comprobar audiencia (`aud`) o aplicacion autorizada (`azp`) cuando Clerk lo expone, existe riesgo de aceptar tokens emitidos para otro cliente, entorno o integracion.

**Que hacer**

- Revisar el contrato real de los tokens de Clerk usados por la aplicacion.
- Si existe `aud`, configurar la audiencia esperada mediante Pydantic Settings e Infisical.
- Si Clerk usa `azp` u otro claim equivalente para identificar cliente autorizado, validarlo explicitamente.
- Separar valores por entorno: development, staging y production no deben aceptar audiencias cruzadas.
- Fallar cerrado si el claim requerido esta presente pero no coincide.

**Implementado**

- Settings: `CLERK_JWT_AZP_ALLOWLIST`, `CLERK_JWT_AUDIENCE_ALLOWLIST` (CSV/JSON).
- `assert_clerk_jwt_authorized_party` + llamada desde `verify_clerk_jwt` (`app/core/security.py`).
- Allowlist no vacia → claim obligatorio y debe coincidir; vacia → no se exige ese claim.
- Staging/production con `CLERK_JWKS_URL` → al menos una allowlist obligatoria (Settings fail-closed).

**Validacion (automatizada)**

```powershell
infisical run -- uv run pytest tests/unit/test_clerk_jwt_audience.py -q
```

**No hacer**

- No hardcodear audiencias en codigo.
- No relajar la validacion en produccion por compatibilidad temporal.
- No aceptar tokens de otro entorno.

>>> Tareas manuales

El codigo ya sabe comprobar `azp` / `aud`, pero no inventa los valores correctos de tu instancia Clerk. Esos valores solo se ven en un JWT real. Hay que copiarlos a Infisical; a partir de ahi la app rechaza tokens de otro cliente o entorno.

En `development`, si las allowlists estan vacias, esa comprobacion **no se aplica** (solo firma y caducidad). Hasta rellenar Infisical, el control del §3 sigue abierto en runtime.

#### 1. Sacar `azp` / `aud` de tu sesion

Objetivo: saber que pone Clerk en tus tokens.

1. Arranca la app y entra con un usuario (login normal).
2. En el navegador: DevTools → Application (o Almacenamiento) → Cookies → tu dominio (`localhost:8000` o el que uses).
3. Busca la cookie `__session`. El valor es un JWT (tres partes separadas por `.`).
4. Copia ese valor y pegalo en https://jwt.io (o un decoder local). Mira el **payload** (JSON del medio).
5. Anota, si existen:
   - `azp` (*authorized party*): quien autorizo el token para tu app. Es el claim mas habitual en Clerk.
   - `aud` (*audience*): para quien va el token. A veces no viene, o viene como string o lista.

Ejemplo ficticio de lo que basta anotar (no el token entero):

- `azp` = `http://localhost:8000`
- `aud` = (si existe) su valor exacto

**No hacer:** no pegar el JWT completo en chats, Git, issues ni capturas. Solo los strings de `azp` / `aud`.

Si el payload no tiene ni `azp` ni `aud`, revisar en Clerk Dashboard el template del session token; sin alguno de esos claims no hay valor que allowlistear con el codigo actual.

>>> 2026/08/05 13:00, OK

#### 2. Meterlo en Infisical (por entorno)

Objetivo: decirle a la app que solo acepte esos valores.

En Infisical, para cada entorno (`dev` / `staging` / `prod`):

| Variable | Cuando | Valor |
|----------|--------|--------|
| `CLERK_JWT_AZP_ALLOWLIST` | Si el JWT tiene `azp` (recomendado) | Exactamente el string de `azp` |
| `CLERK_JWT_AUDIENCE_ALLOWLIST` | Si el JWT tiene `aud` (opcional o complemento) | Exactamente el string de `aud` |

- Formato: un valor, o varios separados por coma si hubiera mas de uno. Ejemplo: `CLERK_JWT_AZP_ALLOWLIST=http://localhost:8000`
- No mezclar entornos: el `azp`/`aud` de prod no debe ir en `dev`, ni al reves. Cada instancia Clerk (o cada `APP_BASE_URL`) puede diferir.
- Basta con una de las dos listas si solo existe un claim. Si existen los dos, se pueden rellenar ambas (mas estricto).

Efecto en runtime:

- Lista vacia → ese claim no se exige.
- Lista con valor → el JWT debe traer ese claim y coincidir; si no → 401 / sesion invalida.

>>> 2026/08/05 13:00, OK

#### 3. Reiniciar y probar login

Objetivo: confirmar que no te has bloqueado a ti mismo.

1. Para la app (y el worker ARQ si corre aparte).
2. Volver a arrancar con Infisical (`infisical run --env=dev -- ...` o el comando habitual).
3. Login de nuevo y navegar un poco.

- Si entra bien → allowlist correcta.
- Si echa a login o 401 → el valor en Infisical no coincide con el JWT (espacio de mas, URL distinta, entorno mal elegido). Repetir el paso 1 con una sesion nueva y comparar caracter a caracter.

>>> 2026/08/05 13:00, OK

#### 4. Antes de staging / production

Objetivo: no dejar produccion sin esta defensa y no romper el arranque.

Si `APP_ENV` es `staging` o `production` **y** `CLERK_JWKS_URL` esta configurado, Settings exige que al menos una allowlist (`CLERK_JWT_AZP_ALLOWLIST` o `CLERK_JWT_AUDIENCE_ALLOWLIST`) no este vacia. Si no, **la app no arranca**.

Checklist antes de subir de entorno:

1. JWT de ese entorno decodificado.
2. Allowlists rellenadas en Infisical de ese entorno.
3. Arranque de prueba con ese `APP_ENV`.
4. Login de humo OK.

#### Orden practico recomendado (local)

1. Paso 1 (leer `azp`/`aud` de `__session`).
2. Paso 2 solo en Infisical `dev`.
3. Paso 3 (reinicio + login).
4. Paso 4 cuando se pase a staging/prod.

### 4. Webhook body limit y replay

- [x] Limitar body antes de parsear en WhatsApp, Telegram, Clerk y futuro Stripe.
- [x] Deduplicar mensajes WhatsApp por message id.
- [x] Deduplicar Telegram por update id.
- [x] Usar Redis `SET NX` con TTL.
- [x] Asegurar `_job_id` determinista cuando aplique.

**Por que importa**

Los webhooks son entrada publica. Aunque esten firmados, pueden usarse para consumo excesivo de memoria, reintentos duplicados, tormentas de jobs o repeticion de eventos antiguos. El limite de body debe aplicarse antes de parsear JSON, porque parsear primero ya consume recursos.

**Que hacer (explicado)**

Orden de defensa en cada POST:

1. **Limite de body** (`read_request_body_limited`): lee el stream hasta `WEBHOOK_MAX_BODY_BYTES` (default 256 KiB). Si se supera, aborta **sin** `json.loads` y **sin** loguear el cuerpo.
2. **Firma sobre bytes crudos**: WhatsApp HMAC, Telegram secret header, Clerk/Svix sobre el mismo body limitado.
3. **Parse JSON** solo si el body cabe y la firma es valida (Telegram parsea tras el limite; la firma no depende del JSON).
4. **Dedupe Redis** (`claim_webhook_event`): `SET webhook:dedupe:<provider>:<id> 1 NX EX <ttl>`.
   - WhatsApp: `messages[].id`
   - Telegram: `update_id`
   - Clerk: header `svix-id`
   - Stripe (futuro): `PROVIDER_STRIPE` + event id (constante reservada en `webhook_ingress.py`)
5. **Job ARQ determinista**: `channel:<canal>:<provider_event_id>` via `enqueue_channel_message(..., provider_event_id=...)`.

Respuestas controladas:

- WA/TG body oversized o replay → HTTP 200 (el proveedor no debe reintentar por "error" nuestro ni ver detalles).
- Clerk body oversized → `AuthError` (401); replay Svix → `{"received": true}` sin side effects.

**Implementado**

- `app/core/webhook_ingress.py`
- Settings: `WEBHOOK_MAX_BODY_BYTES`, `WEBHOOK_DEDUPE_TTL_SECONDS` (default 86400)
- Rutas: `webhooks_whatsapp.py`, `webhooks_telegram.py`, `webhooks.py` (Clerk)
- `app/jobs/queue.py` `_job_id` determinista para canales

**Validacion (automatizada)**

```powershell
infisical run -- uv run pytest tests/unit/test_webhook_ingress.py tests/unit/test_clerk_webhook_memberships.py tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py -q
```

**No hacer**

- No parsear JSON antes de comprobar tamano.
- No usar hashes de payload como unica deduplicacion si existe id de proveedor.
- No loguear cuerpos completos de webhook.
- No usar ids aleatorios para jobs que deben ser idempotentes.

>>> Tareas manuales

El codigo ya limita el body y hace dedupe en Redis. Estas tareas confirman que **en tu entorno real** (dev/staging/prod) Redis, secretos y proveedores estan alineados. Sin ellas, los tests pasan pero un webhook real puede fallar o procesarse dos veces.

Orden recomendado: Redis → TTL/limites → Clerk (siempre activo) → WhatsApp/Telegram solo si esos canales estan en alcance → Stripe cuando exista billing.

#### 1. Redis compartido entre API y worker

**Objetivo:** el claim anti-replay vive en Redis. Si la API y el worker usan Redis distintos, o Redis esta caido, el dedupe no protege (o el webhook rompe).

1. En Infisical, comprueba que `REDIS_URL` es la misma para el proceso de la API y el del worker ARQ.
2. Con la API arrancada:
   ```powershell
   curl http://localhost:8000/health/redis
   ```
   Debe responder OK / status ok (el path exacto del JSON puede variar; lo importante es que no falle). >>> 2026/08/06 11:20 OK
3. Opcional: en Redis CLI, tras un webhook de prueba, busca claves:
   ```text
   KEYS webhook:dedupe:*
   ```
   Ejemplo de clave: `webhook:dedupe:whatsapp:wamid.xxx`, `webhook:dedupe:telegram:12345`, `webhook:dedupe:clerk:msg_xxx`.

**Fallo tipico:** API en Redis local y worker en otro host → jobs y dedupe desincronizados. **No hacer:** silenciar errores de Redis ni saltarse el claim.

#### 2. Entender y (solo si hace falta) ajustar TTL y tamano

**Objetivo:** saber que significan los defaults y cuando tocarlos.

| Variable | Default | Significado |
|----------|---------|-------------|
| `WEBHOOK_DEDUPE_TTL_SECONDS` | `86400` (24 h) | Tiempo que Redis recuerda un `event_id` ya procesado. Cubre reintentos tipicos de Meta/Telegram/Svix. |
| `WEBHOOK_MAX_BODY_BYTES` | `262144` (256 KiB) | Tope de bytes leidos **antes** de parsear JSON. |

- **No bajes el TTL** sin motivo: un reintento del proveedor horas despues volveria a ejecutar efectos (doble mensaje, doble sync).
- **No subas el body** "por si acaso" a varios MB: aumenta superficie de DoS en memoria.
- Solo cambia valores en Infisical tras medir un payload real que falle; documenta el motivo.

Tras cambiar variables: reinicia API (y worker si lee la misma config al arrancar).

#### 3. Clerk (obligatorio en casi cualquier entorno)

**Objetivo:** el mismo delivery Svix no debe aplicar dos veces un cambio de membership.

1. Clerk Dashboard → Webhooks → endpoint de la app (`https://…/api/webhooks/clerk` o tunnel en local).
2. Comprueba eventos suscritos (`organizationMembership.*` como minimo) y que el **Signing Secret** = `CLERK_WEBHOOK_SECRET` en Infisical.
3. Provoca un evento (p. ej. cambiar rol de un usuario en una org de prueba).
4. En Deliveries, abre el delivery: debe ser **2xx**. Anota el `svix-id` (o "Message ID" de Svix).
5. Usa **Retry** / reenvio del **mismo** delivery (mismo `svix-id`):
   - Respuesta otra vez `received: true` / 200.
   - En logs de la app debe aparecer algo como `webhook.dedupe_replay` (provider `clerk`).
   - En BD: el rol/estado de membership **no** debe "parpadear" ni reaplicarse efectos raros; el segundo intento es no-op.

**Si el retry vuelve a mutar datos:** el claim no esta funcionando (Redis, o el handler no usa `svix-id`). Parar y depurar antes de prod.

#### 4. WhatsApp (solo si usas el canal)

**Objetivo:** el mismo `messages[].id` no encola dos jobs ARQ ni genera dos respuestas al cliente.

1. En Meta Developer → tu app → WhatsApp → Webhook: URL apuntando a `/api/webhooks/whatsapp` y verify token = `WHATSAPP_VERIFY_TOKEN`.
2. Confirma `WHATSAPP_APP_SECRET` en Infisical (firma HMAC). En staging/prod no uses `WEBHOOK_ALLOW_UNSIGNED`.
3. Envia un mensaje de texto real al numero conectado.
4. En logs/worker: debe encolarse `process_channel_message` una vez. En Redis: clave `webhook:dedupe:whatsapp:<wamid…>`.
5. **Prueba de replay** (elige una):
   - Si Meta permite reenviar el webhook del mismo mensaje, usalo; o
   - Con un POST firmado de prueba que reutilice el **mismo** `messages[0].id` (mismo body + misma firma HMAC).
6. Segundo intento: HTTP 200, **sin** nuevo job (o log `webhook.dedupe_replay` / `arq.enqueue_channel_duplicate`). El cliente no debe recibir dos respuestas por el mismo mensaje.

**Nota:** eventos que no son mensaje de texto (statuses, etc.) se ignoran a proposito; el dedupe de mensaje aplica cuando hay `id` de mensaje de texto.

#### 5. Telegram (solo si usas el canal)

**Objetivo:** el mismo `update_id` no se procesa dos veces.

1. Comprueba que el bot tiene webhook a `/api/webhooks/telegram/<integration_id>` y que el secret de `setWebhook` coincide con el cifrado en BD (`webhook_secret_enc`).
2. Envia un mensaje de texto al bot.
3. Primer update: se encola un job; en Redis `webhook:dedupe:telegram:<update_id>`.
4. Reenvia el **mismo** JSON de Update (mismo `update_id`) con el header `X-Telegram-Bot-Api-Secret-Token` correcto.
5. Segundo POST: 200, sin segundo job.

**Fallo tipico:** secret mal configurado → 200 silencioso sin encolar (comportamiento fail-closed; no es dedupe). Distinguelo mirando logs `telegram.webhook.invalid_secret`.

#### 6. Stripe (futuro, `Paso09`)

Cuando exista el endpoint Stripe:

- Reutilizar `claim_webhook_event(provider="stripe", event_id=event["id"])` de `webhook_ingress.py`.
- No inventar otra tabla/cola de dedupe.
- Mismo criterio: firma → claim → efectos; retry de Stripe con el mismo `evt_…` = no-op.

No hay tarea operativa hoy salvo recordar esta regla al implementar billing.

#### 7. Que mirar en logs (sin pegar payloads)

Busca estos eventos (structlog / logging):

| Log | Significado |
|-----|-------------|
| `webhook.body_too_large` | Alguien envio un body > limite; no se parseo. |
| `webhook.dedupe_replay` | Replay detectado; no se reejecutan efectos. |
| `arq.enqueue_channel_duplicate` | ARQ ya tenia el `_job_id` determinista. |
| `*.webhook.invalid_signature` / `invalid_secret` | Firma mala; no es dedupe. |

**Nunca** loguees ni pegues en issues el body completo del webhook (PII, tokens, texto del cliente).

#### 8. Checklist rapida antes de staging/prod

- [x] `/health/redis` OK en el host que sirve webhooks.
- [x] `WEBHOOK_ALLOW_UNSIGNED=false` en `dev` (2026-09-22). Staging/prod siguen vacios: ver evidencia al final.
- [ ] Clerk: retry de delivery = no-op en un endpoint real (seccion 3). Cubierto en test, no en el dashboard de Clerk.
- [ ] Si WA activo: un mensaje + replay OK contra Meta (seccion 4). Cubierto en test de integracion.
- [ ] Si TG activo: un mensaje + replay OK contra el bot (seccion 5). Cubierto en test de integracion.
- [x] No hay secretos de webhook en el repo; solo Infisical.

### 5. OCR de imagenes knowledge

- [x] Verificar que toda imagen pasa por `media_limits`.
- [x] Limitar pixeles, edge, bytes y MIME.
- [x] Fallar cerrado si no se puede inspeccionar.
- [x] Tests con imagen enorme simulada o fixture controlada.

**Por que importa**

Las imagenes subidas para knowledge (JPEG/PNG/WebP) se indexan con OCR multimodal (LLM). Sin limites, un atacante o un fichero mal formado puede:

1. **Decompression bomb**: pocos KB en disco → cientos de megapixeles al abrir → agotar RAM del worker ARQ (DoS multi-tenant).
2. **Coste LLM**: enviar una imagen enorme al modelo multimodal consume tokens/latencia sin aportar valor.
3. **Fallos opacos**: Pillow o el SDK petan con trazas que no deben llegar al usuario.

El tope de **bytes** solo no basta: una PNG de 200 KB puede declarar 40 000×40 000 px.

**Que se valida (capas)**

| Capa | Donde | Que comprueba |
|------|--------|----------------|
| Bytes + MIME real | `validate_knowledge_upload` (subida) | Tamano ≤ `KNOWLEDGE_MAX_FILE_SIZE_BYTES` (default 15 MB); MIME por contenido (`python-magic` + firmas), no por `Content-Type` del navegador |
| Pixeles / edge / legibilidad | `inspect_document` en `media_limits` | Lado ≤ `DOCUMENT_MAX_IMAGE_EDGE_PX` (default 20 000); area ≤ `DOCUMENT_MAX_IMAGE_PIXELS` (default 40 Mpx); cabecera legible; fail-closed si no se puede medir |
| Antes de R2 | `knowledge_document_service.create_from_upload` | Si MIME es imagen → `inspect_document` **antes** de subir a R2; rechazo → `UploadValidationError` (modal knowledge, sin job) |
| Antes del LLM | `extract_text_from_image` | Otra vez `inspect_document` (defensa en profundidad: reindex, bypass de subida, etc.); **no** llama a `complete()` si falla |
| Indexacion | `knowledge_index_service.run_index_pipeline` | `MediaLimitExceeded` → `mark_failed` con prefijo `media_limit:` y `detail` legible; sin filtrar paths ni mensajes internos del stack |

Misma barrera que las facturas (`prepare_invoice_media` → `inspect_document`), reutilizando `app/core/media_limits.py` — no hay un segundo set de limites ad hoc para knowledge.

**Implementado**

- `app/core/media_limits.py` — fuente unica de limites
- `app/services/knowledge_document_service.py` — inspeccion en subida (imagenes)
- `app/llm/extraction.py` → `extract_text_from_image` — inspeccion antes del OCR
- `app/services/knowledge_index_service.py` — manejo `MediaLimitExceeded` vs error OCR generico
- Settings (Infisical / env): `DOCUMENT_MAX_IMAGE_EDGE_PX`, `DOCUMENT_MAX_IMAGE_PIXELS`, `KNOWLEDGE_MAX_FILE_SIZE_BYTES`, `KNOWLEDGE_ALLOWED_MIMES`

**Validacion (automatizada)**

```powershell
infisical run -- uv run pytest tests/unit/test_knowledge_image_media_limits.py tests/unit/test_media_limits.py -q
```

Cubre: rechazo por edge/pixeles **sin** llamar al LLM; rechazo de bytes no-imagen con MIME de imagen; aceptacion dentro de limites; rechazo en pipeline de indexacion con `media_limit`; rechazo en subida **sin** tocar R2.

**No hacer**

- No confiar solo en `Content-Type` del cliente.
- No enviar imagenes al OCR antes de `inspect_document`.
- No “recortar” o convertir bombas para “ver si funcionan”.
- No capturar `MediaLimitExceeded` y marcar el documento como `ready` o OCR parcialmente.
- No loguear ni persistir el stack completo / rutas locales en `error_message` de BD.

>>> Tareas manuales

El codigo y los tests unitarios ya cierran el control. Estas tareas confirman que **en tu entorno** (limites Infisical, UI knowledge, worker ARQ) el rechazo se ve bien y no hay llamada OCR cara.

Orden recomendado: (1) limites en Infisical → (2) imagen OK → (3) imagen fuera de limites → (4) logs → (5) staging si aplica.

#### 1. Confirmar limites en Infisical (dev)

**Objetivo:** saber contra que umbrales estas probando. Si no estan definidos, valen los defaults del codigo.

1. Abre Infisical → entorno `dev` (o el que uses local).
2. Anota (o deja default) estas variables:

| Variable | Default en codigo | Significado |
|----------|-------------------|-------------|
| `DOCUMENT_MAX_IMAGE_EDGE_PX` | `20000` | Maximo px en el lado mas largo |
| `DOCUMENT_MAX_IMAGE_PIXELS` | `40000000` | Maximo ancho×alto |
| `KNOWLEDGE_MAX_FILE_SIZE_BYTES` | `15728640` (15 MB) | Tope de bytes en subida |

3. **Para la prueba manual de rechazo** conviene bajar temporalmente solo en `dev`, por ejemplo:
   - `DOCUMENT_MAX_IMAGE_EDGE_PX=800`
   - (opcional) `DOCUMENT_MAX_IMAGE_PIXELS=500000`
4. Reinicia API + worker ARQ con `infisical run` para que lean los valores nuevos.
5. **Cuando termines las pruebas**, restaura los valores de produccion/dev habituales (o borra el override) y reinicia otra vez. No dejes limites artificialmente bajos en un entorno compartido.

>>> _Completado: 2026/08/06 12:20 / OK

#### 2. Imagen valida (camino feliz)

**Objetivo:** una foto o captura con texto, resolucion razonable (p. ej. 1200×800), JPEG/PNG/WebP, debe indexarse.

1. Arranca app + worker ARQ con Infisical.
2. Login → seccion Knowledge → subir imagen con texto legible (cartel, captura de FAQ, etc.).
3. Espera a que el estado pase a listo / indexed (polling HTMX de la fila).
4. Comprueba que el documento no queda en `failed` y que el chat/busqueda knowledge puede recuperar algo del texto (si tienes retrieval activo).

Si falla aqui con imagen normal → no es el §5; mira worker, R2, claves LLM o logs `knowledge.index.*`.

>>> _Completado: 2026/08/06 16:55 / OK

#### 3. Imagen fuera de limites (rechazo en subida)

**Objetivo:** ver el rechazo **antes** de R2/OCR, con mensaje claro en el modal.

1. Con los limites bajos del paso 1 (p. ej. edge 800), prepara una imagen cuyo lado largo sea claramente mayor (p. ej. 2000×1000 PNG/JPEG).
   - En Paint / editor: redimensionar a 2000 px de ancho y guardar.
   - O genera con Python local:
     ```powershell
     uv run python -c "from PIL import Image; Image.new('RGB',(2000,1000),(255,255,255)).save('bomb_knowledge.png')"
     ```
2. Sube `bomb_knowledge.png` (u otra equivalente) en Knowledge.
3. **Esperado:**
   - El modal/lista muestra error de validacion (texto con px / limites), **no** un 500.
   - **No** aparece una fila nueva en estado `indexing`/`ready` para ese fichero.
   - En logs de la API: `knowledge.upload.rejected_by_limits` (o `knowledge.upload.rejected`) con `error_code` tipo `image_too_large` — **sin** body binario ni stack completo.
4. **No esperado:** llamada OCR (`knowledge.image_ocr.done`) ni gasto LLM por ese fichero.

>>> _Completado: 2026/08/06 16:50 / OK

#### 4. (Opcional) Rechazo en indexacion / reindex

**Objetivo:** defensa en profundidad si un fichero ya estaba en R2 (documento antiguo, o subida hecha **antes** de bajar el limite).

1. Sube una imagen **valida** con limites normales → queda en R2 e indexada.
2. Baja `DOCUMENT_MAX_IMAGE_EDGE_PX` por debajo del tamano de esa imagen y reinicia worker.
3. Pulsa **Reindexar** en esa fila (si la UI lo ofrece).
4. **Esperado:** documento pasa a `failed` con mensaje que empieza por `media_limit:` (detalle de px); logs `knowledge.index.media_limit`; **sin** `knowledge.image_ocr.done`.
5. Restaura limites y reinicia.

Si no tienes reindex a mano, puedes saltar este paso: la subida (paso 3) + tests unitarios ya cubren el camino principal.

>>> _Completado: 2026/08/06 16:55 / OK

#### 5. Checklist rapida antes de staging/prod

- [x] En `dev` (2026-09-22) los limites son los defaults de produccion, no los de prueba: edge `20000`, pixeles `40000000`, bytes `15728640`.
- [ ] Los mismos limites revisados en Infisical de staging/prod (esos entornos no tienen secretos todavia).
- [ ] Subida knowledge de imagen OK de humo en staging/prod.
- [x] No hay secretos ni dumps de imagen en el repo. Logs de error de un entorno desplegado: pendiente de ese despliegue.

## Tareas P1

- [x] Revisar CSP y documentar camino para eliminar `unsafe-inline`/`unsafe-eval` (seccion 6). Retirarlos sigue pendiente: Alpine 3 los necesita.
- [x] Confirmar que `LANGFUSE_CAPTURE_CONTENT` falla fuera de development (validator + test).
- [x] Revisar errores publicos para que no filtren payloads (handler generico; seccion 8).
- [x] Asegurar audit log en acciones sobre datos de cliente. Subida de documentos de negocio: `document.upload` en `ingest_uploaded_document` (sin binario). 2026-09-22.

### 6. CSP

**Por que importa**

La Content Security Policy reduce impacto de XSS, pero `unsafe-inline` y `unsafe-eval` debilitan mucho esa proteccion. En una aplicacion con HTMX, Alpine y paneles administrativos, conviene dejar un camino claro para retirarlos sin romper la interfaz.

**Que hacer**

- Inventariar que scripts o estilos obligan actualmente a `unsafe-inline` o `unsafe-eval`.
- Definir si se usaran nonces, hashes o refactor de scripts inline.
- Mantener lista explicita de origenes permitidos.
- Documentar excepciones temporales con motivo y fecha objetivo de eliminacion.

**Estado (2026-09-22)**

CSP en `app/core/security_headers.py`:

- `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`.
- Sin `*` como origen de scripts. `img-src` si permite `https:` (imagenes externas).
- `script-src` incluye `'unsafe-inline'` y `'unsafe-eval'`.
- `style-src` incluye `'unsafe-inline'`.

Camino para quitarlos, sin hacerlo en este paso:

1. Sustituir Alpine estandar por `@alpinejs/csp` y reescribir expresiones que usan `new Function` (arrow functions, asignaciones anidadas, `window.*`).
2. Sacar scripts inline de las plantillas a `app/static/js/` y, si queda alguno, usar nonce por respuesta.
3. Estilos inline: nonce o clases Tailwind; no ampliar `style-src`.
4. Probar login Clerk, sidebar, documentos, chat y settings antes de quitar las dos directivas.

Hasta ese cambio, `unsafe-eval` es una excepcion consciente por Alpine 3, no un olvido.

**Validacion**

- Comprobar que las vistas principales cargan con la CSP endurecida.
- Revisar consola del navegador en flujos HTMX principales.
- Confirmar que no se abren origenes amplios como `*` en `script-src`.

### 7. Langfuse sin contenido de cliente

**Por que importa**

Langfuse debe recibir metadatos operativos, no contenido de cliente. Enviar prompts, documentos, consultas de usuario, respuestas del modelo o errores crudos puede convertir la observabilidad en una fuga de datos.

**Que hacer**

- Confirmar que `LANGFUSE_CAPTURE_CONTENT` solo puede estar activo en development.
- Usar siempre helpers de observabilidad que redactan o sustituyen contenido por metadatos.
- Registrar ids, modelos, latencias, costes, tamanos y estado, pero no texto del usuario ni documentos.

**Validacion**

- Test de configuracion: production/staging fallan si `LANGFUSE_CAPTURE_CONTENT=true`.
- Test de trazas: `input`, `output` y `status_message` no contienen payloads reales.

### 8. Errores publicos

**Por que importa**

Los errores visibles para usuario o integraciones no deben filtrar SQL, prompts, payloads de webhooks, rutas internas, tokens, stack traces ni mensajes crudos de proveedores.

**Que hacer**

- Revisar handlers globales de error.
- Separar mensaje interno logueado de mensaje publico.
- Asegurar que logs internos tampoco incluyen contenido sensible.
- Devolver codigos HTTP coherentes y mensajes genericos accionables.

**Validacion**

- Tests de errores de validacion, auth, webhook invalido y fallo LLM.
- Confirmar que la respuesta publica no contiene payload original ni excepcion cruda.

### 9. Audit log

**Por que importa**

El audit log permite reconstruir quien accedio, modifico, descargo o borro datos de cliente. En multi-tenant es un control de seguridad, soporte y cumplimiento, no una mejora opcional.

**Que hacer**

- Registrar acciones sobre datos de cliente: subir, ver, descargar, modificar, borrar, exportar, consultar con IA o cambiar permisos.
- Incluir tenant, usuario, accion, recurso, resultado, timestamp y metadatos seguros.
- No incluir documentos, prompts, respuestas LLM ni payloads completos.
- Asegurar que acciones SADM quedan diferenciadas de acciones normales de tenant.

**Validacion**

- Tests de acciones criticas que comprueben creacion de audit log.
- Confirmar que el audit log respeta RLS o el modelo de acceso interno definido.

## Tests esperados

```powershell
infisical run -- uv run pytest tests/unit/test_config_webhook_security.py tests/unit/test_security_http.py tests/unit/test_llm_observability.py -q
infisical run -- uv run pytest tests/integration/test_auth_clerk.py tests/integration/test_clerk_membership_sync.py tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py -q
```

Anadir tests nuevos para replay/body limit si no existen.

## Evidencia Fase C (2026-09-22)

Comando (91 passed, ~64 s):

```powershell
infisical run -- uv run pytest tests/unit/test_config_webhook_security.py tests/unit/test_security_http.py tests/unit/test_llm_observability.py tests/unit/test_clerk_jwt_audience.py tests/unit/test_webhook_ingress.py tests/unit/test_knowledge_image_media_limits.py tests/unit/test_media_limits.py tests/unit/test_superadmin_permissions.py tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py tests/integration/test_clerk_membership_sync.py -q -m "not real_llm"
```

Sonda de flags (sin imprimir secretos): `infisical run --env=<slug> -- uv run python` sobre `get_settings()`.

| Entorno Infisical | Resultado |
|-------------------|-----------|
| `dev` | 43 secretos. `APP_ENV=development`. `WEBHOOK_ALLOW_UNSIGNED=false`. `LANGFUSE_CAPTURE_CONTENT=false`. JWKS presente. `CLERK_JWT_AZP_ALLOWLIST=http://localhost:8000`. Limites de imagen en defaults. Claves Clerk, LLM, R2, Langfuse y cifrado presentes. Postgres y Redis en localhost. |
| `staging` | El slug existe y tiene **0 secretos**. La app no arranca ahi. |
| `prod` | El slug existe y tiene **0 secretos**. |
| `production` | El slug **no existe** (404 de Infisical). |

`dev` no es un entorno de salida. Antes de usarlo como pre-produccion hay dos desajustes:

- `APP_BASE_URL` apunta al tunel ngrok, pero `CLERK_JWT_AZP_ALLOWLIST` es `http://localhost:8000`. Un JWT emitido para el tunel no pasa la allowlist.
- `SECURITY_ALLOWED_HOSTS` es `localhost`, `127.0.0.1` y dos IPs LAN. `create_app()` anade tambien el host de `APP_BASE_URL`, asi que el tunel ngrok entra mientras esa URL siga configurada. Si cambias `APP_BASE_URL` sin actualizar la lista, el host nuevo queda fuera.

**Para cerrar staging (lo haces tu en Infisical, no copiando `dev`):**

1. Crear o rellenar el entorno `staging` con secretos propios (Clerk, LLM, R2, Postgres, Redis, `ENCRYPTION_KEY`, Langfuse). No reutilizar los de `dev`.
2. `APP_ENV=staging`, `APP_BASE_URL` del host real, `SECURITY_HTTPS_REDIRECT=true`, `SECURITY_HSTS_ENABLED=true`.
3. `SECURITY_ALLOWED_HOSTS` solo con ese host.
4. `WEBHOOK_ALLOW_UNSIGNED=false` (si se pone `true`, Settings no arranca).
5. `LANGFUSE_CAPTURE_CONTENT=false` (igual: Settings no arranca si es `true`).
6. `CLERK_JWKS_URL` de la instancia de staging y `CLERK_JWT_AZP_ALLOWLIST` (o audience) con el `azp` de un JWT de ese entorno. Sin una de las dos listas, Settings no arranca.
7. Limites de imagen en defaults (`20000` / `40000000` / `15728640`), no los de la prueba manual.
8. Repetir la sonda: `infisical run --env=staging -- uv run python` y comprobar la tabla de arriba.
9. Retry real de un delivery Clerk y, si el canal esta activo, un replay de WhatsApp o Telegram.

## Criterios de aceptacion

- [x] Webhooks firmados, limitados y deduplicados (tests de replay Clerk, WhatsApp y Telegram).
- [x] Roles Clerk sincronizados con BD.
- [x] SADM sigue restringido por org + rol admin + allowlist opcional (`test_superadmin_permissions`).
- [x] OCR knowledge no puede causar decompression bomb (tests + limites de `dev` en defaults).
- [x] Langfuse sin contenido de cliente en codigo: capture desactivado y rechazado fuera de development.
- [x] No hay secretos reales en repo (detect-secrets en el commit `7bc1aea`).
- [ ] Infisical `staging` y `prod` rellenados con secretos distintos de `dev`.

## Orden recomendado de ejecucion

1. Secretos expuestos: primero se rota y sanea para no seguir construyendo sobre material comprometido.
2. Clerk roles y JWT audience: despues se cierra el control de acceso.
3. Webhooks: se limita entrada publica y se evita replay antes de ampliar integraciones.
4. OCR knowledge: se cierra el vector de archivos antes de aumentar ingesta documental.
5. P1: CSP, Langfuse, errores y audit log se endurecen antes de produccion.

No avanzar a desarrollo funcional dependiente de un bloque P0 si ese bloque sigue abierto.
