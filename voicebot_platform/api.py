from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config_store import (
    LANGUAGE_OPTIONS,
    VOICE_OPTIONS,
    create_bot,
    create_test_session,
    duplicate_bot,
    fetch_active_bot_config,
    get_bot,
    get_transcript,
    list_bots,
    list_campaigns,
    list_templates,
    publish_version,
    rollback_bot,
    save_draft,
    search_transcripts,
    upsert_campaign,
)
from .livekit_sessions import LiveKitConfigError, create_webrtc_test_room
from .mongo import ensure_indexes
from .observability import recorder
from .platform_settings import get_langfuse_settings, update_langfuse_settings

app = FastAPI(title="JustDial Voice AI Platform", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_indexes()


def current_user(x_jd_user: str | None = Header(default=None)) -> str:
    return x_jd_user or "local-dev"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/options/voices")
def voices() -> list[dict[str, Any]]:
    return VOICE_OPTIONS


@app.get("/api/options/languages")
def languages() -> list[dict[str, Any]]:
    return LANGUAGE_OPTIONS


@app.get("/api/observability/langfuse")
def langfuse_settings() -> dict[str, Any]:
    settings = get_langfuse_settings()
    return {**settings, "runtime_status": recorder.status()}


@app.put("/api/observability/langfuse")
def langfuse_settings_update(payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        settings = update_langfuse_settings(payload, current_user(x_jd_user))
        return {**settings, "runtime_status": recorder.status()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/templates")
def templates() -> list[dict[str, Any]]:
    return list_templates()


@app.get("/api/runtime/config/{assistant_id}")
def runtime_config(assistant_id: str) -> dict[str, Any]:
    config = fetch_active_bot_config(assistant_id)
    if not config:
        raise HTTPException(status_code=404, detail="active_config_not_found")
    return config


@app.get("/api/bots")
def bots() -> list[dict[str, Any]]:
    return list_bots()


@app.post("/api/bots")
def create_bot_endpoint(payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return create_bot(payload, current_user(x_jd_user))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/bots/{bot_id}")
def bot_detail(bot_id: str):
    doc = get_bot(bot_id)
    if not doc:
        raise HTTPException(status_code=404, detail="bot_not_found")
    return doc


@app.post("/api/bots/{bot_id}/draft")
def draft(bot_id: str, payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return save_draft(bot_id, payload, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/publish")
def publish(bot_id: str, payload: dict[str, Any] | None = None, x_jd_user: str | None = Header(default=None)):
    try:
        return publish_version(bot_id, (payload or {}).get("version_id"), current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/rollback")
def rollback(bot_id: str, payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return rollback_bot(bot_id, payload["version_id"], current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/duplicate")
def duplicate(bot_id: str, x_jd_user: str | None = Header(default=None)):
    try:
        return duplicate_bot(bot_id, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/campaigns")
def campaigns():
    return list_campaigns()


@app.post("/api/campaigns")
def campaign_upsert(payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    return upsert_campaign(payload, current_user(x_jd_user))


@app.post("/api/bots/{bot_id}/test-session")
def test_session(bot_id: str, payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return create_test_session(bot_id, payload, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/webrtc-test-session")
async def webrtc_test_session(
    bot_id: str,
    payload: dict[str, Any],
    x_jd_user: str | None = Header(default=None),
):
    try:
        session = create_test_session(bot_id, payload, current_user(x_jd_user))
        return await create_webrtc_test_room(session["room_metadata"], current_user(x_jd_user))
    except LiveKitConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not create LiveKit test room. Check LIVEKIT_URL/API credentials and agent worker availability. Raw error: {exc}",
        ) from exc


@app.get("/api/transcripts")
def transcripts(
    bot_id: str | None = Query(default=None),
    bot_version_id: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    lead_id: str | None = Query(default=None),
    call_id: str | None = Query(default=None),
    assistant_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    mobile: str | None = Query(default=None),
    text: str | None = Query(default=None),
):
    return search_transcripts(
        {
            "bot_id": bot_id,
            "bot_version_id": bot_version_id,
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "call_id": call_id,
            "assistant_id": assistant_id,
            "status": status,
            "mobile": mobile,
            "text": text,
        }
    )


@app.get("/api/transcripts/{transcript_id}")
def transcript_detail(transcript_id: str):
    doc = get_transcript(transcript_id)
    if not doc:
        raise HTTPException(status_code=404, detail="transcript_not_found")
    return doc
