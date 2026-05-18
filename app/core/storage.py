"""Cliente async sobre S3-compatible (Cloudflare R2). Sin persistencia en disco local."""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import cast

import boto3
import structlog
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import get_settings

logger = structlog.get_logger(__name__)


class Storage:
    """Put/get/delete y presigned URLs usando un único bucket configurado en Settings."""

    def __init__(self) -> None:
        s = get_settings()
        configured = (s.r2_endpoint_url or "").strip()
        endpoint = configured or f"https://{s.r2_account_id}.r2.cloudflarestorage.com"
        secret = s.r2_secret_access_key.get_secret_value()
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=s.r2_access_key_id or None,
            aws_secret_access_key=secret or None,
            region_name=s.r2_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
        self._bucket = s.r2_bucket

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Sube bytes y devuelve la misma key."""
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
        """Descarga el objeto completo en memoria."""

        def _get() -> bytes:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return cast("bytes", resp["Body"].read())

        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        """Elimina el objeto del bucket."""
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._bucket,
            Key=key,
        )
        logger.info("storage.deleted", key=key)

    async def presigned_url_get(self, key: str, ttl: int | None = None) -> str:
        """URL firmada HTTP GET para descarga temporal."""
        ttl = ttl or get_settings().storage_presigned_ttl_seconds

        def _gen() -> str:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl,
            )
            return url

        return cast("str", await asyncio.to_thread(_gen))

    async def presigned_url_put(
        self,
        key: str,
        content_type: str,
        ttl: int | None = None,
    ) -> str:
        """URL firmada HTTP PUT para subida directa al bucket."""
        ttl = ttl or get_settings().storage_presigned_ttl_seconds

        def _gen() -> str:
            url: str = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=ttl,
            )
            return url

        return cast("str", await asyncio.to_thread(_gen))

    async def exists(self, key: str) -> bool:
        """True si el objeto existe (head_object)."""

        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status == 404 or code in ("404", "NoSuchKey"):
                    return False
                raise

        return await asyncio.to_thread(_head)


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    """Singleton perezoso; reutiliza el cliente boto3."""
    return Storage()


def reset_storage_for_tests() -> None:
    """Vacía el singleton (solo tests que cambian env entre casos)."""
    get_storage.cache_clear()
