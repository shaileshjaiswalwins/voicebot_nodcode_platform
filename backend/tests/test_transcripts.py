from datetime import datetime, timezone

from backend.db import transcripts
from backend.routers.transcripts import _build_filter


def _insert(**kwargs):
    doc = {
        "bot_id": "bot1",
        "campaign_id": "camp1",
        "status": "completed",
        "call_id": "call1",
        "lead_id": "lead1",
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(kwargs)
    return transcripts.insert_one(doc).inserted_id


class TestBuildFilter:
    def test_no_filters_returns_empty_query(self):
        assert _build_filter() == {}

    def test_bot_id_and_campaign_id_and_status(self):
        q = _build_filter(bot_id="b1", campaign_id="c1", status="failed")
        assert q == {"bot_id": "b1", "campaign_id": "c1", "status": "failed"}

    def test_source_web_test(self):
        assert _build_filter(source="web_test") == {"source": "web_test"}

    def test_source_batch_includes_missing_field(self):
        q = _build_filter(source="batch")
        assert q["$and"] == [{"$or": [{"source": "batch"}, {"source": {"$exists": False}}]}]

    def test_source_unknown_value_is_ignored(self):
        assert _build_filter(source="bogus") == {}

    def test_text_is_regex_escaped(self):
        q = _build_filter(text="a.b*")
        clause = q["$and"][0]["$or"]
        assert clause[0]["lead_id"]["$regex"] == "a\\.b\\*"
        assert clause[1]["call_id"]["$regex"] == "a\\.b\\*"

    def test_text_and_source_batch_combine_and_clauses(self):
        q = _build_filter(text="x", source="batch")
        assert len(q["$and"]) == 2


class TestListTranscripts:
    def test_requires_auth(self, client):
        resp = client.get("/api/transcripts")
        assert resp.status_code == 401

    def test_empty_list(self, client, auth_headers):
        resp = client.get("/api/transcripts", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_lists_and_excludes_call_events(self, client, auth_headers):
        _insert(call_events=[{"type": "start"}])
        resp = client.get("/api/transcripts", headers=auth_headers)
        assert resp.status_code == 200
        docs = resp.json()
        assert len(docs) == 1
        assert "call_events" not in docs[0]
        assert isinstance(docs[0]["_id"], str)
        assert isinstance(docs[0]["created_at"], str)

    def test_filters_by_bot_id(self, client, auth_headers):
        _insert(bot_id="bot1")
        _insert(bot_id="bot2")
        resp = client.get("/api/transcripts", params={"bot_id": "bot1"}, headers=auth_headers)
        docs = resp.json()
        assert len(docs) == 1
        assert docs[0]["bot_id"] == "bot1"

    def test_limit_is_capped_at_1000(self, client, auth_headers):
        resp = client.get("/api/transcripts", params={"limit": 5000}, headers=auth_headers)
        assert resp.status_code == 422

    def test_results_sorted_newest_first(self, client, auth_headers):
        _insert(call_id="old", created_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
        _insert(call_id="new", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        resp = client.get("/api/transcripts", headers=auth_headers)
        docs = resp.json()
        assert [d["call_id"] for d in docs] == ["new", "old"]


class TestListCallEvents:
    def test_requires_auth(self, client):
        resp = client.get("/api/transcripts/000000000000000000000000/events")
        assert resp.status_code == 401

    def test_invalid_id_returns_404(self, client, auth_headers):
        resp = client.get("/api/transcripts/not-a-valid-id/events", headers=auth_headers)
        assert resp.status_code == 404

    def test_missing_transcript_returns_empty_list(self, client, auth_headers):
        resp = client.get("/api/transcripts/000000000000000000000000/events", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_call_events(self, client, auth_headers):
        tid = _insert(call_events=[{"type": "start"}, {"type": "end"}])
        resp = client.get(f"/api/transcripts/{tid}/events", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == [{"type": "start"}, {"type": "end"}]

    def test_missing_call_events_field_returns_empty_list(self, client, auth_headers):
        tid = _insert()
        resp = client.get(f"/api/transcripts/{tid}/events", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []


class TestRecordingLookup:
    def test_requires_auth(self, client):
        resp = client.get("/api/transcripts/recording-lookup/call1")
        assert resp.status_code == 401

    def test_missing_call_returns_empty_dict(self, client, auth_headers):
        resp = client.get("/api/transcripts/recording-lookup/nope", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_found_call_returns_recording_fields(self, client, auth_headers):
        _insert(call_id="call123", recording_url="https://x/rec.mp3", recording_source="s3")
        resp = client.get("/api/transcripts/recording-lookup/call123", headers=auth_headers)
        assert resp.json() == {"recording_url": "https://x/rec.mp3", "recording_source": "s3"}

    def test_found_call_missing_recording_fields_defaults_empty(self, client, auth_headers):
        _insert(call_id="call456")
        resp = client.get("/api/transcripts/recording-lookup/call456", headers=auth_headers)
        assert resp.json() == {"recording_url": "", "recording_source": ""}


class TestExportCsv:
    def test_requires_auth(self, client):
        resp = client.get("/api/transcripts/export.csv")
        assert resp.status_code == 401

    def test_header_only_when_empty(self, client, auth_headers):
        resp = client.get("/api/transcripts/export.csv", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.text == "call_id,lead_id,status,call_outcome,call_duration_sec,created_at\n"

    def test_exports_rows_with_analysis_outcome(self, client, auth_headers):
        _insert(
            call_id="c1",
            lead_id="l1",
            status="completed",
            analysis={"call_outcome": "Approved"},
            call_duration_sec=42,
        )
        resp = client.get("/api/transcripts/export.csv", headers=auth_headers)
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2
        assert "c1,l1,completed,Approved,42," in lines[1]

    def test_filters_by_outcome(self, client, auth_headers):
        _insert(call_id="approved", analysis={"call_outcome": "Approved"})
        _insert(call_id="rejected", analysis={"call_outcome": "Rejected"})
        resp = client.get(
            "/api/transcripts/export.csv", params={"outcome": "Approved"}, headers=auth_headers
        )
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2
        assert lines[1].startswith("approved,")

    def test_filters_by_date_range(self, client, auth_headers):
        _insert(call_id="in_range", created_at=datetime(2024, 6, 15, tzinfo=timezone.utc))
        _insert(call_id="out_of_range", created_at=datetime(2023, 1, 1, tzinfo=timezone.utc))
        resp = client.get(
            "/api/transcripts/export.csv",
            params={"start_date": "2024-01-01", "end_date": "2024-12-31"},
            headers=auth_headers,
        )
        lines = resp.text.strip().split("\n")
        assert len(lines) == 2
        assert lines[1].startswith("in_range,")

    def test_invalid_date_raises_unhandled_value_error(self, client, auth_headers):
        # GAP: export_csv has no try/except around datetime.fromisoformat(start_date),
        # so a malformed date crashes the request instead of returning 400. This test
        # documents current (broken) behavior; tighten it once the endpoint validates
        # start_date/end_date and returns HTTPException(400, ...) instead.
        import pytest

        with pytest.raises(ValueError):
            client.get(
                "/api/transcripts/export.csv", params={"start_date": "not-a-date"}, headers=auth_headers
            )

    def test_missing_analysis_defaults_blank_outcome(self, client, auth_headers):
        _insert(call_id="no_analysis")
        resp = client.get("/api/transcripts/export.csv", headers=auth_headers)
        lines = resp.text.strip().split("\n")
        assert "no_analysis,lead1,completed,," in lines[1]
