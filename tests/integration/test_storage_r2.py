"""Integración opcional contra R2 o MinIO real (usa credenciales de Infisical/entorno)."""

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
    """Requiere bucket y credenciales válidas."""
    get_settings.cache_clear()
    reset_storage_for_tests()

    storage = get_storage()
    key = f"tests/{uuid.uuid4()}.bin"
    blob = b"x" * 1024
    try:
        await storage.upload_bytes(key, blob, "application/octet-stream")
        assert await storage.exists(key)

        url = await storage.presigned_url_get(key, ttl=120)
        assert urlparse(url).scheme in ("http", "https")
    finally:
        await storage.delete(key)
        reset_storage_for_tests()
        get_settings.cache_clear()
