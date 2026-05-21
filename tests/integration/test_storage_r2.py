"""Test de integración contra R2 real o MinIO. Gateado por RUN_R2_TESTS=1.

Por qué está gateado:
- Requiere credenciales de R2/MinIO inyectadas por Infisical.
- Genera peticiones reales (con potencial coste de R2 en producción).
- Útil antes de desplegar cambios en la capa de storage o al configurar
  un nuevo bucket/entorno.

Este test complementa test_storage.py (que usa moto, sin red) con una
verificación de que la configuración de R2 y las credenciales son correctas
en el entorno donde se ejecuta.
"""

import os
import uuid
from urllib.parse import urlparse

import pytest
from app.config import get_settings
from app.core.storage import get_storage, reset_storage_for_tests

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_R2_TESTS", "").strip() != "1", reason="set RUN_R2_TESTS=1 to enable"
)
async def test_real_r2_round_trip() -> None:
    """Requiere bucket y credenciales válidas en el entorno de ejecución."""
    # Limpia el cache para forzar la lectura de las credenciales actuales del
    # entorno (inyectadas por Infisical), no las de una ejecución previa.
    get_settings.cache_clear()
    reset_storage_for_tests()

    storage = get_storage()
    # UUID en la key: garantiza que runs concurrentes o consecutivos no colisionan.
    # Prefijo "tests/": facilita identificar y limpiar objetos de test en el bucket.
    key = f"tests/{uuid.uuid4()}.bin"
    blob = b"x" * 1024
    try:
        await storage.upload_bytes(key, blob, "application/octet-stream")
        assert await storage.exists(key)

        # TTL corto (2 minutos): suficiente para verificar la URL sin dejar
        # enlaces públicos activos más tiempo del necesario en R2.
        url = await storage.presigned_url_get(key, ttl=120)
        assert urlparse(url).scheme in ("http", "https")
    finally:
        # Siempre borrar el objeto de test de R2, incluso si las aserciones
        # fallaron. Sin esto cada ejecución fallida dejaría residuos en el bucket.
        await storage.delete(key)
        # Limpiar singletons para no contaminar tests posteriores con las
        # credenciales de R2 reales (que pueden no estar disponibles en CI).
        reset_storage_for_tests()
        get_settings.cache_clear()
