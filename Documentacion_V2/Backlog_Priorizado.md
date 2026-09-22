# Backlog_Priorizado

Fecha: 2026-08-04

## P0 - Seguridad y control de coste

1. Rotar y sanear secretos detectados en documentacion historica.
2. Verificar sync de roles/memberships desde Clerk.
3. Anadir dedupe anti-replay a webhooks externos.
4. Cerrar limite de body de webhooks.
5. Cerrar OCR de imagenes knowledge con `media_limits`.
6. Implementar catalogo de planes.
7. Implementar gates por plan.
8. Implementar cuotas y budgets por plan.

## P1 - Plataforma y producto base

1. Consolidar SADM segun alcance V2.
2. Asignar planes desde SADM.
3. Mejorar `/settings/billing` como vista de plan/uso.
4. Cerrar UX de documentos procesando/rechazados.
5. Verificacion de tipo documental antes de extraccion cara.
6. UI de multi-IVA clara: si hay varios tramos, mostrar "Multiple" o desglose.
7. QA real de Clerk, R2, Langfuse, Google Calendar, WhatsApp y Telegram.

## P2 - IA y canales

1. Reforzar chat documental y citations.
2. QA y evals de RAG.
3. Cache semantica de canales con invalidacion clara.
4. Alertas de coste por turnos de chat/tools.
5. Mejoras de prompts de contratos y seguros.

## P3 - Nuevos modulos

1. Analytics SQL read-only.
2. Stripe billing.
3. Reseñas/marketing si se decide como feature nueva.
4. MCP/tooling externo si aporta a operaciones.

## No implementar ahora

- Reescritura total.
- Switcher multi-org.
- Microservicios.
- Kubernetes.
- GraphQL.
- React/Vue/Svelte.
- LangChain/LlamaIndex como base.
