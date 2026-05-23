from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ReturnDocument

from .config import (
    LANGFUSE_BASE_URL,
    LANGFUSE_ENABLED,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    PLATFORM_SETTINGS_COLLECTION,
    VOICEBOT_ENV,
)
from .mongo import get_db
from .serialization import serialize_doc

VALID_ENVIRONMENTS = {"local", "staging", "prod"}


def _now() -> datetime:
    return datetime.utcnow()


def _credentials_present() -> bool:
    return bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL)


def _default_langfuse_settings() -> dict[str, Any]:
    environment = VOICEBOT_ENV if VOICEBOT_ENV in VALID_ENVIRONMENTS else "local"
    return {
        "key": "langfuse",
        "enabled": bool(LANGFUSE_ENABLED),
        "environment": environment,
        "base_url": LANGFUSE_BASE_URL,
        "credentials_configured": _credentials_present(),
        "send_transcripts": True,
        "send_prompts": True,
        "updated_by": "system",
        "updated_at": _now(),
    }


def _insert_defaults() -> dict[str, Any]:
    defaults = _default_langfuse_settings()
    defaults.pop("updated_by", None)
    defaults.pop("updated_at", None)
    return {**defaults, "created_at": _now()}


def get_langfuse_settings() -> dict[str, Any]:
    db = get_db()
    try:
        settings = db[PLATFORM_SETTINGS_COLLECTION].find_one({"key": "langfuse"})
        if not settings:
            settings = db[PLATFORM_SETTINGS_COLLECTION].find_one_and_update(
                {"key": "langfuse"},
                {"$setOnInsert": _insert_defaults()},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
    except Exception as exc:
        settings = {
            **_default_langfuse_settings(),
            "storage_status": "mongo_unavailable",
            "storage_error": str(exc),
        }
    settings["credentials_configured"] = _credentials_present()
    settings["base_url"] = LANGFUSE_BASE_URL
    return serialize_doc(settings)


def update_langfuse_settings(payload: dict[str, Any], user: str) -> dict[str, Any]:
    update: dict[str, Any] = {"updated_by": user, "updated_at": _now()}
    if "enabled" in payload:
        update["enabled"] = bool(payload["enabled"])
    if "environment" in payload:
        environment = str(payload["environment"])
        if environment not in VALID_ENVIRONMENTS:
            raise ValueError("environment must be one of local, staging or prod")
        update["environment"] = environment
    if "send_transcripts" in payload:
        update["send_transcripts"] = bool(payload["send_transcripts"])
    if "send_prompts" in payload:
        update["send_prompts"] = bool(payload["send_prompts"])

    db = get_db()
    settings = db[PLATFORM_SETTINGS_COLLECTION].find_one_and_update(
        {"key": "langfuse"},
        {"$set": update, "$setOnInsert": _insert_defaults()},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    settings["credentials_configured"] = _credentials_present()
    settings["base_url"] = LANGFUSE_BASE_URL
    return serialize_doc(settings)
