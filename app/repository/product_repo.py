from urllib.parse import urlsplit, urlunsplit

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
from app.model.product_model import ProductModel


class ProductRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def upsert_one(
        self,
        *,
        product: dict,
        job_ids: list[ObjectId],
        link_url: str,
        shop: str,
        link_ads_url: str,
        keywords: list[str],
    ) -> ProductModel:
        """Keep one product per canonical product URL while retaining all source jobs."""
        link_url = self._clean_product_url(link_url)
        return self._collection.update_one(
            {"linkUrl": link_url},
            {
                "$set": {
                    "product": product,
                    "linkUrl": link_url,
                    "shop": shop,
                    "linkAdsUrl": link_ads_url,
                },
                "$addToSet": {
                    "jobIds": {"$each": job_ids},
                    "keywords": {
                        "$each": [
                            keyword
                            for keyword in keywords
                            if keyword and keyword.strip().lower() != shop.lower()
                        ]
                    },
                },
            },
            upsert=True,
        )

    @staticmethod
    def _clean_product_url(link_url: str) -> str:
        """Remove tracking query parameters from the stored product URL."""
        parsed = urlsplit(link_url)
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path.removesuffix(".json"),
                "",
                "",
            )
        )

    async def update_ad_counts(
        self,
        *,
        link_url: str,
        store_ads_count: int,
        active_ads_count: int,
        highest_impression_rank: int | None,
    ) -> None:
        link_url = self._clean_product_url(link_url)
        await self._collection.update_one(
            {"linkUrl": link_url},
            {
                "$set": {
                    "storeAdsCount": store_ads_count,
                    "productAdsCount": active_ads_count,
                    "adsCreatedLast3Weeks": store_ads_count,
                    "highestImpressionRankLast3Weeks": highest_impression_rank,
                }
            },
        )
