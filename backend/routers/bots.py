import sys
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..audit import log_audit
from ..auth import require_user
from ..db import bot_versions, bots
from ..models import (
    BotConfig,
    BotCreate,
    BotUpdateConfig,
    CompileFlowPreviewRequest,
    FunctionTestRequest,
)

# flow_compiler.py lives at the repo root (shared with bot.py/bot_pipeline.py/bot_dev.py),
# not inside the backend/ package. Import defensively: in a deployment that only ships
# backend/ (a Docker image copying just this package, a frozen build, etc.) this module
# won't exist, and a top-level ImportError here would take down the entire bots router —
# not just the preview feature — since FastAPI fails to register any route in a router
# whose module fails to import. compile_flow_to_prompt stays None in that case and the
# preview endpoint alone degrades to a 501 instead.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)  # appended, not inserted at 0 — never shadows installed packages
try:
    from flow_compiler import compile_flow_to_prompt
except ImportError:
    compile_flow_to_prompt = None

# Pure custom-function helpers (repo root). Same defensive import: if backend/ ships without
# them, only the function-test endpoint degrades to 501 rather than taking down the router.
try:
    from custom_functions import apply_store_variables, build_http_call, resolve_timeout_seconds
except ImportError:
    build_http_call = None

router = APIRouter(prefix="/api/bots", tags=["bots"])


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as exc:
        raise HTTPException(404, "Bot not found") from exc


def _serialize_bot(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    return doc


def _serialize_version(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    doc["bot_id"] = str(doc["bot_id"])
    return doc


def _persona_name(bot: dict) -> str:
    """The agent's spoken name (config.agent_name). Prefers the published version, falls
    back to the draft so a just-created (draft-only) bot still shows its name."""
    version_id = bot.get("active_version_id") or bot.get("draft_version_id")
    if not version_id:
        return ""
    try:
        version = bot_versions.find_one({"_id": ObjectId(version_id)})
    except Exception:
        return ""
    return ((version or {}).get("config") or {}).get("agent_name", "")


@router.get("")
def list_bots(_: dict = Depends(require_user)) -> list[dict]:
    out = []
    for b in bots.find({"status": {"$ne": "deleted"}}):
        agent_name = _persona_name(b)
        b = _serialize_bot(b)
        b["agent_name"] = agent_name  # spoken persona, distinct from the display name above
        out.append(b)
    return out


@router.post("")
def create_bot(payload: BotCreate, user: dict = Depends(require_user)) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")

    now = datetime.now(timezone.utc).isoformat()
    bot_doc = {
        "name": name,
        "description": payload.description,
        "status": "active",
        "active_version_id": None,
        "owner": user.get("sub"),
        "created_at": now,
        "updated_at": now,
    }
    bot_id = bots.insert_one(bot_doc).inserted_id

    version_doc = {
        "bot_id": bot_id,
        "version": 1,
        "state": "draft",
        "config": payload.config.model_dump(),
        "notes": "Initial version",
        "created_at": now,
    }
    version_id = bot_versions.insert_one(version_doc).inserted_id

    bots.update_one({"_id": bot_id}, {"$set": {"draft_version_id": str(version_id)}})
    bot_doc["_id"] = bot_id
    bot_doc["draft_version_id"] = str(version_id)
    log_audit(user, "create", "bot", str(bot_id), {"name": name})
    return _serialize_bot(bot_doc)


@router.get("/{bot_id}")
def get_bot(bot_id: str, _: dict = Depends(require_user)) -> dict:
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot:
        raise HTTPException(404, "Bot not found")
    versions = list(bot_versions.find({"bot_id": _oid(bot_id)}).sort("version", -1))
    return {"bot": _serialize_bot(bot), "versions": [_serialize_version(v) for v in versions]}


@router.get("/{bot_id}/functions")
def list_bot_functions(bot_id: str, version_id: str = "", _: dict = Depends(require_user)) -> dict:
    """List the custom functions saved against a bot.

    Custom functions are stored inside a version's `config.functions` (versioned/published
    with the rest of the bot config), not in a standalone table. By default this reads the
    bot's live version (active/published), falling back to the draft, then the latest — so
    it reflects what the runtime would actually use. Pass ?version_id=... to inspect a
    specific version instead.
    """
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot:
        raise HTTPException(404, "Bot not found")

    if version_id:
        version = bot_versions.find_one({"_id": _oid(version_id), "bot_id": _oid(bot_id)})
    else:
        target_id = bot.get("active_version_id") or bot.get("draft_version_id")
        version = bot_versions.find_one({"_id": _oid(target_id)}) if target_id else None
        if not version:  # last resort: newest version of any state
            version = bot_versions.find_one({"bot_id": _oid(bot_id)}, sort=[("version", -1)])

    if not version:
        raise HTTPException(404, "No version found for this bot")

    functions = (version.get("config") or {}).get("functions") or []
    return {
        "bot_id": bot_id,
        "version_id": str(version["_id"]),
        "version": version.get("version"),
        "state": version.get("state"),
        "count": len(functions),
        "functions": functions,
    }


def _fork_new_draft(bot_id: str, config: dict, notes: str = "") -> str:
    latest = bot_versions.find_one({"bot_id": _oid(bot_id)}, sort=[("version", -1)])
    next_version = (latest["version"] + 1) if latest else 1
    now = datetime.now(timezone.utc).isoformat()
    version_doc = {
        "bot_id": _oid(bot_id),
        "version": next_version,
        "state": "draft",
        "config": config,
        "notes": notes,
        "created_at": now,
    }
    version_id = str(bot_versions.insert_one(version_doc).inserted_id)
    bots.update_one({"_id": _oid(bot_id)}, {"$set": {"draft_version_id": version_id, "updated_at": now}})
    return version_id


@router.put("/{bot_id}/draft")
def save_draft(bot_id: str, payload: BotUpdateConfig, _: dict = Depends(require_user)) -> dict:
    """'New version' action — always forks a brand-new draft, even if one already exists.
    Distinct from PUT /versions/{version_id}, which edits an existing draft in place."""
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot:
        raise HTTPException(404, "Bot not found")
    version_id = _fork_new_draft(bot_id, payload.config.model_dump())
    return {"draft_version_id": version_id}


@router.put("/{bot_id}/versions/{version_id}")
def update_version(bot_id: str, version_id: str, payload: BotUpdateConfig, _: dict = Depends(require_user)) -> dict:
    """'Update version' action — edits a specific existing DRAFT version's config in place.
    Published versions are immutable and cannot be targeted here (audit item 1/16)."""
    version = bot_versions.find_one({"_id": _oid(version_id), "bot_id": _oid(bot_id)})
    if not version:
        raise HTTPException(404, "Version not found")
    if version["state"] != "draft":
        raise HTTPException(400, "Cannot edit a published version in place — use rollback to fork a new draft")
    now = datetime.now(timezone.utc).isoformat()
    bot_versions.update_one({"_id": _oid(version_id)}, {"$set": {"config": payload.config.model_dump(), "updated_at": now}})
    return {"draft_version_id": version_id}


@router.post("/{bot_id}/unpublish")
def unpublish(bot_id: str, user: dict = Depends(require_user)) -> dict:
    """Moves the currently-published active version back to draft so it can be edited,
    per BuilderView's 'Edit published' action. Only valid when there is no separate draft
    already in progress."""
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot:
        raise HTTPException(404, "Bot not found")
    if bot.get("draft_version_id"):
        raise HTTPException(400, "A draft already exists — resolve or discard it before unpublishing")
    active_id = bot.get("active_version_id")
    if not active_id:
        raise HTTPException(400, "No published version to unpublish")

    now = datetime.now(timezone.utc).isoformat()
    bot_versions.update_one({"_id": ObjectId(active_id)}, {"$set": {"state": "draft", "updated_at": now}})
    bots.update_one(
        {"_id": _oid(bot_id)},
        {"$set": {"draft_version_id": active_id, "active_version_id": None, "updated_at": now}},
    )
    log_audit(user, "unpublish", "bot", bot_id, {"version_id": active_id})
    return {"draft_version_id": active_id}


@router.put("/{bot_id}")
def rename_bot(bot_id: str, payload: dict, _: dict = Depends(require_user)) -> dict:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    now = datetime.now(timezone.utc).isoformat()
    result = bots.find_one_and_update(
        {"_id": _oid(bot_id)},
        {"$set": {"name": name, "description": payload.get("description", ""), "updated_at": now}},
        return_document=True,
    )
    if not result:
        raise HTTPException(404, "Bot not found")
    return _serialize_bot(result)


@router.post("/{bot_id}/publish")
def publish_draft(bot_id: str, user: dict = Depends(require_user)) -> dict:
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot:
        raise HTTPException(404, "Bot not found")
    draft_id = bot.get("draft_version_id")
    if not draft_id:
        raise HTTPException(400, "No draft to publish")

    now = datetime.now(timezone.utc).isoformat()
    # Atomic claim: only the request that actually finds+clears a still-set draft_version_id
    # wins. Without this filter, two concurrent publishes can both read the same
    # draft_version_id before either write lands, and both would publish it (a real race
    # found via test_concurrency.py). find_one_and_update's match+set happens as one
    # operation, so a second concurrent caller's filter no longer matches once the first
    # has cleared the field.
    claimed = bots.find_one_and_update(
        {"_id": _oid(bot_id), "draft_version_id": draft_id},
        {"$set": {"active_version_id": draft_id, "draft_version_id": None, "updated_at": now}},
    )
    if not claimed:
        raise HTTPException(409, "This draft was already published by another request")

    draft_oid = ObjectId(draft_id)
    bot_versions.update_one({"_id": draft_oid}, {"$set": {"state": "published", "published_at": now}})
    log_audit(user, "publish", "bot", bot_id, {"version_id": draft_id})
    return {"active_version_id": draft_id}


@router.post("/{bot_id}/rollback/{version_id}")
def rollback(bot_id: str, version_id: str, user: dict = Depends(require_user)) -> dict:
    """Rollback creates a new draft seeded from a historical version's config, rather than
    mutating history in place — published versions stay immutable (audit item 1 / item 16)."""
    target = bot_versions.find_one({"_id": _oid(version_id), "bot_id": _oid(bot_id)})
    if not target:
        raise HTTPException(404, "Version not found")

    latest = bot_versions.find_one({"bot_id": _oid(bot_id)}, sort=[("version", -1)])
    now = datetime.now(timezone.utc).isoformat()
    version_doc = {
        "bot_id": _oid(bot_id),
        "version": latest["version"] + 1,
        "state": "draft",
        "config": target["config"],
        "notes": f"Rolled back from v{target['version']}",
        "created_at": now,
    }
    new_id = str(bot_versions.insert_one(version_doc).inserted_id)
    bots.update_one({"_id": _oid(bot_id)}, {"$set": {"draft_version_id": new_id}})
    log_audit(user, "rollback", "bot", bot_id, {"from_version": target["version"], "new_draft_version_id": new_id})
    return {"draft_version_id": new_id}


@router.post("/compile-flow-preview")
def compile_flow_preview(payload: CompileFlowPreviewRequest, _: dict = Depends(require_user)) -> dict:
    """Returns the exact step-script text bot.py's build_system_prompt() will append to the
    system prompt for this flow — lets a PM see what "Phase A" compilation produces before
    saving, since the compiled text is advisory guidance to the LLM, not a guaranteed
    deterministic execution (see flow_compiler.py's module docstring)."""
    if compile_flow_to_prompt is None:
        raise HTTPException(501, "Flow preview is unavailable on this deployment (flow_compiler module not found)")
    return {"compiled_prompt": compile_flow_to_prompt(payload.flow.model_dump())}


async def _execute_test_request(call: dict, timeout: float) -> tuple[int, object]:
    """Perform the actual HTTP round-trip for a custom-function dry-run and return
    ``(status_code, decoded_body)``. Isolated so tests can monkeypatch it (no real network)
    and so the endpoint stays a thin orchestrator."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        async with session.request(
            call["method"], call["url"], headers=call["headers"],
            params=call["params"], json=call["json"], data=call["data"],
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = await resp.text()
            return resp.status, body


@router.post("/{bot_id}/functions/test")
async def test_custom_function(
    bot_id: str, payload: FunctionTestRequest, _: dict = Depends(require_user)
) -> dict:
    """Dry-run a custom function server-side so the builder's 'Test' button can validate an
    endpoint without a live call. Returns the HTTP status, latency, decoded response, and any
    variables the configured store_variables would extract. A non-2xx status is reported (not
    an error); only a transport failure sets ``error``."""
    import time as _time

    if build_http_call is None:
        raise HTTPException(501, "Function testing is unavailable on this deployment (custom_functions module not found)")
    if not bots.find_one({"_id": _oid(bot_id)}):
        raise HTTPException(404, "Bot not found")

    fn = payload.function.model_dump()
    if not (fn.get("url") or "").strip():
        raise HTTPException(400, "Function URL is required to run a test")

    call = build_http_call(fn, payload.args or {})
    timeout = resolve_timeout_seconds(fn, default=10.0)
    result = {
        "ok": False, "status_code": None, "latency_ms": 0,
        "response": None, "extracted_vars": {}, "error": None,
        "request": {"method": call["method"], "url": call["url"],
                    "params": call["params"], "json": call["json"], "data": call["data"]},
    }
    _start = _time.perf_counter()
    try:
        status, body = await _execute_test_request(call, timeout)
        result["status_code"] = status
        result["response"] = body
        result["ok"] = 200 <= status < 300
        extracted: dict = {}
        apply_store_variables(fn.get("store_variables"), body, extracted)
        result["extracted_vars"] = extracted
    except Exception as exc:  # noqa: BLE001 — surface any transport failure to the PM
        result["error"] = str(exc) or exc.__class__.__name__
    result["latency_ms"] = int((_time.perf_counter() - _start) * 1000)
    return result


@router.delete("/{bot_id}")
def delete_bot(bot_id: str, user: dict = Depends(require_user)) -> dict:
    """Soft delete only — never removes the bot or its transcripts (audit item 6/22)."""
    result = bots.update_one(
        {"_id": _oid(bot_id)},
        {"$set": {"status": "deleted", "active_version_id": None, "draft_version_id": None}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Bot not found")
    log_audit(user, "delete", "bot", bot_id)
    return {"ok": True}
