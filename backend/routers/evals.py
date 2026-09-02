from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..auth import bot_owner_filter, require_user
from ..db import bot_versions, bots, db
from ..evals import DEFAULT_SCENARIOS, run_evals
from ..models import EvalRunRequest

router = APIRouter(prefix="/api/bots", tags=["evals"])

eval_runs = db["tbl_ai_vb_eval_runs"]


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as exc:
        raise HTTPException(404, "Not found") from exc


@router.post("/{bot_id}/evals/run")
def run_bot_evals(bot_id: str, payload: EvalRunRequest, user: dict = Depends(require_user)) -> dict:
    bot = bots.find_one({"_id": _oid(bot_id), **bot_owner_filter(user)})
    if not bot:
        raise HTTPException(404, "Bot not found")

    version_id = payload.version_id or bot.get("draft_version_id") or bot.get("active_version_id")
    if not version_id:
        raise HTTPException(400, "No draft or published version to evaluate")
    version = bot_versions.find_one({"_id": _oid(version_id), "bot_id": _oid(bot_id)})
    if not version:
        raise HTTPException(404, "Version not found")

    system_prompt = (version.get("config") or {}).get("system_prompt") or ""
    if not system_prompt:
        raise HTTPException(400, "This version has no system_prompt to evaluate")

    scenarios = payload.scenarios or DEFAULT_SCENARIOS
    try:
        result = run_evals(system_prompt, scenarios)
    except Exception as exc:
        raise HTTPException(502, f"Eval run failed: {exc}") from exc

    now = datetime.now(timezone.utc).isoformat()
    run_doc = {
        "bot_id": _oid(bot_id),
        "version_id": version_id,
        "run_by": user.get("sub"),
        "created_at": now,
        **result,
    }
    inserted_id = eval_runs.insert_one(run_doc).inserted_id
    run_doc["_id"] = str(inserted_id)
    run_doc["bot_id"] = str(run_doc["bot_id"])
    return run_doc


@router.get("/{bot_id}/evals")
def list_bot_evals(bot_id: str, user: dict = Depends(require_user)) -> list[dict]:
    if not bots.find_one({"_id": _oid(bot_id), **bot_owner_filter(user)}):
        raise HTTPException(404, "Bot not found")
    docs = list(eval_runs.find({"bot_id": _oid(bot_id)}).sort("created_at", -1).limit(20))
    for d in docs:
        d["_id"] = str(d["_id"])
        d["bot_id"] = str(d["bot_id"])
    return docs
