from pymongo.collection import Collection
from loguru import logger
from typing import Any, Optional

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


    def update_one(
        self,
        request_id: str,
        body: Any = None,
        headers: Any = None,
        status_code: Optional[int] = None,
        status: Optional[str] = None,
        err: Any = None,
        request_body: Any = None,
    ) -> Any:
        try:
        
            update_data = {}

            if body is not None:
                update_data["responseBody"] = body

            if headers is not None:
                update_data["requestHeader"] = headers

            if status_code is not None:
                update_data["statusCode"] = status_code

            if status is not None:
                update_data["status"] = status

            if err is not None:
                update_data["err"] = err

            if request_body is not None:
                update_data["requestBody"] = request_body

            if not update_data:
                return

            self._collection.update_one(
                {"requestId": request_id},
                {"$set": update_data},
                upsert=False,
            )
            
            record = self._collection.find_one(
               {"requestId": request_id}
            )
            
            return record
        except Exception:
            logger.exception("Failed to update proxy log")

