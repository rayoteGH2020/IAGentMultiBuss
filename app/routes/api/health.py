from typing import Any, cast

import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_redis_dep

router = APIRouter(tags=["health"])


async def _redis_ping(r: redis.Redis) -> bool:
    cmd: Any = r.ping()
    return cast("bool", await cmd)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    result = await db.execute(text("SELECT 1"))
    assert result.scalar_one() == 1
    return {"status": "ok"}


@router.get("/health/redis")
async def health_redis(r: redis.Redis = Depends(get_redis_dep)) -> dict[str, str]:
    pong = await _redis_ping(r)
    assert pong is True
    return {"status": "ok"}
