from datetime import date, datetime
from enum import StrEnum
from typing import Any

from bson import ObjectId
from pydantic import Field

from app.base.model_base import MongoBaseModel


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobType(StrEnum):
    KEYWORD = "keyword"
    DOMAIN = "domain"


class JobModel(MongoBaseModel):
    keyword: str
    type: JobType = JobType.KEYWORD
    start_date: date = Field(
        serialization_alias="startDate",
        validation_alias="startDate",
    )
    end_date: date = Field(
        serialization_alias="endDate",
        validation_alias="endDate",
    )
    country: str = "US"
    status: JobStatus = JobStatus.PENDING
    max_results: int | None = Field(
        default=None,
        serialization_alias="maxResults",
        validation_alias="maxResults",
    )
    ads_count: int | None = Field(
        default=None,
        serialization_alias="adsCount",
        validation_alias="adsCount",
    )
    error: str | None = None
    started_at: datetime | None = Field(
        default=None,
        serialization_alias="startedAt",
        validation_alias="startedAt",
    )
    finished_at: datetime | None = Field(
        default=None,
        serialization_alias="finishedAt",
        validation_alias="finishedAt",
    )
    worker_id: str | None = Field(
        default=None,
        serialization_alias="workerId",
        validation_alias="workerId",
    )
    user_id: ObjectId | None = Field(
        default=None,
        serialization_alias="userId",
        validation_alias="userId",
    )
    status_filter: str | None = Field(
        default=None,
        serialization_alias="statusFilter",
        validation_alias="statusFilter",
    )
    must_reach: int = Field(
        default=0,
        serialization_alias="mustReach",
        validation_alias="mustReach",
    )
    store_must_reach: int = Field(
        default=0,
        serialization_alias="storeMustReach",
        validation_alias="storeMustReach",
    )
    qualified_ads_count: int | None = Field(
        default=None,
        serialization_alias="qualifiedAdsCount",
        validation_alias="qualifiedAdsCount",
    )
    store_count: int | None = Field(
        default=None,
        serialization_alias="storeCount",
        validation_alias="storeCount",
    )
    metadata: dict[str, Any] | None = None
