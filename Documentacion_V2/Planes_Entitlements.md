# Planes_Entitlements

Fecha: 2026-08-04 (diseno) · Actualizado: 2026-09-23 (D012)
Estado: **implementado en codigo** — catalogo comercial `basic` | `advanced` | `premium`
(Pasos 02–04 + SADM + Stripe). Seed: `app/core/entitlement_codes.py`; migracion `p67`.

## 1. Objetivo

Convertir la aplicacion modular en un producto gobernado por planes:

- activar/desactivar modulos,
- limitar volumen y coste con **limites duros**,
- mostrar solo lo contratado,
- permitir upgrades (SADM y Stripe),
- evitar `if tenant.plan == ...` dispersos.

## 2. Estado actual (2026-09-23) — D012

Tres ofertas publicadas:

| Code | Nombre UI | Posicionamiento |
| --- | --- | --- |
| `basic` | Basico | Documentos + chat documental + knowledge/RAG + chat knowledge. |
| `advanced` | Avanzado | Basico + citas internas (BBDD saas) + WhatsApp/Telegram (chat knowledge). |
| `premium` | Premium | Mismas capacidades que Avanzado; **limites superiores** (duros). |

Regla comercial de limites:

- Los tres planes tienen **techos duros** (no `null` / no ilimitado self-serve).
- Si el uso supera **Avanzado** → upgrade a **Premium**.
- Si el uso supera **Premium** → contrato custom / override SADM (no oferta self-serve).

Fuera de catalogo publicado (codigo conservado, no evolucionar ni publicitar):

- `calendar_google`, `calendar_voice` (D012).
- `analytics` (D011 — no implementar).

Alias legacy: `free`/`medium` → `basic`; `high` → `advanced`; `total` → `premium`.

Pendiente / ops:

- Rellenar `plans.stripe_price_id` (basic/advanced/premium) + secretos Stripe Infisical.

## 3. Features

| Feature code | Basico | Avanzado | Premium |
| --- | :---: | :---: | :---: |
| `documents` | yes | yes | yes |
| `documents_chat` | yes | yes | yes |
| `knowledge` | yes | yes | yes |
| `knowledge_chat` | yes | yes | yes |
| `appointments` | no | yes | yes |
| `channel_whatsapp` | no | yes | yes |
| `channel_telegram` | no | yes | yes |
| `calendar_google` | no* | no* | no* |
| `calendar_voice` | no* | no* | no* |
| `analytics` | no | no | no |

\* Disponible solo via override SADM si hace falta un piloto interno; no marketing.

## 4. Limites duros (propuesta D012)

| Limit code | Unidad | Basico | Avanzado | Premium |
| --- | --- | ---: | ---: | ---: |
| `documents_per_day` | ficheros | 50 | 200 | 800 |
| `document_retries_per_day` | reintentos | 20 | 80 | 300 |
| `knowledge_uploads_per_day` | ficheros | 25 | 60 | 200 |
| `knowledge_docs_max` | docs activos | 100 | 400 | 1500 |
| `chat_messages_per_day` | mensajes | 100 | 250 | 600 |
| `channel_messages_per_hour` | mensajes/cliente | 0 | 80 | 200 |
| `voice_notes_per_hour` | notas | 0 | 0 | 0 |
| `members_max` | seats | 5 | 15 | 40 |
| `llm_budget_eur_month` | EUR | 30 | 100 | 250 |
| `channel_external_slots` | integraciones | 0 | 2 | 2 |

## 5. Precios orientativos (sin IVA)

Hipotesis inicial; revisar con `llm_calls` / SADM tras 2–4 semanas.

| Plan | Precio piloto sugerido | Objetivo | Techo LLM |
| --- | ---: | ---: | ---: |
| Basico | 59–79 EUR/mes | 79–99 EUR/mes | 30 EUR |
| Avanzado | 149–179 EUR/mes | 179–199 EUR/mes | 100 EUR |
| Premium | 249–299 EUR/mes | 299–349 EUR/mes | 250 EUR |

Anual: ~2 meses de descuento. Setup WA/TG: 99–299 EUR opcional.

## 6. Implementacion

- Resolucion: `entitlement_service` + gates `require_feature`.
- Cuotas Redis + budget: `plan_quota_service` (duro: bloquea al techo).
- SADM `/sadm/plans`: assign + override.
- Stripe: `price_id` → `plan_code` via `assign_tenant_plan`.
- Historial: `tenant_plan_changes`.

## 7. Decisiones

- D011: Analytics no se implementa.
- D012: catalogo Basico / Avanzado / Premium; calendar fuera de oferta; limites duros escalonados.
