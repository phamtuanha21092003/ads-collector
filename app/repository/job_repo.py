from datetime import datetime

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ReturnDocument

from app.base.model_base import utc_now
from app.model.job_model import JobModel, JobStatus


class JobRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def create_running(self, job: JobModel, worker_id: str) -> JobModel:
        now = utc_now()
        job.status = JobStatus.RUNNING
        job.worker_id = worker_id
        job.started_at = now
        job.updated_at = now
        job.error = None

        doc = job.to_mongo()
        doc.pop("_id", None)
        result = await self._collection.insert_one(doc)
        job.id = result.inserted_id
        return job

    async def mark_running(self, job_id: ObjectId, worker_id: str) -> JobModel | None:
        now = utc_now()
        doc = await self._collection.find_one_and_update(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.RUNNING.value,
                    "workerId": worker_id,
                    "startedAt": now,
                    "updatedAt": now,
                    "error": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return JobModel.from_mongo(doc)

    async def mark_done(
        self,
        job_id: ObjectId,
        ads_count: int,
        finished_at: datetime | None = None,
    ) -> None:
        now = finished_at or utc_now()
        await self._collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.DONE.value,
                    "adsCount": ads_count,
                    "finishedAt": now,
                    "updatedAt": now,
                    "error": None,
                }
            },
        )

    async def mark_failed(
        self,
        job_id: ObjectId,
        error: str,
        finished_at: datetime | None = None,
    ) -> None:
        now = finished_at or utc_now()
        await self._collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": JobStatus.FAILED.value,
                    "error": error[:2000],
                    "finishedAt": now,
                    "updatedAt": now,
                }
            },
        )
