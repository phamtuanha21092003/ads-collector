from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bson_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bson_safe(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value.isoformat()
    return value


class MongoBaseModel(BaseModel):
    id: ObjectId | None = Field(default=None, alias="_id")

    created_at: datetime = Field(
        default_factory=utc_now,
        serialization_alias="createdAt",
        validation_alias="createdAt",
    )

    updated_at: datetime = Field(
        default_factory=utc_now,
        serialization_alias="updatedAt",
        validation_alias="updatedAt",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        use_enum_values=True,
    )

    def to_mongo(self) -> dict:
        return _bson_safe(
            self.model_dump(
                by_alias=True,
                exclude_none=True,
            )
        )

    @classmethod
    def from_mongo(cls, data: dict | None):
        if data is None:
            return None

        return cls.model_validate(data)
