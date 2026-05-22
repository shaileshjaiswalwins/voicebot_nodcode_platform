from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient

from .config import (
    BOT_COLLECTION,
    BOT_TEMPLATE_COLLECTION,
    BOT_VERSION_COLLECTION,
    CAMPAIGN_COLLECTION,
    MONGO_DB,
    MONGO_URI,
    TRANSCRIPT_COLLECTION,
)


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    return MongoClient(MONGO_URI)


def get_db():
    return get_client()[MONGO_DB]


def ensure_indexes() -> None:
    db = get_db()
    db[BOT_COLLECTION].create_index([("assistant_id", ASCENDING)], unique=True)
    db[BOT_COLLECTION].create_index([("status", ASCENDING), ("updated_at", DESCENDING)])
    db[BOT_VERSION_COLLECTION].create_index(
        [("bot_id", ASCENDING), ("version", DESCENDING)], unique=True
    )
    db[BOT_VERSION_COLLECTION].create_index([("bot_id", ASCENDING), ("state", ASCENDING)])
    db[BOT_TEMPLATE_COLLECTION].create_index([("template_key", ASCENDING)], unique=True)
    db[CAMPAIGN_COLLECTION].create_index([("campaign_key", ASCENDING)], unique=True)
    db[CAMPAIGN_COLLECTION].create_index([("bot_id", ASCENDING), ("status", ASCENDING)])
    db[TRANSCRIPT_COLLECTION].create_index([("bot_id", ASCENDING), ("created_at", DESCENDING)])
    db[TRANSCRIPT_COLLECTION].create_index([("campaign_id", ASCENDING), ("created_at", DESCENDING)])
    db[TRANSCRIPT_COLLECTION].create_index([("lead_id", ASCENDING), ("call_id", ASCENDING)])

