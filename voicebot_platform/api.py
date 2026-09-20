from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .audit import RequestLoggingMiddleware
from .call_events import events_for_transcript, search_call_events

from .config import (
    DASHBOARD_ORIGINS,
    LIVEKIT_API_KEY,
    LIVEKIT_API_SECRET,
    LIVEKIT_URL,
    TEST_RECORDING_DIR,
    VOICEBOT_ENV,
    validate_startup_config,
)
from .config_store import (
    attach_test_recording,
    create_bot,
    create_test_session,
    delete_bot,
    duplicate_bot,
    fetch_active_bot_config,
    get_bot,
    get_campaign,
    get_transcript,
    list_bots,
    list_campaigns,
    list_templates,
    publish_version,
    rollback_bot,
    save_draft,
    set_campaign_status,
    unpublish_version,
    search_transcripts,
    export_transcripts_csv,
    get_outcome_analytics,
    get_quality_alerts,
    update_bot_meta,
    update_version,
    upsert_campaign,
)
from .livekit_sessions import LiveKitConfigError, close_webrtc_test_room, create_webrtc_test_room
from .mongo import ensure_indexes, get_client
from .observability import recorder
from .language_settings import (
    delete_language_settings,
    list_language_settings,
    seed_default_language_settings,
    upsert_language_settings,
)
from .option_catalogs import (
    delete_language,
    delete_voice,
    list_languages,
    list_voices,
    seed_default_catalogs,
    upsert_language,
    upsert_voice,
)
from .outcome_catalog import list_outcomes, seed_default_outcomes, update_outcome
from .phrase_library import (
    VALID_CATEGORIES as PHRASE_CATEGORIES,
    create_phrase,
    delete_phrase,
    list_phrases,
    seed_default_phrases,
    update_phrase,
)
from .schemas import (
    CampaignPayload,
    CreateBotPayload,
    LangfuseUpdatePayload,
    PhrasePayload,
    PublishPayload,
    RollbackPayload,
    RuntimeSettingsUpdatePayload,
    SaveDraftPayload,
    TestSessionPayload,
    UpdateBotMetaPayload,
    UpdateVersionPayload,
    assert_object_id,
)
from .platform_settings import (
    get_langfuse_settings,
    get_runtime_settings,
    update_langfuse_settings,
    update_runtime_settings,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_startup_config(strict=VOICEBOT_ENV == "prod")
    try:
        ensure_indexes()
    except Exception as exc:
        logger.warning(f"[STARTUP] index setup skipped: {exc}")
    try:
        seed_default_phrases()
    except Exception as exc:
        # Best-effort; the library reader has its own hardcoded fallback.
        logger.warning(f"[STARTUP] phrase seed skipped: {exc}")
    try:
        seed_default_outcomes()
    except Exception as exc:
        logger.warning(f"[STARTUP] outcome catalog seed skipped: {exc}")
    try:
        seed_default_catalogs()
    except Exception as exc:
        logger.warning(f"[STARTUP] voice/language catalog seed skipped: {exc}")
    try:
        seed_default_language_settings()
    except Exception as exc:
        logger.warning(f"[STARTUP] language settings seed skipped: {exc}")
    yield
    try:
        get_client().close()
    except Exception:
        pass


app = FastAPI(title="AcmeCorp Voice AI Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _recording_extension(content_type: str) -> str:
    if "webm" in content_type:
        return ".webm"
    if "ogg" in content_type:
        return ".ogg"
    if "mp4" in content_type or "mpeg" in content_type:
        return ".mp4"
    return ".webm"


def _safe_room_filename(room_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", room_name).strip(".-")
    if not cleaned or not cleaned.startswith("test-"):
        raise HTTPException(status_code=400, detail="Only dashboard test room recordings can be saved.")
    return cleaned


def current_user(x_jd_user: str | None = Header(default=None)) -> str:
    return x_jd_user or "local-dev"


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness only — always returns ok if the API process is up."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    """Readiness probe — exercises Mongo and reports LiveKit credential state.

    Returns 503 if Mongo is unreachable. LiveKit is informational because the
    runtime can keep accepting platform requests even if LiveKit is down (only
    the test-call endpoint depends on it).
    """
    mongo_ok = False
    mongo_error: str | None = None
    try:
        get_client().admin.command("ping")
        mongo_ok = True
    except Exception as exc:
        mongo_error = str(exc)

    livekit_configured = bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET)
    body = {
        "status": "ok" if mongo_ok else "degraded",
        "mongo": {"ok": mongo_ok, "error": mongo_error},
        "livekit": {"configured": livekit_configured, "url": LIVEKIT_URL or None},
        "langfuse": recorder.status(),
        "config_warnings": validate_startup_config(strict=False),
    }
    if not mongo_ok:
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/api/options/voices")
def voices() -> list[dict[str, Any]]:
    return list_voices()


@app.get("/api/options/languages")
def languages() -> list[dict[str, Any]]:
    return list_languages()


@app.get("/api/library/voices")
def voices_admin_list() -> list[dict[str, Any]]:
    return list_voices(include_disabled=True)


@app.post("/api/library/voices")
def voices_upsert(payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return upsert_voice(payload, current_user(x_jd_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/library/voices/{voice_id}")
def voices_delete(voice_id: str):
    try:
        delete_voice(voice_id)
        return {"status": "deleted"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/library/languages")
def languages_admin_list() -> list[dict[str, Any]]:
    return list_languages(include_disabled=True)


@app.post("/api/library/languages")
def languages_upsert(payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return upsert_language(payload, current_user(x_jd_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/library/languages/{language_id}")
def languages_delete(language_id: str):
    try:
        delete_language(language_id)
        return {"status": "deleted"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/library/language-settings")
def language_settings_list() -> list[dict[str, Any]]:
    return list_language_settings()


@app.post("/api/library/language-settings")
def language_settings_upsert(payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    try:
        return upsert_language_settings(payload, current_user(x_jd_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/library/language-settings/{lang_id}")
def language_settings_delete(lang_id: str):
    try:
        delete_language_settings(lang_id)
        return {"status": "deleted"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/observability/langfuse")
def langfuse_settings() -> dict[str, Any]:
    settings = get_langfuse_settings()
    return {**settings, "runtime_status": recorder.status()}


@app.put("/api/observability/langfuse")
def langfuse_settings_update(payload: LangfuseUpdatePayload, x_jd_user: str | None = Header(default=None)):
    try:
        settings = update_langfuse_settings(payload.model_dump(exclude_none=True), current_user(x_jd_user))
        recorder.invalidate_settings_cache()
        return {**settings, "runtime_status": recorder.status()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/settings/runtime")
def runtime_settings() -> dict[str, Any]:
    return get_runtime_settings()


@app.put("/api/settings/runtime")
def runtime_settings_update(payload: RuntimeSettingsUpdatePayload, x_jd_user: str | None = Header(default=None)):
    return update_runtime_settings(payload.model_dump(exclude_none=True), current_user(x_jd_user))


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
def create_bot_endpoint(payload: CreateBotPayload, x_jd_user: str | None = Header(default=None)):
    try:
        return create_bot(payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_bot_id(bot_id: str) -> str:
    try:
        assert_object_id(bot_id, "bot_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return bot_id


@app.get("/api/bots/{bot_id}")
def bot_detail(bot_id: str):
    _validate_bot_id(bot_id)
    doc = get_bot(bot_id)
    if not doc:
        raise HTTPException(status_code=404, detail="bot_not_found")
    return doc


@app.patch("/api/bots/{bot_id}")
def update_bot(bot_id: str, payload: UpdateBotMetaPayload, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        return update_bot_meta(bot_id, payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/draft")
def draft(bot_id: str, payload: SaveDraftPayload, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        return save_draft(bot_id, payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/bots/{bot_id}/versions/{version_id}")
def update_version_endpoint(
    bot_id: str,
    version_id: str,
    payload: UpdateVersionPayload,
    x_jd_user: str | None = Header(default=None),
):
    _validate_bot_id(bot_id)
    try:
        assert_object_id(version_id, "version_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return update_version(bot_id, version_id, payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/publish")
def publish(
    bot_id: str,
    payload: PublishPayload | None = None,
    x_jd_user: str | None = Header(default=None),
):
    _validate_bot_id(bot_id)
    try:
        return publish_version(
            bot_id,
            (payload.version_id if payload else None),
            current_user(x_jd_user),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/rollback")
def rollback(bot_id: str, payload: RollbackPayload, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        return rollback_bot(bot_id, payload.version_id, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/unpublish")
def unpublish(bot_id: str, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        return unpublish_version(bot_id, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/duplicate")
def duplicate(bot_id: str, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        return duplicate_bot(bot_id, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/api/bots/{bot_id}")
def delete_bot_endpoint(bot_id: str, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        delete_bot(bot_id, current_user(x_jd_user))
        return {"status": "deleted", "bot_id": bot_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/campaigns")
def campaigns():
    return list_campaigns()


@app.post("/api/campaigns")
def campaign_upsert(payload: CampaignPayload, x_jd_user: str | None = Header(default=None)):
    return upsert_campaign(payload.model_dump(exclude_none=True), current_user(x_jd_user))


@app.get("/api/campaigns/{campaign_key}")
def campaign_detail(campaign_key: str):
    doc = get_campaign(campaign_key)
    if not doc:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    return doc


@app.patch("/api/campaigns/{campaign_key}/status")
def campaign_set_status(
    campaign_key: str,
    payload: dict[str, Any],
    x_jd_user: str | None = Header(default=None),
):
    new_status = payload.get("status", "")
    if new_status not in ("active", "paused", "draft", "archived"):
        raise HTTPException(status_code=400, detail="status must be one of: active, paused, draft, archived")
    doc = set_campaign_status(campaign_key, new_status, current_user(x_jd_user))
    if not doc:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    return doc


@app.get("/api/analytics/outcomes")
def analytics_outcomes(
    bot_id: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    hours: int | None = Query(default=None, ge=1, le=720),
):
    """Aggregate call outcome counts. Returns counts by status/outcome for the given window."""
    return get_outcome_analytics(
        bot_id=bot_id,
        campaign_id=campaign_id,
        start_date=start_date,
        end_date=end_date,
        hours=hours,
    )


@app.get("/api/analytics/quality-alerts")
def analytics_quality_alerts(
    hours: int = Query(default=1, ge=1, le=24),
    threshold_pct: float = Query(default=30.0, ge=1.0, le=100.0),
):
    """Return alert if disconnected/error calls exceed threshold_pct in the last N hours."""
    return get_quality_alerts(hours=hours, threshold_pct=threshold_pct)


@app.post("/api/bots/{bot_id}/test-session")
def test_session(bot_id: str, payload: TestSessionPayload, x_jd_user: str | None = Header(default=None)):
    _validate_bot_id(bot_id)
    try:
        return create_test_session(bot_id, payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/bots/{bot_id}/webrtc-test-session")
async def webrtc_test_session(
    bot_id: str,
    payload: TestSessionPayload,
    x_jd_user: str | None = Header(default=None),
):
    _validate_bot_id(bot_id)
    try:
        session = create_test_session(
            bot_id, payload.model_dump(exclude_none=True), current_user(x_jd_user)
        )
        return await create_webrtc_test_room(
            session["room_metadata"],
            current_user(x_jd_user),
            agent_name_override=payload.test_worker_agent_name,
        )
    except LiveKitConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not create LiveKit test room. Check LIVEKIT_URL/API credentials and agent worker availability. Raw error: {exc}",
        ) from exc


@app.post("/api/webrtc-test-sessions/{room_name}/close")
async def webrtc_test_session_close(room_name: str):
    try:
        return await close_webrtc_test_room(room_name)
    except LiveKitConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not close LiveKit test room. The browser can still disconnect locally. Raw error: {exc}",
        ) from exc


@app.post("/api/test-recordings/{room_name}")
async def upload_test_recording(room_name: str, request: Request):
    safe_room = _safe_room_filename(room_name)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Recording upload was empty.")
    content_type = request.headers.get("content-type", "audio/webm")
    extension = _recording_extension(content_type)
    TEST_RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    saved_at = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    path = TEST_RECORDING_DIR / f"{safe_room}-{saved_at}{extension}"
    path.write_bytes(body)
    recording_url = f"/api/test-recordings/{path.name}"
    try:
        matched = attach_test_recording(room_name, recording_url, str(path))
        for _attempt in range(8):
            if matched:
                break
            await asyncio.sleep(1)
            matched = attach_test_recording(room_name, recording_url, str(path))
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "room_name": room_name,
        "recording_url": recording_url,
        "recording_path": str(path),
        "transcripts_updated": matched,
    }


@app.get("/api/test-recordings/{filename}")
def get_test_recording(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="Invalid recording filename.")
    path = TEST_RECORDING_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="recording_not_found")
    return FileResponse(path, media_type="audio/webm", filename=safe_name)


@app.get("/api/test-recordings/by-room/{room_name}")
def get_test_recording_by_room(room_name: str):
    safe_room = _safe_room_filename(room_name)
    if not TEST_RECORDING_DIR.exists():
        raise HTTPException(status_code=404, detail="recording_not_found")
    matches = sorted(
        TEST_RECORDING_DIR.glob(f"{safe_room}-*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise HTTPException(status_code=404, detail="recording_not_found")
    path = matches[0]
    return {
        "room_name": room_name,
        "recording_url": f"/api/test-recordings/{path.name}",
        "recording_path": str(path),
        "recording_source": "dashboard_test_local",
    }


@app.get("/api/transcripts")
def transcripts(
    bot_id: str | None = Query(default=None),
    bot_version_id: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    lead_id: str | None = Query(default=None),
    call_id: str | None = Query(default=None),
    assistant_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    mobile: str | None = Query(default=None),
    text: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
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
            "outcome": outcome,
            "mobile": mobile,
            "text": text,
            "start_date": start_date,
            "end_date": end_date,
        },
        limit=limit,
        skip=skip,
    )


@app.get("/api/call-events")
def call_events(
    call_id: str | None = Query(default=None),
    room_name: str | None = Query(default=None),
    bot_id: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    lead_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
):
    try:
        return search_call_events(
            {
                "call_id": call_id,
                "room_name": room_name,
                "bot_id": bot_id,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "event_type": event_type,
                "severity": severity,
                "start_date": start_date,
                "end_date": end_date,
            },
            limit=limit,
            skip=skip,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# NOTE: export.csv MUST be registered before the {transcript_id} routes below.
# FastAPI resolves routes in registration order — a static path segment ("export.csv")
# would otherwise be swallowed by the dynamic {transcript_id} pattern.
@app.get("/api/transcripts/export.csv")
def transcripts_export(
    bot_id: str | None = Query(default=None),
    bot_version_id: str | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    rows = export_transcripts_csv(
        filters={
            "bot_id": bot_id,
            "bot_version_id": bot_version_id,
            "campaign_id": campaign_id,
            "status": status,
            "outcome": outcome,
            "start_date": start_date,
            "end_date": end_date,
        },
        limit=limit,
    )
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("no_data\n")
    output.seek(0)
    filename = f"transcripts_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/transcripts/{transcript_id}/events")
def transcript_events(transcript_id: str, limit: int = Query(default=200, ge=1, le=500)):
    try:
        assert_object_id(transcript_id, "transcript_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = get_transcript(transcript_id)
    if not doc:
        raise HTTPException(status_code=404, detail="transcript_not_found")
    return events_for_transcript(doc, limit=limit)


@app.get("/api/transcripts/{transcript_id}")
def transcript_detail(transcript_id: str):
    try:
        assert_object_id(transcript_id, "transcript_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = get_transcript(transcript_id)
    if not doc:
        raise HTTPException(status_code=404, detail="transcript_not_found")
    return doc


@app.get("/api/library/phrases")
def phrases_list(category: str | None = Query(default=None)):
    try:
        return list_phrases(category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/library/phrase-categories")
def phrase_categories() -> list[str]:
    return sorted(PHRASE_CATEGORIES)


@app.post("/api/library/phrases")
def phrases_create(payload: PhrasePayload, x_jd_user: str | None = Header(default=None)):
    try:
        return create_phrase(payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/library/phrases/{phrase_id}")
def phrases_update(
    phrase_id: str, payload: PhrasePayload, x_jd_user: str | None = Header(default=None)
):
    try:
        assert_object_id(phrase_id, "phrase_id")
        return update_phrase(phrase_id, payload.model_dump(exclude_none=True), current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/library/phrases/{phrase_id}")
def phrases_delete(phrase_id: str):
    try:
        assert_object_id(phrase_id, "phrase_id")
        delete_phrase(phrase_id)
        return {"status": "deleted"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/library/outcomes")
def outcomes_list():
    return list_outcomes()


@app.put("/api/library/outcomes/{key}")
def outcomes_update(key: str, payload: dict[str, Any], x_jd_user: str | None = Header(default=None)):
    # Keys are wired into downstream callback logic (callback.py upgrade rules)
    # so we accept the key from the URL but only update description/display_label.
    try:
        return update_outcome(key, payload, current_user(x_jd_user))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
