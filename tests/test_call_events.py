from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from voicebot_platform import call_events
from voicebot_platform.config import CALL_EVENT_COLLECTION, TRANSCRIPT_COLLECTION
from voicebot_platform.mongo import get_db


def _context() -> dict:
    return {
        "call_id": "CALL-1",
        "room_name": "room-1",
        "assistant_id": "assistant-1",
        "bot_id": "bot-1",
        "bot_version_id": "version-1",
        "campaign_id": "campaign-1",
        "lead_id": "lead-1",
    }


def test_insert_call_event_writes_valid_document():
    doc = call_events.insert_call_event(
        "gemini_error",
        "error",
        "Gemini failed",
        _context(),
        {"error_type": "ConnectionClosed"},
    )

    assert doc is not None
    assert doc["_id"]
    assert doc["event_type"] == "gemini_error"
    assert doc["severity"] == "error"
    assert doc["details"]["error_type"] == "ConnectionClosed"


def test_record_call_event_is_fire_and_forget_but_testable():
    future = call_events.record_call_event("call_started", "info", "Call started", _context())
    doc = future.result(timeout=2)

    assert doc is not None
    assert get_db()[CALL_EVENT_COLLECTION].count_documents({"call_id": "CALL-1"}) == 1


def test_missing_optional_fields_do_not_fail():
    doc = call_events.insert_call_event("call_ended", "not-a-real-severity", "Call ended", {}, None)

    assert doc is not None
    assert doc["severity"] == "info"
    assert doc["call_id"] == ""
    assert doc["details"] == {}


def test_mongo_failure_is_swallowed(monkeypatch):
    class BrokenCollection:
        def insert_one(self, _doc):
            raise RuntimeError("mongo down")

    class BrokenDb:
        def __getitem__(self, _name):
            return BrokenCollection()

    monkeypatch.setattr(call_events, "get_db", lambda: BrokenDb())

    assert call_events.insert_call_event("call_started", "info", "Call started", _context()) is None


def test_search_call_events_filters_and_sorts_chronologically():
    db = get_db()
    base = datetime.utcnow()
    db[CALL_EVENT_COLLECTION].insert_many(
        [
            {
                **_context(),
                "event_type": "callback_failed",
                "severity": "error",
                "message": "later",
                "details": {},
                "created_at": base + timedelta(seconds=2),
            },
            {
                **_context(),
                "event_type": "callback_failed",
                "severity": "error",
                "message": "earlier",
                "details": {},
                "created_at": base,
            },
            {
                **{**_context(), "call_id": "CALL-2"},
                "event_type": "call_started",
                "severity": "info",
                "message": "other call",
                "details": {},
                "created_at": base,
            },
        ]
    )

    docs = call_events.search_call_events(
        {"call_id": "CALL-1", "event_type": "callback_failed", "severity": "error"}
    )

    assert [doc["message"] for doc in docs] == ["earlier", "later"]


def test_transcript_events_api_returns_timeline():
    from voicebot_platform.api import app

    transcript_id = get_db()[TRANSCRIPT_COLLECTION].insert_one(
        {
            "call_id": "CALL-API",
            "room_name": "room-api",
            "transcript": [],
            "created_at": datetime.utcnow(),
        }
    ).inserted_id
    get_db()[CALL_EVENT_COLLECTION].insert_one(
        {
            **{**_context(), "call_id": "CALL-API", "room_name": "room-api"},
            "event_type": "call_started",
            "severity": "info",
            "message": "Call started",
            "details": {},
            "created_at": datetime.utcnow(),
        }
    )

    client = TestClient(app)
    response = client.get(f"/api/transcripts/{transcript_id}/events")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["event_type"] == "call_started"


def test_transcript_events_api_rejects_bad_object_id():
    from voicebot_platform.api import app

    client = TestClient(app)
    response = client.get("/api/transcripts/not-an-object-id/events")

    assert response.status_code == 400
