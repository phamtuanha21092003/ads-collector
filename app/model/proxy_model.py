from app.base.model_base import MongoBaseModel
from datetime import datetime, timezone
from pydantic import Field


class ProxyModel(MongoBaseModel):
    domain: str
    last_ratelimit_at: datetime = Field(
        default_factory=datetime.now(timezone.utc),
        serialization_alias="lastRatelimitAt",
        validation_alias="lastRatelimitAt",
    )
    time_ratelimit: int = Field(
        default=0,
        serialization_alias="timeRatelimit",
        validation_alias="timeRatelimit",
    )

