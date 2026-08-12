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


async def ensure_indexes(settings: Settings) -> None:
    jobs = jobs_collection(settings)
    ads = ads_collection(settings)
    shops = shops_collection(settings)
    configs = system_configs_collection(settings)

    await jobs.create_index([("status", 1), ("createdAt", 1)])
    await ads.create_index([("adId", 1)], unique=True)
    # await ads.create_index(
    #     [("raw.link_url", 1)],
    #     unique=True,
    #     partialFilterExpression={"raw.link_url": {"$type": "string"}},
    #     name="idx_ads_raw_link_url_unique",
    # )
    await ads.create_index([("keywords", 1)])
    await ads.create_index([("jobIds", 1)])
    await shops.create_index([("shop", 1)], unique=True, name="idx_shops_shop_unique")
    await shops.create_index([("jobIds", 1)], name="idx_shop_job_ids")
    await configs.create_index(
        [("key", 1)], unique=True, name="idx_system_configs_key_unique"
    )


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
