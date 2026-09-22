# Paso07 - Chat, RAG, canales e IA segura

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
- Futuro analytics.

## Reglas comunes

- Mensajes con limite de longitud.
- Rate limit por usuario/tenant/canal.
- Tools tipadas.
- Sin SQL libre salvo analytics futuro.
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

- [ ] Abrir `/chat`, crear hilo, preguntar por documento.
- [ ] Ver citations.
- [ ] Ver Langfuse sin contenido.
- [ ] Enviar WhatsApp real.
- [ ] Enviar Telegram real.
- [ ] Crear evento por voz y confirmar en Google Calendar.

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
