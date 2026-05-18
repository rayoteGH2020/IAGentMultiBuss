"""Cliente Langfuse (singleton simple). Sin claves válidas el SDK trabaja en modo deshabilitado."""

from functools import lru_cache

from langfuse import Langfuse

from app.config import get_settings


@lru_cache(maxsize=1)
def get_langfuse() -> Langfuse:
    s = get_settings()
    pk = (s.langfuse_public_key or "").strip() or None
    sk = (s.langfuse_secret_key.get_secret_value() or "").strip() or None
    return Langfuse(
        public_key=pk,
        secret_key=sk,
        host=s.langfuse_host,
    )


def langfuse_tracing_ready() -> bool:
    """True si hay proyecto Langfuse configurado (no garantiza servidor en línea)."""
    s = get_settings()
    pk = (s.langfuse_public_key or "").strip()
    sk = (s.langfuse_secret_key.get_secret_value() or "").strip()
    return bool(pk and sk)
