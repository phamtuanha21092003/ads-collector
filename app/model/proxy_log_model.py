from enum import StrEnum
from typing import Optional

from pydantic import Field

from app.base.model_base import MongoBaseModel


class ProxyLogStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class ProxyLogModel(MongoBaseModel):
    proxy: str

    status: ProxyLogStatus

    url: Optional[str] = Field(
        default=None,
        serialization_alias="url",
        validation_alias="url",
    )

    method: Optional[str] = Field(
        default=None,
        serialization_alias="method",
        validation_alias="method",
    )

    status_code: Optional[int] = Field(
        default=None,
        serialization_alias="statusCode",
        validation_alias="statusCode",
    )

    duration_ms: Optional[int] = Field(
        default=None,
        serialization_alias="durationMs",
        validation_alias="durationMs",
    )

    error: Optional[dict] = Field(
        default=None,
        serialization_alias="error",
        validation_alias="error",
    )
