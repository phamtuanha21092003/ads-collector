from bson import ObjectId
from pydantic import Field
from typing import Optional

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
    shop: Optional[str] = Field(
        default=None,
        serialization_alias="shop",
        validation_alias="shop",
    )
    qualified_ads_count: Optional[int] = Field(
        default=0,
        serialization_alias="qualifiedAdsCount",
        validation_alias="qualifiedAdsCount",
    )
