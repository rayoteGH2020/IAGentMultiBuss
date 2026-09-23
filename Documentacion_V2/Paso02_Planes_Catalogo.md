# Paso02 - Catalogo de planes y resolucion de entitlements

Estado: **cerrado en codigo** (2026-09-23). No reabrir salvo cambio de matriz comercial.

Objetivo: crear la base de planes sin bloquear todavia UI ni workers.

## Dependencias

- Paso01 cerrado o riesgos aceptados explicitamente.
- `Planes_Entitlements.md` aprobado.

## Alcance

Implementar:

- codigos de features y limites,
- modelos `Plan` y `PlanEntitlement`,
- migracion Alembic,
- seed inicial,
- `tenants.plan_code`,
- servicio de resolucion,
- tests.

No implementar todavia:

- gates en rutas,
- cuotas,
- Stripe,
- UI editable completa.

## Cambios esperados

```text
app/core/entitlement_codes.py
app/models/plan.py
app/schemas/entitlements.py
app/services/entitlement_service.py
app/services/plan_service.py
migrations/versions/<rev>_plans_entitlements.py
tests/unit/test_entitlement_service.py
tests/integration/test_plan_catalog.py
```

## Modelo

- `plans`: catalogo global sin `tenant_id`.
- `plan_entitlements`: filas `feature` y `limit`.
- `tenants.plan_code`: nuevo campo, backfill desde `tenants.plan`.
- Mantener `tenants.plan` una migracion mas si se necesita compatibilidad.

## Reglas de resolucion

1. Feature desconocida -> deny.
2. Limit desconocido -> valor conservador o deny.
3. Plan inexistente -> fail-closed.
4. Override solo acepta codigos conocidos.
5. `null` en limite significa unlimited solo si esta declarado.

## Tests

- [x] Seed crea `basic`, `medium`, `high`, `total`.
- [x] `free` legacy migra a `basic`.
- [x] `basic` no tiene `knowledge`.
- [x] `total` tiene `analytics`.
- [x] Override puede reducir y ampliar limites conocidos.
- [x] Override con codigo desconocido falla.
- [x] Plan inexistente fail-closed.
- [x] `mypy` pasa.

## Comandos

```powershell
infisical run -- uv run alembic revision --autogenerate -m "add plan entitlements"
infisical run -- uv run alembic upgrade head
infisical run -- uv run pytest tests/unit/test_entitlement_service.py tests/integration/test_plan_catalog.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [x] El catalogo existe y se puede resolver por tenant.
- [x] Ninguna ruta cambia comportamiento todavia.
- [x] La resolucion esta centralizada.
- [x] No hay `if tenant.plan == ...` nuevo.
