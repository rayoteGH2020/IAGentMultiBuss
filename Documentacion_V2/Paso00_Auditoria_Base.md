# Paso00 - Auditoria base y saneamiento documental

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

Guia vigente: `Documentacion_V2/` (`AGENTS.md` V2, `Arquitectura_V2.md`, `Decision_Log.md` D002). `Documentacion/` antigua no es backlog.

`Documentacion/`: versionada en git (`Documentacion/Leeme.txt`, `Documentacion/README.md`). En disco ya no existe (`git status` = `D`). El borrado **no esta commiteado**. Ultimo commit que las toco: `bf42c08`. Contenido en HEAD: nombres de comandos/variables, sin claves de alta entropia.

Secretos (sin copiar valores):

- `git grep` de patrones `sk_live_`, `sk_test_`, `AKIA`, `ghp_`, `xox`, JWT: sin coincidencias en el arbol versionado.
- `Documentacion_V2`: solo nombres de variable (`CLERK_SECRET_KEY`, etc.), no valores.
- `uv run detect-secrets scan --baseline .secrets.baseline` (excluye `.venv`, `uv.lock`, dumps locales): exit 0, sin hallazgos nuevos.

Migraciones (2026-09-22, Infisical + Postgres local):

- `docker compose -f docker/docker-compose.yml up -d postgres redis` (contenedores `saas-postgres`, `saas-redis`).
- `alembic heads` = `alembic current` = `p64_plans_entitlements_01`.

Guardrails con Infisical: **24 passed** (`test_routes_layering`, `test_superadmin_permissions`, `test_llm_observability`).

Lint/tipos (ejecutados, no limpios):

- `ruff check app tests`: 13 errores (imports, SIM117, noqa). 8 auto-fixables. No bloquean seguridad.
- `mypy app`: 14 errores en 5 ficheros (`document_processing_service`, `channel_chat_service`, `document_type_confirm_service`, `document_panel_service`, `config.py`).

### Gaps reales (quien los cierra)

| Gap | Quien |
| --- | --- |
| Confirmar en proveedores que credenciales historicas de docs viejos estan rotadas (no basta con borrar texto) | Tu (Clerk, LLM, R2, Postgres, Redis, Langfuse, WA/TG) |
| Commit del borrado de `Documentacion/` cuando cierres el working tree | Tu (git) |
| Ruff 13 + mypy 14 | Deuda de codigo; no es P0 de este paso |

## No hacer

- No borrar historico sin confirmar si esta versionado.
- No reescribir codigo funcional.
- No actualizar checklists antiguos como si fueran fuente de verdad.
