from bson import ObjectId
from pydantic import Field

from app.base.model_base import MongoBaseModel


class AdModel(MongoBaseModel):
    # job_id: ObjectId = Field(
    #     serialization_alias="jobId",
    #     validation_alias="jobId",
    # )
    job_ids: list[ObjectId] = Field(
        serialization_alias="jobIds",
        validation_alias="jobIds",
    )

    ad_id: str = Field(
        serialization_alias="adId",
        validation_alias="adId",
    )
    # keyword: str
    keywords: list[str]
    country: str
    raw: dict
