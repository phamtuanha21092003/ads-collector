from motor.motor_asyncio import AsyncIOMotorCollection

from app.model.system_config_model import SystemConfigModel


class SystemConfigRepository:
    def __init__(self, collection: AsyncIOMotorCollection) -> None:
        self._collection = collection

    async def get_by_key(self, key: str) -> SystemConfigModel | None:
        doc = await self._collection.find_one({"key": key})
        return SystemConfigModel.from_mongo(doc)
