from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..audit import log_audit
from ..auth import require_user
from ..db import bots, campaigns
from ..models import AssignBotRequest, SaveStrategyRequest, SetStatusRequest

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


@router.get("")
def list_campaigns(_: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(c) for c in campaigns.find({})]


@router.put("/{campaign_key}/strategy")
def save_strategy(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = SaveStrategyRequest(**payload)
    campaigns.update_one(
        {"campaign_key": campaign_key},
        {
            "$set": {
                "campaign_key": campaign_key,
                "name": body.name,
                "dialing_strategy": body.strategy.model_dump(),
            }
        },
        upsert=True,
    )
    doc = campaigns.find_one({"campaign_key": campaign_key})
    log_audit(user, "update_strategy", "campaign", campaign_key)
    return _serialize(doc)


@router.put("/{campaign_key}/bot")
def assign_bot(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = AssignBotRequest(**payload)
    try:
        bot_oid = ObjectId(body.bot_id)
    except Exception as exc:
        raise HTTPException(404, "Bot not found") from exc
    if not bots.find_one({"_id": bot_oid}):
        raise HTTPException(404, "Bot not found")

    campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"bot_id": body.bot_id}}, upsert=True)
    log_audit(user, "assign_bot", "campaign", campaign_key, {"bot_id": body.bot_id})
    return _serialize(campaigns.find_one({"campaign_key": campaign_key}))


@router.put("/{campaign_key}/status")
def set_status(campaign_key: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    body = SetStatusRequest(**payload)
    result = campaigns.update_one({"campaign_key": campaign_key}, {"$set": {"status": body.status}})
    if result.matched_count == 0:
        raise HTTPException(404, "Campaign not found")
    log_audit(user, "set_status", "campaign", campaign_key, {"status": body.status})
    return _serialize(campaigns.find_one({"campaign_key": campaign_key}))


@router.delete("/{campaign_key}")
def delete_campaign(campaign_key: str, user: dict = Depends(require_user)) -> dict:
    result = campaigns.delete_one({"campaign_key": campaign_key})
    if result.deleted_count == 0:
        raise HTTPException(404, "Campaign not found")
    log_audit(user, "delete", "campaign", campaign_key)
    return {"ok": True}
