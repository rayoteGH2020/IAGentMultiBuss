# Paso09 - Billing con Stripe

Estado: **codigo cerrado / ops pendiente** (2026-09-23). Checkout, portal, webhook, `assign_tenant_plan`, `tenant_plan_changes`, `billing_status` (`p65`). Falta Price IDs + claves Infisical + webhook Dashboard.

Objetivo: conectar cobro real solo despues de tener planes funcionales.

## Dependencias

- Paso02 completado.
- Paso03 completado.
- Paso04 completado.
- SADM puede asignar planes manualmente.

## Alcance

- Mapear `plans.stripe_price_id`.
- Checkout.
- Customer Portal.
- Webhooks firmados.
- Cambio de plan por evento Stripe.
- Estado `past_due` o cancelado.

## Fuera de alcance

- Decidir capacidades en Stripe.
- Confiar en datos enviados por navegador.
- Activar features sin `plan_code`.

## Reglas

- Stripe es fuente de pago, no de permisos internos.
- El webhook firmado cambia `tenant.plan_code`.
- `plan_service.assign_tenant_plan()` es el punto unico.
- Todo cambio de plan escribe `tenant_plan_changes` y audit log.
- Fallos de webhook son idempotentes y reintentables.

## Decisiones comerciales (codigo)

- `past_due` / `invoice.payment_failed`: `billing_status=past_due`, **el plan se mantiene**.
- Suscripcion cancelada/eliminada (`canceled`, `unpaid`, `deleted`): downgrade a `basic` + `billing_status=canceled`.
- Suscripcion `active`/`trialing`: asigna plan por `stripe_price_id` + `billing_status=active`.
- `price_id` sin plan en catalogo: log de error, **no** cambia el plan.

## Eventos minimos

- [x] `checkout.session.completed`.
- [x] `customer.subscription.created`.
- [x] `customer.subscription.updated`.
- [x] `customer.subscription.deleted`.
- [x] `invoice.payment_failed`.

## Implementado (2026-09-23)

- Migracion `p65_stripe_billing_01`: `tenants.stripe_*`, `billing_status`, tabla `tenant_plan_changes` + RLS.
- `app/services/stripe_billing_service.py`: checkout, portal, handler de eventos.
- `POST /api/webhooks/stripe` (firma + body limit + dedupe Redis).
- `/settings/billing`: uso + botones checkout/portal (admin, HTMX).
- Settings Infisical: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY`.

## Tests

- [x] Firma invalida -> 400/AuthError.
- [x] Evento repetido -> idempotente.
- [x] Subscription/checkout activa -> plan asignado.
- [x] Payment failed -> `past_due` sin quitar plan.
- [x] Cancelacion -> downgrade a `basic`.
- [x] Plan inexistente para `price_id` -> no cambiar y alertar (log).

## Comandos

```powershell
infisical run -- uv run alembic upgrade head
infisical run -- uv run pytest tests/unit/test_stripe_billing.py tests/integration/test_stripe_webhook.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Operativa Stripe Dashboard

1. Crear Products/Prices y copiar cada Price ID a `plans.stripe_price_id` (SQL o futuro SADM).
2. Webhook endpoint: `https://<dominio>/api/webhooks/stripe` con los eventos de arriba.
3. Guardar signing secret en Infisical (`STRIPE_WEBHOOK_SECRET`) y la secret key (`STRIPE_SECRET_KEY`).
4. Customer Portal: activar en Stripe Dashboard (Billing → Customer portal).

## Criterios de aceptacion

- [x] Un pago (evento checkout/subscription) activa el plan correcto via `assign_tenant_plan`.
- [x] Un evento falso (firma invalida) no cambia nada.
- [x] Un evento duplicado no duplica cambios (Redis claim).
- [x] La UI de billing refleja plan, uso, estado de cobro y acciones Stripe si hay claves.
