"""Cliente Langfuse (singleton simple). Sin claves válidas el SDK trabaja en modo deshabilitado."""

from functools import lru_cache

from langfuse import Langfuse

from app.config import get_settings


@lru_cache(maxsize=1)
def get_langfuse() -> Langfuse:
    s = get_settings()
    # La doble conversión `(valor or "").strip() or None` normaliza tres casos
    # posibles en la variable de entorno: no definida, vacía, o solo espacios.
    # En todos ellos el resultado es None, que Langfuse SDK interpreta como
    # "sin configurar" y pasa a modo deshabilitado (silencia todas las llamadas).
    # Esto permite que la app arranque y funcione sin Langfuse en desarrollo
    # sin modificar código ni variables de entorno adicionales.
    pk = (s.langfuse_public_key or "").strip() or None
    sk = (s.langfuse_secret_key.get_secret_value() or "").strip() or None
    return Langfuse(
        public_key=pk,
        secret_key=sk,
        # host apunta al Langfuse self-hosted en la VPS (producción) o al
        # contenedor del docker-compose local (desarrollo).
        host=s.langfuse_host,
    )


def langfuse_tracing_ready() -> bool:
    """True si hay proyecto Langfuse configurado (no garantiza servidor en línea).

    Usado por health checks y código que quiera omitir pasos Langfuse-específicos
    cuando el trazado no está disponible, sin tener que capturar excepciones.
    """
    s = get_settings()
    pk = (s.langfuse_public_key or "").strip()
    sk = (s.langfuse_secret_key.get_secret_value() or "").strip()
    return bool(pk and sk)
