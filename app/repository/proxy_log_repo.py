from pymongo.collection import Collection
from loguru import logger

from app.model.proxy_log_model import ProxyLogModel


class ProxyLogRepository:
    def __init__(self, collection: Collection) -> None:
        self._collection = collection

    def insert_one(self, log: ProxyLogModel) -> None:
        try:
            doc = log.to_mongo()
            doc.pop("_id", None)
            self._collection.insert_one(doc)
        except Exception:
            logger.exception("Failed to insert proxy log")
