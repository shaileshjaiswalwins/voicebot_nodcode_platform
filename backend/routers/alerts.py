from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from ..audit import log_audit
from ..auth import owned_bot_ids, require_user
from ..db import alert_incidents, alert_rules
from ..models import AlertRuleCreate, AlertRuleUpdate

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

MAX_RULES_PER_USER = 10


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


def _owner_filter(user: dict) -> dict:
    """Same self-serve ownership shape as auth.bot_owner_filter, applied to alert rules'
    own `created_by` field rather than a bots-collection `owner` field, since a rule isn't
    itself a bot — admins see/manage every rule, everyone else only their own."""
    return {} if user.get("role") == "admin" else {"created_by": user.get("sub")}


def _parse_rule(model_cls: type, payload: dict):
    """Construct an AlertRuleCreate/AlertRuleUpdate from the raw request body ourselves
    (rather than typing the route parameter as the model, which would let FastAPI validate
    it before the handler runs and return a 422) so that a Pydantic ValidationError —
    whether from the window/frequency compatibility invariant or an invalid
    status/call_outcome literal, both enforced on AlertRuleBase in backend/models.py —
    surfaces as the same 400 this router already uses for its other validation errors."""
    try:
        return model_cls(**payload)
    except ValidationError as exc:
        raise HTTPException(400, str(exc))


def _validate_bot_ids(bot_ids: list[str], user: dict) -> None:
    if not bot_ids:
        return
    owned = owned_bot_ids(user)
    if owned is not None and not set(bot_ids).issubset(owned):
        raise HTTPException(400, "filters.bot_ids includes a bot you don't own")


def _get_owned_rule(rule_id: str, user: dict) -> dict:
    try:
        oid = ObjectId(rule_id)
    except Exception:
        raise HTTPException(404, "Alert rule not found")
    rule = alert_rules.find_one({"_id": oid, **_owner_filter(user)})
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    return rule


@router.get("/rules")
def list_rules(user: dict = Depends(require_user)) -> list[dict]:
    return [_serialize(r) for r in alert_rules.find(_owner_filter(user))]


@router.post("/rules")
def create_rule(payload: dict, user: dict = Depends(require_user)) -> dict:
    payload = _parse_rule(AlertRuleCreate, payload)
    _validate_bot_ids(payload.filters.bot_ids, user)

    existing = alert_rules.count_documents({"created_by": user.get("sub")})
    if existing >= MAX_RULES_PER_USER:
        raise HTTPException(400, f"Rule limit reached ({MAX_RULES_PER_USER} rules per user)")

    now = datetime.now(timezone.utc)
    doc = {
        **payload.model_dump(),
        "created_by": user.get("sub"),
        "next_eval_at": now,
        "last_evaluated_at": None,
        "created_at": now,
    }
    inserted_id = alert_rules.insert_one(doc).inserted_id
    doc["_id"] = inserted_id
    log_audit(user, "create", "alert_rule", str(inserted_id))
    return _serialize(doc)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, payload: dict, user: dict = Depends(require_user)) -> dict:
    _get_owned_rule(rule_id, user)
    payload = _parse_rule(AlertRuleUpdate, payload)
    _validate_bot_ids(payload.filters.bot_ids, user)

    now = datetime.now(timezone.utc)
    oid = ObjectId(rule_id)
    # Defense in depth: scope the write itself by owner, not just the pre-check above — so a
    # future refactor that drops _get_owned_rule doesn't silently turn this into a cross-user
    # write (an editor who removes the pre-check but leaves this filter unscoped would still
    # only ever touch rows they own).
    result = alert_rules.find_one_and_update(
        {"_id": oid, **_owner_filter(user)},
        {
            "$set": {
                **payload.model_dump(),
                # Editing a rule resets its evaluation schedule so a stale window/frequency
                # combo never gets evaluated again on the old cadence, matching Retell's
                # behavior (see the plan's incident-lifecycle note).
                "next_eval_at": now,
            }
        },
        return_document=True,
    )
    if not result:
        raise HTTPException(404, "Alert rule not found")

    # A rule edit also clears any active open incident for it — the condition it was based
    # on no longer applies once the rule itself has changed, so a stale incident must not
    # linger in the History view.
    alert_incidents.update_many(
        {"rule_id": rule_id, "status": "open"},
        {"$set": {"status": "resolved", "resolved_at": now}},
    )
    log_audit(user, "update", "alert_rule", rule_id)
    return _serialize(result)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, user: dict = Depends(require_user)) -> dict:
    _get_owned_rule(rule_id, user)
    # Defense in depth: same owner-scoping on the write itself as update_rule above.
    alert_rules.delete_one({"_id": ObjectId(rule_id), **_owner_filter(user)})
    # Same as the PUT path: a deleted rule can't leave a permanently-open incident behind
    # (list_incidents joins through the caller's own rule ids, so an orphaned open incident
    # under a deleted rule would otherwise become permanently invisible, not just stale).
    alert_incidents.update_many(
        {"rule_id": rule_id, "status": "open"},
        {"$set": {"status": "resolved", "resolved_at": datetime.now(timezone.utc)}},
    )
    log_audit(user, "delete", "alert_rule", rule_id)
    return {"ok": True}


@router.get("/incidents")
def list_incidents(user: dict = Depends(require_user)) -> list[dict]:
    owned_rules = list(alert_rules.find(_owner_filter(user), {"_id": 1, "name": 1}))
    owned_rule_ids = [str(r["_id"]) for r in owned_rules]
    rule_names_by_id = {str(r["_id"]): r.get("name", "") for r in owned_rules}
    incidents = alert_incidents.find({"rule_id": {"$in": owned_rule_ids}}).sort("triggered_at", -1)
    # rule_name is stored on the incident at creation time (alert_worker/worker.py's
    # insert_one) going forward — the join here is now just a defensive fallback for
    # legacy incidents created before that fix, which have no rule_name field.
    return [
        {**_serialize(i), "rule_name": i.get("rule_name") or rule_names_by_id.get(i["rule_id"], "")}
        for i in incidents
    ]
