import asyncio
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi.responses import FileResponse, StreamingResponse
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import require_user
from ..db import transcripts

router = APIRouter(tags=["transcripts"])

# Browser-recorded (mic + bot audio mixed, webm) dashboard Test Call recordings.
# This restores the local-recording feature from the pre-refactor voicebot_platform/api.py
# (commits "Record dashboard test calls" / "Add local test recording storage") that got
# dropped when that module was replaced by backend/routers/*. Same default path.
TEST_RECORDINGS_DIR = os.path.expanduser(
    os.getenv("VOICEBOT_TEST_RECORDING_DIR", "~/Documents/voicebot_test_recordings")
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
        raise HTTPException(400, "Only dashboard test room recordings can be saved.")
    return cleaned


def _oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception as exc:
        raise HTTPException(404, "Transcript not found") from exc


def _serialize(doc: dict) -> dict:
    doc["_id"] = str(doc["_id"])
    created = doc.get("created_at")
    if hasattr(created, "isoformat"):
        doc["created_at"] = created.isoformat()
    return doc


def _build_filter(bot_id: str = "", campaign_id: str = "", status: str = "", text: str = "", source: str = "") -> dict:
    query: dict = {}
    if bot_id:
        query["bot_id"] = bot_id
    if campaign_id:
        query["campaign_id"] = campaign_id
    if status:
        query["status"] = status
    and_clauses: list[dict] = []
    if source == "web_test":
        query["source"] = "web_test"
    elif source == "batch":
        # Transcripts written before this field existed have no "source" key at all —
        # treat those as "batch" (the pre-existing majority case: campaign/SIP calls)
        # rather than silently excluding them from either tab.
        and_clauses.append({"$or": [{"source": "batch"}, {"source": {"$exists": False}}]})
    if text:
        escaped = re.escape(text)
        and_clauses.append({"$or": [
            {"lead_id": {"$regex": escaped, "$options": "i"}},
            {"call_id": {"$regex": escaped, "$options": "i"}},
        ]})
    if and_clauses:
        query["$and"] = and_clauses
    return query


@router.get("/api/transcripts")
def list_transcripts(
    bot_id: str = Query(""),
    campaign_id: str = Query(""),
    status: str = Query(""),
    text: str = Query(""),
    source: str = Query(""),
    before: str = Query(""),  # ISO created_at cursor — return docs strictly older than this
    limit: int = Query(50, le=200),
    _: dict = Depends(require_user),
) -> list[dict]:
    query = _build_filter(bot_id, campaign_id, status, text, source)
    if before:
        try:
            query["created_at"] = {"$lt": datetime.fromisoformat(before)}
        except ValueError:
            raise HTTPException(400, "Invalid 'before' cursor") from None
    # config_snapshot (a full copy of the bot config, ~20-40KB) and call_events are never
    # read by the frontend from this response — call_events is fetched separately via
    # GET /api/transcripts/{id}/events, and config_snapshot has no reader anywhere.
    # transcript/muted_transcript (every turn of the call) are the actual bulk of a
    # document's size but the list view only ever shows a turn *count* per row — the
    # detail panel re-fetches the full document via GET /api/transcripts/{id} on select,
    # so dropping them here doesn't lose anything the list itself uses.
    pipeline = [
        {"$match": query},
        {"$sort": {"created_at": -1}},
        {"$limit": limit},
        {"$addFields": {"transcript_count": {"$size": {"$ifNull": ["$transcript", []]}}}},
        {"$project": {"call_events": 0, "config_snapshot": 0, "transcript": 0, "muted_transcript": 0}},
    ]
    docs = transcripts.aggregate(pipeline)
    return [_serialize(d) for d in docs]


@router.get("/api/transcripts/{transcript_id}/events")
def list_call_events(transcript_id: str, _: dict = Depends(require_user)) -> list[dict]:
    doc = transcripts.find_one({"_id": _oid(transcript_id)})
    if not doc:
        return []
    return doc.get("call_events") or []


@router.get("/api/transcripts/recording-lookup/{call_id}")
def recording_lookup(call_id: str, _: dict = Depends(require_user)) -> dict:
    doc = transcripts.find_one({"call_id": call_id})
    if not doc:
        return {}
    return {"recording_url": doc.get("recording_url", ""), "recording_source": doc.get("recording_source", "")}


@router.post("/api/transcripts/recordings/{room_name}")
async def upload_test_recording(room_name: str, request: Request, _: dict = Depends(require_user)) -> dict:
    safe_room = _safe_room_filename(room_name)
    body = await request.body()
    if not body:
        raise HTTPException(400, "Recording upload was empty.")
    content_type = request.headers.get("content-type", "audio/webm")
    extension = _recording_extension(content_type)
    os.makedirs(TEST_RECORDINGS_DIR, exist_ok=True)
    saved_at = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"{safe_room}-{saved_at}{extension}"
    path = os.path.join(TEST_RECORDINGS_DIR, filename)
    with open(path, "wb") as f:
        f.write(body)
    recording_url = f"/api/transcripts/recordings/{filename}"
    # The transcript doc is written by the bot worker after the call ends, which can
    # race this upload (browser stops recording on disconnect, roughly when the worker
    # is also saving) — retry briefly rather than losing the attachment on a near-miss.
    matched = False
    for _attempt in range(8):
        result = transcripts.update_one(
            {"room_name": room_name},
            {"$set": {"recording_url": recording_url, "recording_source": "dashboard_test_local"}},
        )
        matched = result.matched_count > 0
        if matched:
            break
        await asyncio.sleep(1)
    return {
        "room_name": room_name,
        "recording_url": recording_url,
        "transcripts_updated": matched,
    }


@router.get("/api/transcripts/recordings/{filename}")
def get_recording(filename: str, _: dict = Depends(require_user)) -> FileResponse:
    # os.path.basename strips any directory components — filename can only ever
    # resolve to a direct child of TEST_RECORDINGS_DIR, no path traversal.
    safe_name = os.path.basename(filename)
    path = os.path.join(TEST_RECORDINGS_DIR, safe_name)
    if not os.path.isfile(path):
        raise HTTPException(404, "Recording not found")
    return FileResponse(path, media_type="audio/webm", filename=safe_name)


@router.get("/api/transcripts/recordings/by-room/{room_name}")
def get_recording_by_room(room_name: str, _: dict = Depends(require_user)) -> dict:
    safe_room = _safe_room_filename(room_name)
    if not os.path.isdir(TEST_RECORDINGS_DIR):
        raise HTTPException(404, "recording_not_found")
    matches = sorted(
        (f for f in os.listdir(TEST_RECORDINGS_DIR) if f.startswith(f"{safe_room}-")),
        key=lambda name: os.path.getmtime(os.path.join(TEST_RECORDINGS_DIR, name)),
        reverse=True,
    )
    if not matches:
        raise HTTPException(404, "recording_not_found")
    return {
        "room_name": room_name,
        "recording_url": f"/api/transcripts/recordings/{matches[0]}",
        "recording_source": "dashboard_test_local",
    }


@router.get("/api/transcripts/export.csv")
def export_csv(
    bot_id: str = Query(""),
    campaign_id: str = Query(""),
    status: str = Query(""),
    outcome: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
    text: str = Query(""),
    source: str = Query(""),
    _: dict = Depends(require_user),
) -> StreamingResponse:
    query = _build_filter(bot_id, campaign_id, status, text, source)
    if outcome:
        query["analysis.call_outcome"] = outcome
    if start_date or end_date:
        date_filter = {}
        if start_date:
            date_filter["$gte"] = datetime.fromisoformat(start_date)
        if end_date:
            date_filter["$lte"] = datetime.fromisoformat(end_date) + timedelta(days=1)
        query["created_at"] = date_filter

    def rows():
        yield "call_id,lead_id,status,call_outcome,call_duration_sec,created_at\n"
        for doc in transcripts.find(query).sort("created_at", -1).limit(5000):
            analysis = doc.get("analysis") or {}
            created = doc.get("created_at")
            created_str = created.isoformat() if hasattr(created, "isoformat") else str(created or "")
            yield (
                f"{doc.get('call_id', '')},{doc.get('lead_id', '')},{doc.get('status', '')},"
                f"{analysis.get('call_outcome', '')},{doc.get('call_duration_sec', '')},{created_str}\n"
            )

    return StreamingResponse(rows(), media_type="text/csv")


# Must be registered after every other literal /api/transcripts/<segment> route above
# (export.csv, recording-lookup/*, recordings/*) — FastAPI/Starlette matches routes in
# registration order, and this single dynamic segment would otherwise shadow all of them.
@router.get("/api/transcripts/{transcript_id}")
def get_transcript(transcript_id: str, _: dict = Depends(require_user)) -> dict:
    """Full document for the detail panel — the list endpoint above omits transcript/
    muted_transcript to keep the list payload light, so the detail view re-fetches here
    on selection instead of relying on data already being in the list response."""
    doc = transcripts.find_one({"_id": _oid(transcript_id)}, {"config_snapshot": 0})
    if not doc:
        raise HTTPException(404, "Transcript not found")
    return _serialize(doc)
