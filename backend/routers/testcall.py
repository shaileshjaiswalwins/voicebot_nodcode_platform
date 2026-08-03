import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from livekit import api as lkapi

from ..auth import require_user
from ..models import TestCallStartRequest, TestCallStopRequest

router = APIRouter(prefix="/api/testcall", tags=["testcall"])


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
    room_name = f"test-{uuid.uuid4().hex[:12]}"
    agent_name = payload.test_worker_agent_name or os.getenv("LIVEKIT_AGENT_NAME", "voice-bot-justdial-live-2")

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

    token = (
        lkapi.AccessToken(os.getenv("LIVEKIT_API_KEY", ""), os.getenv("LIVEKIT_API_SECRET", ""))
        .with_identity(f"pm-tester-{user.get('sub', 'admin')}")
        .with_grants(lkapi.VideoGrants(room_join=True, room=room_name))
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
