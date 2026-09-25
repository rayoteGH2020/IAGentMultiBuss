# Seguridad_V2

Fecha: 2026-08-04
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
- Stripe futuro: firma Stripe y eventos idempotentes.

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

Riesgo pendiente:

- OCR de imagenes de knowledge debe reutilizar `media_limits` antes de decodificar o enviar al LLM.

## 8. Coste y abuso

Toda accion con coste o carga debe tener:

- feature gate por plan,
- cuota por plan,
- cooldown o dedupe si puede repetirse,
- budget mensual por tenant,
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
- [ ] Tests unit/integration/e2e segun riesgo.
