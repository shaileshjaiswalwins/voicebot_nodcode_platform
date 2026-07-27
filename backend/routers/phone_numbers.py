from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..audit import log_audit
from ..auth import require_user
from ..db import bots, phone_numbers
from ..models import PhoneNumberCreate, PhoneNumberUpdate, ReassignPhoneNumberRequest

router = APIRouter(prefix="/api/phone-numbers", tags=["phone-numbers"])


def _oid(id_str: str, not_found_detail: str = "Phone number not found") -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as exc:
        raise HTTPException(404, not_found_detail) from exc


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    doc.pop("sip_password", None)  # write-only — never echoed back, even to an authed PM
    return doc


@router.get("")
def list_phone_numbers(_: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(p) for p in phone_numbers.find({})]


@router.post("")
def create_phone_number(payload: PhoneNumberCreate, user: dict = Depends(require_user)) -> dict:
    # Application-level check, not a DB unique index (no other collection in this codebase
    # manages indexes) — leaves a narrow race window under concurrent creates, acceptable
    # for this internal admin tool's traffic profile.
    if phone_numbers.find_one({"number": payload.number}):
        raise HTTPException(409, f"Phone number {payload.number} already exists")

    now = datetime.now(timezone.utc).isoformat()
    assigned_bot_version_id = None
    if payload.assigned_bot_id:
        bot = bots.find_one({"_id": _oid(payload.assigned_bot_id, "Assigned bot not found")})
        if not bot:
            raise HTTPException(404, "Assigned bot not found")
        active_version_id = bot.get("active_version_id")
        if not active_version_id:
            raise HTTPException(400, "Assigned bot has no published (active) version")
        assigned_bot_version_id = active_version_id

    doc = {
        "number": payload.number,
        "environment": payload.environment,
        "assigned_bot_id": payload.assigned_bot_id,
        "assigned_bot_version_id": assigned_bot_version_id,
        "status": payload.status,
        "service_id": payload.service_id,
        "aod_ports": payload.aod_ports,
        "name": payload.name,
        "ip": payload.ip,
        "sip_trunk": payload.sip_trunk,
        "sip_username": payload.sip_username,
        "sip_password": payload.sip_password,
        "created_at": now,
        "updated_at": now,
    }
    inserted_id = phone_numbers.insert_one(doc).inserted_id
    doc["_id"] = inserted_id
    log_audit(user, "create", "phone_number", str(inserted_id), {"number": payload.number})
    return _serialize(doc)


@router.put("/{phone_number_id}")
def update_phone_number(phone_number_id: str, payload: PhoneNumberUpdate, user: dict = Depends(require_user)) -> dict:
    phone_doc = phone_numbers.find_one({"_id": _oid(phone_number_id)})
    if not phone_doc:
        raise HTTPException(404, "Phone number not found")

    now = datetime.now(timezone.utc).isoformat()
    update = {
        "status": payload.status,
        "service_id": payload.service_id,
        "aod_ports": payload.aod_ports,
        "name": payload.name,
        "ip": payload.ip,
        "sip_trunk": payload.sip_trunk,
        "sip_username": payload.sip_username,
        "updated_at": now,
    }
    if payload.sip_password:
        update["sip_password"] = payload.sip_password

    phone_numbers.update_one({"_id": _oid(phone_number_id)}, {"$set": update})
    log_audit(user, "update", "phone_number", phone_number_id, {"name": payload.name})
    return _serialize(phone_numbers.find_one({"_id": _oid(phone_number_id)}))


@router.put("/{phone_number_id}/reassign")
def reassign_phone_number(phone_number_id: str, payload: ReassignPhoneNumberRequest, user: dict = Depends(require_user)) -> dict:
    """Hot-swap the bot a phone number routes to. This mapping is meant to be read fresh at
    dispatch time for every incoming call — never cached for a call's duration — so that a
    reassign here takes effect on the very next inbound call with no extra propagation step.
    Wiring the actual dispatch-time read (deciding what bot_id goes into LiveKit room-dispatch
    metadata) is future work, out of scope here."""
    phone_doc = phone_numbers.find_one({"_id": _oid(phone_number_id)})
    if not phone_doc:
        raise HTTPException(404, "Phone number not found")

    bot = bots.find_one({"_id": _oid(payload.bot_id, "Bot not found")})
    if not bot:
        raise HTTPException(404, "Bot not found")

    active_version_id = bot.get("active_version_id")
    if not active_version_id:
        raise HTTPException(400, "Target bot has no published (active) version to assign")

    now = datetime.now(timezone.utc).isoformat()
    phone_numbers.update_one(
        {"_id": _oid(phone_number_id)},
        {
            "$set": {
                "assigned_bot_id": payload.bot_id,
                "assigned_bot_version_id": active_version_id,
                "updated_at": now,
            }
        },
    )
    log_audit(user, "reassign", "phone_number", phone_number_id, {"bot_id": payload.bot_id})
    return _serialize(phone_numbers.find_one({"_id": _oid(phone_number_id)}))


@router.delete("/{phone_number_id}")
def delete_phone_number(phone_number_id: str, user: dict = Depends(require_user)) -> dict:
    result = phone_numbers.delete_one({"_id": _oid(phone_number_id)})
    if result.deleted_count == 0:
        raise HTTPException(404, "Phone number not found")
    log_audit(user, "delete", "phone_number", phone_number_id)
    return {"ok": True}
