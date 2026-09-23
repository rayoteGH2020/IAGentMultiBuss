# Paso07 - Chat, RAG, canales e IA segura

Estado: **codigo cerrado / QA manual pendiente** (2026-09-23). Suite automatica verde; falta QA real Clerk/R2/Langfuse/Calendar/WA/TG.

Objetivo: endurecer todas las superficies conversacionales antes de ampliar alcance.

## Dependencias

- Paso01 completado.
- Paso03/Paso04 recomendados para gates y cuotas.

## Superficies

- `/chat` documental.
- Knowledge/RAG web.
- WhatsApp.
- Telegram.
- Voz a calendario.
- Futuro analytics. → **Anulado** (D011 — no se implementa).

## Reglas comunes

- Mensajes con limite de longitud.
- Rate limit por usuario/tenant/canal.
- Tools tipadas.
- Sin SQL libre (incluido analytics externo: D011 no implementado).
- Citas y grounding.
- Audit log de mensajes y tool calls.
- Langfuse metadata-only.
- Evals para cambios de prompt/tools.
- Respuestas de canal con umbral de confianza.

## Tareas

### Chat documental

- [x] Revisar que tools no devuelven `raw_extraction`.
- [x] Revisar que citations apuntan a datos existentes.
- [x] Confirmar RLS entre tenants.
- [x] Confirmar hide thread no borra datos ni cruza usuario.
- [x] Evals `chat_documents_v1`.

### Knowledge/RAG

- [x] OCR imagenes pasa por limites.
- [x] Reindex invalida cache si aplica.
- [x] Retrieval hibrido tiene evals.
- [x] Chunks no exponen tenant incorrecto.

### Canales externos

- [x] Dedupe anti-replay.
- [x] Gates por plan.
- [x] Limites por hora.
- [x] Confidence threshold.
- [x] Escalado/fallback si baja confianza.
- [x] Cache semantica no cruza tenants ni queda obsoleta indefinidamente.

### Voz

- [x] Limite bytes/duracion/MIME.
- [x] Confirmacion antes de crear evento si confianza baja.
- [x] Google token cifrado.
- [x] Audit log.

## Tests

```powershell
infisical run -- uv run pytest tests/unit/test_chat_tools.py tests/unit/test_chat_tool_runner.py tests/unit/test_chat_service.py tests/unit/test_knowledge_tools.py tests/unit/test_chat_tool_security.py tests/unit/test_chat_citations_security.py tests/unit/test_channel_cache_invalidation.py -q
infisical run -- uv run pytest tests/integration/test_chat_flow.py tests/integration/test_chat_rls_tools.py tests/integration/test_knowledge_chat.py -q
infisical run -- uv run pytest tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py tests/unit/test_voice_event_service.py -q
infisical run -- uv run python -m app.evals.runners.chat_documents
infisical run -- uv run python -m app.evals.runners.knowledge_retrieval
```

## QA manual

Automatizado el 2026-09-22 (70 passed, ~39 s), sin LLM real:

```powershell
infisical run -- uv run pytest tests/unit/test_chat_tools.py tests/unit/test_chat_tool_runner.py tests/unit/test_chat_service.py tests/unit/test_knowledge_tools.py tests/unit/test_chat_tool_security.py tests/unit/test_chat_citations_security.py tests/unit/test_channel_cache_invalidation.py tests/unit/test_voice_event_service.py tests/unit/test_plan_gates.py tests/integration/test_chat_flow.py tests/integration/test_chat_rls_tools.py tests/integration/test_knowledge_chat.py tests/integration/test_chat_web.py tests/integration/test_plan_admin_routes.py tests/integration/test_plan_gates.py -q -m "not real_llm"
```

Eso cubre tools sin `raw_extraction`, citations, RLS de chat, gates de plan, asignacion SADM de plan y voz con confirmacion. No sustituye un login humano.

Smoke en `http://127.0.0.1:8000` el mismo dia:

- `/login` pinta Clerk ("Sign in to MySaas", development mode).
- `/chat`, `/documents`, `/sadm` y `/calendar` sin cookie devuelven 401; el navegador acaba en `/login`.

Sigue pendiente con tu sesion:

- [ ] Abrir `/chat`, crear hilo, preguntar por documento.
- [ ] Ver citations.
- [ ] Ver Langfuse sin contenido (la instancia local esta levantada: `saas-langfuse-web`).
- [ ] Enviar WhatsApp real.
- [ ] Enviar Telegram real.
- [ ] Crear evento por voz y confirmar en Google Calendar.
- [ ] SADM: asignar plan en la UI y comprobar que una feature denegada no aparece.

## Criterios de aceptacion

- [x] Ningun chat cruza tenants.
- [x] Ningun canal responde sin firma valida.
- [x] No hay coste sin cuota.
- [x] Langfuse no contiene texto de usuario ni respuesta.
- [x] Evals no caen por debajo de umbrales acordados.

## Notas de implementacion (cierre)

- Rate chat: tenant (plan) + usuario (`chat_user_daily_message_limit`).
- Hard caps: `chat_max_messages_per_thread`, `chat_max_context_chars`, prefiltro anti-exfil.
- Citations: `filter_citations_existing_for_tenant` antes de persistir.
- Reindex/delete knowledge invalida `channel_response_cache` del tenant.
- Tool allowlist fail-closed si no hay entitlements.
