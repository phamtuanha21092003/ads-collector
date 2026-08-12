from typing import Any

from bson import ObjectId
import httpx
from motor.motor_asyncio import AsyncIOMotorCollection
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.base.model_base import utc_now
from app.config import Settings
from app.model.job_model import JobStatus


def _is_retryable_trendtrack_error(error: BaseException) -> bool:
    if isinstance(error, httpx.RequestError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


class ShopRepository:
    def __init__(self, collection: AsyncIOMotorCollection, settings: Settings) -> None:
        self._collection = collection
        self._settings = settings

    async def upsert_for_domain(
        self,
        shop: str,
        domain_job_id: ObjectId,
        *,
        parent_job_id: ObjectId | None = None,
        root_job_id: ObjectId | None = None,
    ) -> None:
        existing_shop = await self._collection.find_one({"shop": shop})
        now = utc_now()

        metadata = None
        error = None

        try:
            metadata = await self.get_shop(shop)
        except httpx.HTTPStatusError as e:
            error = {
                "statusCode": e.response.status_code,
                "message": str(e),
                "response": e.response.text,
            }
        except httpx.HTTPError as e:
            error = {
                "message": str(e),
            }

        job_ids = [domain_job_id]
        if parent_job_id is not None:
            job_ids.append(parent_job_id)
        if root_job_id is not None:
            job_ids.append(root_job_id)

        if existing_shop is None:
            await self._collection.insert_one(
                {
                    "shop": shop,
                    "updatedAt": now,
                    "jobId": domain_job_id,
                    "lastAdsUpdatedAt": now,
                    "status": JobStatus.RUNNING.value,
                    "qualifiedAdsCount": 0,
                    "createdAt": now,
                    "metadata": metadata,
                    "error": error,
                    "lastShopUpdatedAt": now,
                    "jobIds": job_ids,
                }
            )
            return

        await self._collection.update_one(
            {"shop": shop},
            {
                "$addToSet": {
                    "jobIds": {"$each": job_ids},
                },
                "$set": {
                    "updatedAt": now,
                    "jobId": domain_job_id,
                    "lastAdsUpdatedAt": now,
                    "status": JobStatus.RUNNING.value,
                },
            },
        )

    async def update_qualified_ads_count(
        self,
        shop: str,
        count: int,
        job_id: ObjectId | None,
        *,
        parent_job_id: ObjectId | None = None,
    ) -> None:
        now = utc_now()

        update: dict[str, Any] = {
            "$set": {
                "qualifiedAdsCount": count,
                "updatedAt": now,
                "lastAdsUpdatedAt": now,
                "status": JobStatus.DONE.value,
            }
        }

        job_ids: list[ObjectId] = []
        if job_id is not None:
            update["$set"]["jobId"] = job_id
            job_ids.append(job_id)
        if parent_job_id is not None:
            job_ids.append(parent_job_id)

        if job_ids:
            update["$addToSet"] = {"jobIds": {"$each": job_ids}}

        await self._collection.update_one({"shop": shop}, update)

    async def get_shop(self, shop: str) -> dict[str, Any] | None:
        return await self._get_shop_with_retry(shop)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception(_is_retryable_trendtrack_error),
    )
    async def _get_shop_with_retry(self, shop: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self._settings.trendtrack_api_url}/shops",
                params={"searchType": "domain", "search": shop.removeprefix("www.")},
                headers={"Authorization": f"Bearer {self._settings.trendtrack_api_key}"},
            )
        response.raise_for_status()

        payload = response.json()
        shops = payload.get("data", []) if isinstance(payload, dict) else []
        return shops[0] if shops else None
