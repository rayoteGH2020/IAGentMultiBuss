"""Tests del cliente Storage con moto (emulación de S3 en memoria).

moto intercepta todas las llamadas boto3 dentro del contexto mock_aws() y
las redirige a un servidor S3 falso en memoria. No se hace ninguna petición
de red real, por lo que estos tests son rápidos y no tienen coste de API.
"""

from urllib.parse import urlparse
from uuid import uuid4

import boto3
import pytest
from app.config import get_settings
from app.core.keys import invoice_key
from app.core.storage import Storage
from moto import mock_aws


def test_invoice_key_paths() -> None:
    # Test síncrono: la generación de keys no es async. Verifica que:
    # 1. La key tiene el prefijo correcto con el tenant_id.
    # 2. El UUID de unicidad está presente (contiene "-").
    # 3. La extensión del fichero se preserva tras la sanitización.
    # El nombre "subdir/archivo abril.pdf" tiene una barra y un espacio:
    # prueba que PurePosixPath extrae solo el nombre y que los caracteres
    # problemáticos se eliminan.
    tenant_id = uuid4()
    key = invoice_key(tenant_id, "subdir/archivo abril.pdf")
    assert key.startswith(f"invoices/{tenant_id}/")
    assert "-" in key  # UUID de unicidad presente
    assert ".pdf" in key  # extensión preservada


@pytest.mark.asyncio
async def test_storage_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Se inyectan variables de entorno de S3/moto porque Settings tiene
    # lru_cache: sin limpiar el cache, get_settings() devolvería la configuración
    # anterior (posiblemente vacía o de producción).
    monkeypatch.setenv("R2_ACCOUNT_ID", "test-acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-sk")
    monkeypatch.setenv("R2_BUCKET", "test-bucket")
    # moto requiere un endpoint AWS real (no el de R2) y una región válida.
    # Con R2_ENDPOINT_URL apuntando a R2 y region="auto", moto no funciona.
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://s3.amazonaws.com")
    monkeypatch.setenv("R2_REGION", "us-east-1")
    monkeypatch.setenv("STORAGE_PRESIGNED_TTL_SECONDS", "3600")
    # lru_cache clear: obliga a get_settings() a leer las nuevas vars de entorno.
    get_settings.cache_clear()

    with mock_aws():
        # moto arranca con un estado vacío: el bucket debe crearse explícitamente
        # antes de que Storage pueda subir objetos. Se usa un cliente admin
        # separado (no el de Storage) para evitar acoplar el setup al SUT.
        admin = boto3.client("s3", region_name="us-east-1")
        admin.create_bucket(Bucket="test-bucket")

        # Storage() instanciado dentro de mock_aws(): sus llamadas boto3
        # serán interceptadas por moto para toda la duración del bloque.
        storage = Storage()
        blob = b"hello world"
        key = "test/file.txt"

        # Round-trip completo: upload → exists → download → presigned URL → delete
        await storage.upload_bytes(key, blob, "text/plain")
        assert await storage.exists(key)

        got = await storage.download_bytes(key)
        assert got == blob

        url = await storage.presigned_url_get(key)
        # La URL debe referenciar el bucket y tener un scheme HTTP/HTTPS válido.
        assert "test-bucket" in url
        assert urlparse(url).scheme in ("http", "https")

        await storage.delete(key)
        # Verificación post-delete: el objeto no debe existir.
        assert not await storage.exists(key)

    # Limpieza del cache para que los tests posteriores no hereden la configuración
    # de moto (endpoint AWS, región us-east-1) en lugar de la de R2.
    get_settings.cache_clear()
