from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument

from .config import (
    BOT_COLLECTION,
    BOT_TEMPLATE_COLLECTION,
    BOT_VERSION_COLLECTION,
    CAMPAIGN_COLLECTION,
    DEFAULT_USER,
    LEGACY_TRANSCRIPT_COLLECTION,
    LEGACY_TRANSCRIPT_DB,
    MONGO_DB,
    TRANSCRIPT_COLLECTION,
)
from .mongo import get_client, get_db
from .serialization import serialize_doc


VOICE_OPTIONS = [
    {"id": "Aoede", "label": "Aoede", "provider": "google", "gender": "female"},
    {"id": "Charon", "label": "Charon", "provider": "google", "gender": "male"},
    {"id": "Fenrir", "label": "Fenrir", "provider": "google", "gender": "male"},
    {"id": "Kore", "label": "Kore", "provider": "google", "gender": "female"},
    {"id": "Puck", "label": "Puck", "provider": "google", "gender": "male"},
]

LANGUAGE_OPTIONS = [
    {"id": "hindi", "label": "Hindi", "livekit_code": "hi-IN", "sarvam_code": "hi-IN"},
    {"id": "english", "label": "English", "livekit_code": "en-IN", "sarvam_code": "en-IN"},
    {"id": "tamil", "label": "Tamil", "livekit_code": "ta-IN", "sarvam_code": "ta-IN"},
    {"id": "malayalam", "label": "Malayalam", "livekit_code": "ml-IN", "sarvam_code": "ml-IN"},
    {"id": "kannada", "label": "Kannada", "livekit_code": "kn-IN", "sarvam_code": "kn-IN"},
]


def _now() -> datetime:
    return datetime.utcnow()


def _clean_config(config: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(config)
    result.setdefault("model", "gemini-3.1-flash-live-preview")
    result.setdefault("voice", "Aoede")
    result.setdefault("language", "hindi")
    result.setdefault("livekit_language", "hi-IN")
    result.setdefault("temperature", 0.7)
    result.setdefault("max_call_duration", 300)
    return result


def build_runtime_snapshot(bot: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    config = _clean_config(version.get("config") or {})
    return {
        **config,
        "bot_id": str(bot["_id"]),
        "bot_name": bot.get("name", ""),
        "assistant_id": bot.get("assistant_id", ""),
        "bot_version_id": str(version["_id"]),
        "bot_version": version.get("version"),
        "published_at": version.get("published_at"),
    }


def fetch_active_bot_config(assistant_id: str) -> dict[str, Any] | None:
    if not assistant_id:
        return None
    db = get_db()
    bot = db[BOT_COLLECTION].find_one({"assistant_id": assistant_id, "status": {"$ne": "archived"}})
    if not bot or not bot.get("active_version_id"):
        return None
    version = db[BOT_VERSION_COLLECTION].find_one({"_id": bot["active_version_id"], "state": "published"})
    if not version:
        return None
    return serialize_doc(build_runtime_snapshot(bot, version))


def seed_default_bot(default_config: dict[str, Any], user: str = DEFAULT_USER) -> dict[str, Any]:
    db = get_db()
    config = _clean_config(default_config)
    assistant_id = config.get("assistant_id") or f"assistant-{uuid4()}"
    now = _now()
    bot = db[BOT_COLLECTION].find_one_and_update(
        {"assistant_id": assistant_id},
        {
            "$setOnInsert": {
                "assistant_id": assistant_id,
                "name": "JustDial Lead Qualification",
                "description": "Default outbound lead qualification bot seeded from the current runtime.",
                "owner": user,
                "status": "active",
                "orchestration": {
                    "mode": "prompt_settings",
                    "flow_provider": None,
                    "flow_id": None,
                },
                "created_at": now,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    existing = db[BOT_VERSION_COLLECTION].find_one({"bot_id": bot["_id"], "version": 1})
    if existing:
        version = existing
    else:
        version_id = ObjectId()
        version = {
            "_id": version_id,
            "bot_id": bot["_id"],
            "version": 1,
            "state": "published",
            "config": config,
            "created_by": user,
            "created_at": now,
            "published_by": user,
            "published_at": now,
            "notes": "Initial seed from hardcoded bot config.",
        }
        db[BOT_VERSION_COLLECTION].insert_one(version)
    db[BOT_COLLECTION].update_one(
        {"_id": bot["_id"]},
        {"$set": {"active_version_id": version["_id"], "updated_at": now}},
    )
    db[BOT_TEMPLATE_COLLECTION].update_one(
        {"template_key": "justdial_lead_qualification"},
        {
            "$set": {
                "template_key": "justdial_lead_qualification",
                "name": "JustDial Lead Qualification",
                "description": "Outbound product qualification prompt and tool configuration.",
                "config": config,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return serialize_doc({"bot": db[BOT_COLLECTION].find_one({"_id": bot["_id"]}), "version": version})


def list_bots() -> list[dict[str, Any]]:
    docs = list(get_db()[BOT_COLLECTION].find().sort("updated_at", -1))
    return serialize_doc(docs)


def create_bot(payload: dict[str, Any], user: str) -> dict[str, Any]:
    db = get_db()
    now = _now()
    config = _clean_config(payload.get("config") or {})
    assistant_id = payload.get("assistant_id") or f"assistant-{uuid4()}"
    bot = {
        "assistant_id": assistant_id,
        "name": payload.get("name") or "Untitled Bot",
        "description": payload.get("description", ""),
        "owner": payload.get("owner") or user,
        "status": "draft",
        "orchestration": payload.get("orchestration")
        or {"mode": "prompt_settings", "flow_provider": None, "flow_id": None},
        "created_at": now,
        "updated_at": now,
    }
    bot_id = db[BOT_COLLECTION].insert_one(bot).inserted_id
    version = {
        "bot_id": bot_id,
        "version": 1,
        "state": "draft",
        "config": {**config, "assistant_id": assistant_id},
        "created_by": user,
        "created_at": now,
        "notes": payload.get("notes", ""),
    }
    version_id = db[BOT_VERSION_COLLECTION].insert_one(version).inserted_id
    db[BOT_COLLECTION].update_one({"_id": bot_id}, {"$set": {"draft_version_id": version_id}})
    return serialize_doc(db[BOT_COLLECTION].find_one({"_id": bot_id}))


def get_bot(bot_id: str) -> dict[str, Any] | None:
    db = get_db()
    bot = db[BOT_COLLECTION].find_one({"_id": ObjectId(bot_id)})
    if not bot:
        return None
    versions = list(db[BOT_VERSION_COLLECTION].find({"bot_id": bot["_id"]}).sort("version", -1))
    return serialize_doc({"bot": bot, "versions": versions})


def save_draft(bot_id: str, payload: dict[str, Any], user: str) -> dict[str, Any]:
    db = get_db()
    bot_obj_id = ObjectId(bot_id)
    bot = db[BOT_COLLECTION].find_one({"_id": bot_obj_id})
    if not bot:
        raise KeyError("bot_not_found")
    latest = db[BOT_VERSION_COLLECTION].find_one({"bot_id": bot_obj_id}, sort=[("version", -1)])
    next_version = int((latest or {}).get("version", 0)) + 1
    config = _clean_config(payload.get("config") or {})
    config["assistant_id"] = bot["assistant_id"]
    now = _now()
    draft = {
        "bot_id": bot_obj_id,
        "version": next_version,
        "state": "draft",
        "config": config,
        "created_by": user,
        "created_at": now,
        "notes": payload.get("notes", ""),
    }
    version_id = db[BOT_VERSION_COLLECTION].insert_one(draft).inserted_id
    db[BOT_COLLECTION].update_one(
        {"_id": bot_obj_id},
        {"$set": {"draft_version_id": version_id, "status": "draft", "updated_at": now}},
    )
    return serialize_doc(db[BOT_VERSION_COLLECTION].find_one({"_id": version_id}))


def publish_version(bot_id: str, version_id: str | None, user: str) -> dict[str, Any]:
    db = get_db()
    bot_obj_id = ObjectId(bot_id)
    bot = db[BOT_COLLECTION].find_one({"_id": bot_obj_id})
    if not bot:
        raise KeyError("bot_not_found")
    target_id = ObjectId(version_id) if version_id else bot.get("draft_version_id")
    version = db[BOT_VERSION_COLLECTION].find_one({"_id": target_id, "bot_id": bot_obj_id})
    if not version:
        raise KeyError("version_not_found")
    now = _now()
    db[BOT_VERSION_COLLECTION].update_one(
        {"_id": version["_id"]},
        {"$set": {"state": "published", "published_by": user, "published_at": now}},
    )
    db[BOT_COLLECTION].update_one(
        {"_id": bot_obj_id},
        {
            "$set": {
                "active_version_id": version["_id"],
                "status": "active",
                "updated_at": now,
            },
            "$unset": {"draft_version_id": ""},
        },
    )
    return serialize_doc(db[BOT_VERSION_COLLECTION].find_one({"_id": version["_id"]}))


def rollback_bot(bot_id: str, version_id: str, user: str) -> dict[str, Any]:
    db = get_db()
    version = db[BOT_VERSION_COLLECTION].find_one(
        {"_id": ObjectId(version_id), "bot_id": ObjectId(bot_id), "state": "published"}
    )
    if not version:
        raise KeyError("published_version_not_found")
    now = _now()
    db[BOT_COLLECTION].update_one(
        {"_id": ObjectId(bot_id)},
        {
            "$set": {
                "active_version_id": version["_id"],
                "status": "active",
                "updated_at": now,
                "rolled_back_by": user,
                "rolled_back_at": now,
            }
        },
    )
    return serialize_doc(version)


def duplicate_bot(bot_id: str, user: str) -> dict[str, Any]:
    current = get_bot(bot_id)
    if not current:
        raise KeyError("bot_not_found")
    bot = current["bot"]
    versions = current["versions"]
    source = next((v for v in versions if v.get("state") == "published"), versions[0])
    return create_bot(
        {
            "name": f"{bot.get('name', 'Bot')} Copy",
            "description": bot.get("description", ""),
            "config": source.get("config") or {},
        },
        user,
    )


def list_templates() -> list[dict[str, Any]]:
    return serialize_doc(list(get_db()[BOT_TEMPLATE_COLLECTION].find().sort("name", 1)))


def list_campaigns() -> list[dict[str, Any]]:
    return serialize_doc(list(get_db()[CAMPAIGN_COLLECTION].find().sort("updated_at", -1)))


def upsert_campaign(payload: dict[str, Any], user: str) -> dict[str, Any]:
    db = get_db()
    now = _now()
    campaign_key = payload.get("campaign_key") or f"campaign-{uuid4()}"
    update = {
        "campaign_key": campaign_key,
        "name": payload.get("name") or campaign_key,
        "bot_id": payload.get("bot_id", ""),
        "lead_api": payload.get("lead_api") or {},
        "status": payload.get("status", "draft"),
        "updated_by": user,
        "updated_at": now,
    }
    doc = db[CAMPAIGN_COLLECTION].find_one_and_update(
        {"campaign_key": campaign_key},
        {"$set": update, "$setOnInsert": {"created_at": now, "created_by": user}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return serialize_doc(doc)


def create_test_session(bot_id: str, payload: dict[str, Any], user: str) -> dict[str, Any]:
    bot_bundle = get_bot(bot_id)
    if not bot_bundle:
        raise KeyError("bot_not_found")
    bot = bot_bundle["bot"]
    room_metadata = {
        "assistant_id": bot["assistant_id"],
        "campaign_id": payload.get("campaign_id", "test"),
        "lead_id": payload.get("lead_id", ""),
        "call_id": payload.get("call_id", ""),
        "mobile": payload.get("mobile", ""),
        "srchterm": payload.get("srchterm", ""),
        "buyer_name": payload.get("buyer_name", "Customer"),
        "city": payload.get("city", ""),
        "test_session": True,
    }
    return {
        "room_metadata": room_metadata,
        "instructions": "Pass this metadata when creating the LiveKit room for the controlled test call.",
        "created_by": user,
        "created_at": _now().isoformat(),
    }


def _transcript_sources():
    yield "platform", get_db()[TRANSCRIPT_COLLECTION]
    if LEGACY_TRANSCRIPT_DB != MONGO_DB or LEGACY_TRANSCRIPT_COLLECTION != TRANSCRIPT_COLLECTION:
        yield "legacy", get_client()[LEGACY_TRANSCRIPT_DB][LEGACY_TRANSCRIPT_COLLECTION]


def _transcript_query(filters: dict[str, Any]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    for key in ("bot_id", "bot_version_id", "campaign_id", "lead_id", "call_id", "assistant_id", "status"):
        if filters.get(key):
            query[key] = filters[key]
    if filters.get("mobile"):
        query["$or"] = [
            {"sip_info.caller_number": filters["mobile"]},
            {"lead_record.buyer_details.buyer_number": filters["mobile"]},
        ]
    if filters.get("text"):
        query["transcript.text"] = {"$regex": filters["text"], "$options": "i"}
    return query


def _normalize_transcript_doc(doc: dict[str, Any], source: str) -> dict[str, Any]:
    doc = deepcopy(doc)
    doc["transcript_source"] = source
    doc.setdefault("campaign_id", "")
    doc.setdefault("bot_id", "")
    doc.setdefault("bot_version_id", "")
    if not doc.get("call_id"):
        doc["call_id"] = doc.get("room_name") or str(doc.get("_id", ""))
    return doc


def search_transcripts(filters: dict[str, Any]) -> list[dict[str, Any]]:
    query = _transcript_query(filters)
    docs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source, collection in _transcript_sources():
        for doc in collection.find(query).sort("created_at", -1).limit(100):
            key = (source, str(doc["_id"]))
            if key in seen:
                continue
            seen.add(key)
            docs.append(_normalize_transcript_doc(doc, source))
    def sort_value(item: dict[str, Any]) -> float:
        created_at = item.get("created_at")
        if isinstance(created_at, datetime):
            return created_at.timestamp()
        return 0.0

    docs.sort(key=sort_value, reverse=True)
    return serialize_doc(docs[:100])


def get_transcript(transcript_id: str) -> dict[str, Any] | None:
    obj_id = ObjectId(transcript_id)
    for source, collection in _transcript_sources():
        doc = collection.find_one({"_id": obj_id})
        if doc:
            return serialize_doc(_normalize_transcript_doc(doc, source))
    return None
