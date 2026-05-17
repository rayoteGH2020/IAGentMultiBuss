# Paso 11 — Cliente de almacenamiento R2 (Cloudflare) con presigned URLs

## Objetivo

Implementar la capa de almacenamiento de archivos. Cloudflare R2 (compatible con S3) guardará facturas originales, documentos del módulo 2 y cualquier otro fichero subido por usuarios. La clave del paso es el módulo `app/core/storage.py`: una API simple que el resto del código usará sin conocer detalles de boto3.

Al final del paso, un test sube un fichero, genera presigned URL de descarga, descarga el fichero y lo borra, todo contra R2 (o MinIO local si lo activaste en Docker Compose).

## Pre-requisitos

- Pasos 01-10 completados.
- Cuenta de Cloudflare con R2 activado y bucket creado en región EU.
- Credenciales: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
- Alternativa local: MinIO en `docker-compose.yml` con bucket `saas-files` ya creado.

## Contexto relevante

- `arquitectura.md` sección 2 (Stack — Storage) y sección 7 (Seguridad: archivos siempre en R2, nunca disco del servidor).
- `Agents.md`: NO guardar archivos en disco del servidor; siempre R2. Acceso encapsulado en `app/core/storage.py`.

## Tareas

- [ ] Añadir dependencias: `boto3`, `botocore` (ya vienen juntos).
- [ ] Para tests: `moto[s3]`.
- [ ] Añadir variables R2 a `app/config.py` y `.env.example`.
- [ ] Crear `app/core/storage.py` con clase `Storage` y funciones públicas.
- [ ] Crear `app/core/keys.py` con helpers de generación de claves.
- [ ] Crear `app/core/storage.py` singleton accesible por `get_storage()`.
- [ ] Test unitario con moto en `tests/unit/test_storage.py`.
- [ ] Test de integración opcional contra R2 real en `tests/integration/test_storage_r2.py` (gated por env var).
- [ ] Commit: `feat: r2 storage client with presigned urls`.

## Detalles técnicos

### `app/config.py` (añadir)

```python
class Settings(BaseSettings):
    # ... lo anterior ...
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str = "saas-files"
    r2_endpoint_url: str | None = None  # auto si no se provee
    r2_region: str = "auto"
    storage_presigned_ttl_seconds: int = 3600
```

En `.env.example`:

```
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=saas-files
R2_ENDPOINT_URL=
R2_REGION=auto
```

Para R2 real, el endpoint es `https://<account_id>.r2.cloudflarestorage.com`. Para MinIO local, `http://minio:9000`.

### `app/core/keys.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath


def invoice_key(tenant_id: uuid.UUID, original_filename: str) -> str:
    """Genera una key estable y segura para una factura subida.

    Estructura: invoices/{tenant_id}/{yyyy}/{mm}/{uuid}-{slug_filename}
    """
    now = datetime.now(timezone.utc)
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return f"invoices/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"


def document_key(tenant_id: uuid.UUID, original_filename: str) -> str:
    now = datetime.now(timezone.utc)
    name = PurePosixPath(original_filename).name.replace("/", "_").replace("\\", "_")
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "file"
    return f"documents/{tenant_id}/{now:%Y/%m}/{uuid.uuid4()}-{safe}"
```

### `app/core/storage.py`

```python
from __future__ import annotations

import asyncio
from functools import lru_cache

import boto3
import structlog
from botocore.client import Config

from app.config import settings

logger = structlog.get_logger(__name__)


class Storage:
    def __init__(self) -> None:
        endpoint = (
            settings.r2_endpoint_url
            or f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name=settings.r2_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
        self._bucket = settings.r2_bucket

    async def upload_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        logger.info("storage.uploaded", key=key, size=len(data))
        return key

    async def download_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._bucket, Key=key
        )
        logger.info("storage.deleted", key=key)

    async def presigned_url_get(self, key: str, ttl: int | None = None) -> str:
        ttl = ttl or settings.storage_presigned_ttl_seconds

        def _gen() -> str:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl,
            )

        return await asyncio.to_thread(_gen)

    async def presigned_url_put(
        self, key: str, content_type: str, ttl: int | None = None
    ) -> str:
        ttl = ttl or settings.storage_presigned_ttl_seconds

        def _gen() -> str:
            return self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=ttl,
            )

        return await asyncio.to_thread(_gen)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except self._client.exceptions.ClientError:
                return False

        return await asyncio.to_thread(_head)


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    return Storage()
```

### Test unitario con moto

`tests/unit/test_storage.py`:

```python
import pytest
from moto import mock_aws

from app.core.storage import Storage


@pytest.mark.asyncio
async def test_storage_round_trip(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "test")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("R2_BUCKET", "test-bucket")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://s3.amazonaws.com")

    with mock_aws():
        # Recargar settings y crear bucket
        import boto3
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")

        storage = Storage()
        key = "test/file.txt"
        await storage.upload_bytes(key, b"hello world", "text/plain")
        assert await storage.exists(key)

        data = await storage.download_bytes(key)
        assert data == b"hello world"

        url = await storage.presigned_url_get(key)
        assert "test-bucket" in url

        await storage.delete(key)
        assert not await storage.exists(key)
```

### Test de integración (opcional)

`tests/integration/test_storage_r2.py`:

```python
import os
import uuid
import pytest

from app.core.storage import get_storage


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("RUN_R2_TESTS"), reason="set RUN_R2_TESTS=1 to enable"
)
async def test_real_r2_round_trip():
    storage = get_storage()
    key = f"tests/{uuid.uuid4()}.bin"
    await storage.upload_bytes(key, b"x" * 1024, "application/octet-stream")
    try:
        assert await storage.exists(key)
        url = await storage.presigned_url_get(key, ttl=60)
        assert url.startswith("https://")
    finally:
        await storage.delete(key)
```

## Criterios de aceptación

- Test unitario con moto pasa.
- `mypy` y `ruff` pasan.
- Si configuras R2 real y `RUN_R2_TESTS=1`, el test de integración pasa.
- En el bucket de R2 (o MinIO) puedes ver el objeto subido por el test.

## Comandos útiles

```bash
# Crear bucket en MinIO local
docker compose exec minio mc alias set local http://localhost:9000 minio minio123
docker compose exec minio mc mb local/saas-files

# Listar contenidos del bucket
docker compose exec minio mc ls local/saas-files

# Probar contra R2 real
RUN_R2_TESTS=1 uv run pytest tests/integration/test_storage_r2.py -v
```

## Lo que NO toca este paso

- Subir archivos desde la UI (Paso 13).
- Asociar archivos a `Invoice` (Paso 13).
- Antivirus / validación de mime real (lo añadimos en Paso 13 con validación básica).
- Worker de extracción (Paso 14).

## Posibles problemas

- **`Endpoint` mal formado**: para R2 el formato exacto es `https://<account_id>.r2.cloudflarestorage.com`. Si copias del dashboard de Cloudflare, asegúrate de no incluir el bucket en la URL.
- **Permisos del API token**: el token debe tener permiso "Object Read & Write" sobre el bucket; el de solo lectura silenciosamente falla en `put_object`.
- **`signature_version` distinta**: R2 exige `s3v4`. Sin esa configuración, las presigned URLs fallan con 403.
- **CORS**: si más adelante haces uploads directos navegador→R2 con presigned PUT, configura CORS en el bucket (lo abordamos en Paso 13).

## Siguiente paso

`Paso12.md` — Schema Pydantic `Factura` + función `extract_invoice(file_bytes, mime_type)` con Instructor, prompt versionado `extraction_v1`, y tests con un PDF de fixture.
