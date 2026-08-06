"""Mongo-backed voice and language catalogs.

Replaces the hardcoded VOICE_OPTIONS / LANGUAGE_OPTIONS lists in config_store.py
with editable collections. The /api/options/voices and /api/options/languages
endpoints now read from here so the dashboard can add/disable entries without a
code deploy.

Defaults are seeded on first read if the collection is empty so day-zero
behavior matches the historical hardcoded lists exactly.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from .config import DEFAULT_USER
from .mongo import get_db
from .serialization import serialize_doc

VOICE_COLLECTION = "tbl_ai_vb_voice_catalog"
LANGUAGE_COLLECTION = "tbl_ai_vb_language_catalog"

DEFAULT_VOICES: list[dict[str, str]] = [
    {"id": "Aoede", "label": "Aoede", "provider": "google", "gender": "female"},
    {"id": "Charon", "label": "Charon", "provider": "google", "gender": "male"},
    {"id": "Fenrir", "label": "Fenrir", "provider": "google", "gender": "male"},
    {"id": "Kore", "label": "Kore", "provider": "google", "gender": "female"},
    {"id": "Puck", "label": "Puck", "provider": "google", "gender": "male"},
]

DEFAULT_LANGUAGES: list[dict[str, str]] = [
    {"id": "hindi", "label": "Hindi", "livekit_code": "hi-IN", "sarvam_code": "hi-IN"},
    {"id": "english", "label": "English", "livekit_code": "en-IN", "sarvam_code": "en-IN"},
    {"id": "tamil", "label": "Tamil", "livekit_code": "ta-IN", "sarvam_code": "ta-IN"},
    {"id": "malayalam", "label": "Malayalam", "livekit_code": "ml-IN", "sarvam_code": "ml-IN"},
    {"id": "kannada", "label": "Kannada", "livekit_code": "kn-IN", "sarvam_code": "kn-IN"},
]


_CACHE_TTL_SEC = 60
_cache_lock = threading.Lock()
_voice_cache: tuple[float, list[dict[str, Any]]] | None = None
_lang_cache: tuple[float, list[dict[str, Any]]] | None = None


def _now() -> datetime:
    return datetime.utcnow()


def _invalidate_voices() -> None:
    global _voice_cache
    with _cache_lock:
        _voice_cache = None


def _invalidate_languages() -> None:
    global _lang_cache
    with _cache_lock:
        _lang_cache = None


def seed_default_catalogs(user: str = DEFAULT_USER) -> None:
    db = get_db()
    now = _now()
    voices_coll = db[VOICE_COLLECTION]
    if voices_coll.count_documents({}, limit=1) == 0:
        voices_coll.insert_many([
            {**entry, "enabled": True, "created_at": now, "updated_at": now, "updated_by": user}
            for entry in DEFAULT_VOICES
        ])
        _invalidate_voices()
    langs_coll = db[LANGUAGE_COLLECTION]
    if langs_coll.count_documents({}, limit=1) == 0:
        langs_coll.insert_many([
            {**entry, "enabled": True, "created_at": now, "updated_at": now, "updated_by": user}
            for entry in DEFAULT_LANGUAGES
        ])
        _invalidate_languages()


def list_voices(include_disabled: bool = False) -> list[dict[str, Any]]:
    global _voice_cache
    with _cache_lock:
        if not include_disabled and _voice_cache and (time.monotonic() - _voice_cache[0]) < _CACHE_TTL_SEC:
            return _voice_cache[1]
    try:
        query: dict[str, Any] = {} if include_disabled else {"enabled": {"$ne": False}}
        docs = list(get_db()[VOICE_COLLECTION].find(query).sort("label", 1))
    except Exception:
        docs = []
    if not docs:
        result = [{**entry, "enabled": True} for entry in DEFAULT_VOICES]
    else:
        result = serialize_doc(docs)
    if not include_disabled:
        with _cache_lock:
            _voice_cache = (time.monotonic(), result)
    return result


def list_languages(include_disabled: bool = False) -> list[dict[str, Any]]:
    global _lang_cache
    with _cache_lock:
        if not include_disabled and _lang_cache and (time.monotonic() - _lang_cache[0]) < _CACHE_TTL_SEC:
            return _lang_cache[1]
    try:
        query: dict[str, Any] = {} if include_disabled else {"enabled": {"$ne": False}}
        docs = list(get_db()[LANGUAGE_COLLECTION].find(query).sort("label", 1))
    except Exception:
        docs = []
    if not docs:
        result = [{**entry, "enabled": True} for entry in DEFAULT_LANGUAGES]
    else:
        result = serialize_doc(docs)
    if not include_disabled:
        with _cache_lock:
            _lang_cache = (time.monotonic(), result)
    return result


def _validate_id(value: str, field: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > 80:
        raise ValueError(f"{field} must be 80 characters or fewer")
    return text


def upsert_voice(payload: dict[str, Any], user: str) -> dict[str, Any]:
    voice_id = _validate_id(payload.get("id"), "id")
    label = (payload.get("label") or voice_id).strip()
    update: dict[str, Any] = {
        "id": voice_id,
        "label": label,
        "provider": (payload.get("provider") or "").strip() or "google",
        "gender": (payload.get("gender") or "").strip(),
        "enabled": payload.get("enabled", True),
        "updated_at": _now(),
        "updated_by": user,
    }
    coll = get_db()[VOICE_COLLECTION]
    coll.update_one(
        {"id": voice_id},
        {"$set": update, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    _invalidate_voices()
    return serialize_doc(coll.find_one({"id": voice_id}))


def delete_voice(voice_id: str) -> None:
    voice_id = _validate_id(voice_id, "id")
    result = get_db()[VOICE_COLLECTION].delete_one({"id": voice_id})
    if result.deleted_count == 0:
        raise KeyError("voice_not_found")
    _invalidate_voices()


def upsert_language(payload: dict[str, Any], user: str) -> dict[str, Any]:
    lang_id = _validate_id(payload.get("id"), "id")
    label = (payload.get("label") or lang_id).strip()
    update: dict[str, Any] = {
        "id": lang_id,
        "label": label,
        "livekit_code": (payload.get("livekit_code") or "").strip(),
        "sarvam_code": (payload.get("sarvam_code") or "").strip(),
        "enabled": payload.get("enabled", True),
        "updated_at": _now(),
        "updated_by": user,
    }
    coll = get_db()[LANGUAGE_COLLECTION]
    coll.update_one(
        {"id": lang_id},
        {"$set": update, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    _invalidate_languages()
    return serialize_doc(coll.find_one({"id": lang_id}))


def delete_language(lang_id: str) -> None:
    lang_id = _validate_id(lang_id, "id")
    result = get_db()[LANGUAGE_COLLECTION].delete_one({"id": lang_id})
    if result.deleted_count == 0:
        raise KeyError("language_not_found")
    _invalidate_languages()
