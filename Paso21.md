# Paso 21 — Canales externos: FAQ manual, WhatsApp y Telegram

## Objetivo

Completar el módulo 2 con tres capacidades:

1. **Editor de FAQ manual** — crear/editar preguntas frecuentes directamente desde la UI.
2. **Configuración de canales externos** — tabla `channel_integrations` + tarjetas UI en `/settings/integrations` para que cada tenant registre su número de WhatsApp Business y/o su bot de Telegram.
3. **Canales WhatsApp y Telegram** — recibir mensajes de clientes vía webhook, procesarlos con el pipeline RAG del Paso 20 y responder automáticamente.

Al final del paso, la base de conocimiento se nutre desde dos orígenes (fichero, FAQ), y el chatbot responde tanto desde `/chat` web como desde WhatsApp y Telegram con la base de conocimiento **del tenant correspondiente**.

## Pre-requisitos

- Pasos 18–20 completados: ingesta ficheros operativa, búsqueda híbrida lista, chat RAG con citas funcionando.
- Cuenta **WhatsApp Business API** (Meta for Developers): número verificado, app creada.
- Bot de **Telegram** creado vía `@BotFather` (uno por tenant).
- `ENCRYPTION_KEY` en Infisical (ya usada para Google Calendar; misma clave para cifrar tokens de canal).

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

- Sub-módulo B: FAQ manual.
- Sub-módulo C: Modelo `ChannelIntegration` + migración.
- Sub-módulo D: Tarjetas UI en `/settings/integrations` (WhatsApp + Telegram), solo accesibles por `admin`.
- Sub-módulo E: Webhook WhatsApp + pipeline RAG + respuesta automática + escalado.
- Sub-módulo F: Webhook Telegram + pipeline RAG + respuesta automática + escalado.

### Fuera de Paso 21

- Ingesta por URL (descartada).
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

## Sub-módulo B — FAQ manual

### B.1 — Configuración

- [x] Ampliar `app/config.py`:
  ```python
  knowledge_faq_max_pairs: int = 200
  knowledge_faq_min_answer_chars: int = 10
  ```

### B.2 — Serialización

- [x] Crear `app/core/faq_serializer.py` con `FaqPair`, `serialize_faq()`, `deserialize_faq()`:
  ```python
  class FaqPair(BaseModel):
      question: str
      answer: str

  def serialize_faq(pairs: list[FaqPair]) -> str:
      return "\n\n".join(f"P: {p.question.strip()}\nR: {p.answer.strip()}" for p in pairs)
  ```

### B.3 — Migración y modelo

- [x] Migración `p21_a_knowledge_url_faq_01_add_columns` ya añadió `faq_content text` null.
- [x] `KnowledgeDocument.faq_content: Mapped[str | None]` ya existe en el modelo.

### B.4 — Servicio

- [x] Ampliar `app/services/knowledge_document_service.py`:
  - [x] `create_from_faq()` — serializa pares, sube a R2 (key determinista por doc_id), crea doc con `faq_content`.
  - [x] `update_faq_pairs()` — re-serializa, sobreescribe R2, actualiza `faq_content`, status=pending.
  - [x] `get_faq_pairs(doc)` — parsea `faq_content` con `deserialize_faq()`.

### B.5 — Rutas y UI

- [x] `POST /knowledge/faq` — crear FAQ con validación de pares (mín. 1, respuesta ≥ min_chars).
- [x] `GET /knowledge/{id}/faq` — formulario de edición pre-cargado con pares existentes.
- [x] `PUT /knowledge/{id}/faq` — actualizar pares + re-encolar indexación.
- [x] `components/knowledge_faq_form.html` — modal Alpine con lista dinámica de pares Q/A.
- [x] `components/knowledge_faq_edit_panel.html` — panel de edición para FAQ existentes.
- [x] `pages/knowledge/index.html` — botón «Crear FAQ» junto a URL y subida.

### B.6 — Tests

- [x] `tests/unit/test_faq_serializer.py` — serialize/deserialize, round-trip, casos borde.
- [x] `tests/integration/test_knowledge_faq.py` — flujo completo create_from_faq → job → ready + round-trip de pares en BD.

---

## Sub-módulo C — Modelo `ChannelIntegration`

Tabla central que asocia un tenant con sus canales externos (WhatsApp, Telegram). Sigue el patrón de `CalendarIntegration` (`app/models/calendar_integration.py`).

### C.1 — Modelo ORM

- [x] Crear `app/models/channel_integration.py`:

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

- [x] Migración `p21_c_channel_integrations_01` (incluye `webhook_secret_enc` y política RLS `webhook_select` para lookups cross-tenant):
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

- [x] Crear `app/models/conversation.py` con `Conversation` y `ChannelMessage` (`channel_messages`, atributo `msg_metadata` mapeado a columna `metadata`).
- [x] Migración `p21_c2_conversations_01`: tablas + RLS + índices.

  > **Nota:** se nombra `channel_messages` (no `messages`) para evitar colisión con el modelo `messages` del módulo RAG definido en `arquitectura.md §5`. Verificar con `alembic current` antes de crear migración.

### C.4 — Servicio `channel_integration_service`

- [x] Crear `app/services/channel_integration_service.py`:
  - [x] `get_integration` / `save_integration` / `revoke_integration`
  - [x] `get_integration_by_phone_number_id` — lookup cross-tenant vía política RLS `webhook_select`
  - [x] `get_tenant_by_phone_number_id` — variante que retorna el `Tenant`
  - [x] `get_integration_by_id` — lookup para webhook Telegram
  - [x] `decrypt_api_token` / `decrypt_webhook_secret`

---

## Sub-módulo D — Tarjetas UI en `/admin/integrations` *(desviación de diseño: accesible solo por superadmin vía ADMIN_CLERK_ORG_ID, no por admin de tenant)*

Tarjetas en `pages/admin/channel_integrations_detail.html`, protegidas por `SuperAdmin` dependency.

### D.1 — Configuración

- [x] `app/config.py` ampliado con settings WhatsApp/Telegram, `admin_clerk_org_id`, `channel_cache_*`, SMTP:
  ```python
  # Canales externos (Paso 21)
  whatsapp_api_url: str = "https://graph.facebook.com/v20.0"
  whatsapp_max_response_chars: int = 1000
  telegram_api_url: str = "https://api.telegram.org"
  channel_confidence_threshold_default: float = 0.5
  ```

### D.2 — Rutas web

- [x] Crear `app/routes/web/admin_channel_integrations.py` con `SuperAdmin` dependency:

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

- [x] Crear `app/templates/components/integration_whatsapp.html` (misma estructura que `integration_google_calendar.html`):

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

- [x] Incluida en `pages/admin/channel_integrations_detail.html`.

### D.4 — Tarjeta Telegram

- [x] Crear `app/templates/components/integration_telegram.html`:

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

- [x] Incluida en `pages/admin/channel_integrations_detail.html`.

### D.5 — Context helper actualizado

- [x] `_channel_ctx()` en `routes/web/admin_channel_integrations.py` (carga WA + TG integrations por tenant):
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

- [x] `tests/integration/test_channel_integrations_ui.py`:
  - [x] `test_save_whatsapp_integration_requires_admin()`.
  - [x] `test_save_whatsapp_stores_token_encrypted()` — `api_token_enc` en BD ≠ token original.
  - [x] `test_save_telegram_calls_set_webhook()` — mock del cliente Telegram.
  - [x] `test_disconnect_revokes_and_deletes_webhook()`.
  - [x] `test_non_admin_cannot_configure_channel()`.

---

## Sub-módulo E — Canal WhatsApp (webhook)

### E.1 — Cliente WhatsApp

- [x] Crear `app/core/whatsapp_client.py`:
  ```python
  async def send_text_message(to: str, text: str, phone_number_id: str, api_token: str, settings: Settings) -> None:
      """POST a {whatsapp_api_url}/{phone_number_id}/messages."""

  def verify_webhook_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
      """Compara X-Hub-Signature-256 con HMAC-SHA256(body, app_secret)."""
  ```
  - Truncar `text` a `whatsapp_max_response_chars` si es necesario.
  - Manejar errores 4xx/5xx con logging estructurado.

### E.2 — Servicio conversacional de canal

- [x] Crear `app/services/channel_chat_service.py`:
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

- [x] Crear `app/llm/prompts/channel_external_v1.txt`:
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

- [x] `app/routes/api/webhooks_whatsapp.py` — GET verificación Meta + POST con HMAC-SHA256 + lookup por phone_number_id + enqueue ARQ:
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

- [x] Crear `app/jobs/channel_jobs.py` con `process_channel_message`:
  - Carga tenant + integración + admin_email en sesión BD
  - Llama a `channel_chat_service.answer_for_channel()` y hace commit
  - Envía respuesta si `confidence >= threshold`; escala si no (mensaje + email al admin)
  - Gestión de errores: 1er fallo → "procesando..." + ARQ reintenta; 2º fallo → error amigable
- [x] Registrar en `app/jobs/settings.py` con timeout 120 s.
- [x] `enqueue_channel_message()` añadido a `app/jobs/queue.py`.

### E.6 — Secretos globales WhatsApp

- [x] Settings en `app/config.py` añadidos (`whatsapp_verify_token`, `whatsapp_app_secret`, etc.).
- [ ] Acción manual: añadir `WHATSAPP_VERIFY_TOKEN` y `WHATSAPP_APP_SECRET` en Infisical antes de verificación E.
- [ ] Actualizar `.env.example`.
  ```python
  whatsapp_verify_token: SecretStr = SecretStr("")
  whatsapp_app_secret: SecretStr = SecretStr("")
  whatsapp_api_url: str = "https://graph.facebook.com/v20.0"
  whatsapp_max_response_chars: int = 1000
  ```
- [ ] Actualizar `.env.example`.

### E.7 — Tests

- [x] `tests/unit/test_whatsapp_client.py`:
  - [x] `test_verify_signature_valid()` / `test_verify_signature_invalid()` / `test_verify_signature_missing_prefix()`.
  - [x] `test_send_message_truncates_long_text()` / `test_send_message_sends_to_correct_url()`.
- [x] `tests/unit/test_channel_chat_service.py`:
  - [x] `test_creates_conversation_if_not_exists()`.
  - [x] `test_reuses_existing_conversation()`.
  - [x] `test_confidence_zero_when_no_citations()` (escalado por confianza 0).
  - [x] `test_confidence_positive_when_citations_present()`.
  - [x] `test_knowledge_tools_only_no_invoice_tools()` — guardrail 100 % verificado.
- [x] `tests/integration/test_whatsapp_webhook.py`:
  - [x] `test_webhook_get_verification_ok()`.
  - [x] `test_webhook_get_verification_wrong_token()`.
  - [x] `test_webhook_post_enqueues_job()`.
  - [x] `test_webhook_post_invalid_signature_returns_200_silently()`.
  - [x] `test_webhook_post_unknown_phone_number_id_returns_200()`.

---

## Sub-módulo F — Canal Telegram (webhook)

### F.1 — Cliente Telegram

- [x] Crear `app/core/telegram_client.py`:
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

- [x] `app/routes/api/webhooks_telegram.py` — POST `/{integration_id}` con verificación de secret, lookup cross-tenant, enqueue ARQ:
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

- [x] Columna `webhook_secret_enc bytea null` incluida en migración `p21_c_channel_integrations_01` (Sub-módulo C).
- [x] `save_integration()` genera el secret con `secrets.token_hex(32)`, lo cifra y lo almacena.
- [x] Admin save llama a `telegram_client.set_webhook()` con el secret; admin disconnect llama a `delete_webhook()`.

### F.4 — Tests

- [x] `tests/unit/test_telegram_client.py`:
  - [x] `test_set_webhook_posts_to_correct_url()` / `test_set_webhook_includes_secret_token_when_provided()`.
  - [x] `test_delete_webhook_posts_to_correct_url()`.
  - [x] `test_send_message_truncates_at_4096()` / `test_send_message_does_not_truncate_short_text()`.
  - [x] `test_verify_webhook_secret_valid()` / `test_verify_webhook_secret_invalid()`.
- [x] `tests/integration/test_telegram_webhook.py`:
  - [x] `test_webhook_post_valid_secret_enqueues_job()`.
  - [x] `test_webhook_post_invalid_secret_returns_200_silently()`.
  - [x] `test_webhook_post_unknown_integration_id_returns_200()`.
  - [x] `test_webhook_post_no_text_message_returns_200()`.

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
  config.py                                       # + FAQ/canal settings
  core/
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
    channel_jobs.py                               # process_channel_message
    settings.py                                   # + nuevos jobs
    queue.py                                      # + enqueue helpers
  routes/
    web/
      knowledge.py                                # + /faq, /faq/edit
      integrations.py                             # + tarjetas WA/Telegram, save/disconnect
    api/
      webhooks_whatsapp.py                        # GET+POST /api/webhooks/whatsapp
      webhooks_telegram.py                        # POST /api/webhooks/telegram/{integration_id}
  main.py                                         # + registrar nuevos routers
migrations/versions/
  p21_a_knowledge_url_faq_01_add_columns.py       # añadió source_url + faq_content (source_url ya eliminada)
  p21_c_channel_integrations.py                   # tabla + RLS + índices
  p21_c2_conversations.py                         # conversations + channel_messages + RLS
  p21_e_channel_response_cache_01.py              # semantic cache
  p21_drop_source_url_01.py                       # elimina columna source_url
templates/
  pages/
    knowledge/index.html                          # + FAQ
    settings/integrations.html                    # + include WA/Telegram cards
  components/
    integration_whatsapp.html                     # tarjeta WhatsApp
    integration_telegram.html                     # tarjeta Telegram
    knowledge_faq_form.html                       # editor pares Q/A Alpine
tests/
  unit/
    test_faq_serializer.py
    test_whatsapp_client.py
    test_telegram_client.py
    test_channel_chat_service.py
  integration/
    test_knowledge_faq.py
    test_channel_integrations_ui.py
    test_whatsapp_webhook.py
    test_telegram_webhook.py
```

---

## Verificación manual (checklist)

### Sub-módulo B — FAQ manual

1. [x] `infisical run -- uv run alembic upgrade head` — BD en `p21_drop_source_url_01 (head)`.
2. [x] Pestaña «Crear FAQ» → añadir 3 pares Q/A → guardar.
3. [x] Fila aparece → polling → `ready`.
4. [x] En `/chat` preguntar la pregunta del FAQ → respuesta usa el fragmento.
5. [x] Editar FAQ → modificar respuesta → reindexar → verificar respuesta actualizada.

### Sub-módulo D — Configuración de canales

6. [x] Abrir `/settings/integrations` como `admin` → ver tarjetas WhatsApp y Telegram.
7. [ ] **WhatsApp:** introducir `phone_number_id` + token → guardar → tarjeta muestra estado conectado + URL de webhook.
8. [ ] Verificar en BD: `SELECT id, channel, phone_number_id, display_name, status FROM channel_integrations;`
9. [ ] Verificar que `api_token_enc` ≠ token original (cifrado).
10. [ ] **Telegram:** introducir bot_token → guardar → verificar que `setWebhook` se llamó (log o Telegram API response).
11. [ ] Tarjeta Telegram muestra URL webhook: `{app_base_url}/api/webhooks/telegram/{integration_id}`.
12. [ ] Probar desconexión → `deleteWebhook` llamado (Telegram) → fila `status=inactive`.

### Sub-módulo E — WhatsApp

13. [ ] Configurar webhook en Meta Developer Console (usar ngrok en dev).
14. [ ] `GET /api/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=xyz&hub.verify_token=<token>` → responde `xyz`.
15. [ ] Enviar mensaje desde WhatsApp al número configurado → worker procesa job → respuesta automática llega.
16. [ ] Verificar en BD:
    ```sql
    SELECT c.channel, c.customer_identifier, m.role, LEFT(m.content, 80)
    FROM conversations c JOIN channel_messages m ON m.conversation_id = c.id
    ORDER BY m.created_at DESC LIMIT 6;
    ```
17. [ ] Preguntar algo sin respuesta en la base de conocimiento → mensaje de escalado + audit log `channel.escalated`.

### Sub-módulo F — Telegram

18. [ ] Enviar mensaje al bot de Telegram → worker procesa → respuesta llega al chat.
19. [ ] Verificar conversación en BD (mismo query que paso 16, `channel='telegram'`).
20. [ ] Preguntar sin respuesta → escalado correcto.

### General

21. [ ] `infisical run -- uv run pytest tests/ -q` — todos los tests pasan.
22. [ ] `uv run mypy app` y `uv run ruff check .` — verdes.

---

## Criterios de aceptación

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
