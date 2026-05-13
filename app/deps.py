from collections.abc import AsyncIterator

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_redis
from app.core.db import get_sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_redis_dep() -> redis.Redis:
    return get_redis()
