# Paso03 - Gates por feature

Objetivo: aplicar los entitlements a rutas, sidebar, workers y webhooks.

## Dependencias

- Paso02 completado.

## Alcance

Implementar gates sin cuotas cuantitativas todavia.

Features iniciales:

- `documents`
- `documents_chat`
- `knowledge`
- `knowledge_chat`
- `calendar_google`
- `calendar_voice`
- `appointments`
- `channel_whatsapp`
- `channel_telegram`
- `analytics`

## Cambios esperados

```text
app/deps.py
app/core/templating.py
app/core/permissions.py
app/routes/web/documents.py
app/routes/web/knowledge.py
app/routes/web/chat.py
app/routes/web/calendar.py
app/routes/web/calendar_voice.py
app/routes/web/appointments.py
app/routes/web/integrations.py
app/routes/api/webhooks_whatsapp.py
app/routes/api/webhooks_telegram.py
app/jobs/*
app/templates/components/sidebar.html
app/templates/pages/errors/plan_required.html
tests/integration/test_plan_gates.py
```

## Reglas

- Deep-link a modulo no incluido: respuesta amable, no 500.
- HTMX recibe fragmento coherente o `HX-Redirect`.
- API JSON devuelve 403 con codigo estable.
- Webhooks firmados pero sin feature devuelven 200 y no encolan.
- Workers revalidan feature antes de llamar LLM.
- Sidebar solo muestra features incluidas.

## SADM

SADM no depende de planes comerciales. No crear feature `sadm_only` asignable a tenants.

## Tests

- [x] Tenant `basic` no ve `knowledge`.
- [x] Tenant `basic` no puede subir knowledge por POST directo.
- [x] Tenant `medium` si puede knowledge.
- [x] Tenant sin `appointments` no accede a citas.
- [x] Webhook WA firmado para tenant sin feature no encola.
- [x] Worker de documentos con feature off no llama LLM.
- [x] Sidebar cambia por plan.
- [x] SADM sigue accesible solo para SADM aunque el plan no incluya nada.

## Comandos

```powershell
infisical run -- uv run pytest tests/unit/test_role_nav_access.py tests/integration/test_plan_gates.py -q
infisical run -- uv run pytest tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py -q
infisical run -- uv run ruff check app tests
infisical run -- uv run mypy app
```

## Criterios de aceptacion

- [x] No hay modulo accesible solo por URL si el plan no lo incluye.
- [x] No se genera coste LLM para features off.
- [x] La UI no muestra modulos no contratados.
- [x] No se ha dispersado logica de plan en rutas.
