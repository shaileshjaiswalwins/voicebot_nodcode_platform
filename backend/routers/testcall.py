import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from livekit import api as lkapi

from ..auth import bot_owner_filter, require_user
from ..db import bots, db
from ..models import TestCallStartRequest, TestCallStopRequest

router = APIRouter(prefix="/api/testcall", tags=["testcall"])

_log = logging.getLogger("voicebot_admin")

dispatch_failures = db["tbl_ai_vb_dispatch_failures"]

# How long LiveKit gets to assign a registered worker to a freshly-created dispatch
# before we consider it stuck. A healthy worker picks up a job in well under a second —
# this only needs to be generous enough to not false-positive on a slow network hop.
_DISPATCH_ASSIGN_TIMEOUT_S = 10

# The worker that serves dashboard Test Calls — bot_dev_param.py registers under this exact
# name (see its _AGENT_NAME). LiveKit matches a job to a worker on this string alone, so when
# the two sides disagree the failure is silent: the dispatch is accepted, nothing claims it,
# and the caller waits on "waiting for bot to join" with no error logged anywhere. This used
# to read LIVEKIT_AGENT_NAME, which meant the pairing depended on two processes happening to
# load the same value from .env — and a machine whose env named a different worker got no bot
# at all. Fixed here so clicking Test Call always reaches the worker that handles test calls.
TESTCALL_AGENT_NAME = "voice-bot-acmecorp-dashboard-test"


async def _watch_dispatch_assignment(room_name: str, agent_name: str, bot_id: str) -> None:
    """Distinct failure mode from bot.py's fallback-events (which cover a *resolved*
    call running on the wrong config) and worker-health (which covers a worker process
    being down) — this is "a worker was registered and reachable, but LiveKit never
    actually handed this specific dispatch to any worker at all," which is exactly what
    happened during the Jul 29 incident and left the caller on an infinite "waiting for
    bot to join" with zero record of why. Best-effort: never raises into the caller."""
    await asyncio.sleep(_DISPATCH_ASSIGN_TIMEOUT_S)
    try:
        client = _lk_client()
        try:
            dispatches = await client.agent_dispatch.list_dispatch(room_name=room_name)
        finally:
            await client.aclose()
        assigned = any(d.state.jobs for d in dispatches if d.agent_name == agent_name)
        if not assigned:
            dispatch_failures.insert_one({
                "room_name": room_name,
                "agent_name": agent_name,
                "bot_id": bot_id,
                "timeout_seconds": _DISPATCH_ASSIGN_TIMEOUT_S,
                "created_at": datetime.now(timezone.utc),
            })
            _log.warning(
                f"[DISPATCH] room={room_name} agent={agent_name!r} — no worker was ever "
                f"assigned this job within {_DISPATCH_ASSIGN_TIMEOUT_S}s"
            )
    except Exception as exc:
        _log.warning(f"[DISPATCH] assignment watch failed (non-fatal): {exc}")


def _lk_client() -> lkapi.LiveKitAPI:
    url = os.getenv("LIVEKIT_API_URL", "")
    key = os.getenv("LIVEKIT_API_KEY", "")
    secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not (url and key and secret):
        raise HTTPException(500, "LiveKit credentials are not configured on this backend (.env)")
    return lkapi.LiveKitAPI(url, key, secret)


@router.post("/start")
async def start_test_call(payload: TestCallStartRequest, user: dict = Depends(require_user)) -> dict:
    """Creates a LiveKit room and dispatches the configured worker agent to it, carrying
    the test metadata (lead info + pinned bot version) as room metadata — this is the
    'version-aware test call' mechanism carried forward from the ai_voice_bot_management
    audit: test_bot_version_id lets you test any draft/historical version without publishing.
    """
    try:
        bot_oid = ObjectId(payload.bot_id)
    except Exception as exc:
        raise HTTPException(404, "Bot not found") from exc
    if not bots.find_one({"_id": bot_oid, **bot_owner_filter(user)}):
        raise HTTPException(404, "Bot not found")

    room_name = f"test-{uuid.uuid4().hex[:12]}"
    agent_name = payload.test_worker_agent_name or TESTCALL_AGENT_NAME

    metadata = json.dumps(
        {
            "bot_id": payload.bot_id,
            "test_bot_version_id": payload.test_bot_version_id,
            "campaign_id": payload.campaign_id,
            "lead_id": payload.lead_id,
            "call_id": payload.call_id or room_name,
            "mobile": payload.mobile,
            "srchterm": payload.srchterm,
            "buyer_name": payload.buyer_name,
            "city": payload.city,
            "custom_lead_json": payload.custom_lead_json,
            "pre_call_params": payload.pre_call_params,
            "dynamic_variables": payload.dynamic_variables,
            "test_mode": True,
        }
    )

    client = _lk_client()
    try:
        await client.room.create_room(lkapi.CreateRoomRequest(name=room_name, metadata=metadata))
        await client.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(room=room_name, agent_name=agent_name, metadata=metadata)
        )
    except Exception as exc:
        raise HTTPException(502, f"Could not reach LiveKit server: {exc}") from exc
    finally:
        await client.aclose()

    asyncio.create_task(_watch_dispatch_assignment(room_name, agent_name, payload.bot_id))

    token = (
        lkapi.AccessToken(os.getenv("LIVEKIT_API_KEY", ""), os.getenv("LIVEKIT_API_SECRET", ""))
        .with_identity(f"pm-tester-{user.get('sub', 'admin')}")
        .with_grants(lkapi.VideoGrants(room_join=True, room=room_name))
        .with_attributes(
            {
                "bot_id": str(payload.bot_id or ""),
                "campaign_id": str(payload.campaign_id or ""),
                "city": str(payload.city or ""),
                "test_mode": "true",
            }
        )
        .to_jwt()
    )

    return {
        "room_name": room_name,
        "livekit_token": token,
        "livekit_url": os.getenv("LIVEKIT_BROWSER_URL", os.getenv("LIVEKIT_URL", "")),
    }


@router.post("/stop")
async def stop_test_call(payload: TestCallStopRequest, _: dict = Depends(require_user)) -> dict:
    """Explicit server-side room close — a real bug fix carried forward from the
    ai_voice_bot_management audit: a browser disconnect alone left the agent running and
    the room undeleted."""
    client = _lk_client()
    try:
        await client.room.delete_room(lkapi.DeleteRoomRequest(room=payload.room_name))
    except Exception as exc:
        raise HTTPException(502, f"Could not reach LiveKit server: {exc}") from exc
    finally:
        await client.aclose()
    return {"ok": True}
