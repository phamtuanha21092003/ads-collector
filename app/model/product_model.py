from bson import ObjectId
from pydantic import Field
from app.base.model_base import MongoBaseModel


class ProductModel(MongoBaseModel):
    shop: str
    job_ids: list[ObjectId] = Field(
        default_factory=list,
        serialization_alias="jobIds",
        validation_alias="jobIds",
    )
    productAdsCount: int | None = None
    storeAdsCount: int | None = None
    adsCreatedLast3Weeks: int | None = None
    highestImpressionRankLast3Weeks: int | None = None
    keywords: list[str] = Field(default_factory=list)
    linkUrl: str | None = None
