from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection

from app.base.model_base import utc_now
from app.model.job_model import JobStatus


class ShopRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def upsert_for_domain(self, shop: str, job_id: ObjectId) -> None:
        now = utc_now()
        await self._collection.update_one(
            {"shop": shop},
            {
                "$addToSet": {"jobIds": job_id},
                "$set": {"updatedAt": now, "jobId": job_id, "lastAdsUpdatedAt": now, "status": JobStatus.RUNNING.value},
                "$setOnInsert": {
                    "shop": shop,
                    "createdAt": now,
                    "metadata": {},
                    "qualifiedAdsCount": 0
                },
            },
            upsert=True,
        )

    async def update_qualified_ads_count(
        self,
        shop: str,
        count: int,
        job_id: ObjectId | None,
    ) -> None:
        now = utc_now()

        update = {
            "$set": {
                "qualifiedAdsCount": count,
                "updatedAt": now,
                "lastAdsUpdatedAt": now,
                "status": JobStatus.DONE.value,
            }
        }

        if job_id is not None:
            update["$addToSet"] = {
                "jobIds": job_id,
            }
            update["$set"]["jobId"] = job_id

        await self._collection.update_one(
            {"shop": shop},
            update,
        )
