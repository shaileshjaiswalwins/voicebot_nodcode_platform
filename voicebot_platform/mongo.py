from __future__ import annotations

from functools import lru_cache

from loguru import logger
from pymongo import ASCENDING, DESCENDING, MongoClient

from .config import (
    BOT_COLLECTION,
    BOT_TEMPLATE_COLLECTION,
    BOT_VERSION_COLLECTION,
    CAMPAIGN_COLLECTION,
    MONGO_DB,
    MONGO_URI,
    MONGO_SERVER_SELECTION_TIMEOUT_MS,
    PLATFORM_SETTINGS_COLLECTION,
    TRANSCRIPT_COLLECTION,
)


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=MONGO_SERVER_SELECTION_TIMEOUT_MS)


def get_db():
    return get_client()[MONGO_DB]


def ensure_indexes() -> None:
    db = get_db()
    try:
        db[BOT_COLLECTION].create_index([("assistant_id", ASCENDING)], unique=True)
        db[BOT_COLLECTION].create_index([("status", ASCENDING), ("updated_at", DESCENDING)])
        db[BOT_VERSION_COLLECTION].create_index(
            [("bot_id", ASCENDING), ("version", DESCENDING)], unique=True
        )
        db[BOT_VERSION_COLLECTION].create_index([("bot_id", ASCENDING), ("state", ASCENDING)])
        db[BOT_TEMPLATE_COLLECTION].create_index([("template_key", ASCENDING)], unique=True)
        db[CAMPAIGN_COLLECTION].create_index([("campaign_key", ASCENDING)], unique=True)
        db[CAMPAIGN_COLLECTION].create_index([("bot_id", ASCENDING), ("status", ASCENDING)])
        db[PLATFORM_SETTINGS_COLLECTION].create_index([("key", ASCENDING)], unique=True)
        db[TRANSCRIPT_COLLECTION].create_index([("bot_id", ASCENDING), ("created_at", DESCENDING)])
        db[TRANSCRIPT_COLLECTION].create_index(
            [("campaign_id", ASCENDING), ("created_at", DESCENDING)]
        )
        db[TRANSCRIPT_COLLECTION].create_index([("lead_id", ASCENDING), ("call_id", ASCENDING)])
        db["tbl_ai_vb_phrase_library"].create_index(
            [("category", ASCENDING), ("text", ASCENDING)], unique=True
        )
        db["tbl_ai_vb_outcome_catalog"].create_index([("key", ASCENDING)], unique=True)
        db["tbl_ai_vb_voice_catalog"].create_index([("id", ASCENDING)], unique=True)
        db["tbl_ai_vb_language_catalog"].create_index([("id", ASCENDING)], unique=True)
        db["tbl_ai_vb_language_settings"].create_index([("id", ASCENDING)], unique=True)
        db["tbl_ai_vb_audit_log"].create_index([("created_at", DESCENDING)])
        db["tbl_ai_vb_audit_log"].create_index([("actor", ASCENDING), ("created_at", DESCENDING)])
    except Exception as exc:
        logger.warning(f"[MONGO] index setup skipped: {exc}")
