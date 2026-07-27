from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..audit import log_audit
from ..auth import require_user
from ..db import agent_number_mapping, bot_versions, bots, sip_dispatch_rules
from ..models import MapNumberToAgentRequest

router = APIRouter(prefix="/api/number-mapping", tags=["number-mapping"])


def _persona_name(bot_id: str | None) -> str:
    """The name the agent says out loud (config.agent_name on its published version) — a
    different thing from the bot's display name, and from livekit_agent_name (the worker
    pool). Joined at read time rather than copied into the mapping row so it can't go stale.
    """
    if not bot_id:
        return ""
    try:
        bot = bots.find_one({"_id": ObjectId(bot_id)})
    except Exception:
        return ""
    version_id = (bot or {}).get("active_version_id")
    if not version_id:
        return ""
    version = bot_versions.find_one({"_id": ObjectId(version_id)})
    return ((version or {}).get("config") or {}).get("agent_name", "")


@router.get("")
def list_mapping(_: dict = Depends(require_user)) -> list[dict]:
    """Every inbound number and the agent it is mapped to, if any."""
    mapped = {m["phone_number"]: m for m in agent_number_mapping.find({})}
    rows = []
    for n in sip_dispatch_rules.find({}).sort("phone_number", 1):
        number = n.get("phone_number", "")
        m = mapped.get(number, {})
        bot_id = m.get("bot_id")
        rows.append(
            {
                "phone_number": number,
                "livekit_agent_name": n.get("agent_name", ""),  # worker pool, not the persona
                "environment": n.get("environment", ""),
                "bot_id": bot_id,
                "bot_name": m.get("bot_name"),          # display name
                "persona_name": _persona_name(bot_id),  # what the caller hears
            }
        )
    return rows


@router.put("/agent/{bot_id}")
def map_number(
    bot_id: str,
    payload: MapNumberToAgentRequest,
    user: dict = Depends(require_user),
) -> dict:
    """Give an agent a number. phone_number=null clears whatever it had."""
    try:
        bot = bots.find_one({"_id": ObjectId(bot_id)})
    except Exception as exc:
        raise HTTPException(404, "Agent not found") from exc
    if not bot or bot.get("status") == "deleted":
        raise HTTPException(404, "Agent not found")

    now = datetime.now(timezone.utc).isoformat()

    # An agent holds one number: drop whatever it had before, including on a clear.
    agent_number_mapping.delete_many({"bot_id": bot_id})

    if not payload.phone_number:
        log_audit(user, "unassign", "number_mapping", bot_id)
        return {"bot_id": bot_id, "phone_number": None}

    number = sip_dispatch_rules.find_one({"phone_number": payload.phone_number})
    if not number:
        raise HTTPException(404, "Number not found")

    taken = agent_number_mapping.find_one({"phone_number": payload.phone_number})
    if taken:
        raise HTTPException(409, f"{payload.phone_number} is already mapped to {taken.get('bot_name')}")

    agent_number_mapping.insert_one(
        {
            "phone_number": payload.phone_number,
            "livekit_agent_name": number.get("agent_name", ""),
            "bot_id": bot_id,
            "bot_name": bot.get("name", ""),
            "environment": number.get("environment", ""),
            "created_at": now,
            "updated_at": now,
        }
    )
    log_audit(user, "assign", "number_mapping", bot_id, {"phone_number": payload.phone_number})
    return {"bot_id": bot_id, "phone_number": payload.phone_number}
