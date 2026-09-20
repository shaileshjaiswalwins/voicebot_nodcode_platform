from __future__ import annotations

from datetime import datetime, timezone

from callback_worker import recording


def test_extract_mobile_uses_last_10_digits():
    doc = {"sip_info": {"caller_number": "+91 84315 91402"}}

    assert recording.extract_mobile(doc) == "8431591402"


def test_extract_service_id_prefers_config_snapshot():
    doc = {"config_snapshot": {"recording": {"service_id": "293"}}}

    assert recording.extract_service_id(doc) == 293


def test_extract_recording_url_from_nested_payload():
    payload = {
        "errorCode": 0,
        "data": [
            {
                "call_cli_text": "8431591402",
                "meta": {"recording_url": "http://recordings.example/call.mp3"},
            }
        ],
    }

    assert recording.extract_recording_url(payload) == "http://recordings.example/call.mp3"


def test_extract_recording_url_supports_media_path_sample_shape():
    payload = {
        "errorCode": 0,
        "data": [
            {"call_disposition": "NO ANSWER", "media_path": ""},
            {
                "call_disposition": "ANSWERED",
                "call_duration": 24,
                "media_path": "http://recordings.internal/Recordings/2026/May/21May2026/293/call.mp3",
            },
        ],
    }

    assert recording.extract_recording_url(payload).endswith("/call.mp3")


def test_select_recording_row_picks_closest_answered_media_path():
    payload = {
        "data": [
            {
                "call_disposition": "ANSWERED",
                "call_start_time": "2026-05-21T05:52:01.000Z",
                "media_path": "http://recordings.example/old.mp3",
            },
            {
                "call_disposition": "ANSWERED",
                "call_start_time": "2026-05-21T08:54:47.000Z",
                "media_path": "http://recordings.example/close.mp3",
            },
        ],
    }

    row = recording.select_recording_row(payload, "2026-05-21T08:55:00.000Z")

    assert row["media_path"].endswith("close.mp3")


def test_recording_window_uses_call_start_time():
    doc = {"call_start_time": datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)}

    start, end = recording.recording_window(doc)

    assert start.startswith("2026-03-05")
    assert end.startswith("2026-03-06") or end.startswith("2026-03-05")


def test_quality_flags_for_short_missing_user_audio():
    doc = {
        "call_duration_sec": 9,
        "transcript": [{"role": "assistant", "text": "Hello"}],
    }

    flags = recording.quality_flags(doc, doc["transcript"])

    assert "short_call" in flags
    assert "missing_user_audio" in flags
    assert "assistant_only" in flags
