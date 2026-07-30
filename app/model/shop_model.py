from typing import Any
from datetime import date, datetime, timezone


from bson import ObjectId
from pydantic import Field

from app.base.model_base import MongoBaseModel, utc_now
from app.model.job_model import JobStatus


class ShopModel(MongoBaseModel):
    shop: str
    job_ids: list[ObjectId] = Field(
        default_factory=list,
        serialization_alias="jobIds",
        validation_alias="jobIds",
    )
    job_id: ObjectId = Field(
        default_factory=list,
        serialization_alias="jobId",
        validation_alias="jobId",
    )
    metadata: dict[str, Any] | None = None
    lastAdsUpdatedAt: datetime = Field(
        default_factory=utc_now,
        serialization_alias="lastAdsUpdatedAt",
        validation_alias="lastAdsUpdatedAt",
    )
    qualified_ads_count: int | None = Field(
        default=None,
        serialization_alias="qualifiedAdsCount",
        validation_alias="qualifiedAdsCount",
    )
    status: JobStatus = JobStatus.RUNNING

