# TODO REFACTOR CODE USE DEPENDENCY INJECTION

from typing import Optional

import redis.asyncio as redis
from redis.asyncio import Redis

from app.config import Settings


_redis_client: Optional[Redis] = None


async def connect(settings: Settings) -> Redis:
    global _redis_client

    if _redis_client is None:
        if not settings.redis_uri:
            raise ValueError("REDIS_URI is required")

        _redis_client = redis.from_url(
            settings.redis_uri,
            decode_responses=True,
        )

        await _redis_client.ping()

    return _redis_client
