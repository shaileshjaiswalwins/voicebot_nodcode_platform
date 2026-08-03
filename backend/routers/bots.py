import csv
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..analysis_prompts import CALL_ANALYSIS_KEY, validate_prompt_template
from ..audit import log_audit
from ..auth import require_user
from ..db import bot_versions, bots, transcripts
from ..llm_chat import bot_reply, simulate_turn
from ..models import (
    BotConfig,
    BotCreate,
    BotUpdateConfig,
    CompileFlowPreviewRequest,
    FunctionTestRequest,
    GenerateAgentRequest,
    GeneratePromptRequest,
    LlmChatReplyRequest,
    LlmChatSimulateRequest,
)
from ..prompt_assist import generate_agent_from_description, generate_prompt

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


def _validate_config_analysis_prompt(config: BotConfig) -> None:
    """A per-bot analysis_prompt override fully replaces the global template (not a
    supplement), so a bad one silently breaks post-call analysis for every future call
    on that bot. Enforce the same required-placeholder/JSON-schema contract the global
    Library editor enforces, at every site a BotConfig gets saved."""
    if not config.analysis_prompt.strip():
        return
    try:
        validate_prompt_template(CALL_ANALYSIS_KEY, config.analysis_prompt)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, f"Invalid analysis_prompt: {e}")


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


def _empty_summary() -> dict:
    return {"agent_name": "", "bot_type": "standard", "tags": []}


def _list_bots_by_status(status_filter: dict) -> list[dict]:
    """Shared by the active list and the Recently Deleted panel — same version/call-count
    enrichment either way, just a different Mongo status filter."""
    bot_docs = list(bots.find(status_filter).sort("updated_at", -1))

    # Agent name + bot type live on the version doc, not the bot doc. Batch-fetch every
    # referenced version in one query instead of one find_one() per bot — with N bots that
    # was N+1 round-trips to Mongo (which runs on a remote host here), not N+1 queries against
    # a local DB, so each one adds real network latency to the page load.
    version_ids = []
    for b in bot_docs:
        vid = b.get("active_version_id") or b.get("draft_version_id")
        if vid:
            try:
                version_ids.append(ObjectId(vid))
            except Exception:
                pass
    summary_by_version_id = {}
    if version_ids:
        for v in bot_versions.find(
            {"_id": {"$in": version_ids}}, {"config.agent_name": 1, "config.bot_type": 1, "config.tags": 1}
        ):
            config = v.get("config") or {}
            summary_by_version_id[str(v["_id"])] = {
                "agent_name": config.get("agent_name", ""),
                "bot_type": config.get("bot_type") or "standard",
                "tags": config.get("tags") or [],
            }

    bot_ids = [str(b["_id"]) for b in bot_docs]

    # Call counts (all-time), one aggregation instead of the frontend filtering whatever page
    # of transcripts it happened to have loaded (that count was silently capped at the
    # transcripts view's own fetch limit and wrong for every bot with more calls than that).
    call_counts: dict[str, int] = {}
    for row in transcripts.aggregate([
        {"$match": {"bot_id": {"$in": bot_ids}}},
        {"$group": {"_id": "$bot_id", "count": {"$sum": 1}}},
    ]):
        call_counts[row["_id"]] = row["count"]

    # Today's call count + average call duration, grouped by bot — created_at is stored as
    # an ISO string (see Transcript.created_at), so a string-prefix match against today's
    # date avoids needing a real date type on every historical transcript doc.
    today_str = datetime.now(timezone.utc).date().isoformat()
    calls_today: dict[str, int] = {}
    avg_duration: dict[str, float] = {}
    for row in transcripts.aggregate([
        {"$match": {"bot_id": {"$in": bot_ids}}},
        {
            "$group": {
                "_id": "$bot_id",
                "avg_duration_sec": {"$avg": "$call_duration_sec"},
                "calls_today": {
                    "$sum": {
                        "$cond": [
                            {"$eq": [{"$substrCP": ["$created_at", 0, 10]}, today_str]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]):
        calls_today[row["_id"]] = row["calls_today"]
        avg_duration[row["_id"]] = row.get("avg_duration_sec") or 0

    out = []
    for b in bot_docs:
        vid = b.get("active_version_id") or b.get("draft_version_id")
        summary = summary_by_version_id.get(str(vid)) if vid else None
        summary = summary or _empty_summary()
        bot_id = str(b["_id"])
        b = _serialize_bot(b)
        b["agent_name"] = summary["agent_name"]  # spoken persona, distinct from the display name above
        b["bot_type"] = summary["bot_type"]
        b["tags"] = summary["tags"]
        b["published"] = bool(b.get("active_version_id"))
        b["call_count"] = call_counts.get(bot_id, 0)
        b["calls_today"] = calls_today.get(bot_id, 0)
        b["avg_duration_sec"] = round(avg_duration.get(bot_id, 0) or 0, 1)
        out.append(b)
    return out


@router.get("")
def list_bots(_: dict = Depends(require_user)) -> list[dict]:
    return _list_bots_by_status({"status": {"$ne": "deleted"}})


@router.get("/deleted")
def list_deleted_bots(_: dict = Depends(require_user)) -> list[dict]:
    """Recently Deleted panel — bots are soft-deleted (status='deleted', see delete_bot
    below), never actually removed, so this is just the mirror-image filter of list_bots."""
    return _list_bots_by_status({"status": "deleted"})


@router.post("/{bot_id}/restore")
def restore_bot(bot_id: str, user: dict = Depends(require_user)) -> dict:
    result = bots.update_one(
        {"_id": _oid(bot_id), "status": "deleted"},
        {"$set": {"status": "active"}, "$unset": {"deleted_at": ""}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Deleted bot not found")
    log_audit(user, "restore", "bot", bot_id)
    return {"ok": True}


@router.get("/export")
def export_bots(_: dict = Depends(require_user)) -> Response:
    """CSV export of every active (non-deleted) agent — name, type, lifecycle, tags, calls,
    last updated. Mirrors list_bots' own enrichment so the export always matches what the
    Agents page currently shows."""
    rows = _list_bots_by_status({"status": {"$ne": "deleted"}})
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Agent name", "Type", "Published", "Tags", "Calls (total)", "Calls (today)", "Avg duration (s)", "Last updated"])
    for b in rows:
        writer.writerow([
            b.get("name", ""),
            b.get("agent_name", ""),
            b.get("bot_type", "standard"),
            "yes" if b.get("published") else "no",
            "|".join(b.get("tags") or []),
            b.get("call_count", 0),
            b.get("calls_today", 0),
            b.get("avg_duration_sec", 0),
            b.get("updated_at", ""),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=agents.csv"},
    )


@router.post("")
def create_bot(payload: BotCreate, user: dict = Depends(require_user)) -> dict:
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    _validate_config_analysis_prompt(payload.config)

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


@router.post("/generate-agent")
def generate_agent(payload: GenerateAgentRequest, _: dict = Depends(require_user)) -> dict:
    """Create Agent > Create with AI: derives agent_name/initial_message/system_prompt from a
    free-text description, before any bot exists yet — no bot_id lookup needed. The caller
    (frontend) merges the result into defaultConfig and calls the normal create_bot endpoint."""
    if not payload.description.strip():
        raise HTTPException(400, "description is required")
    try:
        result = generate_agent_from_description(payload.description)
    except Exception as exc:
        raise HTTPException(502, f"Agent generation failed: {exc}") from exc
    return result


@router.get("/{bot_id}")
def get_bot(bot_id: str, _: dict = Depends(require_user)) -> dict:
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot:
        raise HTTPException(404, "Bot not found")
    versions = list(bot_versions.find({"bot_id": _oid(bot_id)}).sort("version", -1))
    return {"bot": _serialize_bot(bot), "versions": [_serialize_version(v) for v in versions]}


@router.post("/{bot_id}/generate-prompt")
def generate_bot_prompt(bot_id: str, payload: GeneratePromptRequest, _: dict = Depends(require_user)) -> dict:
    """AI-assist for the Prompt tab: mode='generate' writes a system_prompt from scratch off a
    free-text description; mode='refine' applies only the described change to the existing
    system_prompt, leaving the rest untouched. Bot lookup is just an existence/auth check —
    the actual prompt text lives in payload, not in a stored version."""
    if not bots.find_one({"_id": _oid(bot_id)}):
        raise HTTPException(404, "Bot not found")
    # analysis_prompt has no from-scratch mode (see prompt_assist.generate_prompt's docstring)
    # — it always refines current_prompt (or the shared default template if that's blank), so
    # the mode=='refine'-only guard below doesn't apply to it.
    if payload.mode == "refine" and payload.target != "analysis_prompt" and not payload.current_prompt.strip():
        raise HTTPException(400, "current_prompt is required for mode='refine'")
    try:
        result = generate_prompt(payload.mode, payload.instruction, payload.current_prompt, payload.target)
    except Exception as exc:
        raise HTTPException(502, f"Prompt generation failed: {exc}") from exc
    return {"text": result}


@router.post("/{bot_id}/llm-chat/reply")
def llm_chat_reply(bot_id: str, payload: LlmChatReplyRequest, _: dict = Depends(require_user)) -> dict:
    """Test LLM > Manual Chat: one text reply from the bot's LLM, no LiveKit/voice involved.
    Stateless — the tester's client resends the full history each call."""
    if not bots.find_one({"_id": _oid(bot_id)}):
        raise HTTPException(404, "Bot not found")
    try:
        text = bot_reply(payload.system_prompt, payload.history, payload.dynamic_variables, payload.function_mocks)
    except Exception as exc:
        raise HTTPException(502, f"LLM chat failed: {exc}") from exc
    return {"text": text}


@router.post("/{bot_id}/llm-chat/simulate-turn")
def llm_chat_simulate_turn(bot_id: str, payload: LlmChatSimulateRequest, _: dict = Depends(require_user)) -> dict:
    """Test LLM > AI Simulated Chat: advances the LLM-vs-LLM simulation by one caller-then-bot
    turn per call, so the frontend can render each pair live instead of waiting for a full
    transcript like evals.run_scenario does."""
    if not bots.find_one({"_id": _oid(bot_id)}):
        raise HTTPException(404, "Bot not found")
    try:
        result = simulate_turn(
            payload.system_prompt, payload.caller_persona, payload.history, payload.dynamic_variables, payload.function_mocks
        )
    except Exception as exc:
        raise HTTPException(502, f"Simulated chat failed: {exc}") from exc
    return result


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
    _validate_config_analysis_prompt(payload.config)
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
    _validate_config_analysis_prompt(payload.config)
    now = datetime.now(timezone.utc).isoformat()
    bot_versions.update_one({"_id": _oid(version_id)}, {"$set": {"config": payload.config.model_dump(), "updated_at": now}})
    # Editing a draft's config is the most common "edit a bot" action but only touches the
    # bot_versions doc — bump the parent bots doc too so "sort agents by last updated"
    # (list_bots' .sort("updated_at", -1)) reflects config-only edits, not just
    # rename/publish/unpublish actions that already set this field elsewhere.
    bots.update_one({"_id": _oid(bot_id)}, {"$set": {"updated_at": now}})
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


@router.get("/{bot_id}/metrics")
def bot_metrics(bot_id: str, days: int = 7, _: dict = Depends(require_user)) -> dict:
    """Per-bot metrics for the builder's Metrics tab.

    success_rate_pct definition: "% of calls where status == 'completed'" — there is no
    clean stored pass/fail field on a transcript, so this mirrors the same status-based
    "ended naturally" heuristic already used by analytics.outcome_analytics()
    (backend/routers/analytics.py: `doc.get("call_end_reason") == "natural" or
    status == "completed"`), simplified to just the status check since call_end_reason
    isn't consistently populated across bots. This is a first-pass definition, not a
    guaranteed business-accurate "success" metric.
    """
    if not bots.find_one({"_id": _oid(bot_id)}):
        raise HTTPException(404, "Bot not found")
    if days not in (7, 30):
        days = 7

    now = datetime.now(timezone.utc)
    today_str = now.date().isoformat()
    window_start = now - timedelta(days=days)
    prev_window_start = now - timedelta(days=days * 2)

    def _count_and_success(start, end) -> tuple[int, int]:
        match = {"bot_id": bot_id, "created_at": {"$gte": start.isoformat()}}
        if end is not None:
            match["created_at"]["$lt"] = end.isoformat()
        total = 0
        completed = 0
        for row in transcripts.aggregate([
            {"$match": match},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
            }},
        ]):
            total = row.get("total", 0)
            completed = row.get("completed", 0)
        return total, completed

    total_calls_all_time = transcripts.count_documents({"bot_id": bot_id})
    calls_today = transcripts.count_documents({"bot_id": bot_id, "created_at": {"$gte": today_str}})
    calls_this_week = transcripts.count_documents({"bot_id": bot_id, "created_at": {"$gte": (now - timedelta(days=7)).isoformat()}})
    calls_this_month = transcripts.count_documents({"bot_id": bot_id, "created_at": {"$gte": (now - timedelta(days=30)).isoformat()}})

    window_total, window_completed = _count_and_success(window_start, None)
    prev_total, prev_completed = _count_and_success(prev_window_start, window_start)

    success_rate_pct = round((window_completed / window_total) * 100, 1) if window_total else 0.0
    prev_success_rate_pct = round((prev_completed / prev_total) * 100, 1) if prev_total else 0.0

    def _trend_pct(current: float, previous: float) -> float | None:
        if not previous:
            return None
        return round(((current - previous) / previous) * 100, 1)

    trend_total_calls_pct = _trend_pct(window_total, prev_total)
    trend_success_rate_pct = _trend_pct(success_rate_pct, prev_success_rate_pct)

    avg_duration_sec = 0.0
    for row in transcripts.aggregate([
        {"$match": {"bot_id": bot_id, "created_at": {"$gte": window_start.isoformat()}}},
        {"$group": {"_id": None, "avg_duration_sec": {"$avg": "$call_duration_sec"}}},
    ]):
        avg_duration_sec = round(row.get("avg_duration_sec") or 0, 1)

    # Per-day volume + avg duration over the window, for the two trend charts.
    daily_volume_by_date: dict[str, int] = {}
    daily_duration_by_date: dict[str, float] = {}
    for row in transcripts.aggregate([
        {"$match": {"bot_id": bot_id, "created_at": {"$gte": window_start.isoformat()}}},
        {"$group": {
            "_id": {"$substrCP": ["$created_at", 0, 10]},
            "count": {"$sum": 1},
            "avg_duration_sec": {"$avg": "$call_duration_sec"},
        }},
    ]):
        daily_volume_by_date[row["_id"]] = row["count"]
        daily_duration_by_date[row["_id"]] = round(row.get("avg_duration_sec") or 0, 1)

    daily_volume = []
    daily_avg_duration = []
    for i in range(days):
        d = (window_start + timedelta(days=i)).date().isoformat()
        daily_volume.append({"date": d, "count": daily_volume_by_date.get(d, 0)})
        daily_avg_duration.append({"date": d, "avg_duration_sec": daily_duration_by_date.get(d, 0.0)})

    outcome_breakdown = []
    for row in transcripts.aggregate([
        {"$match": {"bot_id": bot_id, "created_at": {"$gte": window_start.isoformat()}}},
        {"$group": {"_id": {"$ifNull": ["$analysis.call_outcome", "unclassified"]}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]):
        outcome_breakdown.append({"outcome": row["_id"] or "unclassified", "count": row["count"]})

    return {
        "total_calls": total_calls_all_time,
        "calls_today": calls_today,
        "calls_this_week": calls_this_week,
        "calls_this_month": calls_this_month,
        "avg_duration_sec": avg_duration_sec,
        "success_rate_pct": success_rate_pct,
        "trend_vs_previous_pct": {
            "total_calls": trend_total_calls_pct,
            "success_rate": trend_success_rate_pct,
        },
        "daily_volume": daily_volume,
        "daily_avg_duration": daily_avg_duration,
        "outcome_breakdown": outcome_breakdown,
    }


@router.delete("/{bot_id}")
def delete_bot(bot_id: str, user: dict = Depends(require_user)) -> dict:
    """Soft delete only — never removes the bot or its transcripts (audit item 6/22)."""
    result = bots.update_one(
        {"_id": _oid(bot_id)},
        {"$set": {
            "status": "deleted",
            "active_version_id": None,
            "draft_version_id": None,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Bot not found")
    log_audit(user, "delete", "bot", bot_id)
    return {"ok": True}
