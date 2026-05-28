# Paso 21 — Canales externos: ingesta URL, FAQ manual, WhatsApp y Telegram

## Objetivo

Completar el módulo 2 con cuatro capacidades:

1. **Ingesta por URL** — indexar contenido web como fuente RAG sin subir fichero.
2. **Editor de FAQ manual** — crear/editar preguntas frecuentes directamente desde la UI.
3. **Configuración de canales externos** — tabla `channel_integrations` + tarjetas UI en `/settings/integrations` para que cada tenant registre su número de WhatsApp Business y/o su bot de Telegram.
4. **Canales WhatsApp y Telegram** — recibir mensajes de clientes vía webhook, procesarlos con el pipeline RAG del Paso 20 y responder automáticamente.

Al final del paso, la base de conocimiento se nutre desde tres orígenes (fichero, URL, FAQ), y el chatbot responde tanto desde `/chat` web como desde WhatsApp y Telegram con la base de conocimiento **del tenant correspondiente**.

## Pre-requisitos

- Pasos 18–20 completados: ingesta ficheros operativa, búsqueda híbrida lista, chat RAG con citas funcionando.
- Cuenta **WhatsApp Business API** (Meta for Developers): número verificado, app creada.
- Bot de **Telegram** creado vía `@BotFather` (uno por tenant).
- `ENCRYPTION_KEY` en Infisical (ya usada para Google Calendar; misma clave para cifrar tokens de canal).
- Librería `html2text` para limpieza de HTML en scraping.

## Contexto relevante

| Documento | Sección |
|-----------|---------|
| `arquitectura.md` | §5 (modelo datos: `conversations`, `messages`), §6 módulo 2 (WhatsApp webhook, escalado), §9 (seguridad) |
| `AGENTS.md` | `routes/api/` devuelve JSON; webhooks en `routes/api/`; cifrado tokens; `require_role("admin")` |
| `Paso17.md` | Patrón `CalendarIntegration`: modelo, servicio, tarjeta UI, cifrado con `ENCRYPTION_KEY` |
| `Paso18.md` | Pipeline ingesta: chunking, embed, `index_knowledge_document` worker |
| `Paso19.md` | `knowledge_search_service`, tools de conocimiento |
| `Paso20.md` | Chat RAG con citas, `chat_service`, `answer_for_channel()` |

## Alcance

### Dentro de Paso 21

- Sub-módulo A: Ingesta por URL.
- Sub-módulo B: FAQ manual.
- Sub-módulo C: Modelo `ChannelIntegration` + migración.
- Sub-módulo D: Tarjetas UI en `/settings/integrations` (WhatsApp + Telegram), solo accesibles por `admin`.
- Sub-módulo E: Webhook WhatsApp + pipeline RAG + respuesta automática + escalado.
- Sub-módulo F: Webhook Telegram + pipeline RAG + respuesta automática + escalado.

### Fuera de Paso 21

- Editor WYSIWYG avanzado.
- OCR de PDFs escaneados.
- Reranker externo.
- Billing por canal (Stripe).
- Canal de email.

---

## Arquitectura global de canales externos

```
Cliente (WhatsApp/Telegram)
        │
        ▼
routes/api/webhooks_whatsapp.py   routes/api/webhooks_telegram.py
        │                                    │
        ├── verify_signature()               ├── verify_signature()
        ├── lookup tenant                    ├── lookup tenant
        │   (channel_integrations            │   (channel_integrations
        │    WHERE channel='whatsapp'        │    WHERE channel='telegram'
        │    AND phone_number_id=X)          │    AND integration_id=path)
        │                                    │
        └─────────────────┬──────────────────┘
                          │
                 ARQ job: process_channel_message
                          │
              channel_chat_service.answer_for_channel()
                          │
              knowledge_search_service (RAG Paso 20)
                          │
              LLMClient.complete(task='chat')
                          │
              whatsapp_client / telegram_client → respuesta al cliente
```

---

## Sub-módulo A — Ingesta URL

### A.1 — Configuración

- [ ] Ampliar `app/config.py`:
  ```python
  # Ingesta URL (Paso 21)
  knowledge_url_max_size_bytes: int = 2 * 1024 * 1024
  knowledge_url_timeout_s: int = 30
  knowledge_url_blacklist: list[str] = []
  knowledge_url_allowed_schemes: list[str] = ["https"]
  knowledge_url_max_per_day_per_tenant: int = 20
  ```

### A.2 — Scraping

- [ ] Crear `app/core/web_scraper.py`:
  - [ ] `async def scrape_url(url: str, settings: Settings) -> ScrapedResult`:
    - Validar URL (scheme, blacklist, longitud).
    - `httpx.AsyncClient` con timeout `knowledge_url_timeout_s`.
    - Respetar `robots.txt` (simplificado: si `Disallow: /` → abortar).
    - Convertir HTML → texto plano con `html2text`.
    - Truncar a `knowledge_url_max_size_bytes`.
    - `ScrapedResult(text, title, final_url, char_count)`.
  - [ ] Errores HTTP → `ScrapingError` con mensaje descriptivo.

### A.3 — Migración y modelo

- [ ] Verificar que `source_url text null` existe en `knowledge_documents`. Si no:
  - [ ] Migración `p21_a_knowledge_url`: `ALTER TABLE knowledge_documents ADD COLUMN source_url text`.
  - [ ] Actualizar `KnowledgeDocument.source_url: Mapped[str | None]`.

### A.4 — Worker

- [ ] Crear `app/jobs/knowledge_url_jobs.py`:
  - `index_knowledge_url(ctx, document_id, tenant_id)` — scraping + reutilizar `run_index_pipeline()` de Paso 18.
- [ ] Registrar en `app/jobs/settings.py` y `app/jobs/queue.py`.

### A.5 — Rutas y UI

- [ ] Ampliar `app/routes/web/knowledge.py`: `GET/POST /knowledge/url`.
- [ ] Actualizar `pages/knowledge/index.html`: tab «Añadir URL».

### A.6 — Tests

- [ ] `tests/unit/test_web_scraper.py`: mocks con `respx`; errores HTTP; robots.txt.
- [ ] `tests/integration/test_knowledge_url.py`: URL → job → `chunk_count > 0`.

---

## Sub-módulo B — FAQ manual

### B.1 — Configuración

- [ ] Ampliar `app/config.py`:
  ```python
  knowledge_faq_max_pairs: int = 200
  knowledge_faq_min_answer_chars: int = 10
  ```

### B.2 — Serialización

- [ ] Crear `app/core/faq_serializer.py`:
  ```python
  class FaqPair(BaseModel):
      question: str
      answer: str

  def serialize_faq(pairs: list[FaqPair]) -> str:
      return "\n\n".join(f"P: {p.question.strip()}\nR: {p.answer.strip()}" for p in pairs)
  ```

### B.3 — Migración y modelo

- [ ] Migración `p21_b_knowledge_faq`: `ALTER TABLE knowledge_documents ADD COLUMN faq_content text`.
- [ ] Actualizar `KnowledgeDocument.faq_content: Mapped[str | None]`.

### B.4 — Servicio

- [ ] Ampliar `app/services/knowledge_document_service.py`:
  - `create_from_faq(faq, tenant, user, db)` — serializa pares, sin R2, encola `index_knowledge_document`.
  - `update_faq_pairs(document_id, pairs, tenant, db)` — actualiza `faq_content`, reencola.
  - `get_faq_pairs(document_id, tenant, db) -> list[FaqPair]`.

### B.5 — Rutas y UI

- [ ] Ampliar `app/routes/web/knowledge.py`: `GET/POST /knowledge/faq`, `GET/PUT /knowledge/{id}/faq`.
- [ ] `components/knowledge_faq_form.html`: lista Alpine de pares Q/A con botones añadir/borrar.

### B.6 — Tests

- [ ] `tests/unit/test_faq_serializer.py`.
- [ ] `tests/integration/test_knowledge_faq.py`.

---

## Sub-módulo C — Modelo `ChannelIntegration`

Tabla central que asocia un tenant con sus canales externos (WhatsApp, Telegram). Sigue el patrón de `CalendarIntegration` (`app/models/calendar_integration.py`).

### C.1 — Modelo ORM

- [ ] Crear `app/models/channel_integration.py`:

  ```python
  """Integración de canales externos de mensajería por tenant (Paso 21)."""

  from __future__ import annotations

  import enum
  from datetime import datetime
  from uuid import UUID, uuid4

  from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, UniqueConstraint, func
  from sqlalchemy.dialects.postgresql import UUID as PG_UUID
  from sqlalchemy.orm import Mapped, mapped_column

  from app.models.base import Base


  class ChannelType(enum.StrEnum):
      whatsapp = "whatsapp"
      telegram = "telegram"


  class ChannelIntegrationStatus(enum.StrEnum):
      active = "active"
      inactive = "inactive"
      error = "error"


  class ChannelIntegration(Base):
      """Un canal externo de mensajería por tenant (máximo uno por canal)."""

      __tablename__ = "channel_integrations"

      id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
      tenant_id: Mapped[UUID] = mapped_column(
          PG_UUID(as_uuid=True),
          ForeignKey("tenants.id", ondelete="CASCADE"),
          nullable=False,
          index=True,
      )
      channel: Mapped[str] = mapped_column(String(32), nullable=False)  # ChannelType

      # WhatsApp: phone_number_id de Meta (no el número visible); Telegram: null
      phone_number_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
      # Solo display: "+34 612 345 678" (WhatsApp) o "@MiBot" (Telegram)
      display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

      # Token cifrado con ENCRYPTION_KEY (Fernet / pgcrypto, mismo helper que CalendarIntegration)
      api_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

      status: Mapped[str] = mapped_column(
          String(16), nullable=False, default=ChannelIntegrationStatus.active.value
      )
      confidence_threshold: Mapped[float] = mapped_column(nullable=False, default=0.5)
      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=func.now(), nullable=False
      )
      updated_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
      )

      __table_args__ = (
          UniqueConstraint("tenant_id", "channel", name="uq_channel_integration_per_tenant"),
          Index("ix_channel_integrations_tenant", "tenant_id"),
          # Índice para lookup rápido en webhook: buscar tenant por phone_number_id
          Index("ix_channel_integrations_phone_number_id", "phone_number_id"),
      )
  ```

### C.2 — Migración

- [ ] Migración `p21_c_channel_integrations`:
  ```sql
  CREATE TABLE channel_integrations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    channel text NOT NULL,
    phone_number_id text,
    display_name text,
    api_token_enc bytea,
    status text NOT NULL DEFAULT 'active',
    confidence_threshold numeric NOT NULL DEFAULT 0.5,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_channel_integration_per_tenant UNIQUE (tenant_id, channel)
  );
  ALTER TABLE channel_integrations ENABLE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON channel_integrations
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
  CREATE INDEX ix_channel_integrations_tenant ON channel_integrations (tenant_id);
  CREATE INDEX ix_channel_integrations_phone_number_id ON channel_integrations (phone_number_id);
  ```

### C.3 — Tablas `conversations` y `messages`

- [ ] Verificar si existen como modelos ORM. Si no:
  - [ ] Crear `app/models/conversation.py`:
    ```python
    class Conversation(Base):
        __tablename__ = "conversations"
        id: Mapped[UUID] = ...
        tenant_id: Mapped[UUID] = ...
        channel: Mapped[str] = ...           # web | whatsapp | telegram
        external_id: Mapped[str | None] = ... # número E.164 del cliente
        customer_identifier: Mapped[str | None] = ...
        started_at: Mapped[datetime] = ...
        closed_at: Mapped[datetime | None] = ...

    class ChannelMessage(Base):
        __tablename__ = "channel_messages"   # distinto de chat_messages (módulo 1.5)
        id: Mapped[UUID] = ...
        conversation_id: Mapped[UUID] = ...
        tenant_id: Mapped[UUID] = ...
        role: Mapped[str] = ...              # user | assistant
        content: Mapped[str] = ...
        metadata: Mapped[dict] = ...         # jsonb, citations, confidence
        llm_call_id: Mapped[UUID | None] = ...
        created_at: Mapped[datetime] = ...
    ```
  - [ ] Migración `p21_c2_conversations`: tablas + RLS + índices.

  > **Nota:** se nombra `channel_messages` (no `messages`) para evitar colisión con el modelo `messages` del módulo RAG definido en `arquitectura.md §5`. Verificar con `alembic current` antes de crear migración.

### C.4 — Servicio `channel_integration_service`

- [ ] Crear `app/services/channel_integration_service.py`:
  - `get_integration(db, tenant_id, channel) -> ChannelIntegration | None`
  - `save_integration(db, tenant_id, channel, phone_number_id, api_token, display_name, confidence_threshold)` — cifra token con `ENCRYPTION_KEY`.
  - `revoke_integration(db, tenant_id, channel)` — status = inactive, borra token.
  - `get_tenant_by_phone_number_id(db, phone_number_id) -> Tenant | None` — lookup para webhook WhatsApp.
  - `get_integration_by_id(db, integration_id) -> ChannelIntegration | None` — lookup para webhook Telegram.
  - `decrypt_token(integration) -> str` — descifra `api_token_enc`.

---

## Sub-módulo D — Tarjetas UI en `/settings/integrations`

Dos tarjetas nuevas en `pages/settings/integrations.html`, siguiendo exactamente el patrón de `components/integration_google_calendar.html`. Solo visibles/editables por usuarios con rol `admin`.

### D.1 — Configuración

- [ ] Ampliar `app/config.py`:
  ```python
  # Canales externos (Paso 21)
  whatsapp_api_url: str = "https://graph.facebook.com/v20.0"
  whatsapp_max_response_chars: int = 1000
  telegram_api_url: str = "https://api.telegram.org"
  channel_confidence_threshold_default: float = 0.5
  ```

### D.2 — Rutas web

- [ ] Ampliar `app/routes/web/integrations.py` (o extraer a `routes/web/channel_integrations.py`):

  ```python
  # GET /settings/integrations/whatsapp/status  → tarjeta WhatsApp (fragmento)
  # POST /settings/integrations/whatsapp/save   → guardar phone_number_id + token
  # POST /settings/integrations/whatsapp/disconnect → revocar integración
  # GET /settings/integrations/telegram/status  → tarjeta Telegram (fragmento)
  # POST /settings/integrations/telegram/save   → guardar bot_token, llamar setWebhook
  # POST /settings/integrations/telegram/disconnect → revocar + deleteWebhook
  ```

  - Todos los endpoints `POST` requieren `require_role("admin")`.
  - Devuelven el fragmento de la tarjeta correspondiente (`hx-target` en el componente).

### D.3 — Tarjeta WhatsApp

- [ ] Crear `app/templates/components/integration_whatsapp.html` (misma estructura que `integration_google_calendar.html`):

  **Header:** logo WhatsApp + título + descripción.

  **Cuerpo — estado desconectado:** formulario con:
  - Campo `phone_number_id` (texto, required) — etiqueta: «Phone Number ID de Meta».
  - Campo `api_token` (password, required) — etiqueta: «Token de acceso permanente».
  - Campo `display_name` (texto, optional) — etiqueta: «Número visible (ej. +34 612 345 678)».
  - Campo `confidence_threshold` (number, 0–1, step 0.05) — etiqueta: «Umbral de confianza para respuesta automática».
  - Botón «Conectar».
  - `hx-post="/settings/integrations/whatsapp/save"`, `hx-target="#integration-whatsapp"`, `hx-swap="outerHTML"`.

  **Cuerpo — estado conectado:**
  - Número visible + `phone_number_id` (readonly).
  - URL de webhook para copiar: `{{ app_base_url }}/api/webhooks/whatsapp` con botón copiar (Alpine).
  - Umbral de confianza actual (editable inline).
  - Botón «Desconectar» con `hx-confirm`.

  **Footer — estado conectado:** instrucción: «Registra esta URL en Meta Developer Console → Webhooks → `messages`.»

- [ ] Añadir `{% include "components/integration_whatsapp.html" %}` en `pages/settings/integrations.html`.

### D.4 — Tarjeta Telegram

- [ ] Crear `app/templates/components/integration_telegram.html`:

  **Header:** logo Telegram + título + descripción.

  **Cuerpo — estado desconectado:** formulario con:
  - Campo `bot_token` (password, required) — etiqueta: «Token del bot (@BotFather)».
  - Campo `display_name` (texto, optional) — etiqueta: «Nombre del bot (ej. @MiNegocioBot)».
  - Campo `confidence_threshold` (number, 0–1, step 0.05).
  - Botón «Conectar» — al guardar, el sistema llama automáticamente a `setWebhook` de Telegram.
  - `hx-post="/settings/integrations/telegram/save"`, `hx-target="#integration-telegram"`, `hx-swap="outerHTML"`.

  **Cuerpo — estado conectado:**
  - Nombre del bot (readonly).
  - Webhook URL registrada automáticamente: `{{ app_base_url }}/api/webhooks/telegram/{{ integration.id }}`.
  - Umbral de confianza actual.
  - Botón «Desconectar» (llama a `deleteWebhook` en Telegram + revoca integración).

- [ ] Añadir `{% include "components/integration_telegram.html" %}` en `pages/settings/integrations.html`.

### D.5 — Context helper actualizado

- [ ] Actualizar `_integration_card_ctx()` en `routes/web/integrations.py` para incluir:
  ```python
  whatsapp_integration = await channel_integration_service.get_integration(db, tenant.id, "whatsapp")
  telegram_integration = await channel_integration_service.get_integration(db, tenant.id, "telegram")
  ctx["whatsapp_integration"] = whatsapp_integration
  ctx["whatsapp_connected"] = whatsapp_integration is not None and whatsapp_integration.status == "active"
  ctx["telegram_integration"] = telegram_integration
  ctx["telegram_connected"] = telegram_integration is not None and telegram_integration.status == "active"
  ctx["app_base_url"] = settings.app_base_url
  ```

### D.6 — Tests UI

- [ ] `tests/integration/test_channel_integrations_ui.py`:
  - `test_save_whatsapp_integration_requires_admin()`.
  - `test_save_whatsapp_stores_token_encrypted()` — `api_token_enc` en BD ≠ token original.
  - `test_save_telegram_calls_set_webhook()` — mock del cliente Telegram.
  - `test_disconnect_revokes_and_deletes_webhook()`.
  - `test_non_admin_cannot_configure_channel()`.

---

## Sub-módulo E — Canal WhatsApp (webhook)

### E.1 — Cliente WhatsApp

- [ ] Crear `app/core/whatsapp_client.py`:
  ```python
  async def send_text_message(to: str, text: str, phone_number_id: str, api_token: str, settings: Settings) -> None:
      """POST a {whatsapp_api_url}/{phone_number_id}/messages."""

  def verify_webhook_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
      """Compara X-Hub-Signature-256 con HMAC-SHA256(body, app_secret)."""
  ```
  - Truncar `text` a `whatsapp_max_response_chars` si es necesario.
  - Manejar errores 4xx/5xx con logging estructurado.

### E.2 — Servicio conversacional de canal

- [ ] Crear `app/services/channel_chat_service.py`:
  ```python
  async def answer_for_channel(
      db: AsyncSession,
      tenant: Tenant,
      channel: str,
      customer_identifier: str,
      message_text: str,
  ) -> ChannelResponse:
      """Recupera/crea Conversation, carga historial, llama al pipeline RAG, persiste."""
  ```
  - Reutiliza `chat_service` con `knowledge_tools_enabled=True` y sin tools documentales (solo tools de conocimiento — los clientes externos no acceden a facturas del negocio).
  - Historial: últimos N=10 mensajes de esa `conversation`.
  - `confidence`: score máximo de citas; 0.0 si no hay citas.
  - Devuelve `ChannelResponse(text, confidence, citations_count)`.

  > **Guardrail crítico:** el system prompt para canales externos debe indicar explícitamente que el asistente solo responde sobre la base de conocimiento y NO sobre datos internos del negocio (facturas, contabilidad, etc.).

### E.3 — Prompt para canal externo

- [ ] Crear `app/llm/prompts/channel_external_v1.txt`:
  ```
  Eres el asistente virtual de [COMPANY_NAME].
  Respondes preguntas de clientes sobre los servicios, horarios, precios y políticas
  de la empresa usando exclusivamente la base de conocimiento disponible.

  LÍMITES ESTRICTOS:
  - Solo información de la base de conocimiento de [COMPANY_NAME].
  - No tienes acceso a datos internos (facturas, pedidos, cuentas).
  - Si no encuentras la información, responde:
    "Lo siento, no tengo esa información. Te recomiendo contactar directamente con [COMPANY_NAME]."
  - Responde siempre en el idioma en que te escribe el cliente.
  - Respuestas concisas (máximo 3 párrafos cortos).
  ```

### E.4 — Webhook

- [ ] Crear `app/routes/api/webhooks_whatsapp.py`:
  ```python
  router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

  @router.get("/whatsapp")
  async def whatsapp_verify(
      hub_mode: str = Query(alias="hub.mode"),
      hub_challenge: str = Query(alias="hub.challenge"),
      hub_verify_token: str = Query(alias="hub.verify_token"),
      settings: Settings = Depends(get_settings),
  ) -> PlainTextResponse:
      if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
          return PlainTextResponse(hub_challenge)
      raise HTTPException(403)

  @router.post("/whatsapp")
  async def whatsapp_webhook(
      request: Request,
      db: AsyncSession = Depends(get_db_no_tenant),
  ) -> Response:
      body = await request.body()
      # 1. Verificar firma HMAC con whatsapp_app_secret (secreto global en Infisical)
      # 2. Parsear payload: entry[0].changes[0].value
      # 3. Extraer phone_number_id (metadata.phone_number_id)
      # 4. Buscar tenant: channel_integration_service.get_tenant_by_phone_number_id(db, phone_number_id)
      # 5. Si no encontrado → log warning + return Response(status_code=200)
      # 6. Encolar ARQ job: process_channel_message(tenant_id, channel='whatsapp', ...)
      # 7. return Response(status_code=200)  # SIEMPRE responder 200 a Meta
  ```
  - `whatsapp_verify_token` y `whatsapp_app_secret` son secretos **globales** en Infisical (uno por despliegue, no por tenant). El token del tenant para enviar respuestas sí está cifrado en `channel_integrations`.

### E.5 — Job ARQ

- [ ] Crear `app/jobs/channel_jobs.py`:
  ```python
  async def process_channel_message(
      ctx: dict,
      tenant_id: str,
      channel: str,
      customer_identifier: str,
      message_text: str,
      integration_id: str,
  ) -> None:
      # 1. Resolver tenant y channel_integration de BD
      # 2. channel_chat_service.answer_for_channel(...)
      # 3. Si confidence >= threshold: enviar respuesta
      # 4. Si confidence < threshold: mensaje de escalado + audit log 'channel.escalated'
      # 5. Audit log: 'channel.message_received', 'channel.message_sent'
  ```
- [ ] Registrar en `app/jobs/settings.py`.

### E.6 — Secretos globales WhatsApp

- [ ] Añadir en Infisical:
  - `WHATSAPP_VERIFY_TOKEN` — token arbitrario para verificación Meta.
  - `WHATSAPP_APP_SECRET` — secreto de la app Meta para validar firma HMAC.
- [ ] Actualizar `app/config.py`:
  ```python
  whatsapp_verify_token: SecretStr = SecretStr("")
  whatsapp_app_secret: SecretStr = SecretStr("")
  whatsapp_api_url: str = "https://graph.facebook.com/v20.0"
  whatsapp_max_response_chars: int = 1000
  ```
- [ ] Actualizar `.env.example`.

### E.7 — Tests

- [ ] `tests/unit/test_whatsapp_client.py`:
  - `test_send_message_truncates_long_text()`.
  - `test_verify_signature_valid()` / `test_verify_signature_invalid()`.
- [ ] `tests/unit/test_channel_chat_service.py`:
  - `test_creates_conversation_if_not_exists()`.
  - `test_reuses_existing_conversation()`.
  - `test_escalates_when_confidence_below_threshold()`.
  - `test_knowledge_tools_only_no_invoice_tools()` — asegurar que las tools documentales NO se pasan al canal externo.
- [ ] `tests/integration/test_whatsapp_webhook.py`:
  - `test_webhook_get_verification_ok()`.
  - `test_webhook_get_verification_wrong_token_403()`.
  - `test_webhook_post_enqueues_job()`.
  - `test_webhook_post_invalid_signature_returns_200_silently()` — no exponer errores.
  - `test_webhook_post_unknown_phone_number_id_returns_200()`.

---

## Sub-módulo F — Canal Telegram (webhook)

### F.1 — Cliente Telegram

- [ ] Crear `app/core/telegram_client.py`:
  ```python
  async def set_webhook(bot_token: str, webhook_url: str, settings: Settings) -> None:
      """POST https://api.telegram.org/bot{token}/setWebhook {url: webhook_url}."""

  async def delete_webhook(bot_token: str, settings: Settings) -> None:
      """POST https://api.telegram.org/bot{token}/deleteWebhook."""

  async def send_message(bot_token: str, chat_id: int | str, text: str, settings: Settings) -> None:
      """POST https://api.telegram.org/bot{token}/sendMessage {chat_id, text}."""

  def verify_webhook_secret(token_header: str, webhook_secret: str) -> bool:
      """Compara X-Telegram-Bot-Api-Secret-Token header con el secreto configurado."""
  ```
  - Truncar mensajes a 4096 chars (límite Telegram).
  - `httpx.AsyncClient` en todas las llamadas.

### F.2 — Webhook

- [ ] Crear `app/routes/api/webhooks_telegram.py`:
  ```python
  @router.post("/telegram/{integration_id}")
  async def telegram_webhook(
      integration_id: UUID,
      request: Request,
      db: AsyncSession = Depends(get_db_no_tenant),
  ) -> Response:
      # 1. Verificar X-Telegram-Bot-Api-Secret-Token header
      # 2. Buscar ChannelIntegration por id (channel='telegram', status='active')
      # 3. Extraer tenant_id de la integración
      # 4. Parsear payload: message.chat.id (customer_identifier), message.text
      # 5. Encolar ARQ: process_channel_message(tenant_id, channel='telegram', ...)
      # 6. return Response(status_code=200)  # SIEMPRE 200 a Telegram
  ```
  - El `integration_id` en la URL actúa como el identificador público de tenant para Telegram.
  - Al guardar el bot_token en D.4, se llama automáticamente a `set_webhook` con esta URL.

### F.3 — Secreto por webhook Telegram

Al registrar el webhook, Telegram acepta un parámetro `secret_token` (hasta 256 chars) que devuelve en `X-Telegram-Bot-Api-Secret-Token`. Usar un valor aleatorio generado al guardar la integración, almacenado también cifrado en `channel_integrations` (segundo campo `webhook_secret_enc bytea null`).

- [ ] Añadir columna `webhook_secret_enc bytea null` a `channel_integrations` en la misma migración C.2 (o nueva `p21_f_telegram_secret`).

### F.4 — Tests

- [ ] `tests/unit/test_telegram_client.py`:
  - `test_set_webhook_called_on_save()`.
  - `test_delete_webhook_called_on_disconnect()`.
  - `test_send_message_truncates_at_4096()`.
- [ ] `tests/integration/test_telegram_webhook.py`:
  - `test_webhook_post_valid_secret_enqueues_job()`.
  - `test_webhook_post_invalid_secret_returns_200_silently()`.
  - `test_webhook_post_unknown_integration_id_returns_200()`.

---

## Fase transversal — Observabilidad y guardrails

- [ ] Audit log: acciones `knowledge.url_upload`, `knowledge.faq_create`, `knowledge.faq_edit`, `channel.integration_saved`, `channel.integration_revoked`, `channel.message_received`, `channel.message_sent`, `channel.escalated`.
- [ ] Rate-limit canales externos: máx. 60 msg/hora por `customer_identifier` (Redis token bucket). Evita flood de bots.
- [ ] Rate-limit ingesta URL: 20/día/tenant.
- [ ] `usage_meter.rag_messages_count` incrementado por cada turno de canal externo.
- [ ] `llm_calls` poblado en cada llamada del canal externo.
- [ ] `mypy --strict` y `ruff check` verdes en todos los ficheros nuevos.

---

## Estructura de ficheros nueva / modificada

```
app/
  config.py                                       # + URL/FAQ/canal settings
  core/
    web_scraper.py                                # scraping httpx + html2text
    faq_serializer.py                             # serialización pares Q/A
    whatsapp_client.py                            # cliente API WhatsApp
    telegram_client.py                            # cliente API Telegram
  models/
    channel_integration.py                        # ChannelIntegration, ChannelType
    conversation.py                               # Conversation, ChannelMessage
  schemas/
    knowledge.py                                  # + FaqPair, KnowledgeFaqCreate
    channel.py                                    # ChannelIntegrationRead, ChannelResponse
  services/
    knowledge_document_service.py                 # + create_from_faq, update_faq_pairs
    channel_integration_service.py                # save/get/revoke, lookup por phone_number_id
    channel_chat_service.py                       # answer_for_channel (solo knowledge tools)
  llm/prompts/
    channel_external_v1.txt                       # prompt para clientes externos
  jobs/
    knowledge_url_jobs.py                         # index_knowledge_url
    channel_jobs.py                               # process_channel_message
    settings.py                                   # + nuevos jobs
    queue.py                                      # + enqueue helpers
  routes/
    web/
      knowledge.py                                # + /url, /faq, /faq/edit
      integrations.py                             # + tarjetas WA/Telegram, save/disconnect
    api/
      webhooks_whatsapp.py                        # GET+POST /api/webhooks/whatsapp
      webhooks_telegram.py                        # POST /api/webhooks/telegram/{integration_id}
  main.py                                         # + registrar nuevos routers
migrations/versions/
  p21_a_knowledge_url.py
  p21_b_knowledge_faq.py
  p21_c_channel_integrations.py                   # tabla + RLS + índices
  p21_c2_conversations.py                         # conversations + channel_messages + RLS
templates/
  pages/
    knowledge/index.html                          # + tabs URL / FAQ
    settings/integrations.html                    # + include WA/Telegram cards
  components/
    integration_whatsapp.html                     # tarjeta WhatsApp
    integration_telegram.html                     # tarjeta Telegram
    knowledge_faq_form.html                       # editor pares Q/A Alpine
    knowledge_url_form.html                       # campo URL
tests/
  unit/
    test_web_scraper.py
    test_faq_serializer.py
    test_whatsapp_client.py
    test_telegram_client.py
    test_channel_chat_service.py
  integration/
    test_knowledge_url.py
    test_knowledge_faq.py
    test_channel_integrations_ui.py
    test_whatsapp_webhook.py
    test_telegram_webhook.py
```

---

## Verificación manual (checklist)

### Sub-módulo A — Ingesta URL

1. [ ] `infisical run -- uv run alembic upgrade head`.
2. [ ] Abrir `/knowledge` → pestaña «Añadir URL» → introducir URL pública.
3. [ ] Verificar polling `pending → ready`, `chunk_count > 0`.
4. [ ] En `/chat` preguntar sobre el contenido de la URL → cita chunks de esa URL.

### Sub-módulo B — FAQ manual

5. [ ] Pestaña «Crear FAQ» → añadir 3 pares Q/A → guardar.
6. [ ] Fila aparece → polling → `ready`.
7. [ ] En `/chat` preguntar la pregunta del FAQ → respuesta usa el fragmento.
8. [ ] Editar FAQ → modificar respuesta → reindexar → verificar respuesta actualizada.

### Sub-módulo D — Configuración de canales

9. [ ] Abrir `/settings/integrations` como `admin` → ver tarjetas WhatsApp y Telegram.
10. [ ] **WhatsApp:** introducir `phone_number_id` + token → guardar → tarjeta muestra estado conectado + URL de webhook.
11. [ ] Verificar en BD: `SELECT id, channel, phone_number_id, display_name, status FROM channel_integrations;`
12. [ ] Verificar que `api_token_enc` ≠ token original (cifrado).
13. [ ] **Telegram:** introducir bot_token → guardar → verificar que `setWebhook` se llamó (log o Telegram API response).
14. [ ] Tarjeta Telegram muestra URL webhook: `{app_base_url}/api/webhooks/telegram/{integration_id}`.
15. [ ] Probar desconexión → `deleteWebhook` llamado (Telegram) → fila `status=inactive`.

### Sub-módulo E — WhatsApp

16. [ ] Configurar webhook en Meta Developer Console (usar ngrok en dev).
17. [ ] `GET /api/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=xyz&hub.verify_token=<token>` → responde `xyz`.
18. [ ] Enviar mensaje desde WhatsApp al número configurado → worker procesa job → respuesta automática llega.
19. [ ] Verificar en BD:
    ```sql
    SELECT c.channel, c.customer_identifier, m.role, LEFT(m.content, 80)
    FROM conversations c JOIN channel_messages m ON m.conversation_id = c.id
    ORDER BY m.created_at DESC LIMIT 6;
    ```
20. [ ] Preguntar algo sin respuesta en la base de conocimiento → mensaje de escalado + audit log `channel.escalated`.

### Sub-módulo F — Telegram

21. [ ] Enviar mensaje al bot de Telegram → worker procesa → respuesta llega al chat.
22. [ ] Verificar conversación en BD (mismo query que paso 19, `channel='telegram'`).
23. [ ] Preguntar sin respuesta → escalado correcto.

### General

24. [ ] `infisical run -- uv run pytest tests/ -q` — todos los tests pasan.
25. [ ] `uv run mypy app` y `uv run ruff check .` — verdes.

---

## Criterios de aceptación

- [ ] URL pública se indexa → chunks consultables desde `/chat`.
- [ ] FAQ manual crea chunks; edición dispara reindexación.
- [ ] Admin puede configurar WhatsApp y Telegram desde `/settings/integrations` (tarjetas UI).
- [ ] Tokens almacenados cifrados en `channel_integrations.api_token_enc`.
- [ ] Webhook WhatsApp responde HTTP 200 inmediato; procesamiento asíncrono vía ARQ.
- [ ] Webhook Telegram responde HTTP 200 inmediato; procesamiento asíncrono vía ARQ.
- [ ] Respuestas usan pipeline RAG del Paso 20 con **solo tools de conocimiento** (no tools documentales).
- [ ] Escalado a humano cuando `confidence < threshold` configurable por tenant.
- [ ] RLS: conversaciones y mensajes aislados por tenant.
- [ ] Tenants distintos, canales distintos: tenant A y tenant B pueden tener cada uno su WhatsApp y Telegram sin interferencia.
- [ ] Audit log registra todas las acciones relevantes.
- [ ] `mypy --strict` y `ruff check` verdes.

---

## Comandos útiles

```bash
# Migraciones
infisical run -- uv run alembic upgrade head

# Worker (necesario para jobs nuevos)
infisical run -- uv run arq app.jobs.settings.WorkerSettings

# Tests
infisical run -- uv run pytest tests/unit/test_whatsapp_client.py tests/unit/test_telegram_client.py -v
infisical run -- uv run pytest tests/integration/test_channel_integrations_ui.py -v
infisical run -- uv run pytest tests/integration/test_whatsapp_webhook.py tests/integration/test_telegram_webhook.py -v

# Verificar integración guardada
docker exec saas-postgres psql -U saas -d saas -c \
  "SELECT id, tenant_id, channel, phone_number_id, display_name, status, confidence_threshold \
   FROM channel_integrations;"

# Inspeccionar conversaciones
docker exec saas-postgres psql -U saas -d saas -c \
  "SELECT c.channel, c.customer_identifier, COUNT(m.id) AS msgs \
   FROM conversations c JOIN channel_messages m ON m.conversation_id=c.id \
   GROUP BY c.id ORDER BY c.started_at DESC LIMIT 10;"

# Verificar webhook WhatsApp localmente
curl -X GET "http://localhost:8000/api/webhooks/whatsapp?\
hub.mode=subscribe&hub.challenge=test123&hub.verify_token=<tu_token>"
# Debe responder: test123

# Simular mensaje WhatsApp entrante
curl -X POST http://localhost:8000/api/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=<hmac>" \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"from":"34600000000","type":"text","text":{"body":"¿Cuál es vuestro horario?"}}],"metadata":{"phone_number_id":"<id>"}}}]}]}'

# Simular mensaje Telegram entrante
curl -X POST "http://localhost:8000/api/webhooks/telegram/<integration_id>" \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: <webhook_secret>" \
  -d '{"message":{"chat":{"id":123456789},"text":"¿Cuál es vuestro horario?"}}'
```

---

## Acciones manuales resumidas

| # | Acción | Cuándo |
|---|--------|--------|
| 1 | Añadir `WHATSAPP_VERIFY_TOKEN` y `WHATSAPP_APP_SECRET` en Infisical | Antes de Sub-E |
| 2 | Crear bot Telegram con `@BotFather` por cada tenant de prueba | Antes de Sub-F |
| 3 | Configurar ngrok para webhook dev (WA + Telegram) | Antes de verificación E/F |
| 4 | Registrar URL webhook en Meta Developer Console | Antes de verificación E |
| 5 | `alembic upgrade head` | Tras migraciones C/C2 |
| 6 | Reiniciar worker ARQ con nuevos jobs | Tras registrar en settings.py |
| 7 | Verificación manual completa (25 pasos) | Tras implementar todos los sub-módulos |
| 8 | Actualizar `arquitectura.md §5` con tablas `conversations`/`channel_messages` definitivas | Antes de PR |
| 9 | Commit + PR cuando CI verde | Cierre del paso |

---

## Posibles problemas

| Síntoma | Causa probable | Mitigación |
|---------|----------------|------------|
| Scraping devuelve texto vacío | Página JS-only (SPA) | Advertir en UI; Playwright headless como mejora futura |
| Webhook 403 de Meta | `WHATSAPP_VERIFY_TOKEN` incorrecto | Verificar valor exacto en Infisical vs. Meta Console |
| Telegram `setWebhook` falla | Bot token incorrecto o dominio no HTTPS | Verificar token con `getMe`; usar ngrok en dev |
| `api_token_enc` null en BD | `ENCRYPTION_KEY` no en Infisical | Configurar clave (misma que Google Calendar) |
| Tenant no encontrado en webhook | `phone_number_id` no coincide con BD | Verificar que el tenant guardó la integración con el ID correcto de Meta |
| Respuestas usan tools de facturas | `answer_for_channel` pasa tool set incorrecto | Asegurar que `channel_chat_service` NO inyecta `DOC_TOOLS` |
| Confianza siempre 0 → escalado constante | Sin documentos RAG relevantes o umbral muy alto | Indexar más documentos; bajar `confidence_threshold` en la tarjeta UI |
| Rate limit Redis bloqueando clientes | Umbral muy conservador | Ajustar `channel_rate_limit_msg_per_hour` en config |
| Colisión tabla `messages` en Alembic | Nombre reservado por módulo RAG | Usar `channel_messages` como nombre de tabla |

---

## Siguiente paso

| Paso | Contenido (orientativo) |
|------|------------------------|
| **Paso 22** | Módulo 3: analista SQL conversacional — `data_sources`, introspección de esquema, tool `query_sql`, guardrails SELECT-only, gráficos Chart.js |
