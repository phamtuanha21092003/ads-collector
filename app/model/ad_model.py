from bson import ObjectId
from pydantic import Field

from app.base.model_base import MongoBaseModel


class AdModel(MongoBaseModel):
    job_ids: list[ObjectId] = Field(
        serialization_alias="jobIds",
        validation_alias="jobIds",
    )
    ad_id: str = Field(
        serialization_alias="adId",
        validation_alias="adId",
    )
    keywords: list[str]
    country: str
    raw: dict
