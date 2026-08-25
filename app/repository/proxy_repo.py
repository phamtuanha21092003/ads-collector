from pymongo.collection import Collection
from pymongo import ReturnDocument
from app.base.model_base import utc_now


class ProxyRepository:
    def __init__(self, collection: Collection) -> None:
        self._collection = collection


    def find_proxy_by_domain_and_update(self, domain: str):
        now = utc_now()

        return self._collection.find_one_and_update(
               {"domain": domain},
               {
                    "$set": {
                        "lastRatelimitAt": now
                    },
                    "$inc": {
                        "timeRatelimit": 1
                    }
                },
                return_document=ReturnDocument.BEFORE,
            )

