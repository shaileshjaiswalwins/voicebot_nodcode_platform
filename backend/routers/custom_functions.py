"""CRUD + Test for per-bot custom functions (user-configured API calls).

Stored in tbl_ai_vb_custom_functions, one doc per function, keyed by bot_id (the bot's
ObjectId as a hex string — the same form agent_resolver.py resolves at call time). These
are NOT part of the versioned bot config: edits go live immediately, independent of the
bot's draft/publish lifecycle.
"""

import json
import re
import time

import aiohttp
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..audit import log_audit
from ..auth import require_user
from ..db import bots, custom_functions
from ..models import (
    CustomFunctionCreate,
    CustomFunctionTestRequest,
    CustomFunctionUpdate,
)

router = APIRouter(prefix="/api/bots", tags=["custom_functions"])

_TOKEN_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as exc:
        raise HTTPException(404, "Not found") from exc


def _require_bot(bot_id: str) -> dict:
    bot = bots.find_one({"_id": _oid(bot_id)})
    if not bot or bot.get("status") == "deleted":
        raise HTTPException(404, "Bot not found")
    return bot


def _serialize(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


def _substitute(text: str | None, ctx: dict) -> str | None:
    """Replace {{token}} occurrences in a template string with ctx values. Unknown tokens
    are left verbatim so the user can see what didn't resolve when testing."""
    if not text:
        return text
    return _TOKEN_RE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), text)


def _extract_path(obj, path: str):
    """Walk a dotted path with numeric list indices, e.g. 'results.data.0.buyer_name'.
    Returns None if any segment is missing rather than raising."""
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


async def _perform_request(
    method: str,
    url: str,
    headers: dict,
    params: dict,
    body: str | None,
    body_format: str,
    timeout_ms: int,
) -> tuple[int | None, float, object, str | None]:
    """Execute one HTTP call. Returns (status, latency_ms, parsed_body, error).

    Isolated as a module-level function so tests can monkeypatch it without real network.
    """
    timeout = aiohttp.ClientTimeout(total=max(timeout_ms, 1) / 1000)
    kwargs: dict = {"headers": headers or None, "params": params or None, "timeout": timeout}
    if body and method != "GET":
        parsed = None
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            parsed = None
        if body_format == "json":
            kwargs["json"] = parsed if parsed is not None else None
            if parsed is None:
                kwargs.pop("json")
                kwargs["data"] = body
        else:  # form
            kwargs["data"] = parsed if isinstance(parsed, dict) else body

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, **kwargs) as resp:
                try:
                    parsed_body = await resp.json(content_type=None)
                except Exception:
                    parsed_body = await resp.text()
                latency = (time.monotonic() - start) * 1000
                return resp.status, latency, parsed_body, None
    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        return None, latency, None, str(exc)


@router.get("/{bot_id}/custom-functions")
def list_custom_functions(bot_id: str, _: dict = Depends(require_user)) -> list[dict]:
    _require_bot(bot_id)
    return [_serialize(f) for f in custom_functions.find({"bot_id": bot_id})]


@router.post("/{bot_id}/custom-functions")
def create_custom_function(
    bot_id: str, payload: CustomFunctionCreate, user: dict = Depends(require_user)
) -> dict:
    _require_bot(bot_id)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc = {
        **payload.model_dump(),
        "bot_id": bot_id,
        "created_by": user.get("sub"),
        "created_at": now,
        "updated_at": now,
    }
    inserted_id = custom_functions.insert_one(doc).inserted_id
    doc["_id"] = inserted_id
    log_audit(user, "create", "custom_function", str(inserted_id), {"bot_id": bot_id, "name": payload.name})
    return _serialize(doc)


@router.get("/{bot_id}/custom-functions/{fn_id}")
def get_custom_function(bot_id: str, fn_id: str, _: dict = Depends(require_user)) -> dict:
    fn = custom_functions.find_one({"_id": _oid(fn_id), "bot_id": bot_id})
    if not fn:
        raise HTTPException(404, "Custom function not found")
    return _serialize(fn)


@router.put("/{bot_id}/custom-functions/{fn_id}")
def update_custom_function(
    bot_id: str, fn_id: str, payload: CustomFunctionUpdate, user: dict = Depends(require_user)
) -> dict:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result = custom_functions.find_one_and_update(
        {"_id": _oid(fn_id), "bot_id": bot_id},
        {"$set": {**payload.model_dump(), "updated_at": now}},
        return_document=True,
    )
    if not result:
        raise HTTPException(404, "Custom function not found")
    log_audit(user, "update", "custom_function", fn_id, {"bot_id": bot_id})
    return _serialize(result)


@router.delete("/{bot_id}/custom-functions/{fn_id}")
def delete_custom_function(bot_id: str, fn_id: str, user: dict = Depends(require_user)) -> dict:
    result = custom_functions.delete_one({"_id": _oid(fn_id), "bot_id": bot_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Custom function not found")
    log_audit(user, "delete", "custom_function", fn_id, {"bot_id": bot_id})
    return {"ok": True}


@router.post("/{bot_id}/custom-functions/test")
async def test_custom_function(
    bot_id: str, payload: CustomFunctionTestRequest, _: dict = Depends(require_user)
) -> dict:
    """Execute the function's request server-side with sample_context filled in, and report
    the raw response alongside which {{variables}} its response_mappings resolved to."""
    _require_bot(bot_id)
    ctx = payload.sample_context or {}
    url = _substitute(payload.url, ctx) or ""
    headers = {k: _substitute(v, ctx) for k, v in payload.headers.items()}
    params = {k: _substitute(v, ctx) for k, v in payload.query_params.items()}
    body = _substitute(payload.body, ctx)

    status, latency, response_body, error = await _perform_request(
        payload.method, url, headers, params, body, payload.body_format, payload.timeout_ms
    )

    extracted: dict = {}
    if isinstance(response_body, (dict, list)):
        for m in payload.response_mappings:
            extracted[m.variable] = _extract_path(response_body, m.path)

    return {
        "status": status,
        "latency_ms": round(latency, 1),
        "response_body": response_body,
        "extracted": extracted,
        "error": error,
    }
