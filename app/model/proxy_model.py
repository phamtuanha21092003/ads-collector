from app.base.model_base import MongoBaseModel


class ProxyModel(MongoBaseModel):
    domain: str
