"""Editable phrase library — voicemail / hold-music / DNC signals.

Previously these lived as Python constants inside callback_worker/analysis.py and
bot.py. They are now stored in Mongo so PMs can edit them from the dashboard
without a code deploy. A small in-process TTL cache keeps the per-call reads
cheap, and a hardcoded default list seeds the collection on first use so the
runtime behavior matches day-zero.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from bson import ObjectId

from .config import DEFAULT_USER
from .mongo import get_db
from .serialization import serialize_doc

PHRASE_COLLECTION = "tbl_ai_vb_phrase_library"

VALID_CATEGORIES = {"voicemail", "hold_music", "dnc_trigger"}

# Seeded defaults — kept identical to the historical hardcoded lists so first
# boot behavior matches the previous release exactly.
DEFAULT_PHRASES: dict[str, list[dict[str, str]]] = {
    "voicemail": [
        {"text": "leave a message", "language": "en"},
        {"text": "leave your message", "language": "en"},
        {"text": "please leave a message", "language": "en"},
        {"text": "after the beep", "language": "en"},
        {"text": "after the tone", "language": "en"},
        {"text": "at the beep", "language": "en"},
        {"text": "you have reached", "language": "en"},
        {"text": "you've reached", "language": "en"},
        {"text": "unable to take your call", "language": "en"},
        {"text": "cannot take your call", "language": "en"},
        {"text": "not available to take your call", "language": "en"},
        {"text": "record your message", "language": "en"},
        {"text": "record a message", "language": "en"},
        {"text": "mailbox is full", "language": "en"},
        {"text": "mailbox full", "language": "en"},
        {"text": "voice mail recording", "language": "en"},
        {"text": "voicemail recording", "language": "en"},
        {"text": "you may hang up", "language": "en"},
        {"text": "may hang up now", "language": "en"},
        {"text": "finished recording hang up", "language": "en"},
        {"text": "when you have finished recording", "language": "en"},
    ],
    "hold_music": [
        {"text": "put your call on hold", "language": "en"},
        {"text": "placed your call on hold", "language": "en"},
        {"text": "has put your call on hold", "language": "en"},
        {"text": "पुट योर कॉल ऑन होल्ड", "language": "hi"},
        {"text": "होल्ड पर राख्यो छे", "language": "gu"},
        {"text": "hold par rakho chhe", "language": "gu"},
    ],
    "dnc_trigger": [
        {"text": "dobara mat call karna", "language": "hi"},
        {"text": "remove my number", "language": "en"},
        {"text": "do not call again", "language": "en"},
        {"text": "मुझे call मत करो", "language": "hi"},
        {"text": "number हटा दो", "language": "hi"},
    ],
}


_CACHE_TTL_SEC = 60
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, list[str]]] = {}


def _now() -> datetime:
    return datetime.utcnow()


def _invalidate_cache(category: str | None = None) -> None:
    with _cache_lock:
        if category is None:
            _cache.clear()
        else:
            _cache.pop(category, None)


def seed_default_phrases(user: str = DEFAULT_USER) -> None:
    """Idempotent — only inserts categories that are missing. Safe to call on every startup."""
    db = get_db()
    coll = db[PHRASE_COLLECTION]
    now = _now()
    for category, entries in DEFAULT_PHRASES.items():
        if coll.count_documents({"category": category}, limit=1):
            continue
        rows = [
            {
                "category": category,
                "text": entry["text"],
                "language": entry.get("language", ""),
                "notes": "",
                "created_at": now,
                "created_by": user,
                "updated_at": now,
                "updated_by": user,
            }
            for entry in entries
        ]
        if rows:
            coll.insert_many(rows)
    _invalidate_cache()


def list_phrases(category: str | None = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if category:
        if category not in VALID_CATEGORIES:
            raise ValueError(f"unknown category: {category!r}")
        query["category"] = category
    docs = list(get_db()[PHRASE_COLLECTION].find(query).sort([("category", 1), ("language", 1), ("text", 1)]))
    return serialize_doc(docs)


def create_phrase(payload: dict[str, Any], user: str) -> dict[str, Any]:
    category = payload.get("category")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")
    text = (payload.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > 500:
        raise ValueError("text must be 500 characters or fewer")
    now = _now()
    doc = {
        "category": category,
        "text": text,
        "language": (payload.get("language") or "").strip(),
        "notes": (payload.get("notes") or "").strip(),
        "created_at": now,
        "created_by": user,
        "updated_at": now,
        "updated_by": user,
    }
    inserted_id = get_db()[PHRASE_COLLECTION].insert_one(doc).inserted_id
    _invalidate_cache(category)
    return serialize_doc(get_db()[PHRASE_COLLECTION].find_one({"_id": inserted_id}))


def update_phrase(phrase_id: str, payload: dict[str, Any], user: str) -> dict[str, Any]:
    try:
        obj_id = ObjectId(phrase_id)
    except Exception as exc:
        raise ValueError("invalid phrase id") from exc
    update: dict[str, Any] = {"updated_by": user, "updated_at": _now()}
    if "text" in payload:
        text = (payload.get("text") or "").strip()
        if not text:
            raise ValueError("text cannot be empty")
        if len(text) > 500:
            raise ValueError("text must be 500 characters or fewer")
        update["text"] = text
    if "language" in payload:
        update["language"] = (payload.get("language") or "").strip()
    if "notes" in payload:
        update["notes"] = (payload.get("notes") or "").strip()
    existing = get_db()[PHRASE_COLLECTION].find_one({"_id": obj_id})
    if not existing:
        raise KeyError("phrase_not_found")
    get_db()[PHRASE_COLLECTION].update_one({"_id": obj_id}, {"$set": update})
    _invalidate_cache(existing.get("category"))
    return serialize_doc(get_db()[PHRASE_COLLECTION].find_one({"_id": obj_id}))


def delete_phrase(phrase_id: str) -> None:
    try:
        obj_id = ObjectId(phrase_id)
    except Exception as exc:
        raise ValueError("invalid phrase id") from exc
    existing = get_db()[PHRASE_COLLECTION].find_one({"_id": obj_id})
    if not existing:
        raise KeyError("phrase_not_found")
    get_db()[PHRASE_COLLECTION].delete_one({"_id": obj_id})
    _invalidate_cache(existing.get("category"))


def get_phrase_texts(category: str) -> list[str]:
    """Cached read for runtime/analysis code. Always returns a list of lowercased substrings.

    Falls back to hardcoded defaults if Mongo is unreachable or empty, so the
    voicemail / hold-music detection path never goes silent due to an
    infrastructure hiccup.
    """
    if category not in VALID_CATEGORIES:
        return []
    with _cache_lock:
        entry = _cache.get(category)
        if entry and (time.monotonic() - entry[0]) < _CACHE_TTL_SEC:
            return entry[1]
    try:
        docs = list(get_db()[PHRASE_COLLECTION].find({"category": category}, {"text": 1}))
        texts = [str(d.get("text") or "").lower() for d in docs if d.get("text")]
        if not texts:
            texts = [p["text"].lower() for p in DEFAULT_PHRASES.get(category, [])]
    except Exception:
        texts = [p["text"].lower() for p in DEFAULT_PHRASES.get(category, [])]
    with _cache_lock:
        _cache[category] = (time.monotonic(), texts)
    return texts
