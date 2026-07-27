from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

from app.config import Settings

_client: AsyncIOMotorClient | None = None


def get_client(settings: Settings) -> AsyncIOMotorClient:
    global _client
    if _client is None:
        if not settings.db_uri:
            raise ValueError("DB_URI is required")
        _client = AsyncIOMotorClient(settings.db_uri)
    return _client


def get_db(settings: Settings) -> AsyncIOMotorDatabase:
    return get_client(settings)[settings.db_name]


def jobs_collection(settings: Settings) -> AsyncIOMotorCollection:
    return get_db(settings)["ads_jobs"]


def ads_collection(settings: Settings) -> AsyncIOMotorCollection:
    return get_db(settings)["ads"]


async def ensure_indexes(settings: Settings) -> None:
    jobs = jobs_collection(settings)
    ads = ads_collection(settings)

    await jobs.create_index([("status", 1), ("createdAt", 1)])
    await ads.create_index([("adId", 1)], unique=True)
    await ads.create_index([("keywords", 1)])
    await ads.create_index([("jobIds", 1)])


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
