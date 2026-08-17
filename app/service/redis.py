from typing import Optional

import redis.asyncio as redis


class RedisClient:
    def __init__(self, url: str):
        self._client = redis.from_url(
            url,
            decode_responses=True,
        )

    async def get(self, key: str) -> Optional[str]:
        return await self._client.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: Optional[int] = None,
    ) -> bool:
        return await self._client.set(
            key,
            value,
            ex=ex,
        )

    async def close(self) -> None:
        await self._client.aclose()
