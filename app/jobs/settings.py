"""Configuración del worker ARQ (funciones reales en Paso 14)."""

from typing import ClassVar

from arq.connections import RedisSettings

from app.config import get_settings
from app.jobs.invoice_jobs import process_invoice


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions: ClassVar[list[object]] = [process_invoice]
    max_jobs = 5
    job_timeout = 180
    keep_result = 3600
    max_tries = 2
