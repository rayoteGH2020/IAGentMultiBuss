from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.clerk_frontend import DEFAULT_CLERK_JS_VERSION


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # env_file=None: los secretos los inyecta Infisical en el entorno del
        # proceso (`infisical run -- ...`). Prohibido usar .env (Agents.md §2).
        env_file=None,
        # case_sensitive=False: permite que DATABASE_URL y database_url apunten
        # al mismo campo; Infisical y algunos shells usan MAYÚSCULAS por
        # convención pero la app define los campos en minúsculas.
        case_sensitive=False,
        # extra="ignore": Infisical puede inyectar variables que no están en
        # este modelo (secretos de otros servicios del mismo proyecto). Sin este
        # flag, Pydantic lanzaría ValidationError al arrancar.
        extra="ignore",
    )

    # App
    # Literal restringe los valores permitidos; evita bugs por typos ("prod" en
    # lugar de "production") y permite ramificar comportamiento con `is_dev`.
    app_env: Literal["development", "staging", "production"] = "development"
    # Sin default: campo obligatorio. Si no se inyecta, la app no arranca.
    app_secret_key: SecretStr
    app_base_url: str = "http://localhost:8000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Zona horaria por defecto para mostrar timestamps en plantillas (BD sigue en UTC).
    app_display_timezone: str = "Europe/Madrid"
    # Seguridad HTTP. En produccion se fuerza HTTPS redirect y HSTS desde create_app().
    # SECURITY_ALLOWED_HOSTS debe incluir dominios publicos y hosts internos del proxy.
    security_allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver", "test"]
    security_https_redirect: bool = False
    security_hsts_enabled: bool = False

    # Database
    # Sin defaults: ambas conexiones son imprescindibles para cualquier request.
    database_url: str
    redis_url: str

    # Storage (R2) — se usan en Paso 11
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    # SecretStr oculta la clave en repr() y logs; evita que aparezca en
    # tracebacks o en la respuesta de /health si se serializa el objeto.
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "saas-files"
    # None en producción apunta al endpoint de R2 de Cloudflare resuelto
    # automáticamente por boto3. En desarrollo local se establece a la URL de
    # MinIO (p. ej. http://localhost:9000) para emular R2 sin coste.
    r2_endpoint_url: str | None = None
    r2_public_url: str = ""
    # "auto" es el valor correcto para Cloudflare R2: no usa regiones AWS.
    # Cambiar esto a una región AWS provocaría errores de autenticación con R2.
    r2_region: str = "auto"
    # TTL de 1 hora para URLs prefirmadas: equilibrio entre seguridad (la URL
    # expira) y usabilidad (suficiente para descargar/previsualizar un fichero).
    storage_presigned_ttl_seconds: int = 3600

    # Auth (Clerk) — se usan en Paso 07
    # Defaults vacíos: Clerk puede no estar configurado en fases tempranas del
    # desarrollo (Paso 01-06). La app arranca pero los endpoints protegidos
    # devolverán 401 hasta que se inyecten los valores reales.
    clerk_secret_key: SecretStr = SecretStr("")
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""
    clerk_webhook_secret: SecretStr = SecretStr("")
    # Versión fijada de @clerk/clerk-js (CDN). No usar @latest (supply chain).
    clerk_js_version: str = DEFAULT_CLERK_JS_VERSION

    # LLM providers — se usan en Paso 10
    anthropic_api_key: SecretStr = SecretStr("")
    google_api_key: SecretStr = SecretStr("")
    voyage_api_key: SecretStr = SecretStr("")

    # Observability
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    # En local apunta al Langfuse del docker-compose; en prod a la instancia
    # self-hosted en la VPS (arquitectura.md §2).
    langfuse_host: str = "http://localhost:3000"
    # Captura de contenido íntegro (prompts, documentos, respuestas) en las
    # trazas. Por defecto solo se envían metadatos de evaluación: el contenido
    # de cliente no sale de Postgres/R2 (arquitectura.md §8). Solo activable en
    # desarrollo y con datos sintéticos.
    langfuse_capture_content: bool = False

    # Overrides opcionales del router de modelos (arquitectura.md §8).
    # Si son None, LLMClient usa los DEFAULT_MODELS definidos en llm/client.py.
    # Cada campo se inyecta vía Infisical con su nombre en MAYÚSCULAS
    # (p. ej. LLM_MODEL_EXTRACTION) y tiene prioridad sobre el default.
    # El proveedor se infiere del prefijo del modelo en _resolve_model:
    # "claude-*" → Anthropic, "voyage-*" → Voyage, resto → Google.
    # Permite cambiar de modelo/proveedor en staging o prod sin redeploy.
    llm_model_extraction: str | None = None
    llm_model_chat: str | None = None
    llm_model_classify: str | None = None
    llm_model_sql: str | None = None
    llm_model_translate: str | None = None
    # Override opcional del modelo de transcripción (None → DEFAULT_MODELS["transcription"]).
    llm_model_transcription: str | None = None

    # Límites de recursos en procesado documental.
    # Un PDF o una imagen pequeños en bytes pueden expandirse a gigabytes al
    # decodificarse (decompression bomb). Estos topes se validan ANTES de
    # decodificar y son fail-closed: si no se pueden verificar, se rechaza.
    # Páginas admitidas por documento de negocio (factura, ticket). Todas se
    # envían al LLM, así que subir este número multiplica coste y latencia.
    document_max_pdf_pages: int = 3
    # Área máxima en píxeles tras decodificar (unos 8000 por 5000). Pillow
    # aborta la decodificación al superarlo (Image.MAX_IMAGE_PIXELS).
    document_max_image_pixels: int = 40_000_000
    # Lado máximo: descarta imágenes tipo 1 por 500.000 px que pasan el área.
    document_max_image_edge_px: int = 20_000
    # Techo duro del procesado excepcional autorizado por el superadmin. El
    # override salta los límites de negocio, no los de supervivencia del worker.
    document_override_max_pdf_pages: int = 100
    # Estimación mostrada al superadmin antes de autorizar. Calibrado a ojo con
    # extracciones de 1-3 páginas; ajustar con datos reales de llm_calls.
    document_estimated_seconds_per_page: float = 15.0
    document_estimated_input_tokens_per_page: int = 2_500
    document_estimated_output_tokens_per_page: int = 900
    # Multiplicador sobre el coste de proveedor al repercutir un procesado
    # excepcional al cliente (1.0 = a coste, sin margen).
    document_override_charge_multiplier: float = 1.0

    # Knowledge / RAG ingesta (Paso 18)
    # Los documentos de conocimiento son libros o manuales: el tope es mucho más
    # alto que en facturas, pero existe (pypdf recorre página a página).
    knowledge_max_pdf_pages: int = 300
    # Techo de texto extraído antes de chunkificar (~325k tokens).
    knowledge_max_extracted_chars: int = 1_300_000
    knowledge_max_file_size_bytes: int = 15 * 1024 * 1024  # 15 MB
    knowledge_chunk_target_tokens: int = 600
    knowledge_chunk_overlap_tokens: int = 100
    # Default en runtime: voyage-3-lite (ver resolved_knowledge_embedding_model).
    # Override vía KNOWLEDGE_EMBEDDING_MODEL en Infisical.
    knowledge_embedding_model: str | None = None
    # voyage-3-lite solo admite 512 dimensiones; debe coincidir con vector(N) en BD.
    knowledge_embedding_dimensions: int = 512
    knowledge_index_max_concurrent_per_tenant: int = 3
    knowledge_max_uploads_per_day: int = 20
    # MIME permitidos: PDF, texto plano, Markdown y fotos (JPEG/PNG/WebP).
    # Las imágenes pasan por OCR vía LLM antes de ser chunkificadas (Paso 22).
    knowledge_allowed_mimes: list[str] = [
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "image/jpeg",
        "image/png",
        "image/webp",
    ]
    # Sub-fase opcional: enriquece cada chunk con una línea de contexto vía LLM.
    # Desactivado por defecto; activar solo cuando el pipeline base esté estable.
    knowledge_contextual_retrieval_enabled: bool = False

    # Knowledge retrieval — búsqueda híbrida (Paso 19)
    # RRF: Reciprocal Rank Fusion — k estándar de la literatura; ajustar solo si se evalúa.
    knowledge_rrf_k: int = 60
    # Candidatos previos al merge: cuántos resultados trae cada rama antes de fusionar.
    knowledge_dense_candidates: int = 60  # LIMIT de la query vectorial HNSW
    knowledge_sparse_candidates: int = 60  # LIMIT de la query BM25 tsvector
    # Chunks devueltos al LLM tras el merge. top_k ≤ max_top_k siempre.
    knowledge_default_top_k: int = 10
    knowledge_max_top_k: int = 25  # techo para evitar saturar el contexto LLM

    # Rate-limit de búsqueda por tenant (peticiones/minuto); ventana deslizante en Redis.
    knowledge_search_rpm_limit: int = 120

    # Módulo 2 RAG — chat unificado (Paso 20)
    # True en dev: el LLM recibe tools de conocimiento además de las documentales.
    # En producción puede desactivarse globalmente o por tenant (tenants.settings jsonb, futuro).
    knowledge_tools_enabled: bool = True
    # Citas en UI: máximo por mensaje assistant.
    # El umbral está en 0.0 porque los scores son RRF (escala ~0.01-0.016), no coseno (0-1);
    # el filtro de calidad real es el top_k de la búsqueda.
    knowledge_chat_max_citations: int = 5
    knowledge_chat_min_score_threshold: float = 0.0
    chat_daily_message_limit: int = 60
    chat_max_message_bytes: int = 4096
    chat_history_message_limit: int = 20
    chat_stream_chunk_chars: int = 80

    # Reintentos ante errores transitorios del proveedor LLM (HTTP 429/5xx/529).
    # OFF por defecto: en evals/desarrollo es mejor ver el fallo crudo. En
    # producción activarlo evita que un 503 puntual de Gemini/Anthropic se
    # convierta en un error visible al usuario. Independiente del `max_retries`
    # de Instructor (que solo cubre ValidationError de schema, no errores HTTP).
    llm_retry_transient_errors: bool = False
    # Número máximo de intentos (incluido el primero). 4 = 3 reintentos efectivos.
    llm_retry_max_attempts: int = 4
    # Espera máxima entre reintentos en segundos. El backoff exponencial empieza
    # en 1 s y duplica hasta este techo, con jitter para evitar tormentas.
    llm_retry_max_wait_seconds: float = 15.0

    # Google Calendar OAuth (Paso 17)
    # Defaults vacíos: la app arranca sin integración; /settings/integrations
    # muestra «Conectar» deshabilitado o un aviso hasta que Infisical inyecte
    # GOOGLE_OAUTH_CLIENT_ID y GOOGLE_OAUTH_CLIENT_SECRET.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr = SecretStr("")
    # Scopes solicitados al usuario en el flujo OAuth (espacio-separated, RFC 6749).
    # userinfo.email: necesario para resolver google_email tras el callback.
    google_calendar_scopes: str = (
        "https://www.googleapis.com/auth/calendar.readonly "
        "https://www.googleapis.com/auth/calendar.events "
        "https://www.googleapis.com/auth/userinfo.email"
    )

    # Crypto
    # Clave para cifrar campos sensibles en BD (p. ej. conexiones de clientes
    # en módulo 3 via pgcrypto). 32 bytes en base64 es el tamaño recomendado
    # para AES-256.
    encryption_key: SecretStr = SecretStr("")

    # WhatsApp Business API (Paso 21 E)
    # whatsapp_verify_token: token arbitrario que Meta devuelve en la verificación GET.
    # whatsapp_app_secret: secreto de la app Meta para validar firma HMAC-SHA256.
    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_api_url: str = "https://graph.facebook.com/v20.0"
    whatsapp_max_response_chars: int = 1000

    # Webhooks externos (WhatsApp / Telegram) — fail-closed en staging/prod (Paso CDX 4).
    # True solo en dev local explícito; nunca en staging/production (validado abajo).
    webhook_allow_unsigned: bool = False

    # Telegram Bot API (Paso 21 F)
    telegram_api_url: str = "https://api.telegram.org"

    # Canales externos — umbral de confianza por defecto (Paso 21 E/F)
    channel_confidence_threshold_default: float = 0.5
    # Máximo de mensajes por hora por customer_identifier (token bucket en Redis).
    channel_rate_limit_msg_per_hour: int = 60

    # Semantic cache de canales externos (Paso 21 E/F)
    channel_cache_enabled: bool = True
    channel_cache_ttl_hours: int = 24
    channel_cache_similarity_threshold: float = 0.92
    channel_cache_min_confidence: float = 0.6

    # FAQ manual (Paso 21 B)
    knowledge_faq_max_pairs: int = 200
    knowledge_faq_min_answer_chars: int = 10

    # Admin superusuario (Paso 21)
    # ID de la organización Clerk que identifica al superadmin (Ruben).
    # Si está vacío, las rutas /admin devuelven 403.
    admin_clerk_org_id: str = ""
    # Allowlist opcional (defensa en profundidad): clerk_user_id separados por
    # coma. Vacío = cualquier admin activo de ADMIN_CLERK_ORG_ID. Con valores,
    # además del rol admin el usuario debe estar en la lista.
    superadmin_clerk_user_ids: str = ""

    # Email SMTP — notificaciones internas (Paso 21, solución temporal)
    # Si smtp_host está vacío, los envíos se omiten silenciosamente (útil en dev).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_starttls: bool = True

    # Métricas interno (Paso 15) — token para `/metrics/module1`
    # Bearer token simple para proteger el endpoint de métricas internas.
    # No es auth de usuario; es autenticación máquina-a-máquina (CI, dashboards).
    metrics_token: SecretStr = SecretStr("")

    # Voz → Google Calendar (Paso 23)
    voice_calendar_enabled: bool = True
    voice_max_audio_bytes: int = 8 * 1024 * 1024  # 8 MB
    voice_max_audio_seconds: int = 60  # duración máx. de la nota
    # MIME aceptados por Gemini audio. audio/webm puede requerir transcodificación
    # en algunos entornos; preferir audio/ogg o audio/mp4 desde MediaRecorder.
    voice_allowed_audio_mimes: list[str] = [
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/wav",
        "audio/webm",
    ]
    # Zona horaria para resolver fechas/horas relativas dictadas ("mañana", "a las 5").
    # Futuro: tz por usuario/tenant. De momento, valor único configurable.
    voice_calendar_default_timezone: str = "Europe/Madrid"
    # Duración por defecto si el usuario no especifica fin (minutos).
    voice_event_default_duration_minutes: int = 60
    # Umbral de confianza por debajo del cual la UI exige revisión explícita.
    voice_event_min_confidence: float = 0.5
    # Rate-limit por usuario: notas de voz por hora (ventana deslizante en Redis).
    voice_rate_limit_per_hour: int = 30

    @property
    def resolved_knowledge_embedding_model(self) -> str:
        """Modelo Voyage para RAG; fallback al default de LLMClient si no hay override."""
        return self.knowledge_embedding_model or "voyage-3-lite"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def superadmin_clerk_user_id_set(self) -> frozenset[str]:
        """Allowlist de superadmins; vacía significa "sin restricción extra"."""
        return frozenset(
            part.strip() for part in self.superadmin_clerk_user_ids.split(",") if part.strip()
        )

    @property
    def allows_unsigned_webhooks(self) -> bool:
        """Permite omitir verificación criptográfica solo en dev con flag explícito."""
        return self.is_dev and self.webhook_allow_unsigned

    @model_validator(mode="after")
    def _reject_unsigned_webhooks_outside_dev(self) -> Self:
        if self.webhook_allow_unsigned and not self.is_dev:
            raise ValueError(
                "WEBHOOK_ALLOW_UNSIGNED must be false when APP_ENV is staging or production"
            )
        return self

    @model_validator(mode="after")
    def _reject_langfuse_content_outside_dev(self) -> Self:
        if self.langfuse_capture_content and not self.is_dev:
            raise ValueError(
                "LANGFUSE_CAPTURE_CONTENT must be false when APP_ENV is staging or production"
            )
        return self


# Singleton de configuración: se construye una sola vez leyendo el entorno y
# se cachea. Llamar a Settings() en cada request sería costoso e innecesario
# porque el entorno no cambia durante la vida del proceso.
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
