# Paso05 - Consolidacion SADM

Objetivo: cerrar el alcance real de la consola SADM y eliminar ambiguedades historicas.

## Dependencias

- Paso01 completado.
- Preferible Paso02/Paso03 si se va a trabajar en planes desde SADM.

## Alcance

1. Revisar rutas SADM existentes.
2. Aplicar decision cerrada: retirar de la UI/API SADM la provision de orgs/users (solo Clerk).
3. Anadir gestion de planes si Paso02/Paso03 estan hechos.
4. Reforzar audit log.
5. QA de acceso cross-tenant.

## Decision cerrada: identidades solo en Clerk

**Opcion B (cerrada 2026-08-07):** usuarios y organizaciones se gestionan **unicamente** desde Clerk Dashboard.

- Alta, invitaciones, bajas, roles de org y credenciales: solo Clerk.
- La app sincroniza a BD via webhook/login; SADM puede listar orgs/miembros en **read-only**.
- Retirar de la UI SADM (y no ampliar) cualquier flujo de crear orgs/users o reset de contrasenas desde la app.
- Codigo residual de provision, si existe, se desconecta de la UI o se elimina; no se conserva como feature SADM ni como atajo de prod.

Ver `Decision_Log.md` D005 y `SADM_V2.md` §4.

## Cambios esperados

```text
app/routes/web/admin/*
app/services/admin_service.py
app/services/plan_service.py
app/templates/pages/sadm/*
app/templates/components/sidebar.html
tests/integration/test_sadm_routes.py
tests/integration/test_admin_service.py
tests/integration/test_plan_admin_routes.py
```

## Controles obligatorios

- Todas las rutas `/sadm/*` reciben `SuperAdmin`.
- Acciones POST/DELETE tienen CSRF.
- Cross-tenant solo con `get_db_no_tenant`.
- Si se usa policy `superadmin_select`, queda testeada.
- Acciones sensibles escriben audit log.
- No se muestran secretos ni tokens.

## Funciones SADM P1

- Dashboard.
- Organizaciones read-only (sin alta/baja desde SADM).
- Miembros read-only (gestion de membresias solo en Clerk).
- Usage por tenant.
- Documentos rechazados y procesado excepcional.
- Chat traces con acceso restringido.
- Planes: listar, asignar, override.

## Tests

- [x] Tenant normal admin no accede a SADM.
- [x] Member de org SADM no accede.
- [x] Allowlist funciona.
- [x] SADM ve multiples tenants.
- [x] Accion SADM crea audit log.
- [x] No hay `get_db` en rutas SADM cross-tenant.
- [x] No hay UI/API SADM para crear orgs/users; identidades solo via Clerk + sync.

## Comandos

```powershell
infisical run -- uv run pytest tests/unit/test_superadmin_permissions.py tests/integration/test_sadm_routes.py tests/integration/test_admin_service.py -q
infisical run -- uv run pytest tests/integration/test_plan_admin_routes.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [x] El alcance de SADM esta escrito y reflejado en UI.
- [x] No quedan rutas privilegiadas ambiguas.
- [x] Identidades (users/orgs) solo se gestionan en Clerk; SADM es read-only en ese ambito.
- [x] La asignacion de planes esta disponible si pasos de planes estan cerrados.
- [x] SADM no puede conceder bypass de RLS ni `sadm_only` a tenants.
