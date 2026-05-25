"""Post-call recording lookup and verified transcription helpers."""

from __future__ import annotations

import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from loguru import logger

from voicebot_platform.call_events import record_call_event

from .config import (
    DIALER_RECORDING_API_URL,
    DIALER_RECORDING_CITY,
    DIALER_RECORDING_LOOKAHEAD_HOURS,
    DIALER_RECORDING_LOOKBACK_HOURS,
    DIALER_RECORDING_SERVICE_ID,
    RECORDING_FETCH_TIMEOUT_SEC,
    RECORDING_TRANSCRIBE_TIMEOUT_SEC,
    SARVAM_API_KEY,
    SARVAM_STT_URL,
    VERIFY_TRANSCRIPTS_FROM_RECORDING,
)


_RECORDING_URL_KEYS = {
    "recording_url",
    "recordingUrl",
    "recording_link",
    "recordingLink",
    "recording",
    "call_recording",
    "callRecording",
    "audio_url",
    "audioUrl",
    "file_url",
    "fileUrl",
    "url",
}
IST = timezone(timedelta(hours=5, minutes=30))


def normalize_mobile(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def extract_mobile(doc: dict[str, Any]) -> str:
    lead_record = doc.get("lead_record") or {}
    buyer_details = lead_record.get("buyer_details") or {}
    sip_info = doc.get("sip_info") or {}
    candidates = [
        doc.get("mobile"),
        sip_info.get("caller_number"),
        buyer_details.get("buyer_number"),
        buyer_details.get("mobile"),
    ]
    for candidate in candidates:
        mobile = normalize_mobile(candidate)
        if mobile:
            return mobile
    return ""


def extract_service_id(doc: dict[str, Any]) -> int | None:
    config_snapshot = doc.get("config_snapshot") or {}
    recording_config = config_snapshot.get("recording") or {}
    candidates = [
        recording_config.get("service_id"),
        recording_config.get("dialer_service_id"),
        config_snapshot.get("recording_service_id"),
        config_snapshot.get("dialer_service_id"),
        config_snapshot.get("service_id"),
        DIALER_RECORDING_SERVICE_ID,
    ]
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def recording_window(doc: dict[str, Any]) -> tuple[str, str]:
    anchor = (
        _coerce_datetime(doc.get("call_start_time"))
        or _coerce_datetime(doc.get("created_at"))
        or datetime.now(timezone.utc)
    )
    start = anchor - timedelta(hours=DIALER_RECORDING_LOOKBACK_HOURS)
    end = anchor + timedelta(hours=DIALER_RECORDING_LOOKAHEAD_HOURS)
    return (
        start.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
        end.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _iter_values(payload: Any):
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _iter_values(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_values(item)


def extract_recording_url(payload: Any) -> str:
    for obj in _iter_values(payload):
        if not isinstance(obj, dict):
            continue
        for key, value in obj.items():
            if key in _RECORDING_URL_KEYS and isinstance(value, str):
                candidate = value.strip()
                if candidate.startswith(("http://", "https://")):
                    return candidate
    return ""


def extract_recording_row(payload: Any) -> dict[str, Any]:
    for obj in _iter_values(payload):
        if isinstance(obj, dict) and extract_recording_url(obj):
            return obj
    return {}


async def fetch_recording_metadata(
    doc: dict[str, Any],
    http_session: aiohttp.ClientSession,
) -> dict[str, Any]:
    mobile = extract_mobile(doc)
    service_id = extract_service_id(doc)
    if not mobile:
        return {"status": "unavailable", "reason": "missing_mobile"}
    if service_id is None:
        return {"status": "unavailable", "reason": "missing_service_id", "mobile": mobile}

    start_time, end_time = recording_window(doc)
    payload = {
        "start_time": start_time,
        "end_time": end_time,
        "dialer_city": DIALER_RECORDING_CITY,
        "filter_type": "service_id+call_cli_text",
        "service_id": [service_id],
        "call_cli_text": [mobile],
    }
    async with http_session.post(
        DIALER_RECORDING_API_URL,
        json=payload,
        timeout=aiohttp.ClientTimeout(total=RECORDING_FETCH_TIMEOUT_SEC),
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            return {
                "status": "failed",
                "reason": "recording_api_http_error",
                "status_code": resp.status,
                "body": body[:300],
                "request": payload,
            }
        try:
            data = await resp.json(content_type=None)
        except Exception:
            return {
                "status": "failed",
                "reason": "recording_api_non_json",
                "body": body[:300],
                "request": payload,
            }
    recording_url = extract_recording_url(data)
    if not recording_url:
        return {
            "status": "unavailable",
            "reason": "recording_not_found",
            "response_preview": str(data)[:500],
            "request": payload,
        }
    return {
        "status": "found",
        "recording_url": recording_url,
        "recording_row": extract_recording_row(data),
        "request": payload,
    }


async def download_recording_audio(
    recording_url: str,
    http_session: aiohttp.ClientSession,
) -> tuple[bytes, str]:
    async with http_session.get(
        recording_url,
        timeout=aiohttp.ClientTimeout(total=RECORDING_FETCH_TIMEOUT_SEC),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"recording_download_http_{resp.status}: {body[:120]}")
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return await resp.read(), content_type


async def transcribe_audio_with_sarvam(
    audio_bytes: bytes,
    content_type: str,
    http_session: aiohttp.ClientSession,
) -> str:
    if not SARVAM_API_KEY:
        raise RuntimeError("sarvam_api_key_missing")
    form = aiohttp.FormData()
    filename = "recording.wav" if "wav" in content_type else "recording.mp3"
    form.add_field("file", audio_bytes, filename=filename, content_type=content_type)
    form.add_field("language_code", "hi-IN")
    form.add_field("model", "saaras:v3")
    form.add_field("mode", "transcribe")
    async with http_session.post(
        SARVAM_STT_URL,
        headers={"api-subscription-key": SARVAM_API_KEY},
        data=form,
        timeout=aiohttp.ClientTimeout(total=RECORDING_TRANSCRIBE_TIMEOUT_SEC),
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise RuntimeError(f"sarvam_http_{resp.status}: {body[:200]}")
        data = await resp.json(content_type=None)
    text = (data.get("transcript") or data.get("text") or "").strip()
    if not text:
        raise RuntimeError("sarvam_empty_transcript")
    return text


def transcript_from_text(text: str) -> list[dict[str, Any]]:
    return [{"role": "recording", "text": text}]


def quality_flags(doc: dict[str, Any], analysis_transcript: list[dict[str, Any]]) -> list[str]:
    flags: set[str] = set(doc.get("transcript_quality_flags") or [])
    duration = float(doc.get("call_duration_sec") or 0)
    live_transcript = doc.get("live_transcript") or doc.get("transcript") or []
    user_turns = [
        turn for turn in analysis_transcript
        if str(turn.get("role", "")).lower() in {"user", "buyer", "recording"}
        and (turn.get("text") or "").strip()
    ]
    assistant_turns = [
        turn for turn in analysis_transcript
        if str(turn.get("role", "")).lower() == "assistant"
        and (turn.get("text") or "").strip()
    ]
    if duration and duration < 15:
        flags.add("short_call")
    if not user_turns:
        flags.add("missing_user_audio")
    if assistant_turns and not user_turns:
        flags.add("assistant_only")
    verified = doc.get("verified_transcript") or []
    if verified and live_transcript and len(verified) != len(live_transcript):
        flags.add("live_verified_mismatch")
    latency = doc.get("latency_metrics") or {}
    if float(latency.get("max_response_delay_ms") or 0) > 3000:
        flags.add("high_latency")
    return sorted(flags)


async def verify_transcript_from_recording(
    doc: dict[str, Any],
    collection,
    http_session: aiohttp.ClientSession,
    event_context: dict[str, Any],
) -> dict[str, Any]:
    if not VERIFY_TRANSCRIPTS_FROM_RECORDING:
        return doc
    if doc.get("verified_transcript_status") == "succeeded" and doc.get("verified_transcript"):
        return doc

    doc_id = doc["_id"]
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: collection.update_one(
            {"_id": doc_id},
            {"$set": {"verified_transcript_status": "pending"}},
        ),
    )
    record_call_event(
        "verified_transcript_started",
        "info",
        "Recording transcript verification started",
        event_context,
        {},
    )
    try:
        metadata = await fetch_recording_metadata(doc, http_session)
        update: dict[str, Any] = {"recording_lookup": metadata}
        if metadata.get("recording_url"):
            update["recording_url"] = metadata["recording_url"]
        if metadata.get("status") != "found":
            update.update(
                {
                    "verified_transcript_status": "unavailable",
                    "analysis_transcript_source": "gemini_live",
                }
            )
            await loop.run_in_executor(None, lambda: collection.update_one({"_id": doc_id}, {"$set": update}))
            doc.update(update)
            record_call_event(
                "verified_transcript_unavailable",
                "warning",
                "Recording was not available for verified transcription",
                event_context,
                {"reason": metadata.get("reason"), "request": metadata.get("request")},
            )
            return doc

        audio_bytes, content_type = await download_recording_audio(metadata["recording_url"], http_session)
        verified_text = await transcribe_audio_with_sarvam(audio_bytes, content_type, http_session)
        verified_transcript = transcript_from_text(verified_text)
        update.update(
            {
                "verified_transcript": verified_transcript,
                "verified_transcript_status": "succeeded",
                "transcript_source": "recording_verified",
                "analysis_transcript_source": "recording_verified",
            }
        )
        update["transcript_quality_flags"] = quality_flags({**doc, **update}, verified_transcript)
        await loop.run_in_executor(None, lambda: collection.update_one({"_id": doc_id}, {"$set": update}))
        doc.update(update)
        record_call_event(
            "verified_transcript_succeeded",
            "success",
            "Recording transcript verification succeeded",
            event_context,
            {"text_chars": len(verified_text), "recording_url": metadata["recording_url"]},
        )
        return doc
    except Exception as exc:
        update = {
            "verified_transcript_status": "failed",
            "verified_transcript_error": f"{type(exc).__name__}: {exc}",
            "analysis_transcript_source": "gemini_live",
        }
        update["transcript_quality_flags"] = quality_flags({**doc, **update}, doc.get("transcript") or [])
        await loop.run_in_executor(None, lambda: collection.update_one({"_id": doc_id}, {"$set": update}))
        doc.update(update)
        record_call_event(
            "verified_transcript_failed",
            "error",
            "Recording transcript verification failed",
            event_context,
            {"error_type": type(exc).__name__, "error": str(exc)},
        )
        logger.warning(f"[RECORDING] verification failed for doc {doc_id}: {exc}")
        return doc
