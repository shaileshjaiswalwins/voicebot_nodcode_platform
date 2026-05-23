from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from livekit.api import (
    AccessToken,
    CreateRoomRequest,
    LiveKitAPI,
    RoomAgentDispatch,
    VideoGrants,
)

from .config import (
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
)
from .platform_settings import get_runtime_settings


class LiveKitConfigError(RuntimeError):
    pass


def _require_livekit_config() -> None:
    missing = [
        name
        for name, value in {
            "LIVEKIT_URL": LIVEKIT_URL,
            "LIVEKIT_API_KEY": LIVEKIT_API_KEY,
            "LIVEKIT_API_SECRET": LIVEKIT_API_SECRET,
        }.items()
        if not value
    ]
    if missing:
        raise LiveKitConfigError(
            "LiveKit is not configured. Set " + ", ".join(missing) + " in .env."
        )


async def create_webrtc_test_room(room_metadata: dict, user: str) -> dict:
    _require_livekit_config()
    loop = asyncio.get_running_loop()
    runtime = await loop.run_in_executor(None, get_runtime_settings)
    livekit_api_url = runtime.get("livekit_api_url") or LIVEKIT_URL
    livekit_browser_url = runtime.get("livekit_browser_url") or LIVEKIT_URL
    livekit_agent_name = runtime.get("livekit_agent_name") or "voice-bot-justdial"

    room_name = f"test-{room_metadata['assistant_id'][:8]}-{uuid4().hex[:10]}"
    metadata = {**room_metadata, "room_name": room_name, "test_session": True}
    metadata_json = json.dumps(metadata, ensure_ascii=False)

    livekit = LiveKitAPI(
        url=livekit_api_url,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    try:
        await livekit.room.create_room(
            CreateRoomRequest(
                name=room_name,
                metadata=metadata_json,
                empty_timeout=300,
                departure_timeout=30,
                max_participants=4,
                agents=[
                    RoomAgentDispatch(
                        agent_name=livekit_agent_name,
                        metadata=metadata_json,
                    )
                ],
            )
        )
    finally:
        await livekit.aclose()

    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(f"tester-{uuid4().hex[:10]}")
        .with_name(user)
        .with_metadata(json.dumps({"source": "dashboard_test", "user": user}))
        .with_ttl(timedelta(minutes=30))
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    return {
        "room_name": room_name,
        "livekit_url": livekit_browser_url,
        "token": token,
        "metadata": metadata,
        "agent_name": livekit_agent_name,
        "expires_in_sec": 1800,
        "next_steps": [
            "Browser connects to LiveKit with token.",
            "Browser publishes microphone audio.",
            "LiveKit dispatches the voice-bot agent into the room.",
            "The agent reads room metadata and loads the published bot config.",
        ],
    }
