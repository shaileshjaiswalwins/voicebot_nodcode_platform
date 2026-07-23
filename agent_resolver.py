"""Resolve which dashboard-created agent should answer an inbound call.

    dialed number -> tbl_ai_vb_agent_number_mapping -> bot -> published version -> config

Reads the admin platform DB (ai_voice_bot_management). That is a DIFFERENT database from
the call-transcript DB bot.py writes to (ai_lead_qualify), hence its own client here.
Read-only, and every lookup returns None rather than raising, so an unmapped number or an
unreachable platform DB leaves the caller on the bot's built-in persona instead of failing
the call.
"""

import os
import re

from bson import ObjectId
from pymongo import MongoClient

_PLATFORM_MONGO_URI = os.getenv("PLATFORM_MONGO_URI") or os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
_PLATFORM_DB = os.getenv("VOICEBOT_PLATFORM_DB", "ai_voice_bot_management")

_client: MongoClient | None = None


def _db():
    global _client
    if _client is None:
        _client = MongoClient(_PLATFORM_MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client[_PLATFORM_DB]


def _number_variants(number: str) -> list[str]:
    """Every plausible spelling of a number.

    The stored DNI is landline-style (08069625582) but LiveKit's sip.trunkPhoneNumber may
    arrive as +918069625582 or 8069625582 — the exact format is not yet confirmed on these
    trunks, so match them all rather than guess one.
    """
    digits = re.sub(r"[^\d]", "", number or "")
    if not digits:
        return []
    core = digits[2:] if digits.startswith("91") and len(digits) > 10 else digits
    core = core.lstrip("0")
    if not core:
        return []
    return list({digits, core, f"0{core}", f"91{core}", f"+91{core}"})


def _config_for_bot(db, bot_id: str) -> dict | None:
    try:
        bot = db["tbl_ai_vb_bots"].find_one({"_id": ObjectId(bot_id)})
    except Exception:
        return None
    if not bot or bot.get("status") == "deleted":
        return None
    version_id = bot.get("active_version_id")
    if not version_id:  # draft-only agent — nothing published to answer with
        return None
    version = db["tbl_ai_vb_bot_versions"].find_one({"_id": ObjectId(version_id)})
    return (version or {}).get("config") or None


def _resolve_mapping(dialed_number: str = "", room_name: str = "") -> dict | None:
    """The number→bot mapping row for this call, or None.

    room_name is a fallback for when sip.trunkPhoneNumber is empty: the dispatch rule
    stamps its own prefix on the room (Campaign_8_5060__<caller>_<random>), and each rule
    holds one number. That only holds while a rule maps to a single number.
    """
    db = _db()
    mapping = None

    variants = _number_variants(dialed_number)
    if variants:
        mapping = db["tbl_ai_vb_agent_number_mapping"].find_one({"phone_number": {"$in": variants}})

    if not mapping and room_name:
        prefix = room_name.split("__")[0]
        rule = db["tbl_ai_vb_sip_dispatch_rules"].find_one({"room_prefix": prefix}) if prefix else None
        if rule and rule.get("phone_number"):
            mapping = db["tbl_ai_vb_agent_number_mapping"].find_one({"phone_number": rule["phone_number"]})

    return mapping


def resolve_agent_config(dialed_number: str = "", room_name: str = "") -> dict | None:
    """Config of the agent mapped to this call's number, or None if there isn't one."""
    mapping = _resolve_mapping(dialed_number, room_name)
    if not mapping:
        return None
    return _config_for_bot(_db(), mapping["bot_id"])


def resolve_bot_id(dialed_number: str = "", room_name: str = "") -> str | None:
    """The bot id (hex string) mapped to this call's number, or None. Needed to look up the
    bot's custom functions, which are keyed by this same id — resolve_agent_config returns
    only the config and drops the id."""
    mapping = _resolve_mapping(dialed_number, room_name)
    if not mapping:
        return None
    return str(mapping["bot_id"])


def fetch_custom_functions(bot_id: str, timing: str | None = None) -> list[dict]:
    """Enabled custom functions for a bot (tbl_ai_vb_custom_functions), optionally filtered
    by timing ('pre_call' | 'in_call'). Read-only; returns [] on any error so a DB hiccup
    never fails the call."""
    if not bot_id:
        return []
    try:
        query: dict = {"bot_id": str(bot_id), "enabled": True}
        if timing:
            query["timing"] = timing
        return list(_db()["tbl_ai_vb_custom_functions"].find(query))
    except Exception:
        return []


def render_greeting(config: dict, product: str = "") -> str:
    """The agent's opening line with placeholders filled, or "" if it has none set.

    An inbound caller with no lead has no product, so {product} renders empty and leaves a
    gap ("आपको  की requirement..."). Collapsing whitespace keeps that speakable rather than
    letting TTS voice a stutter — it does not make the sentence read well, and an opening
    line that asks about a product the bot doesn't know is a config problem, not a code one.
    """
    line = (config.get("initial_message") or "").strip()
    if not line:
        return ""
    # Organization defaults to Justdial when the agent leaves it blank, so the bot never
    # says "मैं Riya बोल रही हूँ  से" with a gap where the company name belongs.
    org = (config.get("organization_name") or "").strip() or "Justdial"
    filled = (
        line.replace("{agent_name}", config.get("agent_name") or "")
        .replace("{organization_name}", org)
        .replace("{product}", product or "")
    )
    return re.sub(r"\s{2,}", " ", filled).strip()
