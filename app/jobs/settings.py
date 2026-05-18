"""Configuración del worker ARQ (funciones reales en Paso 14)."""

from typing import ClassVar

from arq.connections import RedisSettings

from app.config import get_settings


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 300
    keep_result = 3600
    functions: ClassVar[list[object]] = []
