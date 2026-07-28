from datetime import date, datetime
from enum import StrEnum

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
