"""Cola ARQ: encolado de jobs (el worker implementa `process_invoice` en Paso 14)."""

from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

_pool: ArqRedis | None = None


@lru_cache(maxsize=1)
def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


def reset_arq_pool_for_tests() -> None:
    """Limpia pool y cache (solo tests que cambian `REDIS_URL` o repetición)."""
    global _pool
    _pool = None
    _redis_settings.cache_clear()


async def enqueue_invoice_processing(invoice_id: UUID, tenant_id: UUID) -> str:
    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "process_invoice",
        str(invoice_id),
        str(tenant_id),
        _job_id=f"invoice:{invoice_id}",
    )
    if job is None:
        msg = "enqueue_invoice_processing returned no job"
        raise RuntimeError(msg)
    return str(job.job_id)
