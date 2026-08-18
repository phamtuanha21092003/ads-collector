# TODO REFACTOR CODE USE DEPENDENCY INJECTION

from typing import Optional

import redis
from redis import Redis

from app.config import Settings


_redis_client: Optional[Redis] = None


def connect(settings: Settings) -> Redis:
    global _redis_client

    if _redis_client is None:
        if not settings.redis_uri:
            raise ValueError("REDIS_URI is required")

        _redis_client = redis.from_url(
            settings.redis_uri,
            decode_responses=True,
        )

        _redis_client.ping()
        print("CONNECT:", id(_redis_client), _redis_client)

    return _redis_client


def get_client() -> Redis:
    if _redis_client is None:
        raise RuntimeError("Redis is not connected")
    return _redis_client
