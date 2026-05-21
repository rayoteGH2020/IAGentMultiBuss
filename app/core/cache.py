from typing import Any

import redis.asyncio as redis

from app.config import get_settings

# Singleton: el cliente Redis gestiona un pool de conexiones interno.
# Crear un cliente nuevo por request abriría y cerraría conexiones TCP
# constantemente, añadiendo latencia y agotando los file descriptors.
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        # cast a Any: los stubs de redis.asyncio tienen firmas genéricas que
        # mypy no resuelve bien en versiones intermedias de la librería;
        # el cast evita el falso positivo sin afectar al comportamiento.
        create_client: Any = redis.from_url
        _client = create_client(
            settings.redis_url,
            # encoding="utf-8": todas las claves y valores se tratan como
            # cadenas UTF-8 en lugar de bytes crudos.
            encoding="utf-8",
            # decode_responses=True: el cliente decodifica automáticamente las
            # respuestas de Redis a str. Sin esto, cada lectura devolvería bytes
            # y habría que llamar a .decode() manualmente en todo el código.
            decode_responses=True,
            # health_check_interval=30: cada 30 s el cliente envía un PING
            # para verificar que la conexión está viva. Previene errores de
            # "connection closed" en periodos de inactividad sin necesidad de
            # reconexión manual. Equivalente a pool_pre_ping en SQLAlchemy.
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    """Cierra el cliente Redis limpiamente durante el apagado de la app.

    Llamado en el lifespan de FastAPI (main.py). aclose() (no close()) porque
    es la variante async del cliente; close() existe pero es síncrona y no
    espera a que las operaciones pendientes terminen.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
