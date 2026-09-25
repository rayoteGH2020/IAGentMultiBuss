# Paso00 - Auditoria base y saneamiento documental

Estado: **parcial** (2026-09-23). Evidencia de repo/migraciones/tests en docs; rotacion de secretos en proveedores e Infisical staging/prod = **Ops**.

Objetivo: dejar claro el estado real del repo antes de implementar mas producto.

## Alcance

Este paso no cambia codigo de aplicacion salvo que se detecte un bloqueo critico. Su salida principal es evidencia:

- comandos ejecutados,
- estado de migraciones,
- estado de tests,
- secretos rotados o pendientes,
- documentos antiguos saneados o marcados como historicos.

## Riesgo arquitectonico

Seguir implementando con documentacion antigua contradictoria puede reactivar decisiones descartadas, duplicar features y abrir agujeros de seguridad.

## Tareas

- [x] Confirmar que `Documentacion_V2` es la guia vigente.
- [x] Revisar si `Documentacion/` esta versionada o es local.
- [x] Buscar secretos en documentacion antigua sin copiarlos.
- [ ] Rotar credenciales encontradas.
- [x] Verificar migraciones a HEAD.
- [x] Ejecutar tests rapidos de guardrails.
- [x] Ejecutar lint/typecheck si el entorno lo permite.
- [x] Crear una lista corta de gaps reales, no historicos.

## Comandos

```powershell
git status --short
git ls-files Documentacion
git grep -n "TOKEN\|PASSWORD\|SECRET\|API_KEY\|Bearer"
infisical run -- uv run alembic heads
infisical run -- uv run alembic current
infisical run -- uv run pytest tests/unit/test_routes_layering.py tests/unit/test_superadmin_permissions.py tests/unit/test_llm_observability.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

Si `uv` falla por cache global en Windows:

```powershell
$env:UV_CACHE_DIR = "D:\AppsIA\IAgentMultiBuss\.uv-cache"
uv run pytest tests/unit/test_routes_layering.py tests/unit/test_superadmin_permissions.py tests/unit/test_llm_observability.py -q
```

## Criterios de aceptacion

- [x] No quedan secretos reales activos en documentos.
- [ ] Los secretos expuestos han sido rotados.
- [x] `alembic current` coincide con HEAD.
- [x] Tests de guardrails pasan.
- [x] Se documentan bloqueos reales y quien debe resolverlos.

## Evidencia 2026-09-22

Guia vigente: `Documentacion_V2/`. `Documentacion/` antigua se elimino del repo en `eae1c00`.

Secretos (sin copiar valores):

- `git grep` de patrones `sk_live_`, `sk_test_`, `AKIA`, `ghp_`, `xox`, JWT: sin coincidencias en el arbol versionado.
- `Documentacion_V2`: solo nombres de variable, no valores.
- `detect-secrets` contra `.secrets.baseline` en el commit `7bc1aea`: exit 0.

Migraciones: `alembic current` = `p64_plans_entitlements_01` (head), Postgres local.

Guardrails con Infisical: **24 passed** (`test_routes_layering`, `test_superadmin_permissions`, `test_llm_observability`).

Lint/tipos: los 13 de ruff y 14 de mypy de la manana se corrigieron antes del commit `7bc1aea` (el hook de pre-commit los exige).

### Gaps reales (quien los cierra)

| Gap | Quien |
| --- | --- |
| Confirmar en proveedores que credenciales historicas de docs viejos estan rotadas (no basta con borrar texto) | Tu (Clerk, LLM, R2, Postgres, Redis, Langfuse, WA/TG) |
| Infisical `staging` y `prod` vacios | Tu |

## No hacer

- No borrar historico sin confirmar si esta versionado.
- No reescribir codigo funcional.
- No actualizar checklists antiguos como si fueran fuente de verdad.
