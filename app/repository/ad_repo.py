import re

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import UpdateOne

from app.base.model_base import utc_now
from app.model.ad_model import AdModel

# THIS FIELD IS BREAKING CHANGE OF RAW DATA
ADS_VERSION = 2


class AdRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def upsert_one(self, ad: AdModel) -> None:
        now = utc_now()
        keywords = [keyword for keyword in ad.keywords if keyword]
        job_ids = [job_id for job_id in ad.job_ids if job_id is not None]
        await self._collection.update_one(
            {"adId": ad.ad_id},
            {
                "$addToSet": {
                    "keywords": {"$each": keywords},
                    "jobIds": {"$each": job_ids},
                },
                "$set": {
                    "country": ad.country,
                    "raw": ad.raw,
                    "shop": ad.shop,
                    "updatedAt": now,
                    "version": ADS_VERSION,
                },
                "$setOnInsert": {
                    "adId": ad.ad_id,
                    "createdAt": now,
                    "qualifiedAdsCount": 0,
                },
            },
            upsert=True,
        )

    async def upsert_many(self, ads: list[AdModel]) -> int:
        if not ads:
            return 0

        now = utc_now()
        operations: list[UpdateOne] = []
        for ad in ads:
            keywords = [keyword for keyword in ad.keywords if keyword]
            job_ids = [job_id for job_id in ad.job_ids if job_id is not None]
            operations.append(
                UpdateOne(
                    {
                        "$or": [
                            {"raw.link_url": ad.raw["link_url"]},
                            {"adId": ad.ad_id},
                        ]
                    },
                    {
                        "$addToSet": {
                            "keywords": {"$each": keywords},
                            "jobIds": {"$each": job_ids},
                        },
                        "$set": {
                            "updatedAt": now,
                            "version": ADS_VERSION,
                        },
                        "$setOnInsert": {
                            "adId": ad.ad_id,
                            "country": ad.country,
                            "raw": ad.raw,
                            "shop": ad.shop,
                            "createdAt": now,
                            "qualifiedAdsCount": 0,
                        },
                    },
                    upsert=True,
                )
            )

        written = 0
        chunk_size = 100
        for index in range(0, len(operations), chunk_size):
            chunk = operations[index : index + chunk_size]
            result = await self._collection.bulk_write(chunk, ordered=False)
            written += (
                result.upserted_count + result.modified_count + result.matched_count
            )
        return written

    async def count_by_job(self, job_id: ObjectId) -> int:
        return await self._collection.count_documents({"jobIds": job_id})

    async def count_by_shop(self, shop: str) -> int:
        return await self._collection.count_documents({"shop": shop})

    async def count_active_by_product_url(self, product_url: str) -> int:
        # Raw URLs may contain tracking parameters, but must target this handle.
        product_page_url = product_url.removesuffix(".json")
        link_pattern = rf"^{re.escape(product_page_url)}(?:[/?#]|$)"
        return await self._collection.count_documents(
            {"raw.link_url": {"$regex": link_pattern}, "raw.is_active": True}
        )

    async def update_ads_qualified(
        self,
        parent_id: ObjectId,
        count: int,
        shop: str,
    ) -> None:
        if count <= 0:
            return
        now = utc_now()
        await self._collection.update_one(
            {"jobIds": parent_id, "shop": shop},
            {
                "$set": {"updatedAt": now, "qualifiedAdsCount": count},
            },
        )
