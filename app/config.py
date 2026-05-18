from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    app_secret_key: SecretStr
    app_base_url: str = "http://localhost:8000"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Database
    database_url: str
    redis_url: str

    # Storage (R2) — se usan en Paso 11
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: SecretStr = SecretStr("")
    r2_bucket: str = "saas-files"
    r2_endpoint_url: str | None = None
    r2_public_url: str = ""
    r2_region: str = "auto"
    storage_presigned_ttl_seconds: int = 3600

    # Auth (Clerk) — se usan en Paso 07
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
    langfuse_host: str = "http://localhost:3000"

    llm_model_extraction: str | None = None
    llm_model_chat: str | None = None
    llm_model_classify: str | None = None
    llm_model_sql: str | None = None

    # Crypto
    encryption_key: SecretStr = SecretStr("")

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
