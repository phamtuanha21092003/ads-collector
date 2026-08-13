from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

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


def shops_collection(settings: Settings) -> AsyncIOMotorCollection:
    return get_db(settings)["shops"]


def system_configs_collection(settings: Settings) -> AsyncIOMotorCollection:
    return get_db(settings)["system_configs"]


def products_collection(settings: Settings) -> AsyncIOMotorCollection:
    return get_db(settings)["products"]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
