"""Per-language behavior settings — timeout message, inactivity nudge, and
language-style notes (the chunk fed to the LLM as part of the system prompt).

Previously hardcoded in bot.py as `HINDI_LANG_CONFIG`. Now per-language docs
PMs can edit. bot.py reads on call start with a TTL cache and snapshots the
result into the per-call config_snapshot so live calls aren't affected by
mid-call edits.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from .config import DEFAULT_USER
from .mongo import get_db
from .serialization import serialize_doc

LANGUAGE_SETTINGS_COLLECTION = "tbl_ai_vb_language_settings"

# Default per-language settings — ported from bot.py:HINDI_LANG_CONFIG. The
# `lang_notes` body is long; keep it editable so PMs can tune phrasing
# without a code deploy.
DEFAULT_LANGUAGE_SETTINGS: dict[str, dict[str, str]] = {
    "hindi": {
        "name": "Hindi",
        "timeout_message": "जी, details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए धन्यवाद.",
        "inactivity_nudge": "हेलो जी, क्या आप वहाँ हैं?",
        "lang_notes": (
            "LANGUAGE NOTES — HINDI\n\n"
            "INPUT: The buyer typically speaks Hindi, Hinglish, or Indian-accented English. If audio is unclear and no explicit language-switch has happened, assume Hindi. If the buyer clearly speaks in English or explicitly requests a language change, honour it — refer to LANGUAGE SWITCHING rules above.\n\n"
            "STYLE: Natural spoken Hinglish — how a real person talks on a call. Conversational, warm, never formal or literary.\n"
            "  Good: 'हाँ जी', 'अच्छा', 'ठीक है', 'samajh gaya', 'okay jee'\n"
            "  Avoid: 'आपकी बात सुनकर खुशी हुई', 'मैं आपकी सहायता के लिए यहाँ हूँ'\n\n"
            "FILLERS — sprinkle naturally, don't force them:\n"
            "अच्छा, हाँ, जी, तो, ठीक है — use when they fit the moment. Vary them. Don't start every response the same way."
        ),
    },
    "english": {
        "name": "English",
        "timeout_message": "Thank you for your time. The relevant sellers will contact you soon. Goodbye!",
        "inactivity_nudge": "Hello, are you still there?",
        "lang_notes": (
            "LANGUAGE NOTES — ENGLISH\n\n"
            "Use natural spoken English (warm and colloquial, not formal). Vary your acknowledgements.\n"
            "Good: 'Got it', 'Alright', 'Okay', 'Sure'.\n"
            "Avoid corporate phrasing like 'I appreciate your response' or 'Please be advised'."
        ),
    },
}


_CACHE_TTL_SEC = 60
_cache_lock = threading.Lock()
_cache: tuple[float, dict[str, dict[str, Any]]] | None = None


def _now() -> datetime:
    return datetime.utcnow()


def _invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def seed_default_language_settings(user: str = DEFAULT_USER) -> None:
    db = get_db()
    coll = db[LANGUAGE_SETTINGS_COLLECTION]
    now = _now()
    for lang_id, entry in DEFAULT_LANGUAGE_SETTINGS.items():
        coll.update_one(
            {"id": lang_id},
            {
                "$setOnInsert": {
                    "id": lang_id,
                    "name": entry["name"],
                    "timeout_message": entry["timeout_message"],
                    "inactivity_nudge": entry["inactivity_nudge"],
                    "lang_notes": entry["lang_notes"],
                    "created_at": now,
                    "created_by": user,
                    "updated_at": now,
                    "updated_by": user,
                }
            },
            upsert=True,
        )
    _invalidate()


def list_language_settings() -> list[dict[str, Any]]:
    docs = list(get_db()[LANGUAGE_SETTINGS_COLLECTION].find().sort("name", 1))
    return serialize_doc(docs)


def get_language_settings(lang_id: str) -> dict[str, Any] | None:
    """Cached read used by bot.py at call start."""
    global _cache
    with _cache_lock:
        if _cache and (time.monotonic() - _cache[0]) < _CACHE_TTL_SEC:
            return _cache[1].get(lang_id)
    try:
        docs = list(get_db()[LANGUAGE_SETTINGS_COLLECTION].find())
        mapping = {d["id"]: serialize_doc(d) for d in docs if d.get("id")}
    except Exception:
        mapping = {}
    if not mapping:
        mapping = {lang_id: {**entry, "id": lang_id} for lang_id, entry in DEFAULT_LANGUAGE_SETTINGS.items()}
    with _cache_lock:
        _cache = (time.monotonic(), mapping)
    return mapping.get(lang_id)


def upsert_language_settings(payload: dict[str, Any], user: str) -> dict[str, Any]:
    lang_id = (payload.get("id") or "").strip()
    if not lang_id:
        raise ValueError("id is required")
    if len(lang_id) > 80:
        raise ValueError("id must be 80 characters or fewer")
    update: dict[str, Any] = {"id": lang_id, "updated_at": _now(), "updated_by": user}
    if "name" in payload:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("name cannot be empty")
        if len(name) > 100:
            raise ValueError("name must be 100 characters or fewer")
        update["name"] = name
    for field, max_len in (
        ("timeout_message", 1000),
        ("inactivity_nudge", 500),
        ("lang_notes", 8000),
    ):
        if field in payload:
            value = (payload.get(field) or "").strip()
            if len(value) > max_len:
                raise ValueError(f"{field} must be {max_len} characters or fewer")
            update[field] = value
    coll = get_db()[LANGUAGE_SETTINGS_COLLECTION]
    coll.update_one(
        {"id": lang_id},
        {"$set": update, "$setOnInsert": {"created_at": _now(), "created_by": user}},
        upsert=True,
    )
    _invalidate()
    return serialize_doc(coll.find_one({"id": lang_id}))


def delete_language_settings(lang_id: str) -> None:
    lang_id = (lang_id or "").strip()
    if not lang_id:
        raise ValueError("id is required")
    result = get_db()[LANGUAGE_SETTINGS_COLLECTION].delete_one({"id": lang_id})
    if result.deleted_count == 0:
        raise KeyError("language_settings_not_found")
    _invalidate()
