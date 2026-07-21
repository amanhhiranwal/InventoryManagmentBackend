from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from app.core.config import settings

# Async MongoDB Connection (Motor)
client = AsyncIOMotorClient(settings.MONGO_URI)
mongo_db = client[settings.MONGO_DB]

# Sync MongoDB Connection (PyMongo)
sync_client = MongoClient(settings.MONGO_URI)
sync_mongo_db = sync_client[settings.MONGO_DB]