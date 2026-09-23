# Planes_Entitlements

Fecha: 2026-08-04 (diseno) · Actualizado: 2026-09-23
Estado: **implementado en codigo** (Pasos 02–04 + SADM plans + Stripe Paso09). Matriz canonica de features/limites; seed en `app/core/entitlement_codes.py` y migracion `p64`.

## 1. Objetivo

Convertir la aplicacion modular en un producto gobernado por planes:

- activar/desactivar modulos,
- limitar volumen y coste,
- mostrar solo lo contratado,
- permitir upgrades (SADM y Stripe),
- evitar `if tenant.plan == ...` dispersos.

## 2. Estado actual (2026-09-23)

Implementado:

- Catalogo `plans` + `plan_entitlements` (`basic|medium|high|total`).
- `tenants.plan_code` (+ `plan` legacy alineado en writes).
- Resolucion unica: `entitlement_service`.
- Gates: `require_feature` (rutas, sidebar, workers, webhooks).
- Cuotas Redis + budget LLM (`plan_quota_service`).
- Override SADM: `tenants.settings.entitlements_override`.
- Kill-switch: `ENTITLEMENTS_DISABLED_FEATURES`.
- UI `/sadm/plans` (assign + override).
- Historial `tenant_plan_changes` (RLS) + audit log.
- Stripe: checkout/portal/webhook → `assign_tenant_plan` via `stripe_price_id`.
- `/settings/billing`: plan, uso, `billing_status`, acciones Stripe si hay claves.

Pendiente / ops:

- Rellenar `plans.stripe_price_id` y secretos Stripe en Infisical.
- Feature `analytics` en plan `total` sin producto Paso08.
- Revisar soft cap `llm_budget` en `total` para piloto (hoy puede ser `null` en matriz).

## 3. Planes iniciales

| Code 		| Nombre UI | Posicionamiento |
| --- 		| --- 		| --- |
| `basic` 	| Basico 	| Documentos ligeros + chat documental basico.   |
| `medium` 	| Medio 	| Basico + knowledge/RAG + chat sobre knowledge |
| `high` 	| Alto 		| Medio + citas + canales externos. |
| `total` 	| Total 	| Todo + analytics + calendario Google + limites altos. |

`free` legacy se migra a `basic`.

## 4. Features

| Feature code 			| basic | medium | high	 		| total |
| --- 					| --- 	| --- 	 | --- 	 		| --- 	|
| `documents` 			| yes 	| yes 	 | yes	 		| yes |
| `documents_chat` 		| yes 	| yes 	 | yes	 		| yes |
| `knowledge` 			| no 	| yes 	 | yes	 		| yes |
| `knowledge_chat` 		| no 	| yes 	 | yes	 		| yes |
| `calendar_google` 	| no	| no 	 | no			| yes |
| `calendar_voice` 		| no	| no 	 | no	 		| yes |
| `appointments` 		| no 	| no 	 | yes 	 		| yes |
| `channel_whatsapp`	| no	| no 	 | yes 			| yes |
| `channel_telegram` 	| no	| no	 | yes 			| yes |
| `analytics` 			| no 	| no 	 | no 	 		| yes |

## 5. Limites iniciales

| Limit code 					| Unidad 			| basic 	| medium 	| high 	| total |
| --- 							| --- 				| ---: 		| ---: 		| ---: 	| ---: |
| `documents_per_day` 			| ficheros 			| 30 		| 100 		| 300 	| 1000 |
| `document_retries_per_day` 	| reintentos 		| 10 		| 30 		| 100 	| 500 |
| `knowledge_uploads_per_day` 	| ficheros 			| 0 		| 20 		| 50 	| 200 |
| `knowledge_docs_max` 			| docs activos 		| 0 		| 50 		| 200	| 1000 |
| `chat_messages_per_day` 		| mensajes 			| 40 		| 80 		| 150	| 400 |
| `channel_messages_per_hour` 	| mensajes/cliente  | 0 		| 0 		| 60 	| 120 |
| `voice_notes_per_hour` 		| notas 			| 0 		| 0 		| 0		| 60 |
| `members_max` 				| seats 			| 3 		| 10 		| 25 	| 100 |
| `llm_budget_eur_month` 		| EUR 				| 5 		| 25 		| 80 	| null |
| `channel_external_slots` 		| integraciones 	| 0 		| 0 		| 2 	| 2 |

`null` significa ilimitado solo si el plan u override lo declara explicitamente.

### 5.1 Coste neto estimado y precio orientativo

Estos importes son una hipotesis inicial para pricing. Deben revisarse tras 2-4
semanas de uso real con `llm_calls`, `usage_meter` y metricas SADM.

Definicion de coste neto estimado:

- Incluye coste variable esperado de LLM, embeddings, storage/R2 marginal, Redis/colas y margen de infraestructura compartida.
- No incluye IVA, IRPF/sociedades, soporte humano intensivo, comisiones Stripe, coste comercial ni horas de desarrollo.
- No asume que todos los limites se consumen al 100%; si un tenant consume el techo completo de forma sostenida, deben aplicarse cuotas, budget mensual u override comercial.

| Plan 		| Coste neto estimado/mes 	| Techo LLM recomendado 				| Precio inicial sugerido al cliente 	| Precio objetivo cuando haya datos |
| --- 		| ---: 						| ---: 									| ---: 									| ---: |
| `basic` 	| 2-5 EUR 					| 5 EUR 								| 29 EUR/mes 							| 39 EUR/mes |
| `medium` 	| 8-18 EUR 					| 25 EUR 								| 69 EUR/mes 							| 89-99 EUR/mes |
| `high` 	| 25-60 EUR 				| 80 EUR 								| 149 EUR/mes 							| 179-199 EUR/mes |
| `total` 	| 80-180 EUR 				| 200 EUR inicial, no `null` en piloto 	| desde 299 EUR/mes 					| 399-499 EUR/mes o custom |

Recomendaciones:

- En piloto, no dejar `llm_budget_eur_month = null` para `total`: usar un soft cap inicial de 200 EUR y subirlo por override SADM si el cliente lo justifica.
- Mantener margen bruto objetivo minimo del 70% en `basic`/`medium` y revisar `high`/`total` cliente a cliente.
- Si un cliente usa muchos canales externos, analytics o documentos largos, pasar a precio custom antes de aumentar limites.
- Registrar precios sin IVA en la documentacion comercial y aplicar IVA segun fiscalidad.

Referencias de coste:

- El codigo fuente usa `app/llm/pricing.py` como tabla operativa en EUR por millon de tokens.
- Revisar tarifas oficiales de Anthropic, Google Gemini y Voyage antes de cerrar precios publicos.

## 6. Modelo de datos objetivo

```text
plans
- id
- code unique
- name
- description
- sort_order
- is_active
- is_public
- stripe_price_id nullable
- created_at
- updated_at

plan_entitlements
- id
- plan_id
- kind: feature | limit
- code
- enabled nullable
- limit_value nullable
- unique(plan_id, kind, code)

tenant_plan_changes
- id
- tenant_id
- from_plan_code
- to_plan_code
- changed_by_user_id
- reason
- metadata
- created_at
```

`plans` y `plan_entitlements` son catalogo global sin RLS tenant. `tenant_plan_changes` lleva RLS.

## 7. Codigo objetivo

```text
app/core/entitlement_codes.py
app/models/plan.py
app/schemas/entitlements.py
app/services/entitlement_service.py
app/services/plan_service.py
app/deps.py
app/core/rate_limiter.py
app/routes/web/admin/plans.py
app/templates/pages/sadm/plans/*
```

DTO:

```python
class Entitlements(BaseModel):
    plan_code: str
    features: frozenset[str]
    limits: dict[str, Decimal | None]

    def has(self, feature: str) -> bool: ...
    def limit(self, code: str) -> Decimal | None: ...
```

## 8. Resolucion

Prioridad:

1. Kill-switch global de `Settings`.
2. Override `tenants.settings["entitlements_override"]`.
3. Catalogo del plan.
4. Fail-closed.

No resolver multiples veces por request: cachear en `request.state.entitlements`.

## 9. Enforcement

Cada feature se aplica en:

- rutas web/API,
- sidebar,
- services,
- workers,
- webhooks,
- templates,
- QA manual.

Ejemplo:

```python
RequireKnowledge = Annotated[Entitlements, Depends(require_feature("knowledge"))]
```

Workers:

```python
ents = await entitlement_service.resolve_tenant(db, tenant_id)
if not ents.has("documents"):
    mark_failed_without_llm(...)
    return
```

Webhooks:

- si firma valida pero feature no incluida: `200` + log + no job.

## 10. Tests minimos

- Merge plan + override.
- Plan inexistente fail-closed.
- Feature off -> 403 o pagina plan required.
- Feature on -> 200.
- Sidebar oculta modulos.
- Worker no llama LLM si feature off.
- Webhook no encola si feature off.
- Limite `null` se interpreta como unlimited.
- SADM cambia plan y audit log queda registrado.
