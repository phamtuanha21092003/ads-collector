from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection


class ProductRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def upsert_one(
        self,
        *,
        product: dict,
        job_ids: list[ObjectId],
        link_url: str,
    ) -> None:
        """Keep one product per canonical product URL while retaining all source jobs."""
        await self._collection.update_one(
            {"linkUrl": link_url},
            {
                "$set": {"product": product, "linkUrl": link_url},
                "$addToSet": {"jobIds": {"$each": job_ids}},
            },
            upsert=True,
        )
