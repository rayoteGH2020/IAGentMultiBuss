# SADM_V2

Fecha: 2026-08-04 · Actualizado: 2026-09-23
Estado: alcance vigente; planes y consolidacion **implementados** (Paso05).

## 1. Objetivo

SADM es la consola interna de plataforma. Sirve para operar el SaaS, no para que tenants gestionen su propio negocio.

Debe permitir:

- ver organizaciones,
- ver miembros sincronizados,
- ver uso y costes,
- revisar documentos rechazados o excepcionales,
- ver trazas de chat de forma controlada,
- asignar planes y overrides,
- consultar metricas operativas.

## 2. Acceso

Solo `SuperAdmin`:

- tenant actual coincide con `ADMIN_CLERK_ORG_ID`,
- membership activa,
- role `admin`,
- si `SUPERADMIN_CLERK_USER_IDS` esta configurado, el usuario esta en allowlist.

Un member/viewer dentro de la org SADM no es SADM.

## 3. Sesiones y BD

Rutas SADM:

- usan `get_db_no_tenant()` cuando necesitan datos cross-tenant,
- nunca usan `get_db()` por accidente,
- si leen tablas con RLS, activan una politica explicita de lectura SADM,
- no escriben datos cross-tenant sin audit log.

## 4. Provision de identidades

Decision cerrada (Opcion B / `Decision_Log` D005):

- Usuarios y organizaciones se gestionan **unicamente** desde Clerk Dashboard.
- Webhook/login sincronizan BD; SADM solo lista orgs/miembros en read-only.
- No hay UI/API SADM (ni otras pantallas de plataforma) para crear orgs, invitar usuarios o resetear credenciales.
- Codigo residual de provision: retirado; identidades solo Clerk (D005 / Paso05 cerrado).

## 5. Funciones permitidas

Implementado:

- `/sadm`: dashboard.
- `/sadm/organizations`: listado read-only y miembros.
- `/sadm/usage`: consumo por tenant.
- `/sadm/documents`: revision de rechazados y procesado excepcional.
- `/sadm/chat-traces` / chat-usage: trazas y uso de chat.
- `/sadm/plans`: catalogo, asignacion de plan y overrides (Paso02–04 + Paso05).

## 6. Funciones prohibidas por defecto

Sin decision nueva:

- crear SADM desde UI,
- crear/invitar usuarios u organizaciones desde SADM (solo Clerk),
- conceder `sadm_only` por plan,
- borrar tenants automaticamente por webhook,
- exponer secretos o tokens,
- mostrar contenido bruto de Langfuse,
- saltar RLS sin policy auditada,
- resetear contrasenas desde la app.

## 7. Audit log

Requiere audit:

- cambio de plan,
- cambio de override,
- autorizacion de procesado excepcional,
- descarga o apertura de original de cliente desde SADM,
- cambios de membership en la app (si existieran; la fuente de verdad es Clerk),
- cambios de integraciones de canal.

## 8. Tests minimos

- Sin auth -> no accede.
- Tenant normal admin -> no accede a `/sadm`.
- Member de org SADM -> no accede.
- Admin de org SADM -> accede.
- Allowlist activa -> usuario no incluido no accede.
- Rutas SADM usan `get_db_no_tenant`.
- Consulta cross-tenant devuelve varios tenants solo para SADM.
- Accion SADM escribe audit log.
