from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from app.base.model_base import utc_now


class ShopRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def upsert_for_domain(self, shop: str, job_id: ObjectId) -> None:
        now = utc_now()
        await self._collection.update_one(
            {"shop": shop},
            {
                "$addToSet": {"jobIds": job_id},
                "$set": {"updatedAt": now},
                "$setOnInsert": {
                    "shop": shop,
                    "createdAt": now,
                    "metadata": {"qualifiedAdsCount": 0},
                },
            },
            upsert=True,
        )

    async def update_qualified_ads_count(self, shop: str, count: int) -> None:
        if count <= 0:
            return
        now = utc_now()
        await self._collection.update_one(
            {"shop": shop},
            {
                "$set": {
                    "metadata.qualifiedAdsCount": count,
                    "updatedAt": now,
                },
            },
        )
