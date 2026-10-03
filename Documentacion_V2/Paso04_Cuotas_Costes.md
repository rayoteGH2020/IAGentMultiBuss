# Paso04 - Cuotas, limites y budgets por plan

Estado: **cerrado en codigo** (2026-09-23). No reabrir salvo cambio de limites comerciales.

Objetivo: cerrar el riesgo de coste no controlado mediante limites por plan.

## Dependencias

- Paso02 y Paso03 completados.

## Alcance

Implementar limites cuantitativos y circuit breaker de coste para:

- documentos por dia,
- reintentos por dia,
- knowledge uploads por dia,
- mensajes chat por dia,
- mensajes canal por hora,
- notas de voz por hora,
- maximo de miembros,
- presupuesto LLM mensual.

## Cambios esperados

```text
app/core/rate_limiter.py
app/services/usage_meter_service.py
app/services/chat_usage_service.py
app/services/document_processing_service.py
app/services/document_upload_service.py
app/services/knowledge_document_service.py
app/services/channel_chat_service.py
app/services/voice_event_service.py
app/services/membership_service.py
app/routes/web/settings.py
app/templates/pages/settings/billing.html
tests/unit/test_plan_limits.py
tests/integration/test_plan_quota_documents.py
tests/integration/test_plan_quota_chat.py
```

## Reglas

- Redis controla ventanas cortas: dia/hora.
- `usage_meter` controla agregados mensuales y billing.
- Budget mensual se lee antes de llamadas LLM caras.
- Si el limite se supera, la respuesta es clara y no llama al proveedor.
- SADM puede ver uso y, en paso posterior, ajustar plan/override.

## Mensajes de error

El usuario debe ver un mensaje de negocio:

- "Has alcanzado el limite diario de documentos de tu plan."
- "Has alcanzado el limite de mensajes de hoy."
- "Esta funcion no esta incluida en tu plan."

No exponer nombres internos como `documents_per_day` salvo logs.

## Tests

- [x] Upload bajo limite -> procesa.
- [x] Upload sobre limite -> no sube/no encola.
- [x] Retry sobre limite -> no reintenta.
- [x] Chat sobre limite -> no llama LLM.
- [x] Budget mensual superado -> bloquea LLM caro.
- [x] `total` con limite null no bloquea por cantidad, salvo kill-switch.
- [x] Contadores se incrementan una vez, sin doble conteo en retry fallido.

## Comandos

```powershell
infisical run -- uv run pytest tests/unit/test_plan_limits.py tests/integration/test_plan_quota_documents.py tests/integration/test_plan_quota_chat.py -q
infisical run -- uv run pytest tests/unit/test_usage_meter_service.py tests/integration/test_usage_service.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [x] Riesgo de extracciones/reintentos ilimitados queda cerrado.
- [x] No hay coste LLM despues de denegar por limite.
- [x] SADM puede auditar uso.
- [x] Settings globales siguen como defaults/kill-switch, no como plan comercial.
