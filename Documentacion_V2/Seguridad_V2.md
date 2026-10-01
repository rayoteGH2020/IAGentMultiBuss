# Seguridad_V2

Fecha: 2026-08-04 · Actualizado: 2026-09-30
Estado: checklist y modelo de seguridad para todo desarrollo V2.

## 1. Principio

La seguridad es condicion de aceptacion, no hardening final. Una feature no esta terminada si puede:

- cruzar tenants,
- generar coste sin limite,
- filtrar contenido a Langfuse,
- aceptar webhooks falsos o repetidos,
- procesar archivos sin limites,
- ejecutar SQL peligroso,
- exponer secretos.

## 2. Riesgo P0 detectado en documentacion historica

Hay documentacion antigua con credenciales o tokens reales. No copiar esos valores a V2, issues, commits, logs ni chats.

Accion obligatoria:

1. Rotar los tokens y contrasenas que aparezcan en documentos antiguos.
2. Mover secretos validos a Infisical.
3. Eliminar o sanear la documentacion antigua que contenga secretos.
4. Revisar historial Git si esos ficheros estuvieron versionados.
5. Ejecutar `detect-secrets` y actualizar baseline solo despues de rotar.

Comandos orientativos:

```powershell
infisical run -- uv run detect-secrets scan --all-files
pre-commit run detect-secrets --all-files
git grep -n "TOKEN\\|PASSWORD\\|SECRET\\|API_KEY\\|Bearer"
```

No documentar los valores encontrados.

## 3. Autenticacion y autorizacion

Obligatorio:

- Validar JWT con JWKS de Clerk.
- Validar audiencia o equivalente configurado para la app.
- Resolver `user`, `tenant`, `membership`.
- Sincronizar rol y estado activo desde Clerk.
- Si membership esta revocada o degradada, aplicar inmediatamente.
- Proteger rutas por rol y por feature de plan.

Tests minimos:

- Usuario sin sesion -> redirect/401.
- Usuario de otro tenant no accede.
- Member no accede a ruta admin.
- Member de org SADM no accede a `/sadm`.
- Admin degradado en Clerk pierde privilegios locales.

## 4. Multi-tenancy y RLS

Toda tabla con datos de cliente:

- `tenant_id` `NOT NULL`.
- indice por `tenant_id`.
- `ENABLE ROW LEVEL SECURITY`.
- `FORCE ROW LEVEL SECURITY`.
- policy `tenant_isolation`.
- `WITH CHECK` para escrituras.

Patron de query:

- `get_db()` en rutas tenant.
- `set_tenant_context()` antes de la primera query.
- `WHERE tenant_id = tenant.id` en services como defensa adicional.

SADM:

- `get_db_no_tenant()` solo con `SuperAdmin`.
- Si una tabla con RLS necesita lectura cross-tenant, usar flag/policy explicita y test.

## 5. Webhooks

Todo webhook externo debe tener:

- validacion criptografica de firma,
- limite de body antes de parsear,
- deduplicacion anti-replay,
- rate limit,
- parseo estricto,
- logs sin payload sensible,
- respuesta `200` segura cuando el proveedor requiere ack pero el negocio decide no procesar.

Casos:

- Clerk: Svix, idempotencia, sync de org/user/membership updated/deleted.
- WhatsApp: HMAC `X-Hub-Signature-256`, dedupe por message id.
- Telegram: secret token, dedupe por update id.
- Canales (WhatsApp/Telegram), tools con datos del cliente final: la identidad (`channel` + `customer_identifier`) sale siempre del webhook, nunca del modelo, y toda lectura o cambio de citas filtra por ella (D030). **Riesgo abierto hasta cerrar `Paso12`:** las tools de citas actuales (Google Calendar) listan y cancelan citas de todo el tenant; no activar citas por canal para clientes reales antes.
- Pagos: sin proveedor (D016, Stripe retirado). Si se integra uno: firma del proveedor, body limit y dedupe anti-replay como el resto de webhooks.

## 6. LLM y Langfuse

Prohibido enviar contenido de cliente a Langfuse:

- documentos,
- OCR,
- mensajes de usuario,
- respuestas del modelo,
- chunks RAG,
- SQL generado,
- errores crudos,
- nombres de ficheros si identifican cliente.

Permitido:

- modelo,
- provider,
- task,
- prompt version,
- roles y conteos,
- chars,
- tokens,
- coste,
- latencia,
- schema de salida,
- campos presentes/ausentes,
- tipo de error.

Tests:

```powershell
uv run pytest tests/unit/test_llm_observability.py -q
```

## 7. Archivos y documentos

Antes de procesar:

- Validar MIME y extension.
- Verificar magic bytes donde sea posible.
- Limitar bytes.
- Limitar paginas PDF.
- Limitar pixeles y dimensiones.
- Fallar cerrado si no se puede inspeccionar.
- No guardar archivos de cliente en disco local; usar R2.

Resuelto: el OCR de imagenes de knowledge reutiliza `media_limits` antes de decodificar o enviar al LLM (Backlog P0-5).

## 8. Coste y abuso

Toda accion con coste o carga debe tener:

- feature gate por plan,
- cuota por plan,
- cooldown o dedupe si puede repetirse,
- budget mensual por tenant,
- cupo mensual persistente en `quota_usage` cuando el limite es mensual (D027),
- metricas en `usage_meter`,
- audit log cuando toque datos de cliente.

Incluye:

- upload documentos,
- retry documentos,
- chat,
- knowledge upload,
- embeddings,
- canales externos,
- voz.
- ~~analytics~~ — **no** (D011: modulo 3 Analytics/BI no se implementa).

## 8b. Logs y auditoria

- **Logs sin datos personales** (Backlog P2c-2, hecho 2026-10-01). Norma en `app/core/log_redaction.py`:
  - Identificadores de personas (telefono, `chat_id`, email): `pseudonymize()`, un HMAC-SHA256 con `APP_SECRET_KEY` truncado a 16 hex, en campos `*_ref` (`customer_ref`, `to_ref`, `email_ref`). Permite correlacionar lineas del mismo cliente sin exponer el dato. Si se rota `APP_SECRET_KEY`, cambian los seudonimos.
  - Excepciones de terceros (SDK, BD, Redis, SMTP, parsers): `error_type=type(exc).__name__`, nunca `str(exc)`. Los mensajes de `AppError` y `UploadValidationError` los escribe la app y si se registran (`exc.message`); por eso **no** deben interpolar datos personales ni texto de terceros (el detalle de Google va en `details`, que el log de `app_error` ya no registra).
  - Nunca nombres de fichero, importes, comercios, proveedores, partes contrarias ni aseguradoras extraidos.
  - Tracebacks fuera de `development`: tipo de la excepcion y de sus causas mas frames `fichero:linea:funcion`, sin mensaje ni variables locales. Lo aplican el procesador de structlog y un filtro en los handlers de stdlib (root, `uvicorn`, `arq`). En `development` se mantiene el traceback completo.
  - Worker ARQ: configura el mismo logging en `on_startup` (antes usaba el de structlog por defecto, que con `rich` vuelca las variables locales). Los argumentos de cada job no se registran (`process_channel_message` recibe telefono y texto del cliente) ni el texto de la excepcion en "job failed".
  - Guardia en CI: `tests/unit/test_logs_no_personal_data.py` falla si un `logger.*` de `app/` usa campos como `filename`, `email`, `customer_identifier`, `total`... o `str(exc)`.
- **`audit_log` solo insercion** (Backlog P2c-1, hecho 2026-10-01, migracion `p78_audit_insert_only_01`): `saas_app` sin `UPDATE`/`DELETE`. Borrar un tenant (CASCADE) o un usuario (SET NULL) sigue funcionando: Postgres ejecuta las acciones de FK como propietario de la tabla. Ninguna migracion nueva puede re-concederlo (`tests/unit/test_migrations_audit_insert_only.py`).
- **Metadata de auditoria sin datos personales en claro** (el SADM la lee): fuera `client_name` de `scheduling.appointment_created`, el titulo del evento de voz y el texto de excepciones en `knowledge.index_failed` y en el error de las tools del chat. Quedan datos personales en otras entradas, pendientes de decision (Backlog P2c-7).
- **IP de auditoria** (Backlog P2c-3, hecho 2026-10-01): un unico helper, `app/routes/web/audit_context.py`, que usa solo `request.client.host`. Uvicorn la toma de `X-Forwarded-For` solo si la conexion viene de `--forwarded-allow-ips`, limitado a la subred fija de la red interna de Compose (`172.30.0.0/24`, la de Caddy). En el `Dockerfile` sin Compose, `127.0.0.1`. Al poner Cloudflare delante: `trusted_proxies` en Caddy (P2c-4).
- **Ficheros de cliente:** se sirven por una ruta que audita y redirige (302) a una URL prefirmada de vida corta; nunca incrustar URLs prefirmadas en el HTML (`AGENTS.md` §7).
- Alcance de lo que se audita: `AGENTS.md` §7.

## 9. CSP y frontend

Estado actual admite CSP compatible con Clerk y Alpine. Riesgo residual:

- `unsafe-inline` y `unsafe-eval` reducen proteccion XSS.

Plan:

1. No empeorar CSP.
2. Evitar JS inline nuevo salvo necesidad justificada.
3. Evaluar Alpine CSP build.
4. Migrar scripts inline a assets con nonce/hash cuando sea viable.

## 10. Checklist por PR

- [ ] No hay secretos nuevos.
- [ ] Rutas protegidas por auth, role y feature si aplica.
- [ ] Mutaciones web tienen CSRF.
- [ ] Services filtran tenant.
- [ ] RLS/migraciones actualizadas.
- [ ] Webhooks firmados/deduped si aplica.
- [ ] LLM via cliente propio.
- [ ] Langfuse metadata-only.
- [ ] Rate limit/cuota si hay coste.
- [ ] Audit log si toca datos de cliente.
- [ ] Logs y metadata de auditoria sin datos personales.
- [ ] Tests unit/integration/e2e segun riesgo.
