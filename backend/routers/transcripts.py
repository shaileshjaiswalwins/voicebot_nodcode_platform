from datetime import datetime, timedelta, timezone

from fastapi.responses import StreamingResponse
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_user
from ..db import transcripts

router = APIRouter(tags=["transcripts"])


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


def _build_filter(bot_id: str = "", campaign_id: str = "", status: str = "", text: str = "") -> dict:
    query: dict = {}
    if bot_id:
        query["bot_id"] = bot_id
    if campaign_id:
        query["campaign_id"] = campaign_id
    if status:
        query["status"] = status
    if text:
        query["$or"] = [
            {"lead_id": {"$regex": text, "$options": "i"}},
            {"call_id": {"$regex": text, "$options": "i"}},
        ]
    return query


@router.get("/api/transcripts")
def list_transcripts(
    bot_id: str = Query(""),
    campaign_id: str = Query(""),
    status: str = Query(""),
    text: str = Query(""),
    limit: int = Query(200, le=1000),
    _: dict = Depends(require_user),
) -> list[dict]:
    query = _build_filter(bot_id, campaign_id, status, text)
    docs = transcripts.find(query).sort("created_at", -1).limit(limit)
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


@router.get("/api/transcripts/export.csv")
def export_csv(
    bot_id: str = Query(""),
    campaign_id: str = Query(""),
    status: str = Query(""),
    outcome: str = Query(""),
    start_date: str = Query(""),
    end_date: str = Query(""),
    text: str = Query(""),
    _: dict = Depends(require_user),
) -> StreamingResponse:
    query = _build_filter(bot_id, campaign_id, status, text)
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
