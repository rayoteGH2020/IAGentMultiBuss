# Paso 23 — Multimodal de voz: crear eventos de Google Calendar desde el micrófono

## Objetivo

Permitir que un usuario **dicte por voz** (desde el micrófono del móvil o del navegador) una cita en lenguaje natural —p. ej. *"mañana a las cinco y media reunión con Juan en la oficina, una hora"*— y que el sistema:

1. **Transcriba** el audio a texto (multimodal voz→texto).
2. **Extraiga** una propuesta de evento estructurada (`summary`, `start`, `end`, `description`) interpretando fechas/horas relativas.
3. Muestre una **pantalla de confirmación editable** (HTMX) antes de crear nada.
4. Tras confirmar, **cree el evento** en el Google Calendar conectado del usuario (reutilizando el Paso 17).
5. Todos los eventos se han de crear con dos recordatorios, uno 24 horas antes y otro 1 hora antes.

Al final del paso, desde `/calendar/voice` (o un botón de micrófono en la home/agenda) el usuario graba, revisa y confirma, y el evento aparece en su Google Calendar. Todo auditado y con coste LLM registrado en `llm_calls`.

---

## ✅ Decisión de stack: Gemini Audio

La transcripción de voz se implementa con **Gemini audio** vía `google-genai`, ya presente en el stack (`AGENTS.md` §1: solo Anthropic y Google). `GOOGLE_API_KEY` ya está en Infisical. No se añade ningún proveedor nuevo.

**Ventajas clave de esta elección:**
- Cero fricción: misma clave y SDK ya usados en el módulo 1 de extracción.
- Multimodal nativo: puede transcribir y extraer estructura en una sola llamada si el prompt lo permite.
- Coste bajo con Gemini 2.5 Flash para audios cortos (<60 s).
- Observabilidad unificada: `llm_calls`, Langfuse y `LLMClient` sin código adicional.

> **Opción futura — Whisper (OpenAI API o self-hosted):**
> Cuando se requiera mayor precisión en español con acento regional, o cuando
> clientes exijan que el audio no salga de la infraestructura propia (GDPR estricto),
> se puede sustituir Gemini por Whisper self-hosted (faster-whisper / Ollama) o la
> OpenAI Whisper API. La arquitectura ya lo facilita: `_resolve_model("transcription")`
> + override en `llm_model_transcription` (settings). Requiere aprobación de stack
> según `AGENTS.md` §12 antes de implementarse.

---

## Pre-requisitos

- **Paso 17 completado**: OAuth de Google Calendar operativo (`calendar_integrations`, `calendar_service`, `GoogleCalendarClient`). El scope `calendar.events` ya se solicita en `google_calendar_scopes` (`app/config.py`), por lo que **crear eventos no requiere reconsentimiento**.
- El usuario que va a dictar debe tener **Google Calendar conectado** (`/settings/integrations`).
- `GOOGLE_API_KEY` en Infisical (Gemini; ya usada para extracción del módulo 1).
- HTTPS en el origen servido: la **Web Audio / MediaRecorder API** del navegador solo funciona en contextos seguros (`https://` o `localhost`). En dev con móvil real, usar ngrok/túnel HTTPS.

## Contexto relevante

| Documento | Sección |
|-----------|---------|
| `arquitectura.md` | §6 (módulo 1 multimodal directo al LLM), §8 (capa LLM: `complete`, router, prompts versionados, guardrails), §9 (seguridad, audit log), §7 (HTMX página/fragmento, SSE) |
| `AGENTS.md` | §1 (stack: solo Anthropic/Google), §3 (capas `routes/→services/→llm/+core/`), §4 (async, type hints), §8 (no SDK LLM desde routes/services), §11 (no guardar archivos cliente en disco), §12 (desviación de stack) |
| `Paso17.md` | Patrón `CalendarIntegration`, `calendar_service.create_calendar_event`, `GoogleCalendarClient`, OAuth y refresh de token |
| `Paso22.md` | Patrón de pantalla de confirmación previa a una acción con efectos |
| `keepCoding.md` | Ítem "Multimodales / Whisper" → sugerencia origen de este paso |

## Alcance

### Dentro de Paso 23

- Sub-módulo A: configuración y guardrails de voz (tamaño, duración, MIME, rate-limit, flag).
- Sub-módulo B: capa LLM — transcripción (Gemini audio) + extracción estructurada del borrador de evento (Instructor), con prompts versionados.
- Sub-módulo C: servicio `voice_event_service` (orquesta transcribir → borrador; confirmar → crear evento) reutilizando `calendar_service`.
- Sub-módulo D: rutas web + UI HTMX/Alpine (grabador de micrófono, confirmación editable, resultado).
- Sub-módulo E: observabilidad, audit log y tests.

### Fuera de Paso 23

- **Whisper / OpenAI** como proveedor — queda fuera de este paso; ver sección "Opción futura" en la nota de stack si se desea considerar en el futuro.
- Crear eventos por voz **automáticamente sin confirmación** (guardrail: siempre se confirma).
- Edición/borrado de eventos por voz (solo creación en MVP).
- Voz en canales externos (WhatsApp/Telegram notas de voz) → será un paso posterior reutilizando este servicio.
- Exponer la creación de eventos como **tool del chat** (módulo 1.5) → opcional/futuro (ver §F).
- Detección de idioma multi-locale avanzada y zonas horarias por usuario (se usa una tz por defecto configurable).
- Persistencia del audio (no se guarda; procesamiento efímero en memoria).

---

## Arquitectura global

```
Navegador móvil  (Alpine: MediaRecorder)
        │  graba audio (ogg/opus | mp4) — estado puramente cliente
        ▼
POST /calendar/voice/transcribe   (multipart: audio)        routes/web/calendar_voice.py
        │
        ├── validación: MIME + magic bytes + tamaño + duración (core/uploads patrón)
        ├── rate-limit por (tenant_id, user_id)  (Redis)
        │
        ▼
voice_event_service.draft_from_audio()                       services/voice_event_service.py
        │
        ├── llm/voice_calendar.transcribe_audio()  ── Gemini audio (google-genai) → texto
        │        └── LLMClient: registra llm_call task="transcription"
        │
        └── llm/voice_calendar.draft_event_from_transcript()
                 └── LLMClient.complete(task="classify", response_model=VoiceEventDraft)
                     (Instructor; inyecta "ahora" + timezone para fechas relativas)
        │
        ▼
Fragmento HTMX de CONFIRMACIÓN (campos editables + transcripción)   components/voice_event_confirm.html
        │  usuario revisa/edita summary, start, end, description
        ▼
POST /calendar/voice/confirm   (form normal, NO audio)
        │
        ▼
voice_event_service.confirm_event()
        └── calendar_service.create_calendar_event()  ── GoogleCalendarClient → Google Calendar
        │        (token del usuario, refresh automático Paso 17)
        ▼
Fragmento HTMX de RESULTADO (enlace al evento)              components/voice_event_result.html
```

**Principios clave:**
- El audio **nunca se persiste** (ni disco ni R2): se procesa en memoria y se descarta (`AGENTS.md` §11 sobre no guardar archivos de cliente en disco; aquí directamente no se guarda).
- **Dos pasos obligatorios** (transcribir→confirmar→crear): la voz es propensa a errores; nunca se crea un evento sin confirmación humana.
- El evento se crea en el **Google Calendar del usuario de la aplicación que dicta** (un miembro del equipo del negocio, autenticado por Clerk), **nunca para clientes externos** del tenant (esos solo interactúan con el chatbot RAG del módulo 2). La integración (`calendar_integrations`) está claveada por **(`tenant_id`, `user_id`)**: el `tenant_id` aísla por RLS y siempre está presente; el `user_id` identifica al miembro concreto y selecciona su calendario. Cada request se ejecuta bajo un único tenant activo (`request.state.tenant` → `app.current_tenant`).

> **Invariante de producto (actual):** aunque Clerk soporta un usuario en varias organizaciones y `memberships` es técnicamente M2M, **este aplicativo opera por ahora con un único usuario y una única organización (1 usuario = 1 tenant)**. El código no necesita cambios por ello (siempre hay un tenant activo por sesión); si en el futuro se habilita multi-organización, este flujo ya es correcto al estar claveado por (`tenant_id`, `user_id`).

---

## Sub-módulo A — Configuración y guardrails

### A.1 — Settings (`app/config.py`)

- [x] Añadir bloque de voz:
  ```python
  # Voz → Google Calendar (Paso 23)
  voice_calendar_enabled: bool = True
  voice_max_audio_bytes: int = 8 * 1024 * 1024          # 8 MB
  voice_max_audio_seconds: int = 60                     # duración máx. de la nota
  # MIME aceptados (los que admite Gemini audio). webm/opus puede requerir
  # transcodificación; preferir ogg/opus o mp4/aac desde MediaRecorder.
  voice_allowed_audio_mimes: list[str] = [
      "audio/ogg",
      "audio/mpeg",
      "audio/mp4",
      "audio/aac",
      "audio/wav",
      "audio/webm",
  ]
  # Zona horaria por defecto para resolver fechas/horas relativas dictadas.
  # Futuro: tz por usuario/tenant. De momento, valor único configurable.
  voice_calendar_default_timezone: str = "Europe/Madrid"
  # Duración por defecto si el usuario no dice fin (minutos).
  voice_event_default_duration_minutes: int = 60
  # Umbral de confianza por debajo del cual la UI exige revisión explícita.
  voice_event_min_confidence: float = 0.5
  # Rate-limit por usuario: notas de voz/hora (Redis, ventana deslizante).
  voice_rate_limit_per_hour: int = 30
  # Override opcional del modelo de transcripción (si None → DEFAULT_MODELS).
  llm_model_transcription: str | None = None
  ```

### A.2 — Validación de entrada

- [x] Reutilizar el patrón de `app/core/uploads.py` para validar **MIME real por magic bytes** (no fiarse del `Content-Type`) y tamaño.
- [x] Rechazar audio > `voice_max_audio_bytes` con `ValidationError` (`app.core.errors`).
- [x] Duración: si el contenedor expone duración, validar contra `voice_max_audio_seconds`; si no, confiar en el límite de bytes como cota superior.

---

## Sub-módulo B — Capa LLM: transcripción + borrador de evento

> Toda llamada pasa por `app/llm/client.py` (`AGENTS.md` §8). No se invoca `google-genai` desde `services`/`routes`.

### B.1 — Nuevo `TaskType` y modelo por defecto (`app/llm/client.py`)

- [x] Ampliar `TaskType`:
  ```python
  TaskType = Literal["extraction", "chat", "sql", "classify", "embedding", "transcription"]
  ```
- [x] Añadir a `DEFAULT_MODELS`:
  ```python
  "transcription": "gemini-2.5-flash",   # Gemini soporta audio nativo
  ```
- [x] Añadir override en `_resolve_model` (mapear `transcription` → `settings.llm_model_transcription`).

### B.2 — Método de transcripción en `LLMClient`

- [x] Añadir `async def transcribe(...)` (o método dedicado) que:
  - Construye un `Part` de audio con `google.genai.types` (`Part.from_bytes(data=audio, mime_type=...)`).
  - Llama a `google_client.aio.models.generate_content` con instrucción "transcribe literalmente el audio en su idioma original".
  - Devuelve `str` (transcripción).
  - Registra un `LLMCall` con `task="transcription"`, tokens, coste, latencia y traza Langfuse (mismo patrón `finally` que `complete()`/`embed()`).
  - **Solo Gemini**: si el modelo resuelto no es Google, lanzar `ValidationError` (Anthropic no transcribe audio).

  ```python
  async def transcribe(
      self,
      *,
      audio: bytes,
      mime_type: str,
      tenant_id: UUID,
      db: AsyncSession,
  ) -> str: ...
  ```

### B.3 — Schema del borrador (`app/schemas/calendar.py`)

- [x] Definir **dos modelos** en `app/schemas/calendar.py`:

  **`_VoiceEventExtraction`** — schema exclusivo de Instructor (entrada/salida del LLM). No contiene `transcript` porque el LLM no debe ecoar su propia entrada; es innecesario y consume tokens:
  ```python
  class _VoiceEventExtraction(BaseModel):
      """Schema interno para Instructor. No exponer fuera de app/llm/."""
      summary: str = Field(min_length=1, max_length=500, description="Título del evento")
      description: str | None = Field(default=None, max_length=8000)
      start: str = Field(description="ISO 8601 dateTime con offset, o date YYYY-MM-DD")
      end: str = Field(description="ISO 8601 dateTime con offset, o date YYYY-MM-DD")
      all_day: bool = False
      confidence: float = Field(ge=0, le=1, description="Confianza global de la interpretación")
      needs_clarification: bool = Field(
          default=False, description="True si falta información esencial (fecha/hora ambigua)"
      )
      clarification_reason: str | None = None
  ```

  **`VoiceEventDraft`** — modelo público que usa el servicio (C) y los templates (D). Añade `transcript` asignado por el servicio tras la llamada al LLM, no extraído por Instructor:
  ```python
  class VoiceEventDraft(BaseModel):
      """Borrador de evento listo para confirmación. transcript asignado por el servicio."""
      transcript: str                          # asignado en voice_event_service, no por el LLM
      summary: str
      description: str | None = None
      start: str
      end: str
      all_day: bool = False
      confidence: float
      needs_clarification: bool = False
      clarification_reason: str | None = None
  ```

- [x] `to_event_create()` helper en `VoiceEventDraft` → `CalendarEventCreate`. **No incluye reminders**; los inyecta `voice_event_service.confirm_event()` (ver Sub-C).

- [x] Ampliar `CalendarEventCreate` (campo opcional, retrocompatible con el resto del código):
  ```python
  class CalendarEventCreate(BaseModel):
      summary: str = Field(min_length=1, max_length=500)
      description: str | None = Field(default=None, max_length=8000)
      start: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")
      end: str = Field(description="ISO 8601 dateTime o date (YYYY-MM-DD)")
      reminders: list[dict] | None = None  # None → Google usa los recordatorios por defecto
  ```
  El campo es `None` por defecto: todo el código existente (`/calendar/events`, edición inline, tests) sigue funcionando sin cambios.

- [x] Actualizar `GoogleCalendarClient.create_event()` (`app/core/google_calendar_client.py`) para incluir el bloque de reminders en el body solo cuando el campo no sea `None`:
  ```python
  if event_data.reminders is not None:
      body["reminders"] = {"useDefault": False, "overrides": event_data.reminders}
  ```

### B.4 — Módulo `app/llm/voice_calendar.py`

- [x] Crear con dos funciones (análogo a `app/llm/extraction.py`):
  ```python
  TRANSCRIBE_PROMPT_VERSION = "voice_transcribe_v1"
  DRAFT_PROMPT_VERSION = "voice_event_v1"

  async def transcribe_audio(audio: bytes, mime_type: str, *, tenant_id, db) -> str: ...

  async def draft_event_from_transcript(
      transcript: str, *, now_iso: str, timezone: str, default_duration_min: int,
      tenant_id, db,
  ) -> _VoiceEventExtraction: ...
  ```
  - `draft_event_from_transcript` usa `LLMClient.complete(task="classify", response_model=_VoiceEventExtraction)`.
  - Devuelve `_VoiceEventExtraction` (sin `transcript`). El servicio en Sub-C construye `VoiceEventDraft` asignando el `transcript` recibido de `transcribe_audio()`.
  - **Inyecta el contexto temporal**: "ahora" (`now_iso`) y `timezone` en el prompt, para resolver "mañana", "el viernes", "a las 5".
  - No se necesita ningún tipo `VoiceEventDraftResult`: patrón idéntico a `extraction.py → ExtractedInvoice`. El `llm_call_id` lo persiste `LLMClient` internamente; el servicio no lo necesita.

### B.5 — Prompts versionados (`app/llm/prompts/`)

- [x] `voice_transcribe_v1.txt` — instrucción de transcripción literal, sin interpretar.
- [x] `voice_event_v1.txt` — reglas de extracción:
  - Resolver fechas/horas relativas respecto a `{now_iso}` en `{timezone}`.
  - Si no se dice duración, usar `default_duration_min`.
  - `start`/`end` en ISO 8601 con offset de la zona.
  - Marcar `needs_clarification=true` si la fecha/hora es ambigua o falta.
  - No inventar asistentes ni lugares no mencionados.

---

## Sub-módulo C — Servicio `voice_event_service`

- [x] Crear `app/services/voice_event_service.py`:
  ```python
  async def draft_from_audio(
      db, *, tenant_id, user_id, audio: bytes, mime_type: str, redis,
  ) -> VoiceEventDraft:
      """Valida, rate-limita, transcribe y extrae el borrador. NO crea nada."""

  async def confirm_event(
      db, *, tenant_id, user_id, event: CalendarEventCreate, request_ctx,
  ) -> CalendarEvent:
      """Crea el evento en Google Calendar del usuario (reutiliza calendar_service)."""
  ```
  - `draft_from_audio`:
    1. Comprueba `voice_calendar_enabled`.
    2. Verifica que el usuario tiene integración **activa** (`calendar_service.get_integration`); si no → `NotFoundError` con mensaje accionable ("Conecta Google Calendar en Ajustes").
    3. Valida audio (MIME/magic bytes/tamaño) y rate-limit por `(tenant_id, user_id)`.
    4. `voice_calendar.transcribe_audio()` → `transcript: str`.
    5. `voice_calendar.draft_event_from_transcript(transcript, ...)` → `_VoiceEventExtraction`.
    6. Ensambla `VoiceEventDraft` asignando `transcript` (del paso 4) al resultado del paso 5:
       ```python
       extraction = await voice_calendar.draft_event_from_transcript(transcript, ...)
       draft = VoiceEventDraft(transcript=transcript, **extraction.model_dump())
       ```
    7. Audit log `calendar.voice_transcribed` (sin guardar el audio; `metadata`: duración aprox., confidence, needs_clarification).
    8. Devuelve `VoiceEventDraft`.
  - `confirm_event`:
    - La firma recibe el `CalendarEventCreate` ya construido por la ruta (con `start`/`end` ya convertidos a ISO por `local_input_to_google_iso`). El servicio **siempre sobreescribe `reminders`** independientemente de lo que venga del formulario, para garantizar el invariante del objetivo #5:
      ```python
      # Constante a nivel de módulo en voice_event_service.py
      VOICE_REMINDERS: list[dict[str, object]] = [
          {"method": "popup", "minutes": 1440},  # 24 h antes
          {"method": "popup", "minutes": 60},    # 1 h antes
      ]
      ```
      ```python
      # Dentro de confirm_event()
      event_payload = event.model_copy(update={"reminders": VOICE_REMINDERS})
      await calendar_service.create_calendar_event(db, tenant_id, user_id, event_payload)
      ```
      Usar `model_copy(update=...)` en lugar de reconstruir el objeto evita repetir los campos y preserva cualquier dato extra que pueda tener el schema en el futuro.
    1. `event_payload = event.model_copy(update={"reminders": VOICE_REMINDERS})`.
    2. `calendar_service.create_calendar_event(db, tenant_id, user_id, event_payload)`.
    3. Audit log `calendar.event_created_from_voice` con `event_id`.
- [x] **No conoce HTTP** (recibe `bytes`/Pydantic, devuelve Pydantic). Cumple `AGENTS.md` §3.

---

## Sub-módulo D — Rutas web + UI

### D.1 — Rutas (`app/routes/web/calendar_voice.py`)

- [x] Crear router `prefix="/calendar/voice"` (devuelve **HTML**, patrón página/fragmento `render()`):
  ```python
  # GET  /calendar/voice            → página con grabador (o aviso si no hay integración)
  # POST /calendar/voice/transcribe → multipart audio → fragmento de CONFIRMACIÓN editable
  # POST /calendar/voice/confirm    → form (summary/start/end/description) → fragmento RESULTADO
  ```
  - Dependencias: `CurrentUser`, `CurrentTenant`, `RedisDep`, `get_db` (igual que `routes/web/chat.py`).
  - Imports necesarios de `app.core.calendar_datetime`: `local_input_to_google_iso` (conversión de fechas del formulario).
  - `transcribe`: `audio: UploadFile`; lee bytes en memoria, llama a `voice_event_service.draft_from_audio`, renderiza confirmación.
  - `confirm`: campos por `Form()`. Antes de construir `CalendarEventCreate`, convertir `start` y `end` con `local_input_to_google_iso()` (`app.core.calendar_datetime`), que añade el offset de zona horaria requerido por Google Calendar API:
    ```python
    # "2025-06-02T17:30"  →  "2025-06-02T17:30:00+02:00"
    start_iso = local_input_to_google_iso(start_form)
    end_iso   = local_input_to_google_iso(end_form)
    ```
    Sin esta conversión Google rechaza la petición (campo `start`/`end` sin offset). El mismo patrón se usa en los endpoints existentes de `/calendar/events`.
  - Manejo de errores `AppError` → fragmento con toast (no romper la página).
- [x] Registrar el router en `app/main.py`.

### D.2 — Grabador de micrófono (Alpine)

- [x] Componente Alpine en `app/static/js/alpine-components.js` (`Alpine.data("voiceRecorder", ...)`):
  - Usa **MediaRecorder API** (estado puramente cliente → permitido por la regla de stack para capacidades de cliente; no es lógica de negocio).
  - Estados: `idle | recording | uploading | error`. Cronómetro visible; corta a `voice_max_audio_seconds`.
  - Al detener, envía el `Blob` por HTMX (`hx-post` con `FormData`) o `fetch` a `/calendar/voice/transcribe`, target = contenedor de confirmación.
  - `x-cloak` para evitar FOUC; botón con `cursor-pointer` y área táctil ≥ 44px (accesibilidad).
- [x] **Progressive enhancement**: si el navegador no soporta `MediaRecorder`, mostrar input `type="file" accept="audio/*" capture="microphone"` como fallback (el móvil abre la grabadora nativa).

### D.3 — Templates

- [x] `app/templates/pages/calendar/voice.html` — página completa (extiende layout); incluye el grabador o el aviso "conecta Google Calendar".
- [x] `app/templates/components/voice_event_recorder.html` — botón micrófono + estados Alpine + `hx-indicator`.
- [x] `app/templates/components/voice_event_confirm.html` — formulario editable pre-cargado con el borrador:
  - Muestra `transcript` (lo que se entendió) como ayuda.
  - Campos: `summary`, `start` (datetime-local), `end` (datetime-local), `description`.
  - Si `confidence < voice_event_min_confidence` o `needs_clarification`: banner de aviso destacando que revise.
  - Botón "Crear evento" (`hx-post="/calendar/voice/confirm"`, `hx-confirm` opcional) + "Descartar".
- [x] `app/templates/components/voice_event_result.html` — éxito con enlace `html_link` al evento en Google Calendar + botón "Dictar otro".
- [x] Punto de entrada: botón "Nueva cita por voz" en la página **Calendario** (`/calendar`), junto a "Crear evento". (La configuración de la conexión sigue en Ajustes › Integraciones; la visualización y creación de eventos vive en `/calendar`.)

---

## Sub-módulo E — Observabilidad, seguridad y tests

### E.1 — Observabilidad y audit

- [x] `llm_calls` poblado en transcripción (`task="transcription"`) y en el borrador estructurado (`task="classify"`), cada uno con su coste/latencia/traza Langfuse.
- [x] Audit log: `calendar.voice_transcribed` y `calendar.event_created_from_voice` (helpers en `app/services/audit_service.py`).
- [ ] `usage_meter`: opcional, contabilizar notas de voz si se quiere para billing (no obligatorio en MVP).

### E.2 — Guardrails

- [x] Audio efímero: nunca se escribe a disco ni R2; se descarta tras transcribir.
- [x] Validación MIME por magic bytes + límite de bytes/duración.
- [x] Rate-limit por usuario (`voice_rate_limit_per_hour`) en Redis.
- [x] Confirmación humana obligatoria antes de crear el evento.
- [x] Aislamiento por RLS con `tenant_id` (siempre presente); el `user_id` selecciona el calendario del miembro dentro de ese tenant. Un usuario no puede crear en el calendario de otro.

### E.3 — Tests

- [x] `tests/unit/test_voice_event_schema.py` — `VoiceEventDraft.to_event_create()`, validaciones de longitud, `all_day`.
- [x] `tests/unit/test_voice_calendar_llm.py` — `draft_event_from_transcript` con cliente LLM **mockeado**: verifica que se inyecta `now_iso`/`timezone` y que se mapea a `CalendarEventCreate`; resolución de "mañana 17:30" → ISO correcto (mock determinista).
- [x] `tests/unit/test_voice_event_service.py`:
  - [x] `test_draft_requires_active_integration()` (sin Calendar → `NotFoundError`).
  - [x] `test_draft_rejects_oversized_audio()`.
  - [x] `test_draft_rejects_bad_mime()`.
  - [x] `test_rate_limit_blocks_after_threshold()`.
  - [x] `test_confirm_calls_calendar_create()` (mock `calendar_service`).
  - [x] `test_confirm_always_sets_voice_reminders()` — verifica que `confirm_event()` llama a `calendar_service.create_calendar_event` con `reminders=[{"method":"popup","minutes":1440}, {"method":"popup","minutes":60}]`, incluso si el `CalendarEventCreate` recibido tiene `reminders=None`.
- [x] `tests/integration/test_calendar_voice_routes.py`:
  - [x] `test_transcribe_returns_confirmation_fragment()` (LLM + transcripción mockeados).
  - [x] `test_confirm_creates_event_and_returns_result()` (`GoogleCalendarClient` mockeado).
  - [x] `test_voice_disabled_returns_friendly_message()` (`voice_calendar_enabled=False`).
- [x] `mypy --strict` y `ruff check` verdes en todo lo nuevo.

---

## Estructura de ficheros nueva / modificada

```
app/
  config.py                                   # + settings de voz (A.1)
  llm/
    client.py                                 # + TaskType "transcription", DEFAULT_MODELS, transcribe()
    voice_calendar.py                         # transcribe_audio + draft_event_from_transcript
    prompts/
      voice_transcribe_v1.txt                 # prompt transcripción literal
      voice_event_v1.txt                      # prompt extracción de evento
  schemas/
    calendar.py                               # + VoiceEventDraft (+ to_event_create)
                                              # + CalendarEventCreate.reminders (campo opcional)
  core/
    google_calendar_client.py                 # create_event(): añadir bloque reminders al body
  services/
    voice_event_service.py                    # draft_from_audio + confirm_event (inyecta VOICE_REMINDERS)
    audit_service.py                          # + helpers de audit de voz
  routes/web/
    calendar_voice.py                         # GET /calendar/voice, POST /transcribe, /confirm
  main.py                                     # + registrar router
  static/js/
    alpine-components.js                      # + Alpine.data("voiceRecorder", ...)
  templates/
    pages/calendar/voice.html
    components/voice_event_recorder.html
    components/voice_event_confirm.html
    components/voice_event_result.html
tests/
  unit/
    test_voice_event_schema.py
    test_voice_calendar_llm.py
    test_voice_event_service.py
  integration/
    test_calendar_voice_routes.py
```

> **Sin migraciones nuevas**: se reutiliza `calendar_integrations` (Paso 17). El scope `calendar.events` ya está en `google_calendar_scopes`.

---

## Verificación manual (checklist)

### Preparación

1. [x] `infisical run -- uv run alembic upgrade head` — BD al día (sin cambios de este paso, solo confirmar).
2. [x] Conectar Google Calendar en `/settings/integrations` con el usuario de prueba.
3. [x] Arrancar app por HTTPS accesible desde el móvil (ngrok en dev): `ngrok http 8000`.
4. [x] Arrancar worker no es necesario (flujo síncrono); sí el servidor: `infisical run -- uv run uvicorn app.main:app --reload`.

### Flujo voz → evento

5. [x] Abrir `/calendar/voice` en el móvil → conceder permiso de micrófono.
6. [x] Dictar: *"Mañana a las cinco y media reunión con Juan, una hora"* → detener.
7. [x] Aparece pantalla de confirmación con transcripción y campos `summary`, `start`, `end` pre-rellenados y coherentes con la fecha de mañana 17:30–18:30.
8. [x] Editar el título si hace falta → "Crear evento".
9. [x] Aparece resultado con enlace al evento; abrir el enlace → existe en Google Calendar.
10. [ ] Verificar en BD/observabilidad:
    ```sql
    SELECT task, model, status, input_tokens, output_tokens, cost_eur
    FROM llm_calls
    WHERE task IN ('transcription','classify')
    ORDER BY created_at DESC LIMIT 4;
    ```
11. [ ] Audit log registra `calendar.voice_transcribed` y `calendar.event_created_from_voice`:
    ```sql
    SELECT action, resource_type, created_at
    FROM audit_log
    WHERE action LIKE 'calendar.%'
    ORDER BY created_at DESC LIMIT 5;
    ```

### Guardrails

12. [ ] Dictar algo ambiguo (*"quedamos un día de estos"*) → `needs_clarification` → banner de aviso; el evento NO se crea hasta confirmar campos válidos.
13. [ ] Subir un audio > 8 MB (o no-audio renombrado) → mensaje de error amable, sin crear evento.
14. [ ] Superar el rate-limit (`voice_rate_limit_per_hour`) → mensaje de "demasiadas peticiones".
15. [ ] Usuario sin Google Calendar conectado → mensaje "Conecta Google Calendar en Ajustes".

### General

16. [ ] `infisical run -- uv run pytest tests/unit/test_voice_event_service.py tests/integration/test_calendar_voice_routes.py -v` — verde.
17. [ ] `uv run mypy app` y `uv run ruff check .` — verdes.

---

## Criterios de aceptación

- [ ] El usuario puede dictar por voz desde el móvil y obtener un **borrador editable** de evento.
- [ ] La transcripción usa **Gemini audio** (sin proveedores fuera de stack).
- [ ] Fechas/horas relativas ("mañana", "el viernes a las 5") se resuelven con la tz configurada.
- [ ] **Confirmación humana obligatoria** antes de crear el evento.
- [ ] El evento se crea en el **Google Calendar del usuario** (reutiliza Paso 17), con enlace de retorno.
- [ ] El evento incluye **dos recordatorios automáticos**: 24 horas antes y 1 hora antes (popup). El resto de eventos creados desde la UI estándar no se ven afectados (campo `reminders` opcional en `CalendarEventCreate`).
- [ ] El audio **no se persiste** en disco ni R2.
- [ ] `llm_calls` registra transcripción y extracción; `audit_log` registra ambas acciones.
- [ ] Guardrails: validación MIME/tamaño/duración, rate-limit por usuario, aislamiento RLS por `tenant_id` y selección de calendario por `user_id`.
- [ ] Fallback sin `MediaRecorder` mediante input de archivo con captura nativa.
- [ ] `mypy --strict` y `ruff check` verdes.

---

## Comandos útiles

```bash
# Servidor de desarrollo (HTTPS vía ngrok para micrófono en móvil)
infisical run -- uv run uvicorn app.main:app --reload
ngrok http 8000

# Tests
infisical run -- uv run pytest tests/unit/test_voice_calendar_llm.py -v
infisical run -- uv run pytest tests/integration/test_calendar_voice_routes.py -v

# Calidad
uv run ruff check . && uv run ruff format . && uv run mypy app

# Ver coste de las llamadas de voz
docker exec saas-postgres psql -U saas -d saas -c \
  "SELECT task, model, status, cost_eur, latency_ms FROM llm_calls \
   WHERE task IN ('transcription','classify') ORDER BY created_at DESC LIMIT 10;"

# Simular transcribe con un audio local (multipart)
curl -X POST https://<ngrok>/calendar/voice/transcribe \
  -H "Cookie: <sesion_clerk>" \
  -F "audio=@nota.ogg;type=audio/ogg"
```

---

## Acciones manuales resumidas

| # | Acción | Cuándo |
|---|--------|--------|
| 1 | Confirmar `GOOGLE_API_KEY` en Infisical (Gemini audio) | Antes de Sub-B |
| 2 | Conectar Google Calendar del usuario de prueba (`/settings/integrations`) | Antes de verificación |
| 3 | Túnel HTTPS (ngrok) para probar micrófono en móvil real | Antes de verificación D |
| 4 | (Opcional) Decidir tz por defecto definitiva en `voice_calendar_default_timezone` | Sub-A |
| 5 | Verificar que Gemini acepta el MIME que produce el navegador objetivo (ogg/opus vs webm) | Sub-D / Posibles problemas |
| 6 | Commit + PR cuando CI verde | Cierre del paso |

---

## Posibles problemas

| Síntoma | Causa probable | Mitigación |
|---------|----------------|------------|
| El navegador no graba | Origen no seguro (`http://` en móvil) | Servir por HTTPS (ngrok/Cloudflare); `localhost` sí funciona |
| Gemini rechaza el audio | `audio/webm;opus` no soportado directamente | Grabar como `audio/ogg` o `audio/mp4`; documentar MIME válidos; transcodificar como mejora futura |
| Fechas mal interpretadas | Falta contexto temporal o tz | Inyectar siempre `now_iso` + `timezone` en `voice_event_v1.txt`; bajar `voice_event_min_confidence` para forzar revisión |
| Evento creado en calendario equivocado | Integración mal resuelta | `calendar_service.get_integration(db, tenant_id, user_id)`; nunca crear sin integración activa |
| Token de Google caducado | Access token expirado | `ensure_fresh_token` del Paso 17 refresca automáticamente |
| "transcription" falla con Anthropic | Modelo override apunta a Claude | `transcribe()` exige proveedor Google; validar en `_resolve_model` |
| Coste alto por audios largos | Notas muy largas | Limitar `voice_max_audio_seconds`; truncar/avisar en UI |
| Audio sensible enviado a Google | PII en la nota de voz | Documentar en `/legal/subprocessors`; si en el futuro se requiere que el audio no salga de la infraestructura propia, valorar Whisper self-hosted (ver "Opción futura" en nota de stack) |

---

## Siguiente paso (orientativo)

| Paso | Contenido |
|------|-----------|
| **Paso 24** (propuesta) | Notas de voz entrantes en **WhatsApp/Telegram** reutilizando `voice_event_service` + `channel_chat_service`; y exponer la creación de eventos como **tool del chat** (módulo 1.5) con nueva `ToolFamily.calendar`. |
