"""Cliente async sobre S3-compatible (Cloudflare R2). Sin persistencia en disco local.

Por qué boto3 (SDK síncrono) en lugar de aioboto3 o aiobotocore:
boto3 es la librería oficial y más estable. Las variantes async han tenido
roturas de API en versiones menores. El patrón asyncio.to_thread() sobre
boto3 síncrono es el enfoque recomendado para I/O-bound sin bloquear el
event loop, sin sacrificar estabilidad de la interfaz.
"""

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
        # strip() elimina espacios que pueden aparecer en variables de entorno
        # copiadas manualmente. El or "" previene None antes del strip.
        configured = (s.r2_endpoint_url or "").strip()
        # Si R2_ENDPOINT_URL no está definido (producción), se construye el
        # endpoint de Cloudflare R2 a partir del account_id. En desarrollo,
        # R2_ENDPOINT_URL apunta a MinIO local (http://localhost:9000).
        endpoint = configured or f"https://{s.r2_account_id}.r2.cloudflarestorage.com"
        secret = s.r2_secret_access_key.get_secret_value()
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            # `or None`: boto3 interpreta una cadena vacía como credencial inválida
            # y lanza error en la primera operación. None le indica que no hay
            # credenciales configuradas, lo que produce un error más claro.
            aws_access_key_id=s.r2_access_key_id or None,
            aws_secret_access_key=secret or None,
            # r2_region="auto" es el valor correcto para R2; no usa regiones AWS.
            region_name=s.r2_region,
            config=Config(
                # SigV4: R2 requiere firma de versión 4 (igual que AWS S3).
                # Usar v2 o sin firma causaría errores de autenticación con R2.
                signature_version="s3v4",
                # retries: boto3 reintenta automáticamente errores transitorios
                # (5xx, timeouts de red) hasta 3 veces con backoff exponencial.
                retries={"max_attempts": 3},
            ),
        )
        self._bucket = s.r2_bucket

    async def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Sube bytes y devuelve la misma key."""
        # asyncio.to_thread: ejecuta la llamada síncrona de boto3 en un hilo
        # del pool sin bloquear el event loop de FastAPI/ARQ. Es el patrón
        # estándar para I/O-bound síncrono en código async.
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=data,
            # ContentType se almacena como metadato en R2 y lo devuelve en las
            # respuestas GET. Necesario para que los navegadores descarguen el
            # fichero con el tipo correcto (PDF abre en visor, imagen en galería).
            ContentType=content_type,
        )
        logger.info("storage.uploaded", key=key, size=len(data))
        # Devolver la key permite encadenar: `key = await storage.upload_bytes(...)`
        return key

    async def download_bytes(self, key: str) -> bytes:
        """Descarga el objeto completo en memoria.

        Para archivos grandes el consumo de RAM puede ser elevado. Aceptable
        dado el límite de 20 MB en uploads; si se levanta ese límite habrá que
        considerar streaming en lugar de cargar todo en memoria.
        """

        def _get() -> bytes:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            # resp["Body"] es un StreamingBody; .read() lo consume completo.
            # cast informa a mypy del tipo real porque los stubs de boto3
            # tipan Body como StreamingBody, no como bytes directamente.
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
        """URL firmada HTTP GET para descarga temporal.

        La URL incluye una firma HMAC válida durante `ttl` segundos. Pasado ese
        tiempo R2 devuelve 403. No requiere credenciales del cliente: el
        navegador puede descargar el fichero directamente desde R2 sin pasar
        por la app, ahorrando ancho de banda del servidor.
        """
        # TTL con fallback a la configuración global: permite sobreescribir
        # por llamada (p. ej. un enlace de 5 min para preview vs 1 h para descarga).
        ttl = ttl or get_settings().storage_presigned_ttl_seconds

        def _gen() -> str:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl,
            )
            return url

        # cast: generate_presigned_url devuelve str pero los stubs de boto3
        # lo tipan como Any en algunas versiones.
        return cast("str", await asyncio.to_thread(_gen))

    async def presigned_url_put(
        self,
        key: str,
        content_type: str,
        ttl: int | None = None,
    ) -> str:
        """URL firmada HTTP PUT para subida directa al bucket.

        Permite que el cliente suba ficheros directamente a R2 sin pasar por
        el servidor de la app (evita doble tráfico de red en ficheros grandes).
        No se usa en el flujo de upload actual (que va por el servidor) pero
        está preparado para una futura feature de subida directa desde el navegador.
        """
        ttl = ttl or get_settings().storage_presigned_ttl_seconds

        def _gen() -> str:
            url: str = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    # ContentType debe incluirse en los Params del PUT firmado:
                    # el cliente TIENE que enviar el mismo header Content-Type
                    # o R2 rechazará la petición con 403 (firma no coincide).
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
                # head_object solo recupera metadatos, no el body: mucho más
                # eficiente que get_object para comprobar existencia.
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                # R2 y distintas versiones del SDK de boto3 pueden devolver el
                # código 404 como int (HTTPStatusCode) o como string en el campo
                # Code. Se comprueban ambas formas para ser robustos.
                if status == 404 or code in ("404", "NoSuchKey"):
                    return False
                # Cualquier otro error (permisos, red, throttling) se re-lanza:
                # solo silenciamos el 404, que es el comportamiento esperado.
                raise

        return await asyncio.to_thread(_head)


# Singleton: boto3.client mantiene un pool de conexiones HTTP persistentes.
# Crear un cliente por request desperdiciaría conexiones y añadiría latencia
# de handshake TLS en cada operación de storage.
@lru_cache(maxsize=1)
def get_storage() -> Storage:
    """Singleton perezoso; reutiliza el cliente boto3."""
    return Storage()


def reset_storage_for_tests() -> None:
    """Vacía el singleton para tests que necesitan un cliente con config distinta.

    Se usa cache_clear() en lugar de asignar None a una variable global porque
    el singleton se gestiona a través de lru_cache, no de una variable de módulo.
    """
    get_storage.cache_clear()
