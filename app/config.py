from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Overrides opcionales del router de modelos (arquitectura.md §8).
    # Si son None, LLMClient usa los DEFAULT_MODELS definidos en llm/client.py.
    # Útil para cambiar el modelo en staging sin tocar código.
    llm_model_extraction: str | None = None
    llm_model_chat: str | None = None
    llm_model_classify: str | None = None
    llm_model_sql: str | None = None

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

    # Crypto
    # Clave para cifrar campos sensibles en BD (p. ej. conexiones de clientes
    # en módulo 3 via pgcrypto). 32 bytes en base64 es el tamaño recomendado
    # para AES-256.
    encryption_key: SecretStr = SecretStr("")

    # Metrics interno (Paso 15) — token para `/metrics/module1`
    # Bearer token simple para proteger el endpoint de métricas internas.
    # No es auth de usuario; es autenticación máquina-a-máquina (CI, dashboards).
    metrics_token: SecretStr = SecretStr("")

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


# Singleton de configuración: se construye una sola vez leyendo el entorno y
# se cachea. Llamar a Settings() en cada request sería costoso e innecesario
# porque el entorno no cambia durante la vida del proceso.
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
