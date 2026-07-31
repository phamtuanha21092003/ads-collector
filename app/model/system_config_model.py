from app.base.model_base import MongoBaseModel

# Keep in sync with tool-dropship-api SystemConfigModel.
PROXIES_ROTATION_META_KEY = "PROXIES_ROTATION_META"


class SystemConfigModel(MongoBaseModel):
    key: str
    value: str
