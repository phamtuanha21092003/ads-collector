from typing import Any

from bson import ObjectId
from pydantic import Field

from app.base.model_base import MongoBaseModel


class ShopModel(MongoBaseModel):
    shop: str
    job_ids: list[ObjectId] = Field(
        default_factory=list,
        serialization_alias="jobIds",
        validation_alias="jobIds",
    )
    metadata: dict[str, Any] | None = None
