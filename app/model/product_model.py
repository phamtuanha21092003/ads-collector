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
    linkUrl: str | None = None
