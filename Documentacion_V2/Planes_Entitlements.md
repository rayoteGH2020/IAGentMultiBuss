# Planes_Entitlements

Fecha: 2026-08-04 (diseno) · Actualizado: 2026-09-30
Estado: **implementado en codigo** — catalogo comercial `basic` | `advanced` | `premium`
(Pasos 02–04 + SADM). Seed: `app/core/entitlement_codes.py`; migraciones `p67`, `p74` (miembros), `p76` (presupuesto de IA) y `p77` (cupos mensuales).

> **Este documento describe el estado actual del codigo.** El objetivo de producto (limites mensuales, presupuesto de IA, cupos, `quota_pending`, etc.) esta en `especificacion-planes-y-cuotas.md`. Este documento se actualiza a medida que se implementa.

## 1. Objetivo

Convertir la aplicacion modular en un producto gobernado por planes:

- activar/desactivar modulos,
- limitar volumen y coste con **limites duros**,
- mostrar solo lo contratado,
- permitir upgrades (solo SADM, D016),
- evitar `if tenant.plan == ...` dispersos.

## 2. Estado actual (2026-09-23) — D012

Tres ofertas publicadas:

| Code | Nombre UI | Posicionamiento |
| --- | --- | --- |
| `basic` | Basico | Documentos + chat documental + knowledge/RAG + chat knowledge. |
| `advanced` | Avanzado | Basico + citas internas (BBDD saas) + WhatsApp/Telegram (chat knowledge). |
| `premium` | Premium | Mismas capacidades que Avanzado; **limites superiores** (duros). |

Regla comercial de limites:

- Los tres planes tienen **techos duros** en volumen y coste (no `null` / no ilimitado self-serve). No aplica al **historico visible**, que en Premium no tiene limite (`history_months`, `especificacion-planes-y-cuotas.md` §3.1, D017).
- Si el uso supera **Avanzado** → upgrade a **Premium**.
- Si el uso supera **Premium** → contrato custom / override SADM (no oferta self-serve).

Fuera de catalogo publicado (codigo conservado, no evolucionar ni publicitar):

- `calendar_google`, `calendar_voice` (D012).
- `analytics` (D011 vigente). El analista de datos para Premium queda para despues del producto minimo (D018, `especificacion-planes-y-cuotas.md` §4.5); al retomarlo, decision nueva que sustituya a D011.

Alias legacy: `free`/`medium` → `basic`; `high` → `advanced`; `total` → `premium`.

Pendiente / ops:

- Metodo de cobro de los planes: **pendiente de decidir** (D016, Backlog P3-2). Stripe retirado.
- Citas por WhatsApp/Telegram (parte de `appointments` + `channel_*` en Avanzado y Premium): hoy el canal solo resuelve bien el conocimiento; las citas pasan al modulo interno en `Paso12_ConexionWa_Tel_Calendario.md` (D030, Backlog P2-6). Al cerrarlo, actualizar la descripcion de Avanzado aqui y en `PLAN_META`.

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

## 4. Limites duros en el seed actual (D012)

> **Cifras vigentes de producto: `especificacion-planes-y-cuotas.md` §3** (limites mensuales, presupuesto de IA 6 / 15 / 30 EUR, chat D023, etc.).
> Esta tabla describe lo que hoy tiene el seed del codigo (`PLAN_LIMITS`). Difiere de la especificacion en los limites diarios, que esta sustituye por mensuales (pasos 3-4 de su §9 y D023, pendientes).
> Ya alineados: `members_max` 3 / 9 / 20 (D022, migracion `p74`) y `llm_budget_eur_month` 6 / 15 / 30 (D026, migracion `p76`).
> Limites mensuales de la spec §3 (facturas, tickets, reintentos, chat, contratos) en el catalogo desde `p77` (D027), aun sin aplicar: se activan por bloques y entonces se retiran los diarios de esta tabla.

| Limit code | Unidad | Basico | Avanzado | Premium |
| --- | --- | ---: | ---: | ---: |
| `documents_per_day` | ficheros | 50 | 200 | 800 |
| `document_retries_per_day` | reintentos | 20 | 80 | 300 |
| `knowledge_uploads_per_day` | ficheros | 25 | 60 | 200 |
| `knowledge_docs_max` | docs activos | 100 | 400 | 1500 |
| `chat_messages_per_day` | mensajes | 100 | 250 | 600 |
| `channel_messages_per_hour` | mensajes/cliente | 0 | 80 | 200 |
| `voice_notes_per_hour` | notas | 0 | 0 | 0 |
| `members_max` | seats | 3 | 9 | 20 |
| `llm_budget_eur_month` | EUR | 6 | 15 | 30 |
| `channel_external_slots` | integraciones | 0 | 2 | 2 |

## 5. Precios

Precios vigentes: **`especificacion-planes-y-cuotas.md` §2.2** (22 / 49 / 99 EUR al mes sin IVA; anual = 10 mensualidades). Sustituyen a la hipotesis inicial de D012 (59–349 EUR/mes), que queda descartada.

## 6. Implementacion

- Resolucion: `entitlement_service` + gates `require_feature`.
- Cuotas Redis + budget: `plan_quota_service` (duro: hoy bloquea al llegar al techo). La especificacion cambia los documentos a `quota_pending` sin bloquear la subida (§4.2 y §4.3), pendiente de implementar (bloque 2 del cierre del producto minimo).
- Cupos mensuales (D027): `monthly_quota_service` + tabla `quota_usage` (consumo atomico por bolsa, bolsa facturas + tickets, devoluciones, mes natural en hora de Espana). Base hecha; cada bloque del cierre lo conecta a su limite y retira el diario correspondiente.
- Presupuesto de IA (D019, D026): email al admin al 80 %, corte del chat y email al SADM al 90 %, bloqueo de toda la IA al 100 % (`ensure_llm_budget`).
- SADM `/sadm/plans`: assign + override permanente (`entitlements_override`) + ampliacion de un cupo solo para el mes en curso (`quota_usage.extra`, auditada). El presupuesto de IA no tiene ampliacion mensual: solo override permanente (Backlog P2b-27).
- Unico punto de cambio de plan: SADM (ningun rol de tenant, D016). La primera asignacion es inmediata; las siguientes se programan para el dia 1 del mes siguiente (`plan_change_service` + cron `apply_scheduled_plan_changes`, D027).
- Historial: `tenant_plan_changes`.

## 7. Decisiones

- D011: Analytics no se implementa (vigente hasta retomar el analista de Premium, D018).
- D012: catalogo Basico / Avanzado / Premium; calendar fuera de oferta; limites duros escalonados.
- D016: plan asignado solo por SADM; Stripe retirado; cobro pendiente de decidir.
- D017: historico visible (`history_months`) solo para facturas y tickets; contratos por vigencia. Pendiente de implementar (bloque 6).
- D018: analista de datos de Premium aplazado hasta despues del producto minimo.
- D019: presupuesto de IA con aviso al 80 % y corte del chat al 90 % (implementado).
- D022: `members_max` 3 / 9 / 20 (implementado, `p74`).
- D023: chat con un cupo mensual (`chat_questions_per_month`) y limite de ritmo, sin topes diarios. Pendiente de implementar (bloque 4).
- D026: `llm_budget_eur_month` 6 / 15 / 30 (implementado, `p76`).
- D027: cupos mensuales, periodo, carga inicial de contratos, cambios de plan programados y retirada de limites diarios (bloque 1 implementado, `p77`).
- D030: citas por WhatsApp/Telegram sobre el modulo interno, identidad del cliente final desde el webhook y antelacion minima de 24 h por tenant para cambiar o cancelar (pendiente, Paso12).
