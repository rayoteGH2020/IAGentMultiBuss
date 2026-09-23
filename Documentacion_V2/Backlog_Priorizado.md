# Backlog_Priorizado

Fecha actualizacion: 2026-09-23
Estado: alineado con codigo en `RamaCursor01` (HEAD de planes `p64` + Stripe `p65` en working tree).

Leyenda: **Hecho** = en codigo y tests. **Ops** = falta accion humana / entorno. **Pendiente** = producto no implementado.

## P0 - Seguridad y control de coste

| # | Item | Estado |
| --- | --- | --- |
| 1 | Rotar y sanear secretos de documentacion historica | **Ops** (repo saneado; rotacion en proveedores pendiente) |
| 2 | Sync roles/memberships Clerk | **Hecho** (+ QA retry real en Dashboard: ops) |
| 3 | Dedupe anti-replay webhooks | **Hecho** (Clerk/WA/TG/Stripe) |
| 4 | Limite de body webhooks | **Hecho** |
| 5 | OCR knowledge + `media_limits` | **Hecho** |
| 6 | Catalogo de planes | **Hecho** (Paso02, `p64`) |
| 7 | Gates por plan | **Hecho** (Paso03) |
| 8 | Cuotas y budgets por plan | **Hecho** (Paso04) |

## P1 - Plataforma y producto base

| # | Item | Estado |
| --- | --- | --- |
| 1 | Consolidar SADM V2 | **Hecho** (Paso05) |
| 2 | Asignar planes desde SADM | **Hecho** (`/sadm/plans`) |
| 3 | `/settings/billing` plan/uso + Stripe | **Hecho** en codigo (Paso09); Price IDs + Infisical: **Ops** |
| 4 | UX documentos procesando/rechazados | **Hecho** (Paso06) |
| 5 | Verificacion de tipo antes de extraccion | **Hecho** (Paso06) |
| 6 | UI multi-IVA | **Hecho** (Paso06) |
| 7 | QA real Clerk/R2/Langfuse/Calendar/WA/TG | **Ops** (Paso07 QA manual) |

## P2 - IA y canales

| # | Item | Estado |
| --- | --- | --- |
| 1 | Chat documental y citations | **Hecho** (Paso07 codigo) |
| 2 | QA y evals de RAG | **Hecho** en suite; regresion continua |
| 3 | Cache semantica canales + invalidacion | **Hecho** |
| 4 | Alertas de coste por turnos chat/tools | Parcial (`usage_meter` / SADM); alertas push no |
| 5 | Prompts contratos/seguros | **Hecho** base (Paso06) |

## P3 - Nuevos modulos

| # | Item | Estado |
| --- | --- | --- |
| 1 | Analytics SQL read-only | **Pendiente** (Paso08 / F2) |
| 2 | Stripe billing | **Hecho** en codigo (Paso09 / F1); operativa Stripe Dashboard: **Ops** |
| 3 | Resenas/marketing | **Pendiente** (no priorizado) |
| 4 | MCP/tooling externo | **Pendiente** (no priorizado) |

## No implementar ahora

- Reescritura total.
- Switcher multi-org.
- Microservicios.
- Kubernetes.
- GraphQL.
- React/Vue/Svelte.
- LangChain/LlamaIndex como base.

## Orden recomendado restante

1. Ops: Infisical staging/prod, rotacion credenciales, QA manual Paso07.
2. Ops Stripe: Price IDs + webhook + claves Infisical.
3. Soft-launch Paso10 cuando staging este vivo.
4. Producto nuevo: Paso08 Analytics si se vende el plan `total` con BI.
