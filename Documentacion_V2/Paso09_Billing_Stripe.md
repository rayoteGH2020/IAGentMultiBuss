# Paso09 - Billing con Stripe

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
- `plan_service.assign_plan()` es el punto unico.
- Todo cambio escribe `tenant_plan_changes` y audit log.
- Fallos de webhook son idempotentes y reintentables.

## Eventos minimos

- `checkout.session.completed`.
- `customer.subscription.created`.
- `customer.subscription.updated`.
- `customer.subscription.deleted`.
- `invoice.payment_failed`.

## Tests

- [ ] Firma invalida -> 400.
- [ ] Evento repetido -> idempotente.
- [ ] Subscription activa -> plan asignado.
- [ ] Payment failed -> estado segun decision comercial.
- [ ] Cancelacion -> downgrade o read-only segun decision.
- [ ] Plan inexistente para `price_id` -> no cambiar y alertar.

## Comandos

```powershell
infisical run -- uv run pytest tests/integration/test_stripe_webhook.py tests/unit/test_plan_service.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [ ] Un pago activa el plan correcto.
- [ ] Un evento falso no cambia nada.
- [ ] Un evento duplicado no duplica cambios.
- [ ] La UI de billing refleja plan y uso.
