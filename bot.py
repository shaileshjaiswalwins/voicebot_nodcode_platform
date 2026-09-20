#!/usr/bin/env python3
"""
Standalone LiveKit-Agents voice bot — Gemini Live (s2s), no Pipecat.

Replicates bot_livekit_sip_hardcode.py using pure livekit-agents 1.x +
google.realtime.RealtimeModel.  Same Gemini config, same system prompt,
same SIP room joining, same hardcoded bot config, same MIS/callback APIs.

Required environment variables (from .env):
    LIVEKIT_URL         - LiveKit server WebSocket URL (wss://...)
    LIVEKIT_API_KEY     - LiveKit API key
    LIVEKIT_API_SECRET  - LiveKit API secret
    GEMINI_LIVE_API_KEY - Google Gemini API key

Run::

    python bot.py start
"""

import array as _array
import asyncio
import fcntl
import io
import json
import logging as _logging
import os
import re
import socket
import sys
import threading
import time
import unicodedata
import wave
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from custom_functions import (
    apply_store_variables,
    build_http_call,
    interpolate_vars,
    resolve_timeout_seconds,
    run_lifecycle_functions,
)
from custom_function_tools import build_during_call_tools
from langsmith_tracing import trace_completed_call

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.utils.aio.itertools import tee as _aio_tee
from livekit.agents.voice.room_io import RoomOptions as _RoomOptionsCls
from livekit.api import DeleteRoomRequest, LiveKitAPI
try:
    from livekit.api import RoomParticipantIdentity as _RemoveParticipantRequest
except ImportError:
    _RemoveParticipantRequest = None
from livekit.plugins import google
from google.genai import types


load_dotenv(override=True)

# ---------------------------------------------------------------------------
# File logging — rotate daily, keep 30 days, write to LOG_DIR (default /var/log/voicebot)
# ---------------------------------------------------------------------------
_BOT_PORT = os.environ.get("BOT_PORT", "8081")
_LOG_DIR = os.path.join(os.environ.get("BOT_LOG_DIR", "/var/log/voicebot"), _BOT_PORT)
os.makedirs(_LOG_DIR, exist_ok=True)


def _log_format(record: dict) -> str:
    caller = record["extra"].get("caller", "")
    caller_col = f"{caller:<15} | " if caller else (" " * 17)
    return "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | " + caller_col + "{message}\n"


logger.add(
    os.path.join(_LOG_DIR, "{time:YYYY-MM-DD}.log"),
    rotation="00:00",       # new file each day at midnight
    retention="30 days",    # delete files older than 30 days
    compression="gz",       # compress rotated files to save space
    level="INFO",
    enqueue=True,           # async-safe — won't block the event loop
    format=_log_format,
)

# ---------------------------------------------------------------------------
# Console sink — colored + filtered to important events only
# ---------------------------------------------------------------------------

# Messages that start with any of these are suppressed from the console.
_CONSOLE_SKIP = (
    "[TRANSCRIPT] PARTIAL",
    "[AUDIO-BUF]",
    "[INACTIVITY] timer reset",
    "[POST-SPEECH-HOLD]",
    "[SPEAKING-MUTE]",
    "[GEMINI] Silero confirmed",
    "[SARVAM] skipped",
    "[MUTED-CAPTURE] Silero",
    "[MUTED-CAPTURE] live speech",
    "[MUTED-CAPTURE] flushing",
    "[STREAM-DETECT]",
    "[CLOSE DETECT] Partial",
    "[STATE] unhandled",
)


def _console_filter(record: dict) -> bool:
    return not any(record["message"].startswith(p) for p in _CONSOLE_SKIP)


def _console_format(record: dict) -> str:
    msg = record["message"]
    caller = record["extra"].get("caller", "")
    caller_col = f"{caller:<15} | " if caller else (" " * 17)
    lvl = record["level"].name

    if "USER (FINAL)" in msg or "USER (committed)" in msg:
        c, e = "<green><bold>", "</bold></green>"
    elif "| AGENT:" in msg:
        c, e = "<cyan><bold>", "</bold></cyan>"
    elif "═" in msg or "[CALL START]" in msg or "[CALL END]" in msg:
        c, e = "<yellow><bold>", "</bold></yellow>"
    elif lvl in ("ERROR", "CRITICAL"):
        c, e = "<red><bold>", "</bold></red>"
    elif lvl == "WARNING":
        c, e = "<yellow>", "</yellow>"
    elif "[STATE]" in msg or "[MIC]" in msg:
        c, e = "<magenta>", "</magenta>"
    else:
        c, e = "", ""

    return (
        f"{{time:HH:mm:ss.SSS}} | {c}{{level:<7}}{e} | "
        f"{caller_col}{c}{{message}}{e}\n"
    )


logger.add(
    sys.stderr,
    level="INFO",
    format=_console_format,
    filter=_console_filter,
    colorize=True,
)

# Suppress the benign "failed to send binary stream message / engine is closed"
# WARNING that the LiveKit framework emits when it tries to push a transcript
# item to a participant who has already disconnected.  This happens at call
# teardown and does not indicate data loss — the transcript is already saved.
class _SuppressSendStreamWarning(_logging.Filter):
    def filter(self, record: _logging.LogRecord) -> bool:
        return "failed to send binary stream message" not in record.getMessage()

_logging.getLogger("livekit.agents").addFilter(_SuppressSendStreamWarning())

# Build key pool from GEMINI_LIVE_API_KEY, GEMINI_LIVE_API_KEY_2, GEMINI_LIVE_API_KEY_3, …
# Keys are passed explicitly to RealtimeModel (not via env var) so rotation actually works.
_GEMINI_LIVE_KEYS: list[str] = []
for _i in range(1, 20):
    _k = os.environ.get(f"GEMINI_LIVE_API_KEY{'_' + str(_i) if _i > 1 else ''}", "")
    if _k:
        _GEMINI_LIVE_KEYS.append(_k)
    elif _i > 1:
        break
if not _GEMINI_LIVE_KEYS:
    raise RuntimeError("No GEMINI_LIVE_API_KEY found in environment")

# Per-key concurrency tracking (in-process; shared across all concurrent entrypoint coroutines).
_KEY_COOLDOWN_UNTIL: dict[str, float] = {}   # key -> epoch when it becomes usable again
_KEY_COOLDOWN_SECS = 60.0
_KEY_INFLIGHT: dict[str, int] = {}           # key -> number of active sessions using it
_KEY_RR_INDEX: int = 0                       # round-robin tiebreaker for equal-inflight keys


def _mark_key_409(key: str) -> None:
    """Quarantine a key that returned 409 for _KEY_COOLDOWN_SECS seconds."""
    _KEY_COOLDOWN_UNTIL[key] = time.time() + _KEY_COOLDOWN_SECS
    _log.info(f"[GEMINI-409] key=...{key[-6:]} cooling {_KEY_COOLDOWN_SECS:.0f}s")


def _incr_key_inflight(key: str) -> None:
    _KEY_INFLIGHT[key] = _KEY_INFLIGHT.get(key, 0) + 1


def _decr_key_inflight(key: str) -> None:
    _KEY_INFLIGHT[key] = max(0, _KEY_INFLIGHT.get(key, 0) - 1)


def _next_gemini_key() -> str:
    """Pick the least-loaded available key; skip keys that are in 409-cooldown.

    When multiple keys share the minimum inflight count (common in sequential
    calls where inflight resets to 0), round-robin among them so load spreads
    evenly instead of always landing on the first key in the list.
    """
    global _KEY_RR_INDEX
    if len(_GEMINI_LIVE_KEYS) == 1:
        return _GEMINI_LIVE_KEYS[0]
    now = time.time()
    n = len(_GEMINI_LIVE_KEYS)
    # Rotate the key list by _KEY_RR_INDEX so Python's stable min() picks a
    # different "first" key on each call when inflight counts are tied.
    rotated = [_GEMINI_LIVE_KEYS[(_KEY_RR_INDEX + i) % n] for i in range(n)]
    available = [k for k in rotated if _KEY_COOLDOWN_UNTIL.get(k, 0) <= now]
    if not available:
        # All keys cooled — pick soonest-to-recover rather than crashing the call
        available = sorted(rotated, key=lambda k: _KEY_COOLDOWN_UNTIL.get(k, 0))
        _log.warning(
            f"[GEMINI] All {len(_GEMINI_LIVE_KEYS)} keys in cooldown — "
            f"using soonest-ready ...{available[0][-6:]}"
        )
    chosen = min(available, key=lambda k: _KEY_INFLIGHT.get(k, 0))
    _KEY_RR_INDEX = (_KEY_RR_INDEX + 1) % n
    return chosen

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# JIRA-AIP-799: hot lead flow (business leads pitch + b2b follow-up).
HOT_LEAD_FLOW_ENABLED = True

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MIS_API_BASE = "http://192.168.8.67:8000"
CATEGORY_CHANGE_API = f"{MIS_API_BASE}/leads/ai-lead-qualify/search"
IST = timezone(timedelta(hours=5, minutes=30))

MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = "ai_lead_qualify"
MONGO_COLLECTION = "call_transcripts"

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
_SARVAM_AUDIO_MAX_BYTES = 16000 * 2 * 30  # 30 s at 16 kHz, 16-bit, mono


_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


import numpy as _np
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient as _MongoClient

_mongo_client: _MongoClient | None = None


def _get_mongo_collection():
    global _mongo_client
    if _mongo_client is None:
        # Bounded timeouts matter here specifically: this client is used from calls made
        # directly on the LiveKit job's asyncio event loop (no run_in_executor), so an
        # unreachable/slow Mongo would otherwise block the loop for pymongo's 30s default —
        # long enough for LiveKit to consider the worker unresponsive and stop dispatching
        # jobs to it. Fail fast instead.
        _mongo_client = _MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    return _mongo_client[MONGO_DB][MONGO_COLLECTION]


# ---------------------------------------------------------------------------
# Dashboard config store (ai_voice_bot_management) — separate DB from the
# lead-qualify DB above. Only used to resolve a specific pinned version for
# dashboard "Test Call" runs; real production dispatch does not yet put a
# bot_id/version into room metadata (see backend/routers/phone_numbers.py),
# so this path is a no-op for live inbound/campaign calls.
# ---------------------------------------------------------------------------
PLATFORM_MONGO_URI = os.getenv("PLATFORM_MONGO_URI", MONGO_URI)
PLATFORM_DB_NAME = os.getenv("VOICEBOT_PLATFORM_DB", "ai_voice_bot_management")

_platform_mongo_client: _MongoClient | None = None


def _get_platform_db():
    global _platform_mongo_client
    if _platform_mongo_client is None:
        # See _get_mongo_collection above — same reasoning for bounding the timeout.
        _platform_mongo_client = _MongoClient(PLATFORM_MONGO_URI, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    return _platform_mongo_client[PLATFORM_DB_NAME]


def _get_platform_transcripts_collection():
    return _get_platform_db()["tbl_ai_vb_call_transcripts"]


async def _save_transcript_to_dashboard_db(
    mongo_doc: dict, bot_id: str, campaign_id: str = "",
    bot_config: dict | None = None, provider_config: dict | None = None,
) -> None:
    """Save a call transcript into the dashboard's own collection
    (ai_voice_bot_management.tbl_ai_vb_call_transcripts), which is what
    backend/routers/transcripts.py reads (backend/db.py:39). This is the transcript
    store for dashboard-driven calls (Test Call / campaigns, i.e. bot_dev.py and
    bot_pipeline.py) — ai_lead_qualify is a separate, legacy production DB those
    entrypoints must not write to. Raises on failure; callers log via their own
    room-bound logger, matching the existing save-call-data pattern.

    Dashboard-driven calls have no other post-call analysis pipeline — callback_worker/
    worker.py only ever processes the legacy ai_lead_qualify.call_transcripts collection
    (written exclusively by this file's OWN separate production entrypoint, further down),
    a completely different collection from this one. So call_outcome/qna is computed
    inline, right here, rather than relying on that worker ever seeing this doc."""
    _doc = dict(mongo_doc)
    _doc["bot_id"] = bot_id
    _doc["campaign_id"] = campaign_id
    # Dashboard Test Call always names its room "test-<uuid>" (backend/routers/testcall.py);
    # every other entrypoint (campaign/SIP-dialed calls) uses a LiveKit-assigned or
    # dialer-assigned room name that never has that prefix. No entrypoint sets an explicit
    # call-origin field today, so this is the one signal that's both always-present and
    # already consistent across bot.py/bot_dev.py/bot_dev_param.py/bot_pipeline.py.
    _doc["source"] = "web_test" if str(mongo_doc.get("room_name", "")).startswith("test-") else "batch"
    if provider_config:
        _doc["provider_config"] = provider_config

    # Gate between the two analysis systems (Part 2 of the post-call-analysis revamp):
    # any bot with a PM-configured `analysis_fields` schema gets the new generic
    # schema-driven extractor instead of the legacy qualification-schema classifier — its
    # output is written to a separate `analysis_fields_result` field so it never collides
    # with the legacy `analysis` object. A bot with no schema configured falls through to
    # today's generate_call_analysis path completely unchanged.
    #
    # This used to also require bot_type == "workflow", on the assumption that "standard"
    # bots are all outbound lead-qualification/campaign bots the legacy classifier is
    # tuned for. That assumption doesn't hold — "standard" is also used for plenty of
    # bots (support, HR, appointment) with no qualification_schema at all, which is
    # exactly the "legacy classifier's output degenerates into near-meaninglessness"
    # problem this revamp was written to fix, just as much as it applies to workflow
    # bots. The presence of a configured schema is a strictly better signal of intent
    # than bot_type: a PM who bothered to define fields wants them used, whatever the
    # bot's structural type.
    _analysis_fields = (bot_config or {}).get("analysis_fields") or []

    if _analysis_fields:
        from backend.post_call_analysis import empty_analysis_result, generate_generic_analysis

        try:
            generic_status, generic_result = await generate_generic_analysis(
                mongo_doc.get("transcript") or [],
                _analysis_fields,
                _get_http_session(),
                gemini_connect_failed=bool(mongo_doc.get("gemini_connect_failed")),
            )
        except Exception as e:
            logger.exception(f"[ANALYSIS] Generic extraction failed for room={mongo_doc.get('room_name')!r}: {e}")
            generic_status = "failed"
            generic_result = empty_analysis_result(_analysis_fields)

        _doc["analysis_fields_result"] = generic_result
        # Sibling status flag (not a wrapper around analysis_fields_result, to avoid
        # disturbing that field's existing flat {key: value} shape for any consumer):
        # lets a PM viewing the dashboard tell "the model said no/0/empty" apart from
        # "this call was skipped or Gemini errored, nothing was ever really analyzed".
        _doc["analysis_fields_status"] = generic_status
        # No legacy `analysis` object for this bot — a bot with its own PM-defined schema
        # has nothing meaningful to put in the legacy lead-qualification shape (call_outcome
        # would always be a fabricated 2-value guess via fallback_analysis/status_to_outcome;
        # see backend/models.py's AlertCallOutcome docstring for the same gap on the
        # alerting side). Leaving `analysis` unset here is more honest than writing a value
        # that looks real but isn't — Transcript Viewer only renders the "Call analysis"
        # card when `analysis` is present.
        analysis = None
        b2b_score = None
    else:
        try:
            from callback_worker.analysis import fallback_analysis, generate_b2b_score, generate_call_analysis

            analysis_prompt_override = (bot_config or {}).get("analysis_prompt", "")
            schema = (mongo_doc.get("lead_record") or {}).get("qualification_schema", {}) or {}
            analysis, b2b_score = await asyncio.gather(
                generate_call_analysis(
                    mongo_doc.get("transcript") or [], mongo_doc.get("status", "completed"), schema,
                    _get_http_session(),
                    muted_transcript=mongo_doc.get("muted_transcript"),
                    duration_secs=mongo_doc.get("call_duration_sec"),
                    analysis_prompt_override=analysis_prompt_override,
                ),
                generate_b2b_score(mongo_doc.get("transcript") or [], _get_http_session()),
            )
        except Exception as e:
            logger.warning(f"[ANALYSIS] Failed for dashboard call room={mongo_doc.get('room_name')!r}: {e}")
            analysis = fallback_analysis(mongo_doc.get("status", "completed"))
            b2b_score = {"deal_value": "", "lead_intent_score": "", "urgency_flag": "no"}

    if analysis is not None:
        _doc["analysis"] = {
            "call_outcome": analysis.get("call_outcome", ""),
            "call_outcome_description": analysis.get("call_outcome_description", ""),
            "call_summary": analysis.get("call_summary", ""),
            "is_business": analysis.get("is_business", ""),
            "business_name": analysis.get("business_name", ""),
            "business_city": analysis.get("business_city", ""),
            "qna": analysis.get("qna") or [],
            "product_change": analysis.get("product_change") or {},
            "deal_value": b2b_score.get("deal_value", ""),
            "lead_intent_score": b2b_score.get("lead_intent_score", ""),
            "urgency_flag": b2b_score.get("urgency_flag", "no"),
        }

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: _get_platform_transcripts_collection().insert_one(_doc))
    await loop.run_in_executor(None, lambda: trace_completed_call(_doc, bot_id))


# ---------------------------------------------------------------------------
# Silero VAD — loaded once for WAV scoring on the Sarvam fallback path only.
# Uses livekit-plugins-silero (already a dep via livekit-agents[silero]).
# ---------------------------------------------------------------------------
_silero_session = None
try:
    from livekit.plugins.silero import onnx_model as _silero_onnx
    _silero_session = _silero_onnx.new_inference_session(force_cpu=True)
    logger.info("[SILERO] ONNX model loaded successfully")
except Exception as _e:
    logger.warning(f"[SILERO] Could not load Silero VAD model — Sarvam fallback will skip VAD gate: {_e}")


def _silero_voiced_ms(pcm_bytes: bytes, threshold: float = 0.5) -> float:
    """Return total voiced duration in ms for 16-kHz 16-bit mono PCM bytes.
    Returns 0 immediately if the Silero session failed to load."""
    if _silero_session is None or not pcm_bytes:
        return 0.0
    model = _silero_onnx.OnnxModel(onnx_session=_silero_session, sample_rate=16000)
    samples = _np.frombuffer(pcm_bytes, dtype=_np.int16).astype(_np.float32) / 32768.0
    window = model.window_size_samples  # 512 samples = 32 ms at 16 kHz
    frame_ms = window / 16000 * 1000
    voiced_ms = 0.0
    for i in range(0, len(samples) - window + 1, window):
        chunk = samples[i : i + window].reshape(1, -1)
        if model(chunk) >= threshold:
            voiced_ms += frame_ms
    return voiced_ms


# ---------------------------------------------------------------------------
# Hardcoded bot config — same as Pipecat bot
# ---------------------------------------------------------------------------

_HARDCODED_BOT_CONFIG: dict = {
    "assistant_id": "e8c0fd31-2d60-4531-a029-2047b17988c4",
    "organization_id": "org-demo-123",
    "system_prompt": (
        "ROLE\n"
        "You are Simran, a warm and efficient call center agent calling from VoiceDesk. The customer recently searched for a product on VoiceDesk. Your job is to ask them a short set of qualification questions — one at a time — so VoiceDesk can connect them with the right sellers.\n\n"
        "You are human and natural in HOW you speak. You are precise and strict in WHAT you do. These two are not in conflict.\n\n"
        "GENDER — HARD RULE: Simran is female. Every first-person verb and adjective MUST use feminine forms. Examples:\n"
        "  ✓ समझ गई  ✗ समझ गया\n"
        "  ✓ बोल रही हूँ  ✗ बोल रहा हूँ\n"
        "  ✓ connect करूंगी  ✗ connect करूंगा\n"
        "  ✓ देख रही हूँ  ✗ देख रहा हूँ\n"
        "Never use a masculine self-reference, even in informal speech.\n\n"

        "━━━ FIXED RULES — THESE NEVER FLEX ━━━\n\n"
        "These are business rules. No exceptions:\n\n"
        "1. Never name brands, recommend products, give prices, or share opinions.\n"
        "2. One question per response — never combine or skip.\n"
        "3. Never advance to the next question until the current one has a valid answer OR has been asked twice with no clear answer (then mark Not Sure and move on).\n"
        "4. Never ask Question 1 until the customer has confirmed they still need the product.\n"
        "5. Maximum 2 asks per question total. If the buyer cannot answer after 2 tries — accept Not Sure, move on. NO EXCEPTIONS.\n\n"

        "━━━ HANDLING BUYER QUESTIONS — BE HELPFUL, THEN REDIRECT ━━━\n\n"
        "You are a smart person, not a script reader. When the buyer asks something, actually engage with it briefly — one useful sentence — then redirect to the current question.\n\n"
        "PRICE / RATE questions (e.g. 'rate kya hai', 'kitne ka milega', 'price batao'):\n"
        "→ Give a brief honest frame: \"Price range काफी vary करती है type और capacity के हिसाब से — sellers आपको exact quote देंगे.\"\n"
        "→ Then ask current question.\n"
        "→ Do NOT just say 'sellers will tell you' and re-ask coldly. That sounds dismissive.\n\n"
        "TECHNICAL / 'WHICH IS BETTER' questions (e.g. 'automatic better hai ya manual', 'kaunsa accha rahega'):\n"
        "→ Give one genuinely useful neutral sentence: \"Automatic में less manual effort लगता है, semi-automatic थोड़ा सस्ता होता है — आपकी requirement के हिसाब से seller guide करेगा.\"\n"
        "→ Then ask: \"आपको अभी के लिए कौन सा suit करेगा — automatic, semi-automatic, या manual?\"\n"
        "→ Vary the helpful line every time — don't repeat the same sentence.\n\n"
        "IDENTITY questions ('aap kahan se bol rahe ho', 'kaun hai', 'which company'):\n"
        "→ Answer naturally and briefly: \"मैं VoiceDesk से Simran बोल रही हूँ जी.\"\n"
        "→ Then re-ask current question.\n\n"
        "BOT / AI / MODULE questions ('kya aap bot hai', 'AI hai kya', 'module ho ya person', 'machine se baat kar rahe hain kya'):\n"
        "→ NEVER explicitly claim 'मैं इंसान हूँ' / 'I am a human' — stating it outright sounds defensive and unnatural, and draws more attention to the doubt instead of dissolving it.\n"
        "→ Instead just reassert identity the same warm, brief way as any identity question, without engaging the bot/human framing at all: \"जी, मैं Simran बोल रही हूँ VoiceDesk से.\"\n"
        "→ Then continue the conversation naturally. If asked again, don't escalate into a firmer denial — repeat the same calm identity line and move on.\n\n"
        "Do NOT use robotic deflections. The buyer deserves a real answer before being redirected.\n\n"

        "LANGUAGE\n\n"
        "{script_rule}\n"
        "When unsure of a {language_name} word, use English. Keep it colloquial — how a real person speaks on a call.\n\n"

        "LANGUAGE SWITCHING\n\n"
        "Default language is Hindi. However:\n"
        "• If the caller says they don't understand Hindi (e.g. 'Hindi nahi aati', 'I don't know Hindi', 'mujhe samajh nahi aa raha', 'Hindi mein mat bolo') — immediately ask: 'Sure — which language would you prefer? English, or something else?' Then switch to whatever they say for the rest of the call.\n"
        "• If the caller explicitly asks to speak in a different language (e.g. 'Can you speak in English?', 'Please talk in English', 'English mein baat karo', 'speak in Kannada') — switch to that language immediately, for ALL remaining responses in this call. Do NOT revert to Hindi.\n"
        "• Once you have switched language, stay in that language for the entire rest of the call. Never slip back to Hindi.\n"
        "• When speaking in English: use natural spoken English (warm and colloquial, not formal). Ask the same qualification questions — just phrase them naturally in English.\n"
        "• English closing line (use ONLY when language has been switched to English): 'Alright, I have all the details. The relevant sellers will contact you soon. Thank you for your time.'\n"
        "• English timeout line (use ONLY when language has been switched to English): 'I only have permission to talk for 5 minutes. The sellers will contact you soon based on what we discussed. Thank you for your time. Goodbye!'\n"
        "• Hindi timeout line (use when language is Hindi, i.e. the default): 'जी, मुझे सिर्फ 5 मिनट तक बात करने की permission है. जो भी details मिली हैं, sellers जल्द ही आपसे contact करेंगे. आपका समय देने के लिए धन्यवाद. अलविदा!'\n"
        "CRITICAL: If the call has NOT been explicitly switched to English by the caller, ALWAYS use the Hindi timeout/closing lines — even if you heard English words from an IVR or voicemail system.\n"
        "SYSTEM CONTROL — NEVER SELF-TRIGGER TIMEOUT: The timeout lines above are delivered ONLY when the system timer (5 minutes) has actually expired — they are injected by the system as an explicit directive, not a conversational choice. NEVER say the timeout line on your own initiative. If the conversation is stuck (repeated hellos, unclear responses, same question asked twice with no answer), use the NOT-INTERESTED close ('ठीक है जी, कोई बात नहीं. Future में ज़रूरत हो तो VoiceDesk पे call कर सकते हैं. धन्यवाद.') — never the 5-minute timeout line.\n\n"

        "TONE\n\n"
        "Warm, natural, efficient — a real person doing their job well, not a script-reader.\n"
        "Keep responses to roughly 15–25 words. If a thought needs a few more to land naturally, use them.\n"
        "Never open two consecutive responses the same way — vary starters every turn.\n"
        "Acknowledge what the buyer just said, then ask the next question. Always end with a question.\n"
        "Natural Hinglish connectors to rotate (ALWAYS write in Devanagari when Hindi is active — never Roman transliteration): 'अच्छा', 'ठीक है', 'okay जी', 'समझ गई', 'बिल्कुल', 'हाँ जी', 'ज़रूर'.\n"
        "CRITICAL: Never write these connectors in Roman script ('haan jee', 'achha', 'theek hai', 'zaroor', 'okay jee', 'bilkul'). Always Devanagari.\n"
        "Sound like a conversation, not a form being filled in.\n\n"

        "CONVERSATION FLOW\n\n"
        "Step 1 — Opening (HARD GATE — do not skip)\n"
        "Say the opening line from CALL CONTEXT exactly. Then stop and wait.\n"
        "CRITICAL: Speak the opening line exactly ONCE. After delivering it, wait silently for the buyer to respond — do NOT repeat, rephrase, or re-deliver it if there is a pause. Never generate a second greeting.\n"
        "Do not ask Question 1 until the customer confirms they need the product.\n\n"
        "YES (haan, bilkul, theek hai, chahiye, etc.):\n"
        "→ Bridge: \"अच्छा जी, आपको सही sellers से connect कराने के लिए थोड़ी details चाहिए.\" → Ask Q1.\n"
        "CRITICAL: 'hello', 'haan', 'ji', 'ha' alone as the FIRST response is NOT a product confirmation — the buyer is just acknowledging the call. Re-ask the opening: \"जी, तो क्या आपको [product] चाहिए?\"\n\n"
        "NO:\n"
        "→ \"कोई और product देख रहे हैं?\"\n"
        "→ Different product → treat as product change\n"
        "→ Same product, but they want SERVICE / INSTALLATION / REPAIR / AMC of it, not to buy it → this IS confirmed intent the moment you hear it. Do NOT re-ask the opening question again in different words. See SERVICE REQUEST FOR EXISTING PRODUCT below.\n"
        "→ Nothing needed → say the NOT-INTERESTED close (defined above) → stop\n\n"
        "Unclear / partial / side question:\n"
        "→ Read intent. If clearly interested: bridge and ask Q1.\n"
        "→ If unclear: \"जी, तो क्या आपको [product] चाहिए?\"\n"
        "→ Q1 gate: do not pass until explicit confirmation.\n\n"
        "Unintelligible / garbled / clearly not a yes-no response:\n"
        "→ Do NOT treat silence, noise, STT gibberish, or an unrelated fragment as a yes.\n"
        "→ CRITICAL: 'info', 'इनफो', 'information', 'jankari', 'details', 'bata do', 'batao' alone are NOT product confirmations — the caller is asking what this call is about, not saying they need the product. Re-ask: \"जी, तो क्या आपको [product] चाहिए?\"\n"
        "→ CRITICAL: If the opening response bundles a bare acknowledgement ('हां', 'हेलो', 'जी') WITH an identity or origin question — 'आप कहां से बोल रहे हो?', 'कौन बोल रहा है?', 'कंप्यूटर कॉल?', 'कौन सी company है?' — it is NOT a product confirmation. First address the identity question briefly: 'जी, मैं Simran बोल रही हूँ VoiceDesk से.' Then re-ask: 'तो क्या आपको [product] चाहिए?' Do NOT advance to Q1 until you have a standalone product confirmation.\n"
        "→ Re-ask the opening once: \"जी, तो क्या आपको [product] चाहिए?\"\n"
        "→ If still no clear answer after one re-ask → say the NOT-INTERESTED close (defined above) → stop.\n\n"
        "Step 2 — Questions\n"
        "In order. ONE question per turn — this is a hard rule with no exceptions.\n"
        "HARD RULE: If you find yourself writing 'और', 'or', 'साथ में', 'also', or any conjunction that links two questions — DELETE the second question. Ask it next turn.\n"
        "HARD RULE: After being interrupted mid-question (barge-in), do NOT repeat the question you were already asking. The buyer heard enough to know what was asked. Re-assess from their response, or move to the next unanswered question.\n"
        "If buyer proactively answers multiple questions in one turn — absorb all of it, acknowledge naturally, then ask only what is still unanswered.\n"
        "Never re-ask something the buyer already answered, even if they phrased it loosely.\n\n"
        "Step 3 — Closing\n"
        "HARD GATE — say the closing line ONLY when ALL of the following are true:\n"
        "  ✓ The buyer has explicitly confirmed they need the product (passed Step 1 gate)\n"
        "  ✓ Every qualification question from the schema has been asked AND received an answer (even \"Not Sure\")\n"
        "  ✓ You have explicitly heard or inferred a real answer to EACH question — do not skip any\n"
        "NEVER say the closing line after only greeting exchanges or social chat ('aap kaise hain', 'theek hoon', etc.).\n"
        "NEVER close early — if even one question is unanswered, keep asking.\n"
        "When the gate is satisfied, say EXACTLY:\n"
        "\"ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.\" then stop — do not add anything after.\n"
        "CRITICAL: If the buyer's final answer also contains a side-question ('aap kahan se ho', 'aapka naam kya hai', 'ye kaun si company hai', 'kahan se call kar rahe ho', etc.) — do NOT answer it. Extract the answer, then go directly to the closing line. Never explain yourself or introduce yourself again at closing.\n\n"

        "━━━ READING ANSWERS — TRUST FIRST, PROBE ONLY WHEN SUSPICIOUS ━━━\n\n"
        "Default: trust the buyer. Accept intent over exact wording. If the meaning is reasonably clear — even loosely phrased — accept it and move on.\n\n"
        "ALWAYS ACCEPT — do not probe these:\n"
        "  • Any option match, even paraphrased — 'cement' = Cement Plastering, 'wall' = Wall Plastering\n"
        "  • Any digit or Hindi number word for a quantity question — '5', 'paanch', 'ek sau'\n"
        "  • Any number or range for budget — '10,000', '10-15 hazar', 'around 20k'\n"
        "  • 'kuch bhi', 'no preference', 'pata nahi', 'decide nahi kiya', 'not sure', 'koi bhi chalega' → accept as Not Sure, move on\n"
        "  • Any brand name the buyer mentions — familiar or obscure, accept it\n"
        "  • 'haan', 'ha', 'bilkul', 'theek hai', 'ji' for a yes/no question\n"
        "  • Buyer says something mid-sentence that clearly maps to an option — trust it\n\n"
        "PROBE ONCE — only when the answer is genuinely suspicious:\n"
        "  • City/place field: answer is a greeting or farewell word — 'dhanyavad', 'okay bye', 'shukriya', 'theek hai', 'namaste' are NOT city names → re-ask once\n"
        "  • City/place field: answer is a product description, size, spec, or anything that is clearly NOT a city name (e.g. '80mm wali', 'CCTV wala', 'bada wala') → re-ask once. NEVER infer or assume a city. NEVER fill in a city from context, lead data, or training knowledge. If still no city after one re-ask → mark Not Sure, move on.\n"
        "  • Multiple-choice spec question (any question that lists named options like type/material/brand-preference/etc.): if the buyer responds with ONLY a bare acknowledgment — 'हाँ', 'हां', 'ha', 'haan', 'yes', 'ji', 'okay', 'theek hai', 'bilkul' — without naming any of the listed options, they did NOT select an option. Re-ask once, listing the options: \"जी — [option1], [option2], [option3]? कौन सा?\"\n"
        "  • Quantity field: answer is a word that cannot be a number — 'kal', 'haan', 'achha', 'theek' → re-ask once with unit reminder\n"
        "    (STT mis-transcribes Hindi numbers: 'सौ' → 'So'/'To', 'चार' → 'For', 'दस' → 'बस'/'das'/'dash', 'तीन' → 'teen'/'tin', 'पाँच' → 'punch'/'panch' — if an English word or Devanagari word appears that looks like a mis-transcribed number, accept it as that number rather than re-asking. "
        "EXCEPTION: if 'to' appears BETWEEN two numbers (e.g. '30 to 50'), it is a RANGE connector, NOT 'सौ'/hundred — read it as 'between 30 and 50', never as 3050 or 3250.)\n"
        "  • Budget field: clearly non-numeric and not a 'not sure' variant — re-ask once\n"
        "  • Answer is an obvious non-answer — sarcasm, a counter-question about something unrelated, gibberish\n"
        "  • Sarcastic/indirect: 'paidal lene aa jaana' ≠ delivery/pickup — re-ask\n\n"
        "When probing: re-ask once, naturally, different phrasing each time, short options reminder.\n"
        "If still unclear after one probe → mark Not Sure, move on. NEVER a third ask. This is a hard rule.\n"
        "COUNTING: Each question gets maximum 2 attempts total (1 original ask + 1 re-ask). After that, Not Sure, next question. No exceptions, no matter how important the answer seems.\n"
        "Never echo an answer back to 'confirm' it. Valid answer → acknowledge and continue.\n"
        "Never explain, justify, or comment on the buyer's choice. Do NOT say why their answer is good or what it implies.\n"
        "WRONG: 'बिल्कुल, automatic में less manual effort लगता है।' — this is unsolicited commentary on their choice.\n"
        "RIGHT: 'बिल्कुल जी।' → next question.\n\n"

        "SPECIFIC SITUATIONS\n\n"
        "Buyer asks what the difference between options is:\n"
        "One neutral factual sentence — no opinion or recommendation. Then re-ask with all options.\n"
        "Example: \"Split AC में indoor और outdoor दोनों units होते हैं, window AC एक single unit होती है — तो आपको कौन सा चाहिए?\"\n\n"
        "Brand preference question:\n"
        "Ask naturally: \"कोई खास brand prefer करते हैं, या कुछ भी चलेगा?\"\n"
        "Accept any brand name, even unfamiliar ones. Never list brands yourself.\n"
        "Unknown brand: \"ज़रूर — ऐसे sellers से connect कराएंगे.\" Move on.\n\n"
        "Budget question:\n"
        "Accept any number or range. If genuinely vague ('thoda', 'reasonable') — re-ask once. Then Not Sure.\n\n"
        "Quantity question:\n"
        "Accept any digit, Hindi number word, OR a quantity range ('30 to 50', '30 se 50', '20–40', 'between 20 and 40'). "
        "If the buyer gives a range, record it as-is (e.g. '30–50 units') — NEVER concatenate the two numbers into one (\"30 to 50\" is a range of 30–50, NOT the number 3250). "
        "If the answer is a non-numeric word that cannot be a number — re-ask once with the unit.\n\n"
        "NEW BUYER / FIRST TIME / 'I DON'T KNOW' (CRITICAL):\n"
        "Signals: 'main naya hoon', 'bilkul naya hoon', 'pehli baar le raha hoon', 'mujhe kuch pata nahi', 'aap hi batao', 'jo accha ho wahi chahiye', 'mujhe kaise pata hoga', 'samajh nahi aata'.\n"
        "→ DO NOT re-ask the same question. That is the worst thing you can do to a new buyer.\n"
        "→ Empathize briefly: \"कोई बात नहीं जी, sellers आपको सब guide कर लेंगे.\"\n"
        "→ Mark the current question as Not Sure and MOVE ON to the next question immediately.\n"
        "→ If ALL remaining questions are getting 'I don't know' responses — close the call warmly. The buyer is engaged and interested; sellers will handle the rest.\n\n"
        "BUYER ASKS TO BE CONNECTED WITH A SELLER / EXPERT:\n"
        "Signals: 'kisi se baat karao', 'seller se milao', 'expert se baat karni hai', 'koi jaankaar chahiye', 'aap kisi ko bhejo', 'directly baat karni hai'.\n"
        "→ This is a STRONG positive signal — the buyer IS interested, they just want expert guidance.\n"
        "→ Respond warmly: \"ज़रूर जी, मैं आपको relevant sellers से connect करूंगी — वो सब detail में guide करेंगे.\"\n"
        "→ For any remaining unanswered questions: mark them Not Sure and proceed directly to closing.\n"
        "→ Close the call. Do NOT keep asking questions after this signal.\n\n"
        "CALLER IS A SELLER / MANUFACTURER OF THIS PRODUCT (CRITICAL):\n"
        "Signals — caller says they make, sell, supply, or distribute THE SAME PRODUCT they were asked about:\n"
        "  'main manufacturer hoon', 'main banata hoon', 'main bechta hoon', 'main supplier hoon',\n"
        "  'main dealer hoon', 'main distributor hoon', 'yahi toh main bechta hoon',\n"
        "  'hamari company yahi banati hai', 'meri factory mein yahi banta hai',\n"
        "  'main khud iska wholesale karta hoon', 'hum log yahi supply karte hain'.\n"
        "→ Do NOT continue asking qualification questions. Ask exactly ONE confirmation:\n"
        "  \"अच्छा जी — तो आप [product] खुद बेचते / बनाते हैं, खरीदने के लिए नहीं?\"\n"
        "→ If confirmed: close warmly — \"ठीक है जी, समझ गई. तो आपको इस product की ज़रूरत नहीं होगी. आपके time के लिए धन्यवाद.\"\n"
        "→ If denied (they actually ARE a buyer): apologize briefly and continue from the current question — \"माफी जी, मैं समझ गई — तो [current question]?\"\n"
        "IMPORTANT: Do NOT ask any spec questions after the seller signal. One confirmation, then close or continue — nothing else in between.\n\n"

        "SERVICE / INSTALLATION / REPAIR / AMC OF THE SAME PRODUCT — A NEW LEAD, NOT A COMPLAINT (CRITICAL):\n"
        "Signals — buyer wants servicing, installation, repair, or AMC/maintenance of the opening product, as a fresh requirement, with NO mention of a specific past purchase, defect, or seller:\n"
        "  'iski service karvani hai', 'service chahiye', 'servicing karvani hai', 'installation karni hai',\n"
        "  'fitting karvani hai', 'maintenance chahiye', 'AMC chahiye', 'repair karvani hai'.\n"
        "→ The FIRST time you hear this, treat it as CONFIRMED intent — do NOT re-ask the opening confirmation a second or third time in slightly different words ('क्या आपको X चाहिए?' → 'क्या आपको X की service चाहिए?' → 'क्या आपको X के लिए service चाहिए?'). One acknowledgement, then move immediately — looping the same yes/no question is the worst thing you can do here.\n"
        "→ This is a category change (buying the product vs. servicing it are different lead categories) — follow the SERVICE trigger in the PRODUCT CHANGE — TOOL RULE section below to fetch the correct schema. Do NOT continue asking the ORIGINAL product's purchase-oriented questions (e.g. brand preference, features to choose when buying) for a servicing request.\n"
        "→ DISTINCTION FROM GRIEVANCE: if the buyer mentions a defect, a seller who didn't respond, refund, or 'not working' about something they ALREADY bought — that is GRIEVANCE below (redirect + close), not a new service lead.\n\n"

        "GRIEVANCE / COMPLAINT (CRITICAL):\n"
        "Signals — caller mentions a complaint, defective product, seller not responding, delivery not received, refund, repair, service not given, or anything framed as a complaint or problem with a past purchase:\n"
        "  'complaint hai', 'shikayat hai', 'complaint darj karni hai', 'problem aa rahi hai',\n"
        "  'kaam nahi kar raha', 'band ho gaya', 'nahi bheja', 'call nahi kar raha', 'jawab nahi deta',\n"
        "  'wapas karna hai', 'refund chahiye', 'repair karni hai', 'service nahi mili'.\n"
        "→ Do NOT try to log, register, or handle the complaint. You are a lead qualification agent — not a complaint handler.\n"
        "→ Acknowledge briefly and redirect in one sentence: \"जी, complaints के लिए आपको VoiceDesk की website पर जाकर Customer Care section में contact करना होगा — वहाँ पूरी मदद मिलेगी.\"\n"
        "→ Then close warmly: \"आपके time के लिए धन्यवाद.\" → stop.\n"
        "→ Do NOT ask any qualification questions after a grievance signal.\n\n"

        "PERSISTENT OFF-TOPIC (buyer keeps avoiding the question):\n"
        "→ First off-topic: engage briefly with their point, then re-ask.\n"
        "→ Second off-topic on same question: re-ask once more, different phrasing.\n"
        "→ Third time with no answer: accept Not Sure, move on. Never loop more than twice on any question.\n\n"

        "━━━ VERY LARGE QUANTITY — CONFIRMATION STEP ━━━\n\n"
        "If the buyer answers a QUANTITY question with a number ≥ 5,000 (of any unit), do NOT accept it immediately.\n"
        "First ask ONE confirmation question — word-for-word repeat the number and unit:\n"
        "  \"जी, क्या आपको [number] [unit] की requirement है?\"\n"
        "Examples:\n"
        "  Buyer says '50,000 kg'  → Ask: \"जी, क्या आपको 50,000 kg की requirement है?\"\n"
        "  Buyer says 'पचास हज़ार piece' → Ask: \"जी, क्या आपको 50,000 piece की requirement है?\"\n"
        "  Buyer says '10 lakh litre'  → Ask: \"जी, क्या आपको 10 lakh litre की requirement है?\"\n\n"
        "Wait for response:\n"
        "  • Confirmed (हाँ / yes / हां जी / bilkul / सही है): accept the number, continue normally.\n"
        "  • Corrected (buyer gives a different number): accept the corrected value, do NOT re-confirm again.\n"
        "  • Unclear / no answer: re-ask the original quantity question once — \"तो आपको कितनी quantity चाहिए?\"\n\n"
        "CRITICAL: Ask this confirmation exactly ONCE. Never ask it twice for the same answer.\n"
        "CRITICAL: Do NOT apply this rule for numbers below 5,000 — no echoing or confirming small quantities.\n\n"

        "Product change mid-call:\n"
        "\"आपको [original] चाहिए या [new product]?\" — wait for answer.\n\n"
        "Off-topic / irrelevant:\n"
        "Brief warm acknowledge, then re-ask: \"हाँ — तो [current question]?\"\n"
        "Persistent off-topic loop (3+ times): \"मैं सिर्फ requirements note कर रही हूँ — [current question]?\"\n\n"
        "Not interested: say the NOT-INTERESTED close (defined above) → stop\n"
        "Job-seeking caller — TWO cases, handled differently:\n"
        "\n"
        "CASE A — Explicit upfront signal: caller directly states they want a job before any qualification questions begin.\n"
        "Signals (common examples — not exhaustive): \"नौकरी चाहिए\", \"जॉब चाहिए\", \"job chahiye\", \"naukri chahiye\", \"rozgar chahiye\", \"job milega\", \"vacancy hai kya\", \"apply karna hai\", \"job ke liye apply\", \"job se related hoon\", \"जॉब से रिलेटेड\", \"job search kar raha/rahi hoon\", \"job dhundh raha/rahi hoon\", \"interview ke liye call\", \"resume bheja tha\", \"fresher hoon\", \"part time job chahiye\", \"ghar se kaam chahiye\".\n"
        "Core rule: if the caller's first substantive response makes clear they are personally seeking employment — not buying a product — close immediately. Do NOT ask a clarifying question for Case A.\n"
        "Action: acknowledge in one sentence, then close immediately with the NOT-INTERESTED phrase.\n"
        "Example: one line — \"जी, यह VoiceDesk का product enquiry number है — job opportunities के लिए हम help नहीं कर सकते.\" — then the NOT-INTERESTED close (defined above). → stop\n"
        "\n"
        "CASE B — Mid-conversation signal: caller has already confirmed the product, but their answers to qualification questions reveal they are describing their OWN experience, qualifications, or career (not a hiring need).\n"
        "Examples of mid-conversation signals: \"mera experience 5 saal hai\", \"maine CNC operate kiya\", \"mujhe job karna tha\", \"main khud job dhundh raha hoon\", \"मैंने diploma किया है\", \"experience है मेरे पास\".\n"
        "Action: ask ONE clarification question before closing — do not assume.\n"
        "Clarification: \"जी, समझ गई — क्या आप खुद के लिए job ढूंढ रहे हैं, या किसी को hire करने के लिए placement चाहिए?\"\n"
        "  → If they confirm job-seeking (\"खुद के लिए\", \"job chahiye mujhe\", \"haan job dhundh raha hoon\", etc.): close with the NOT-INTERESTED phrase. → stop\n"
        "  → If they confirm hiring (\"hire karna hai\", \"placement chahiye\", \"kisi ko rakhna hai\", etc.): continue qualification from where you left off.\n"
        "  → If still unclear after clarification: treat as job-seeker and close. → stop\n"
        "Rude, abusive, or profane language: close immediately — \"ठीक है जी, शुक्रिया.\" → stop. Do NOT respond to the content of abusive speech.\n"
        "Reschedule: \"ठीक है जी, [time] पे बात करते हैं.\" → stop\n"
        "EXCEPTION — enrichment complete: if ALL qualification questions are already answered (every question has a real answer or Not Sure), do NOT use the reschedule phrase regardless of whether a specific time was given. Instead say the Step 3 closing line (defined above). → stop\n"
        "CRITICAL — TIME-REFERENCE OVERRIDES QUANTITY: If the buyer says any number word (चार, पाँच, दस, 4, 5, etc.) followed by OR near a time-of-day word (बजे, o'clock, AM, PM, बजे के बाद, बजे तक, घंटे बाद) — treat the ENTIRE utterance as a reschedule request, NOT as a quantity answer. Even if you are currently on the quantity question. Even if the number appears first and the time word appears in a fragment you only partially heard. Respond: \"ठीक है जी, [time] पे बात करते हैं.\" → stop immediately. Do NOT ask the quantity question again.\n"
        "Example: buyer says 'मैम, चार बजे बात कर रहे हैं' or just 'चार बजे' or 'four बजे' while you are asking about quantity → this is reschedule, not an answer of 4 units.\n\n"

        "━━━ BEFORE YOU RESPOND ━━━\n\n"
        "1. Did the buyer answer the current question? Trust clear intent — accept it and move on.\n"
        "   Genuinely suspicious answer? Probe once. Still unclear? Mark Not Sure and continue.\n"
        "2. Am I asking exactly one question — not two, not zero?\n"
        "3. About to name a brand / quote a price / share an opinion? → Deflect first, then re-ask.\n"
        "4. Have ALL questions been answered? If not — do not close, no matter how natural it feels.\n"
        "5. Does my response sound like a real person mid-conversation, or like a form-filler?"
    ),
    "initial_message": "हेलो, मैं Simran बोल रही हूँ VoiceDesk से — आपको {product} की requirement है ना?",
    "call_end_text": "ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.",
    "function_calling": True,
    "functions": [
        {
            "name": "FetchLead",
            "description": "Fetch customer lead details from VoiceDesk MIS API at call start.",
            "url": "http://192.168.8.67:8000/leads/ai-lead-qualify/mis",
            "method": "GET",
            "headers": {},
            "query_params": {"lead_id": "", "mobile": "", "page": "1", "limit": "1", "ai_partner": "inh-suny-bot"},
            "body_format": "json",
            "custom_body": "",
            "schema": {},
        },
        {
            "name": "FetchCategorySchema",
            "description": "Call this when the buyer changes their product requirement mid-call. Fetches the new qualification schema for the new product category.",
            "url": f"{MIS_API_BASE}/leads/ai-lead-qualify/search",
            "method": "GET",
            "headers": {},
            "query_params": {"lead_id": "", "search_term": ""},
            "body_format": "json",
            "custom_body": "",
            "schema": {
                "type": "object",
                "properties": {
                    "srchterm": {"type": "string", "description": "New product search term in English"}
                },
                "required": ["srchterm"],
            },
        },
    ],
    "api_urls": {
        "mis_api_base": "http://192.168.8.67:8000",
        "category_change_api": f"{MIS_API_BASE}/leads/ai-lead-qualify/search",
    },
"prompt_config": {
        "script_rule": (
            "By default, write in Hindi (Devanagari) script.\n"
            "Natural Hinglish is encouraged — mix in everyday English words the way a real call center agent would (e.g. 'okay', 'sure', 'details', 'sellers', 'connect', 'requirement').\n"
            "API-provided English words (from question.text or option.text): always use them exactly as-is.\n"
            "EXCEPTION — Language switching: If the caller has explicitly asked you to speak in a different language (English or any other), switch to that language entirely and do NOT write in Devanagari for the rest of the call.\n"
            "Outside of an explicit language-switch request, do NOT output non-Devanagari script.\n"
        ),
        "closing_instruction": (
            "Once every question has an answer, say the closing line in whichever language is active:\n"
            "• Hindi (default): \"ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.\"\n"
            "• English (if language was switched): \"Alright, I have all the details. The relevant sellers will contact you soon. Thank you for your time.\"\n\n"
            "Say this once, only when ALL questions are done — not after just the budget question, not mid-call.\n"
            "Don't add anything after the closing line. The call ends there."
        ),
        "timeout_message": (
            "जी, मुझे सिर्फ 5 मिनट तक बात करने की permission है. "
            "जो भी details मिली हैं, sellers जल्द ही आपसे contact करेंगे. "
            "आपका समय देने के लिए धन्यवाद. अलविदा!"
        ),
    },
    "language": "hindi",
    "temperature": 0.7,
    "gemini_start_sensitivity": "START_SENSITIVITY_HIGH",
    "gemini_end_sensitivity": "END_SENSITIVITY_LOW",
    "gemini_silence_duration_ms": 1500,
    "gemini_prefix_padding_ms": 200,
    "max_call_duration": 300,
    "sarvam_min_rms": 600,
    "sarvam_min_speech_ms": 500,
    "sarvam_min_speech_ms_singleword": 800,
    "sarvam_silero_threshold": 0.5,
    "sarvam_silero_min_speech_ms": 120,
    "gemini_silero_fallback_speech_ms": 150,
    "post_speech_hold_ms": 300,
    "filler_message": ["अच्छा,", "हाँ,", "जी,", "तो,", "ठीक है,"],
    "function_filler_message": ["एक moment जी,", "जी, देख रही हूँ,"],
}


# Reason codes for why fetch_bot_config fell back to _HARDCODED_BOT_CONFIG — used by
# record_fallback_event() below so "why did this call get the hardcoded Simran bot" is a
# queryable Mongo record instead of something only visible by grepping a worker's local log
# file after the fact (which was exactly the problem the night this was added: three
# different, unrelated causes — a stale duplicate worker process, a worker assignment
# timeout, and this function's own None-returns — all produced the identical symptom, and
# telling them apart required reading raw logs across two machines).
FALLBACK_REASON_NO_IDS = "no_ids_in_room_metadata"
FALLBACK_REASON_MALFORMED_IDS = "malformed_ids"
FALLBACK_REASON_DB_UNREACHABLE = "platform_db_unreachable"
FALLBACK_REASON_VERSION_NOT_FOUND = "version_not_found"
FALLBACK_REASON_CONFIG_EMPTY = "version_doc_has_no_config"


async def fetch_bot_config(bot_id: str, test_bot_version_id: str) -> tuple[dict | None, str]:
    """Resolve a specific bot version's config from the dashboard's config store, for
    dashboard "Test Call" runs only (see backend/routers/testcall.py, which is the only
    dispatch path that puts bot_id/test_bot_version_id into room metadata today).

    Returns (None, reason) — caller falls back to _HARDCODED_BOT_CONFIG — if either id is
    missing, malformed, the platform DB is unreachable, or no matching version is found;
    this keeps real production calls, whose room metadata never carries these keys,
    completely unaffected. reason is one of the FALLBACK_REASON_* constants above, or ""
    on success, so callers can record *why* without re-deriving it from log text."""
    if not bot_id or not test_bot_version_id:
        return None, FALLBACK_REASON_NO_IDS
    try:
        version_oid = ObjectId(test_bot_version_id)
        bot_oid = ObjectId(bot_id)
    except (InvalidId, TypeError):
        logger.warning(f"[CONFIG] Malformed bot_id/test_bot_version_id in room metadata: {bot_id!r}/{test_bot_version_id!r}")
        return None, FALLBACK_REASON_MALFORMED_IDS

    loop = asyncio.get_running_loop()
    try:
        version_doc = await loop.run_in_executor(
            None,
            lambda: _get_platform_db()["tbl_ai_vb_bot_versions"].find_one(
                {"_id": version_oid, "bot_id": bot_oid}
            ),
        )
    except Exception as exc:
        logger.warning(f"[CONFIG] Could not reach platform DB for bot_id={bot_id!r} version={test_bot_version_id!r}: {exc}")
        return None, FALLBACK_REASON_DB_UNREACHABLE

    if not version_doc:
        logger.warning(f"[CONFIG] No bot_version found for bot_id={bot_id!r} version={test_bot_version_id!r}")
        return None, FALLBACK_REASON_VERSION_NOT_FOUND
    config = version_doc.get("config") or None
    if config is None:
        return None, FALLBACK_REASON_CONFIG_EMPTY
    return config, ""


def record_fallback_event(
    *, room_name: str, bot_id: str, test_bot_version_id: str, reason: str, worker: str,
) -> None:
    """Best-effort, queryable audit trail for every call that ran on _HARDCODED_BOT_CONFIG
    instead of the dashboard-configured bot — surfaced via GET /api/diagnostics/fallback-events
    (backend/routers/diagnostics.py). Never raises: a logging failure must not affect the call
    it's describing, and this fires from inside the hot call-setup path on every worker."""
    try:
        _get_platform_db()["tbl_ai_vb_bot_config_fallback_events"].insert_one({
            "room_name": room_name,
            "bot_id": bot_id,
            "test_bot_version_id": test_bot_version_id,
            "reason": reason,
            "worker": worker,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as exc:
        logger.warning(f"[CONFIG] record_fallback_event failed (non-fatal): {exc}")


_WORKER_HEARTBEAT_INTERVAL_S = float(os.getenv("WORKER_HEARTBEAT_INTERVAL_S", "10"))


def _worker_heartbeat_loop(agent_name: str, pid: int) -> None:
    coll = _get_platform_db()["tbl_ai_vb_worker_heartbeats"]
    hostname = socket.gethostname()
    started_at = datetime.now(timezone.utc)
    while True:
        try:
            coll.update_one(
                {"agent_name": agent_name},
                {"$set": {
                    "agent_name": agent_name,
                    "pid": pid,
                    "host": hostname,
                    "last_seen": datetime.now(timezone.utc),
                    "started_at": started_at,
                }},
                upsert=True,
            )
        except Exception as exc:
            logger.warning(f"[WORKER HEARTBEAT] update failed (non-fatal): {exc}")
        time.sleep(_WORKER_HEARTBEAT_INTERVAL_S)


def start_worker_heartbeat(agent_name: str) -> None:
    """Starts a background thread that upserts this worker process's liveness into
    tbl_ai_vb_worker_heartbeats every _WORKER_HEARTBEAT_INTERVAL_S seconds, surfaced via
    GET /api/diagnostics/worker-health. Without this, a crashed or network-partitioned
    worker is completely invisible until someone places a real call and it silently hangs
    on "waiting for bot to join" — call once from the top-level `if __name__ ==
    "__main__":` guard of each worker entrypoint script, before cli.run_app(...) (which
    blocks), not from inside per-job code — one heartbeat per worker process, not per call."""
    t = threading.Thread(
        target=_worker_heartbeat_loop, args=(agent_name, os.getpid()), daemon=True, name="worker-heartbeat",
    )
    t.start()


# ---------------------------------------------------------------------------
# Language tables
# ---------------------------------------------------------------------------

HINDI_LANG_CONFIG = {
    "name": "Hindi",
    "timeout_message": "जी, details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए धन्यवाद.",
    "stt_lang_code": "hi-IN",
    "inactivity_phrase": "क्या आप अभी line पर हैं?",
    "inactivity_end_phrase": "जी, कोई response नहीं आया, इसलिए मैं call समाप्त कर रही हूँ. अगर future में आपको किसी भी तरह की requirement हो, तो आप VoiceDesk पर कभी भी call कर सकते हैं. धन्यवाद.",
    "lang_notes": (
        "LANGUAGE NOTES — HINDI\n\n"
        "INPUT: The buyer typically speaks Hindi, Hinglish, or Indian-accented English. If audio is unclear and no explicit language-switch has happened, assume Hindi. If the buyer clearly speaks in English or explicitly requests a language change, honour it — refer to LANGUAGE SWITCHING rules above.\n\n"
        "STYLE: Natural spoken Hinglish — how a real person talks on a call. Conversational, warm, never formal or literary.\n"
        "  Good: 'हाँ जी', 'अच्छा', 'ठीक है', 'samajh gaya', 'okay jee'\n"
        "  Avoid: 'आपकी बात सुनकर खुशी हुई', 'मैं आपकी सहायता के लिए यहाँ हूँ'\n\n"
        "FILLERS — STRICT RULE:\n"
        "You MAY start a response with a filler word (अच्छा, हाँ, जी, तो, ठीक है) BUT you MUST continue immediately into your answer in the SAME sentence — NEVER end your turn on a filler alone.\n"
        "✓ CORRECT:  'जी, कितनी quantity चाहिए?'\n"
        "✗ WRONG:    'जी.' [stop] ... [pause] ... 'कितनी quantity चाहिए?'\n"
        "The filler and the question must be ONE continuous utterance with no pause between them. Vary fillers; don't start every response with 'अच्छा'.\n\n"
        "TTS: Write 'डेढ़ ton' not '1.5 ton'. Write 'ढाई ton' not '2.5 ton'.\n\n"
        "NUMBERS — HARD RULE: Always say a number as a whole number, never digit-by-digit (Hindi 'shunya' for zero sounds broken when repeated). "
        "If a number must be read digit-by-digit (e.g. a code like a grade number), spell the digits out in English words, never in Hindi — "
        "e.g. 1100 → 'one one zero zero', 3003 → 'three zero zero three', 5052 → 'five zero five two'.\n\n"
        "DECIMALS — HARD RULE: Never say 'दशमलव' (Hindi for decimal point) when reading a decimal number aloud. Always read decimal options the English way instead — "
        "e.g. 9.3 → 'nine point three', 3.4 → 'three point four'.\n\n"
        "NEVER use these overly formal words:\n"
        "शयनकक्ष, बैठक कक्ष, कार्यालय, स्थापित, आवश्यकता, पर्याप्त, उपयुक्त, उचित, सूचित, प्राप्त, विवरण, अनुसार, सुविधाजनक\n\n"
        "RELIGIOUS / CULTURAL GREETINGS — STRICT RULE:\n"
        "Phrases like 'जय जय गुरुदेव', 'जय श्री राम', 'जय माता दी', 'राधे राधे', 'jai gurudev', 'jai shri ram' "
        "are regional phone-answering greetings — NOT expressions of disinterest or goodbye. "
        "When the buyer says any such phrase, acknowledge warmly with a short 'जी जी' or 'जी, बिल्कुल' "
        "and IMMEDIATELY continue the product qualification. NEVER close the call or say 'कोई बात नहीं' in response to these."
    ),
}

ENGLISH_LANG_CONFIG = {
    "name": "English",
    "timeout_message": "Great, we've got your details. Relevant sellers will reach out to you shortly. Thanks for your time.",
    "stt_lang_code": "en-IN",
    "inactivity_phrase": "Are you still there?",
    "inactivity_end_phrase": "Since we haven't heard a response, I'll end the call here. Feel free to call VoiceDesk again anytime you have a requirement. Thank you.",
    "lang_notes": (
        "LANGUAGE NOTES — ENGLISH\n\n"
        "INPUT: The buyer is speaking English. Stay in English for the rest of the call unless they explicitly ask to switch.\n\n"
        "STYLE: Natural spoken Indian-English — how a real person talks on a call. Conversational and warm, never formal or literary.\n"
        "  Good: 'Sure', 'Okay, got it', 'Alright'\n"
        "  Avoid: 'It brings me great pleasure to assist you', 'I am here to help you'\n\n"
        "NUMBERS — HARD RULE: Always say a number as a whole number, never digit-by-digit unless it's a code (e.g. 1100 → 'eleven hundred', a code like 1100 read digit-by-digit → 'one one zero zero').\n\n"
        "DECIMALS: Read decimals the natural way — e.g. 9.3 → 'nine point three'."
    ),
}

# Registry of supported conversational languages, keyed by the BotConfig.language
# value. Add a new language by adding an entry here — no other code changes
# needed as long as the entry provides the same keys as the ones above.
LANG_CONFIGS: dict = {
    "hi": HINDI_LANG_CONFIG,
    "en": ENGLISH_LANG_CONFIG,
}
# Accept the older full-word spellings too, since some existing bot configs / scripts use them.
_LANG_KEY_ALIASES = {"hindi": "hi", "english": "en"}
DEFAULT_LANG_KEY = "hi"


def resolve_lang_config(bot_config: dict | None) -> dict:
    """Per-bot language lookup. bot_config['language'] is a free-text key into
    LANG_CONFIGS (e.g. "hi", "en"); unknown/missing values fall back to
    DEFAULT_LANG_KEY so existing bots keep behaving exactly as before."""
    key = ((bot_config or {}).get("language") or DEFAULT_LANG_KEY).strip().lower()
    key = _LANG_KEY_ALIASES.get(key, key)
    return LANG_CONFIGS.get(key, LANG_CONFIGS[DEFAULT_LANG_KEY])


# Tone presets layered on top of whichever language is selected. "casual" matches
# today's existing behaviour (the default), "formal" is an opt-in per-bot variant.
TONE_CONFIGS: dict = {
    "casual": {
        "name": "Casual",
        "notes": (
            "TONE — CASUAL (default)\n\n"
            "Speak like a friendly, efficient call-center agent talking to someone they respect but aren't stiff with. "
            "Contractions, colloquial fillers, and a relaxed pace are all fine. Keep sentences short."
        ),
    },
    "formal": {
        "name": "Formal",
        "notes": (
            "TONE — FORMAL\n\n"
            "Speak politely and professionally — as if addressing a senior client. Avoid slang and overly casual fillers. "
            "Use complete, courteous sentences (e.g. prefer a respectful register over shortened colloquial phrasing), "
            "but stay natural and conversational — never robotic or literary. Do not become curt or terse."
        ),
    },
}
DEFAULT_TONE_KEY = "casual"


def resolve_tone_config(bot_config: dict | None) -> dict:
    key = ((bot_config or {}).get("tone") or DEFAULT_TONE_KEY).strip().lower()
    return TONE_CONFIGS.get(key, TONE_CONFIGS[DEFAULT_TONE_KEY])


# Back-compat aliases — some call sites still reference these module-level names.
INACTIVITY_PHRASE = HINDI_LANG_CONFIG["inactivity_phrase"]
INACTIVITY_END_PHRASE = HINDI_LANG_CONFIG["inactivity_end_phrase"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_mobile(number: str) -> str:
    n = number.strip().replace(" ", "").replace("-", "")
    if n.startswith("+91"):
        n = n[3:]
    elif n.startswith("91") and len(n) == 12:
        n = n[2:]
    if n.startswith("0") and len(n) == 11:
        n = n[1:]
    return n


async def fetch_lead(lead_id: str = "", mobile: str = "", mis_api_base: str = MIS_API_BASE) -> dict | None:
    today = _date.today().strftime("%Y-%m-%d")
    yesterday = (_date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    if lead_id:
        params = f"lead_id={lead_id}&page=1&limit=1&ai_partner=inh-suny-bot&fromdate={yesterday}&todate={today}"
    elif mobile:
        params = f"mobile={mobile}&page=1&limit=1&ai_partner=inh-suny-bot&fromdate={yesterday}&todate={today}"
    else:
        return None

    url = f"{mis_api_base}/leads/ai-lead-qualify/mis?{params}"
    logger.info(f"[API FETCH] GET {url}")
    try:
        session = _get_http_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            data = await resp.json()
            results_block = data.get("results", {})
            records = results_block.get("data", [])
            total = results_block.get("total", results_block.get("count", "?"))
            if records:
                record = records[0]
                buyer = record.get("buyer_details", {})
                logger.info(
                    f"[API FETCH] {resp.status} OK — total={total} | "
                    f"lead_id={record.get('_id')} | catname={record.get('catname')} | "
                    f"srchterm={record.get('search_context', {}).get('searched_keyword', '')} | "
                    f"buyer={buyer.get('buyer_name')} | city={buyer.get('buyer_city')} | "
                    f"mobile={buyer.get('buyer_number')}"
                )
                return record
            else:
                logger.warning(
                    f"[API FETCH] {resp.status} — no results (total={total}) | raw={data}"
                )
    except Exception as e:
        logger.error(f"[API FETCH] fetch_lead failed: {e}")
    return None


async def _build_sample_from_search(srchterm: str, buyer_name: str, category_api: str, lead_id: str = "test_lead") -> dict | None:
    if not category_api:
        return None
    try:
        params = {"lead_id": lead_id, "search_term": srchterm}
        async with _get_http_session().get(category_api, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = json.loads(await resp.text())
        schema = data.get("results", {}).get("search_result", {}) if isinstance(data, dict) else {}
        catname = schema.get("catname", srchterm)
        questions = schema.get("question", [])
        return {
            "_id": "test_lead", "call_id": "TEST_CALL",
            "buyer_details": {"buyer_name": buyer_name, "buyer_number": "0000000000", "buyer_city": "", "is_business": 0},
            "search_context": {
                "searched_keyword": srchterm,
                "searched_product": {"product_name": catname, "product_id": "", "attributes": {}},
            },
            "catname": catname,
            "qualification_schema": schema,
        }
    except Exception as e:
        logger.error(f"[Sample] Failed to fetch schema for '{srchterm}': {e}")
        return None


async def _execute_function_call(fn_name: str, fn_args: dict, functions: list[dict], call_state: dict) -> dict:
    fn_cfg = next((f for f in functions if f.get("name") == fn_name), None)
    if not fn_cfg:
        return {"error": f"Function {fn_name!r} not configured"}

    url = fn_cfg.get("url", "")
    method = fn_cfg.get("method", "POST").upper()
    headers = fn_cfg.get("headers") or {}
    merged = {**(fn_cfg.get("query_params") or {}), **fn_args}
    if fn_name == "FetchCategorySchema":
        # New search API expects lead_id + search_term; we keep tool arg name as `srchterm`
        # for LLM friendliness and map it here.
        srchterm = merged.pop("srchterm", "") or merged.pop("search_term", "")
        lead_id = call_state.get("record_id") or (call_state.get("lead_record") or {}).get("_id") or ""
        merged["lead_id"] = lead_id
        merged["search_term"] = srchterm
    # Per-function timeout (default 10s preserves prior behavior); body encoding honors the
    # configured body_mode (json default, or 'form'). See custom_functions.py.
    timeout = aiohttp.ClientTimeout(total=resolve_timeout_seconds(fn_cfg, default=10.0))
    body_mode = fn_cfg.get("body_mode") or ("form" if fn_cfg.get("body_format") == "form" else "json")
    logger.info(f"[FnCall] {method} {url} | args={merged} | timeout={timeout.total}s")

    try:
        sess = _get_http_session()
        if method == "GET":
            async with sess.get(url, params=merged, headers=headers, timeout=timeout) as resp:
                result = json.loads(await resp.text())
        elif body_mode == "form":
            async with sess.request(method, url, data=merged, headers=headers, timeout=timeout) as resp:
                result = json.loads(await resp.text())
        else:
            async with sess.request(method, url, json=merged, headers=headers, timeout=timeout) as resp:
                result = json.loads(await resp.text())

        # Extract configured response fields into dynamic variables (call_state["vars"]) so the
        # prompt and post-call functions can reference them. No-op when store_variables unset.
        _stored = apply_store_variables(fn_cfg.get("store_variables"), result, call_state.setdefault("vars", {}))
        if _stored:
            logger.info(f"[FnCall] {fn_name} stored vars: {list(_stored)}")

        if fn_name == "FetchCategorySchema":
            schema = result.get("results", {}).get("search_result", {}) if isinstance(result, dict) else {}
            questions = _reorder_questions_for_confidence(schema.get("question", []))
            schema["question"] = questions
            catname = schema.get("catname", "the new product")
            lead_record = call_state.get("lead_record") or {}
            old_product = lead_record.get("catname", "")

            # Persist new schema so save_call_data and the callback worker
            # analyze the buyer's answers against the NEW questions.
            lead_record["qualification_schema"] = schema
            lead_record["catname"] = catname
            search_ctx = lead_record.setdefault("search_context", {})
            search_ctx["searched_keyword"] = catname
            search_ctx.setdefault("searched_product", {})["product_name"] = catname
            call_state["lead_record"] = lead_record

            # Receiver expects {"product_name": "<new product>"} only.
            call_state["product_change"] = {"product_name": catname}

            questions_text = build_questions_text({"question": questions})
            first_q = questions[0].get("text", "").strip() if questions else ""
            logger.info(
                f"[FetchCategorySchema] Product changed: {old_product!r} → {catname!r} "
                f"| {len(questions)} questions | schema persisted to lead_record"
            )
            return {
                "success": True,
                "product": catname,
                "total_questions": len(questions),
                "questions_text": questions_text,
                "instruction": (
                    f"The buyer now needs {catname}. "
                    f"Acknowledge their new requirement naturally and briefly (do NOT say 'product change' or announce a change — just acknowledge what they need). "
                    f"Then IMMEDIATELY ask Question 1: '{first_q}'. "
                    f"Do NOT say anything about sellers or closing the call."
                ),
            }
        return result
    except Exception as e:
        logger.error(f"[FnCall] {fn_name} failed: {e}")
        return {"error": str(e)}


async def call_configured_function(func_config: dict, runtime_params: dict) -> dict | None:
    """Fire a configured custom function (used by pre_call / post_call lifecycle hooks).

    Request shape and body encoding are computed by custom_functions.build_http_call so the
    logic stays unit-tested and identical to the in-call tool path. `custom_body` may be a
    JSON string on legacy configs — parse it before merging. Returns the decoded JSON
    response, or None on any failure (lifecycle hooks must never break a call)."""
    if not (func_config.get("url") or "").strip():
        return None

    # Legacy configs stored custom_body as a JSON string; normalize to a dict.
    cfg = dict(func_config)
    body = cfg.get("custom_body")
    if isinstance(body, str):
        try:
            cfg["custom_body"] = json.loads(body)
        except Exception:
            cfg["custom_body"] = {}

    # Preserve prior behavior: lifecycle hooks don't send empty/falsy runtime params.
    filtered_params = {k: v for k, v in (runtime_params or {}).items() if v}
    call = build_http_call(cfg, filtered_params)
    timeout = aiohttp.ClientTimeout(total=resolve_timeout_seconds(cfg, default=8.0))
    logger.info(f"[FUNC CALL] {call['method']} {call['url']} | timeout={timeout.total}s")
    try:
        session = _get_http_session()
        async with session.request(
            call["method"], call["url"], headers=call["headers"],
            params=call["params"], json=call["json"], data=call["data"], timeout=timeout,
        ) as resp:
            return await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"[FUNC CALL] {func_config.get('name')!r} failed: {e}")
    return None


async def save_call_log_to_backend(payload: dict):
    url = f"{BACKEND_URL}/backend/api/call-logs"
    try:
        session = _get_http_session()
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                logger.warning(f"[CALL LOG] Backend {resp.status}: {text[:200]}")
    except Exception as e:
        logger.error(f"[CALL LOG] Failed to save: {e}")



# ---------------------------------------------------------------------------
# Prompt / schema helpers (verbatim from Pipecat bot)
# ---------------------------------------------------------------------------

_GENERIC_OPTIONS = {
    "yes", "no", "not sure", "maybe", "both", "none", "other", "others",
    "don't know", "dont know", "not decided", "undecided", "any", "either",
}


def _question_confidence_priority(q: dict) -> int:
    """Lower = buyer is more likely to give a fast, confident answer.

    quantity (a bare number) beats a finite-option radio pick, which beats an
    open-ended/subjective question (brand preference, free text). Asking the
    high-confidence questions first means more questions get a real answer
    before the buyer disengages — Enriched instead of just Interested.
    """
    q_type = q.get("type", "")
    if q_type == "quantity":
        return 0
    text = (q.get("text") or "").lower()
    opts = q.get("option") or []
    if opts and "brand" not in text:
        return 1
    return 2


def _reorder_questions_for_confidence(questions: list[dict]) -> list[dict]:
    """Stable-sort qualification questions by _question_confidence_priority.

    Ties (e.g. all radio questions) keep the backend-provided relative order.
    """
    return sorted(questions, key=_question_confidence_priority)


def _build_question_phrase_rules(questions: list[dict]) -> str:
    if not questions:
        return ""
    lines = [
        "QUESTION PHRASE RULES (STRICT — DO NOT DEVIATE)",
        "",
        "Ask each question naturally in Hindi. The English text below is the meaning — express it in Hindi as a short spoken question.",
        "",
    ]
    for i, q in enumerate(questions, 1):
        text = q.get("text", "").strip().rstrip(":")
        q_type = q.get("type", "")
        opts = [o.get("text", "") for o in (q.get("option") or []) if o.get("text")]
        units = q.get("quantity_unit") or [] if q_type == "quantity" else None
        lines.append(f'{i}. Meaning: "{text}"')
        if q_type == "quantity":
            units_str = ", ".join(units) if units else "any unit"
            lines.append(f'   Answer type: QUANTITY — numeric only')
            lines.append(f'   Units expected: {units_str}')
            lines.append(f'   ONLY accept: digits or Hindi number words')
            lines.append(f'   NEVER echo a value back unless it is a recognised number or explicit Not Sure.')
        elif opts:
            normalized_opts = [o.strip().lower() for o in opts]
            if normalized_opts and all(o in _GENERIC_OPTIONS for o in normalized_opts):
                lines.append(f'   Answer type: yes/no — ask naturally, do NOT read options aloud')
                lines.append(f'   Valid answers: {", ".join(opts)}')
                lines.append(f'   If response is not clearly yes/no: re-ask once, then accept as Not Sure.')
            elif "brand" in text.lower():
                lines.append(f'   Answer type: brand preference — ask naturally. DO NOT list any brand names')
                lines.append(f'   Accept: any brand name the user mentions, OR "no preference"')
            else:
                lines.append(f'   Options (read aloud as guide): {", ".join(opts)}')
                lines.append(f'   Accept: ONLY an answer that aligns (even paraphrased) with one of the listed Options, OR an explicit "Not Sure". If unrelated, re-ask once per ANSWER VALIDATION rule.')
        lines.append("")
    lines += [
        "RULE:",
        "- Ask in Hindi only",
        "- Keep it short and conversational",
        "- DO NOT combine questions",
        "- DO NOT add new questions",
    ]
    return "\n".join(lines)


def build_questions_text(schema: dict, is_business=None) -> str:
    questions = schema.get("question", [])
    lines = []
    if not questions:
        lines.append("No specific questions — gather general requirements naturally.")
    else:
        for i, q in enumerate(questions, 1):
            text = q.get("text", "").strip().rstrip(":")
            q_type = q.get("type", "")
            if q_type == "radio":
                opts = [o.get("text", "") for o in (q.get("option") or []) if o.get("text")]
                lines.append(f"{i}. {text}")
                if opts:
                    lines.append(f"   Options: {', '.join(opts)}")
            elif q_type == "quantity":
                units = q.get("quantity_unit") or []
                lines.append(f"{i}. {text}")
                lines.append(f"   Ask for amount and unit ({', '.join(units) if units else 'any unit'})")
            else:
                lines.append(f"{i}. {text}")

    # NOTE: The business-gate handling that used to be restated here is now
    # emitted once via business_prompt_section in build_system_prompt() (the
    # detailed version), which is present in the same assembled prompt whenever
    # is_business == "". The `is_business` param is retained for call-site
    # compatibility but no longer appends a duplicate block here.

    return "\n".join(lines)



_PROMPT_FILE = Path(__file__).parent / "system_prompt.txt"
_CONFIG_FILE = Path(__file__).parent / "prompt_config.json"


def _load_prompt_config() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def build_system_prompt(record: dict | None, lang_key: str | None = None, bot_config: dict | None = None, pipeline_mode: bool = False) -> str:
    _bc = bot_config or {}
    _pc = _bc.get("prompt_config") or {}
    if _bc.get("system_prompt"):
        base_prompt = _bc["system_prompt"]
    elif _PROMPT_FILE.exists():
        base_prompt = _PROMPT_FILE.read_text(encoding="utf-8")
    else:
        base_prompt = _bc.get("system_prompt", "You are Simran, a product qualification agent for VoiceDesk.")

    _lang_cfg = resolve_lang_config(_bc)
    _tone_cfg = resolve_tone_config(_bc)

    cfg = _load_prompt_config()
    language_name = cfg.get("language_name") or _lang_cfg["name"]

    if _pc.get("script_rule"):
        script_rule = _pc["script_rule"]
    elif cfg.get("script_rule"):
        script_rule = cfg["script_rule"]
    else:
        script_rule = f"Every word MUST be in {language_name} script ONLY."

    lang_notes = _lang_cfg.get("lang_notes", "")
    lang_notes_block = f"\n\nLANGUAGE NOTES\n\n{lang_notes}\n" if lang_notes else ""

    tone_notes = _tone_cfg.get("notes", "")
    tone_notes_block = f"\n\n{tone_notes}\n" if tone_notes else ""

    base = (
        base_prompt.replace("{script_rule}", script_rule).replace("{language_name}", language_name)
        + lang_notes_block
        + tone_notes_block
    )

    _functions_cfg = _bc.get("functions") or []
    _has_fetch_schema = any(f.get("name") == "FetchCategorySchema" for f in _functions_cfg)
    if _bc.get("function_calling") and _has_fetch_schema:
        base += (
            "\n\n━━━ PRODUCT CHANGE — TOOL RULE (MANDATORY) ━━━\n\n"
            "You have access to the FetchCategorySchema function.\n"
            "AMBIGUOUS PRODUCT REFERENCE: If the buyer vaguely questions the recorded product or mentions a possibly-different\n"
            "product WITHOUT clearly stating they want it instead (e.g. asking when an inquiry for something else was made,\n"
            "or naming a product fragment without rejecting the current one), do NOT reassert or insist the current product\n"
            "is correct. Do NOT say things like 'X की ही इंक्वायरी है'. Instead ask a neutral clarifying question naming both:\n"
            "\"जी, आपको [original product] चाहिए या [mentioned product]?\" — let the buyer decide, never tell them what they\n"
            "already inquired for.\n\n"
            "When the user confirms they want a DIFFERENT product:\n"
            "  1. Ask EXACTLY this — no paraphrasing, no restructuring:\n"
            "     \"जी, आपको [original product] चाहिए या [new product]?\"\n"
            "     Replace [original product] and [new product] with the actual product names. Nothing else.\n"
            "     WRONG: 'तो क्या आपको X की जगह Y चाहिए, या आप Y भी देखना चाहते हैं?' — both clauses name Y, never do this.\n"
            "     WRONG: any rephrasing, elaboration, or sentence that mentions the new product twice.\n"
            "  2. As soon as they say YES / हां / confirm: say EXACTLY 'जी, एक second जी — देख रहे हैं.' (nothing more), then IMMEDIATELY call FetchCategorySchema(srchterm=\"<new product in English>\").\n"
            "     Do NOT continue asking questions from the old schema.\n"
            "  3. When the function returns: say the 'instruction' field, then ask Question 1 from the new schema.\n"
            "  SCHEMA RELEVANCE — MANDATORY: Before asking any schema question after a product change, verify it makes sense for the EXACT variant the buyer described.\n"
            "  Examples of irrelevant questions to skip (mark Not Sure, move on):\n"
            "    • Vehicle type (Sedan/SUV/Truck) when buyer wants a home appliance motor\n"
            "    • Cooling capacity / star rating when buyer wants a replacement motor part, not a full AC unit\n"
            "    • Any field that is clearly for a different sub-category than what the buyer said\n"
            "  When in doubt: ask the buyer one sentence — 'Sellers आपको guide करेंगे इसमें' — mark Not Sure, continue.\n"
            "If they reconfirm the original product: continue without calling the function.\n\n"
            "SERVICE / INSTALLATION / REPAIR / AMC OF THE SAME PRODUCT — ALSO A TRIGGER:\n"
            "When the buyer wants servicing, installation, repair, or AMC of the SAME product named in the opening line (not to buy it) — see the SERVICE section above for signals:\n"
            "  1. Do NOT run the '[original] चाहिए या [new]?' either-or question — the product name is not in question, only purchase-vs-service is, so that phrasing doesn't fit and only adds a redundant confirmation loop.\n"
            "  2. Acknowledge once — 'जी, एक second जी — देख रहे हैं.' — then IMMEDIATELY call FetchCategorySchema(srchterm=\"<product name in English> service\").\n"
            "  3. Do NOT ask any question from the original purchase schema first (quantity to buy, brand preference, etc.) — go straight to the tool call.\n"
            "  4. When the function returns: say the 'instruction' field, then ask Question 1 from the new (service) schema. Apply the same SCHEMA RELEVANCE check as above.\n\n"
            "⚠ SELLER / MANUFACTURER BLOCK — HARD RULE (takes priority over product change):\n"
            "If the user says they MAKE, MANUFACTURE, PRODUCE, SELL, or SUPPLY any product —\n"
            "do NOT call FetchCategorySchema under any circumstances. This is a SELLER signal.\n"
            "Key signals (any form of these): 'banate hain' / 'banata hoon' / 'banaate hain' /\n"
            "'हम बनाते हैं' / 'हम ही बनाते' / 'main banata' / 'hum bechte' / 'हम बेचते हैं' /\n"
            "'we make it' / 'we manufacture' / 'hum supplier' / 'hum dealer' / 'khud banate'.\n"
            "→ Treat exactly like the CALLER IS A SELLER section above: one confirmation, then close.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

    if not record or not record.get("buyer_details"):
        return base

    buyer = record.get("buyer_details", {})
    search = record.get("search_context", {})
    schema = record.get("qualification_schema", {})
    if isinstance(schema, dict) and schema.get("question"):
        schema["question"] = _reorder_questions_for_confidence(schema["question"])
    product = search.get("searched_product", {})

    name = buyer.get("buyer_name", "customer")
    keyword = search.get("searched_keyword", "")
    product_name = keyword or product.get("product_name", "")
    questions = schema.get("question", [])
    is_business_flag = buyer.get("is_business_flag")
    try:
        is_business_flag = int(is_business_flag) if is_business_flag is not None else None
    except (ValueError, TypeError):
        is_business_flag = None
    route_cat = buyer.get("route_cat")
    try:
        route_cat = int(route_cat) if route_cat is not None else None
    except (ValueError, TypeError):
        route_cat = None
    company_name = record.get("company_name", "").strip()
    # company_name present → lead is linked to a seller the buyer was browsing.
    # bd=2 (Details Page) is one source; company_name alone is sufficient.
    from_details_page = bool(company_name)

    _route_cat_question = (
        f"यह call आपके '{product_name}' requirement के लिए है। "
        f"क्या यह enquiry job requirement के लिए है या आपको {product_name} की requirement है?"
    )
    if pipeline_mode:
        # TTS already spoke the intro ("हेलो, मैं Simran बोल रही हूँ VoiceDesk से।").
        # LLM's first response asks the product question — with seller context when
        # the lead is linked to a company the buyer was browsing.
        if route_cat == 1:
            mandatory_opening = _route_cat_question
        elif from_details_page:
            mandatory_opening = (
                f"जी, आप {company_name} के product देख रहे थे — "
                f"आपको {product_name} की requirement है ना?"
            )
        else:
            mandatory_opening = f"आपको {product_name} की requirement है ना?"
    else:
        if route_cat == 1:
            mandatory_opening = (
                f"हेलो, मैं Simran बोल रही हूँ VoiceDesk से — {_route_cat_question}"
            )
        elif from_details_page:
            mandatory_opening = (
                f"हेलो, मैं Simran बोल रही हूँ VoiceDesk से — "
                f"आप {company_name} के product देख रहे थे — "
                f"आपको {product_name} की requirement है ना?"
            )
        else:
            mandatory_opening = (
                f"हेलो, मैं Simran बोल रही हूँ VoiceDesk से — "
                f"आपको {product_name} की requirement है ना?"
            )

    # Single source for the question list: _build_question_phrase_rules carries
    # the numbered "meaning → ask in Hindi" framing AND per-type answer
    # validation (quantity/radio/brand/yes-no) plus options/units — a superset
    # of build_questions_text's plain list. Emitting it once (instead of once
    # here and again as a trailing mapping_block) removes a full duplicate copy
    # of the questions. build_questions_text is still used by the product-change
    # tool path (_execute_function_call).
    questions_block = (
        _build_question_phrase_rules(questions)
        or "No specific questions — gather general requirements naturally."
    )

    closing_instruction = (
        _bc.get("call_end_text")
        or _pc.get("closing_instruction")
        or cfg.get("closing_instruction")
        or "After all questions are answered, close the call warmly."
    )

    route_cat_prompt_section = ""
    if route_cat == 1:
        _job_seeker_close = "Okay, aapki requirement note kar li hai. Dhanyavaad."
        route_cat_prompt_section = f"""
━━━ JOB-SEEKER vs SERVICE-REQUIREMENT DISAMBIGUATION — MANDATORY FIRST STEP ━━━

⚠ CRITICAL: The mandatory opening line above already asked the buyer to clarify whether this
call is about a JOB/employment or about their "{product_name}" requirement. Resolve this
BEFORE moving to the qualification questions below — do not ask any qualification question
until the buyer's answer to the opening line is heard.

IF the buyer says they are looking for a JOB / employment (e.g. "job ke liye hai", "naukri
chahiye hai", "job requirement hai", "employment ke liye", "naukri ke liye"):
  - Say EXACTLY this line and nothing else:
    "{_job_seeker_close}"
  - Do NOT ask any qualification question and do NOT say anything else. This ends the call.

IF the buyer says they need the "{product_name}" service/product (e.g. "{product_name} ki
requirement hai", "haan, {product_name} chahiye", "I want to avail {product_name}", or any
other clear confirmation that this is a product/service inquiry, not a job inquiry):
  - This confirms the product requirement — do NOT ask the mandatory-opening product
    confirmation question again. Continue immediately with the QUALIFICATION QUESTIONS below,
    exactly as you would on any normal call.

IF the answer is unclear or does not clearly indicate either option:
  - Re-ask once: "जी, बस यह बता दीजिए — यह job requirement के लिए है या {product_name} की
    requirement के लिए?"
  - Still unclear → treat it as a product/service requirement (NOT a job seeker) and continue
    with the qualification questions — never close the call over this ambiguity.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

    business_prompt_section = ""
    if HOT_LEAD_FLOW_ENABLED and is_business_flag == 5:
        _gate_q = "क्या यह requirement आपके business के लिए है?"
        _pitch_q = "क्या आप भी अपने business के लिए VoiceDesk से verified leads receive करना चाहेंगे?"
        _b2b_q = "क्या आपका business B2B है?"
        _city_q = "आपके business की city क्या है?"
        _name_q = "आपके business का नाम क्या है?"
        _hotlead_close = "Aapki requirement ke liye verified sellers aapse connect kar lenge aur hamari taraf se bhi jald hi sampark kiya jayega. Dhanyavaad."
        business_prompt_section = f"""
━━━ BUSINESS LEADS PITCH — AFTER QUALIFICATION COMPLETE ━━━

⚠ CRITICAL: This flow runs ONLY when the buyer has answered every qualification question
normally and the call has reached the Step 3 closing gate (defined above). It replaces ONLY
that final closing moment — it does not apply anywhere else in the call.

─── THIS FLOW DOES NOT APPLY — close the call exactly as instructed elsewhere in this
prompt, with NO gate question and nothing from this section ───
  • Caller is a confirmed seller/manufacturer of the product
  • Caller is seeking employment (job seeker)
  • Caller is abusive or using profane language
  • Caller has a grievance/complaint about a past purchase
  • Caller explicitly says they do not want to talk / want to end the call
  • Caller is clearly annoyed or agitated
  • Caller said NOT-INTERESTED or otherwise disengaged at any point before qualification was
    fully complete (e.g. Step 1 NO with no other product, mid-call "zaroorat nahi" / "nahi
    chahiye" / "band karo", unresponsive or repeatedly avoiding questions)
  • Any other path that ends the call before all qualification questions have been answered
In every one of these cases, just close the call the way you would have without this section
existing at all — do not ask the gate question, do not run any part of this flow.

This flow has up to 5 sequential questions, asked ONE AT A TIME, in this exact order, and only
once qualification is fully done and normal closing has been reached.
Never combine two of them into one turn — each step only runs if the previous step's YES
condition was met.

⚠ HARD RULE — STEP 1 AND STEP 2 ARE NEVER THE SAME TURN: They sound similar but ask
different things — Step 1 asks whether the requirement is for business use; Step 2 asks
whether the buyer wants to RECEIVE LEADS for that business. They are two separate turns with
a real gap for the buyer to respond in between:
  1. Say ONLY Step 1's question. Then STOP and wait for the buyer's reply.
  2. Only after hearing that reply — never in the same breath — say Step 2's question.
A single "haan" / "हाँ" only answers the ONE question that was just asked out loud. NEVER
treat one yes as satisfying both Step 1 and Step 2, and NEVER write both questions into one
response even if the buyer sounds eager. If you notice yourself about to say both in a row,
stop after Step 1 and wait.

SKIPPING AHEAD — RELEVANCE-GATED ONLY: If the buyer volunteers, unprompted, a piece of
information that directly and unambiguously answers a LATER step below (e.g. they state their
business's city or business name before you've asked for it), accept it, skip that specific
step, and continue from the next step that is still unanswered. Only skip a step this way when
the volunteered content is substantive and clearly matches what THAT step asks for — a place
name only satisfies Step 4, a business/company name only satisfies Step 5. NEVER use a bare
yes/no or acknowledgement ("haan", "ji", "theek hai", "bilkul") to silently fill in a later
step — those words only ever answer the specific question that was just asked. If the
volunteered information could plausibly belong to more than one step, or its relevance is not
obvious, do NOT skip anything — ask each step normally.

─── STEP 1 — GATE QUESTION (always asked first, exactly once) ───
  "{_gate_q}"

IF caller says NO (नहीं / no / personal / ghar ke liye — requirement is NOT for their business):
  - Accept: "अच्छा जी, कोई बात नहीं." → standard closing line. Do NOT ask the pitch question.

IF caller says they do NOT have a business at all (koi business nahi hai / personal buyer):
  - Accept: "अच्छा जी, कोई बात नहीं." → standard closing line. Do NOT ask the pitch question.

IF caller says YES (हाँ / हां / ji / bilkul / yes — requirement IS for their business):
  - Move immediately to STEP 2. Do NOT close yet.

IF no clear yes/no:
  - Re-ask once: "जी, {_gate_q}"
  - Still unclear → standard closing line. Do NOT ask the pitch question.

─── STEP 2 — LEADS PITCH (only if Step 1 = YES) ───
  "{_pitch_q}"

IF caller says YES:
  - Move immediately to STEP 3. Do NOT close yet.

IF caller says they do NOT want leads (नहीं / no / nahi chahiye):
  - Accept: "अच्छा जी, कोई बात नहीं." → standard closing line.

IF caller says they are NOT a business owner:
  - Accept: "अच्छा जी, कोई बात नहीं." → standard closing line.

IF no clear yes/no:
  - Re-ask once: "जी, {_pitch_q}"
  - Still unclear → standard closing line.

─── STEP 3 — B2B QUESTION (only if Step 2 = YES) ───
  "{_b2b_q}"
  Accept whatever answer is given (yes / no / not sure) and move immediately to STEP 4 — do not
  re-ask more than once.

─── STEP 4 — BUSINESS CITY (only if Step 2 = YES) ───
  "{_city_q}"
  Accept the city given and move immediately to STEP 5.

─── STEP 5 — BUSINESS NAME (only if Step 2 = YES) ───
  "{_name_q}"
  Accept the name given, THEN use this closing line ONLY (replace standard closing entirely):
    "{_hotlead_close}"

CRITICAL: Once the caller says YES at Step 2, this call is a confirmed hot lead regardless of
what happens afterward. If the call disconnects during or after Step 2's YES — before Step 3, 4,
or 5 is reached or finished — do NOT try to rush through the remaining steps or skip ahead. Just
continue asking them in order, one at a time, for as long as the call lasts.

Keep it natural — one question per turn, not an interrogation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    elif HOT_LEAD_FLOW_ENABLED and is_business_flag in (1, 2, 3, 4):
        _pitch_q = (
            "क्या आप अपने business के लिए leads लेना चाहेंगे?"
            if is_business_flag == 3
            else "क्या आप अपने business के लिए verified leads लेना चाहेंगे?"
        )
        _hotlead_close = "Aapki requirement ke liye verified sellers aapse connect kar lenge aur hamari taraf se bhi jald hi sampark kiya jayega. Dhanyavaad."
        business_prompt_section = f"""
━━━ BUSINESS LEADS PITCH — MANDATORY OVERRIDE ━━━

⚠ CRITICAL: This pitch MUST be asked before ANY call closing — no exceptions except the
hard exclusions listed below. This rule OVERRIDES the NOT-INTERESTED close and all other
closing paths defined above.

─── HARD EXCLUSIONS (close directly, skip pitch) ───
  1. Caller is a confirmed seller/manufacturer of the product
  2. Caller is seeking employment (job seeker)
  3. Caller is abusive or using profane language
  4. Caller has a grievance/complaint about a past purchase

─── ALL OTHER CASES — pitch is mandatory before closing ───

This includes:
  • Qualification complete normally → pitch → close
  • User says they don't need the product mid-call ("zaroorat nahi", "nahi chahiye", "band karo") →
    acknowledge briefly ("अच्छा जी, कोई बात नहीं.") → THEN ask pitch → close
  • Step 1: user says NO, no other product either → ask pitch → close
  • User is unresponsive or keeps avoiding questions → ask pitch → close
  • Call is about to end for any other reason → ask pitch first

TRANSITION when user is disinterested mid-call:
  Acknowledge: "अच्छा जी, कोई बात नहीं." then naturally pivot:
  "एक minute — {_pitch_q}"
  Do NOT say the NOT-INTERESTED close before asking the pitch.

─── PITCH QUESTION ───
  "{_pitch_q}"

─── RESPONSES ───

IF caller says YES:
  - Use this closing line ONLY (replace standard closing entirely):
    "{_hotlead_close}"

IF caller says they do NOT want leads (नहीं / no / nahi chahiye):
  - Accept: "अच्छा जी, कोई बात नहीं." → standard closing line.

IF caller says they are NOT a business owner:
  - Accept: "अच्छा जी, कोई बात नहीं." → standard closing line.

IF no clear yes/no:
  - Re-ask once: "जी, {_pitch_q}"
  - Still unclear → standard closing line.

Keep it natural — one question, not an interrogation.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

    _details_page_handling = (
        f"\n━━━ DETAILS-PAGE CONTEXT ━━━\n"
        f"This buyer was browsing '{company_name}' on VoiceDesk. Your opening references "
        f"that company only to set context — it is the SELLER they were viewing, not their own business.\n"
        f"If the caller says they weren't looking at that company / don't recognise it:\n"
        f"  → Acknowledge briefly: \"अच्छा जी, कोई बात नहीं.\" and continue with the product "
        f"question immediately.\n"
        f"Do NOT treat this as a gate, and never re-ask or insist on the company name.\n"
    ) if from_details_page else ""

    lead_section = f"""
━━━ CALL CONTEXT ━━━

Customer: {name}
Product search: {keyword}
Product: {product_name}
{_details_page_handling}
━━━ MANDATORY OPENING ━━━
Your VERY FIRST utterance MUST be EXACTLY this line, word-for-word, no additions, no preamble, no translation:

{mandatory_opening}

Speak it immediately. Do not wait for the customer to say anything.
{route_cat_prompt_section}
━━━ QUALIFICATION QUESTIONS (ask in this exact order, one at a time) ━━━

{questions_block}

Closing (after all questions answered):
{closing_instruction}

━━━ ANSWER COMPLETENESS RULE (MANDATORY) ━━━

NEVER move to the next question or close the call if the current question has no answer.

- If the buyer skips a question, ignores it, or only talks about something else: gently re-ask the SAME question once before moving on.
  Example: "जी, [question] — यह भी बता दीजिए."
- If the buyer says they don't know / not sure / can't say: accept "पता नहीं" or "Not sure" as the answer and move on.
- NEVER leave a question with a completely blank answer.
- ONLY close the call after every question has received at least some response (even "not sure").

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{business_prompt_section}"""
    return base + lead_section


# ---------------------------------------------------------------------------
# Transcript builder for livekit-agents session history
# ---------------------------------------------------------------------------

def build_transcript_from_session(session: AgentSession) -> list[dict]:
    ROLE_MAP = {"user": "buyer", "assistant": "agent"}
    transcript = []

    # Try multiple attribute names across livekit-agents versions
    history = getattr(session, "history", None) or getattr(session, "chat_ctx", None)
    if history is None:
        return transcript

    _msg_attr = getattr(history, "messages", None) or getattr(history, "items", None)
    if callable(_msg_attr):
        messages = _msg_attr()
    elif _msg_attr is not None:
        messages = _msg_attr
    else:
        messages = []

    for msg in messages:
        role = getattr(msg, "role", None)
        if role is None:
            continue
        role_str = role.value if hasattr(role, "value") else str(role)
        if role_str in ("system", "tool"):
            continue

        content = getattr(msg, "content", None) or getattr(msg, "text_content", None) or ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, str):
                    parts.append(c)
                elif hasattr(c, "text"):
                    parts.append(c.text or "")
                elif isinstance(c, dict):
                    parts.append(c.get("text", ""))
            text = " ".join(parts).strip()
        else:
            text = str(content).strip() if content else ""

        if not text or text.startswith("Call connected."):
            continue
        transcript.append({"role": ROLE_MAP.get(role_str, role_str), "text": text})

    return transcript



# ---------------------------------------------------------------------------
# Closing-phrase detection (CallEndDetector equivalent)
# ---------------------------------------------------------------------------

_CLOSE_MARKERS = (
    "thank you for your time", "have a nice day", "have a great day",
    "goodbye", "good bye", "take care",
    "धन्यवाद", "शुक्रिया", "अलविदा", "आपका दिन शुभ हो", "दिन शुभ हो", "शुभ हो",
    "दिन अच्छा हो", "आपका दिन अच्छा हो", "ख्याल रखिए", "ख्याल रखें", "शुभकामनाएं",
    "dhanyavaad", "dhanyawad", "shukriya", "shubh ho", "alvida", "din shubh ho",
    "sellers आपसे contact", "sellers contact करेंगे",
    "नन्ദி", " நல்ல நாள்", "ಧನ್ಯವಾದ",
    "sellers will contact you", "relevant sellers will contact you",
)


def _dedup_words(text: str) -> str:
    words = text.split()
    if not words:
        return text
    result = [words[0]]
    for w in words[1:]:
        if w.strip(".,!?।…") != result[-1].strip(".,!?।…"):
            result.append(w)
    return " ".join(result)


# Phrases that appear in not-interested / seller-detected / no-need closes —
# saved as "not_interested" rather than "completed" (no full qualification).
_NOT_INTERESTED_MARKERS = (
    "कोई बात नहीं",           # standard not-interested close
    "koi baat nahi",
    "ज़रूरत नहीं होगी",        # seller-detected close
    "zaroorat nahi hogi",
    "ज़रूरत नहीं है",
    "जरूरत नहीं",
    "future में ज़रूरत",       # not-interested coda "if you need in future"
    "future mein zaroorat",
    "voicedesk पे call",        # trailing phrase in not-interested close only
    "voicedesk pe call",
    "ज़रूरत हो तो",
    "zaroorat ho toh",
    "इस product की ज़रूरत",    # seller-detected: "you don't need this product"
    "खुद बेचते",               # seller-confirmed
    "खुद बनाते",
)

_SUCCESS_CLOSE_MARKERS = (
    "सारी details मिल गईं",   # canonical success close (Hinglish)
    "सारी डिटेल्स मिल गई",   # Devanagari "details" variant
    "details मिल गई",          # partial — covers "मिल गईं" and "मिल गई हैं"
    "डिटेल्स मिल गई",          # pure Devanagari partial
    "all the details",
    "i have all the details",
)


def _is_closing_phrase(text: str) -> bool:
    normalized = _dedup_words(text or "").lower()
    return any(marker.lower() in normalized for marker in _CLOSE_MARKERS)


def _is_not_interested_close(closing_buf: str) -> bool:
    """True when the closing phrase is a not-interested / seller-detected close.
    A success close containing 'सारी details मिल गईं' overrides not-interested markers."""
    n = unicodedata.normalize("NFC", closing_buf).lower()
    if any(m.lower() in n for m in _SUCCESS_CLOSE_MARKERS):
        return False
    return any(m.lower() in n for m in _NOT_INTERESTED_MARKERS)


# Hindi/Hinglish profanity patterns for code-level abuse detection.
# Checked against user transcripts before sending to Gemini so abusive
# callers are ended immediately regardless of LLM response latency.
_ABUSIVE_PATTERNS: tuple[str, ...] = (
    "मां चोद", "माँ चोद", "मादरचोद", "madarchod", "maadarchod",
    "बहन चोद", "बहनचोद", "भेनचोद", "behenchod", "bhenchod",
    "चुतिया", "chutiya", "bhosdike", "bhosdika", "bhosdiki",
    "रंडी", "randi", "रांड", "haraami",
    "gaand maar", "गांड मार", "gaand mara",
)


def _is_abusive_text(text: str) -> bool:
    """Return True if the transcript contains explicit profanity or abuse.

    Single-token patterns use space/string boundaries to prevent false positives
    from brand names that happen to contain a slur as a suffix (e.g. 'ब्रांड'
    contains 'रांड' but is NOT abusive). Multi-word patterns still use substring
    matching because space-separated phrases don't embed into other words.
    Note: Devanagari virama (्) is not \\w, so \\b fails; explicit space/boundary
    anchors are required.
    """
    n = unicodedata.normalize("NFC", text or "").lower()
    for p in _ABUSIVE_PATTERNS:
        p_lower = p.lower()
        if " " in p_lower:
            if p_lower in n:
                return True
        else:
            if re.search(
                r'(?:^|(?<=\s))' + re.escape(p_lower) + r'(?=\s|[,।!?.]|$)',
                n,
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def entrypoint(ctx: JobContext):  # noqa: C901
    room_name = ctx.job.room.name
    try:
        _room_meta_raw = json.loads(ctx.job.room.metadata or "{}")
    except (json.JSONDecodeError, TypeError):
        _room_meta_raw = {}

    # 1. Kick off lead pre-fetch immediately — runs while connect() + wait_for_participant() happen
    _lead_id_meta = _room_meta_raw.get("lead_id", "")
    _room_mobile_m = re.search(r'__(\d{10,12})_', room_name)
    _room_mobile = normalize_mobile(_room_mobile_m.group(1)) if _room_mobile_m else ""
    _log = logger.bind(caller=_room_mobile or room_name[-15:])

    _SEP = "═" * 68
    _log.info(_SEP)
    _log.info(
        f"[CALL START] room={room_name} | lead_id={_lead_id_meta!r} | mobile={_room_mobile!r}"
    )
    _log.info(_SEP)

    _early_lead_task: asyncio.Task | None = None
    if _lead_id_meta or _room_mobile:
        _early_lead_task = asyncio.create_task(
            fetch_lead(lead_id=_lead_id_meta, mobile=_room_mobile, mis_api_base=MIS_API_BASE)
        )

    await ctx.connect()
    await ctx.wait_for_participant()

    # 2. Resolve bot config and settings
    _bot_id_meta = _room_meta_raw.get("bot_id", "")
    _test_version_meta = _room_meta_raw.get("test_bot_version_id", "")
    if _bot_id_meta and _test_version_meta:
        _bc, _fallback_reason = await fetch_bot_config(_bot_id_meta, _test_version_meta)
    else:
        _bc, _fallback_reason = None, FALLBACK_REASON_NO_IDS
    _bot_config: dict = _bc or _HARDCODED_BOT_CONFIG
    if _bc is None:
        record_fallback_event(
            room_name=room_name, bot_id=_bot_id_meta, test_bot_version_id=_test_version_meta,
            reason=_fallback_reason, worker=os.getenv("LIVEKIT_AGENT_NAME", ""),
        )
        _log.warning(f"[CONFIG] Falling back to hardcoded assistant — reason={_fallback_reason!r}")

    _prefetched_lead = await _early_lead_task if _early_lead_task is not None else None

    _api_urls = _bot_config.get("api_urls") or {}
    _mis_api_base        = _api_urls.get("mis_api_base") or MIS_API_BASE
    _category_change_api = _api_urls.get("category_change_api") or CATEGORY_CHANGE_API
    _language           = ((_bot_config.get("language") or DEFAULT_LANG_KEY).strip().lower())
    _temperature        = float(_bot_config.get("temperature") or 0.4)
    _vad_start          = _bot_config.get("gemini_start_sensitivity") or "START_SENSITIVITY_HIGH"
    _vad_end            = _bot_config.get("gemini_end_sensitivity")   or "END_SENSITIVITY_HIGH"
    _vad_silence_ms     = int(_bot_config.get("gemini_silence_duration_ms") or 1500)
    _vad_prefix_ms      = int(_bot_config.get("gemini_prefix_padding_ms")   or 100)
    _max_call_duration  = int(_bot_config.get("max_call_duration") or 300)
    _sarvam_min_rms                  = int(_bot_config.get("sarvam_min_rms") or 600)
    _sarvam_min_speech_ms            = int(_bot_config.get("sarvam_min_speech_ms") or 500)
    _sarvam_min_speech_ms_singleword = int(_bot_config.get("sarvam_min_speech_ms_singleword") or 1500)
    _sarvam_silero_threshold          = float(_bot_config.get("sarvam_silero_threshold") or 0.5)
    _sarvam_silero_min_speech_ms      = int(_bot_config.get("sarvam_silero_min_speech_ms") or 400)
    _gemini_silero_fallback_speech_ms = int(_bot_config.get("gemini_silero_fallback_speech_ms") or 150)
    _post_speech_hold_ms             = int(_bot_config.get("post_speech_hold_ms") or 800)
    # Inactivity timer knobs — configurable so production can tune without redeploy.
    # Total first-nudge latency = first_rescue_secs + first_nudge_gap_secs (default 8s).
    _inactivity_first_rescue_secs    = float(_bot_config.get("inactivity_first_rescue_secs")    or 4.0)
    _inactivity_first_nudge_gap_secs = float(_bot_config.get("inactivity_first_nudge_gap_secs") or 4.0)  # was 6.0 → total 8s
    _inactivity_nudge_secs           = float(_bot_config.get("inactivity_nudge_secs")           or 10.0)
    _inactivity_close_secs           = float(_bot_config.get("inactivity_close_secs")           or 5.0)
    _functions: list[dict] = _bot_config.get("functions") or []
    _function_calling   = bool(_bot_config.get("function_calling", False)) and bool(_functions)
    _lang_cfg           = resolve_lang_config(_bot_config)

    # 3. Per-call state
    call_state = {
        "record_id": None,
        "call_id": room_name,
        "lead_record": None,
        "product_change": {},
        "ended_naturally": False,
        "save_done": False,
        "call_start_time": None,
    }
    sip_info = {"caller_number": "", "dialed_number": ""}

    if _prefetched_lead:
        call_state["record_id"] = _prefetched_lead.get("_id") or _prefetched_lead.get("ref_id")
        call_state["call_id"]   = (
            _room_meta_raw.get("call_id", "")
            or _prefetched_lead.get("call_id", "")
            or room_name
        )
        call_state["lead_record"] = _prefetched_lead

    # 3b. Pre-call custom functions — fetch lead/caller details etc. before the greeting.
    # Extracted store_variables land in call_state["vars"] and are interpolated into the
    # system prompt below. Failures never block the call (see run_lifecycle_functions).
    _pre_call_params = {
        "lead_id": _lead_id_meta or (call_state.get("record_id") or ""),
        "mobile": _room_mobile,
        "call_id": call_state.get("call_id") or room_name,
    }
    # Test-call only: tester-seeded overrides for query_params this bot's own pre_call
    # functions define beyond the fixed lead_id/mobile/call_id trio above (dashboard's
    # dynamic "Test Call Parameters" modal, one field per function's query_params). Real
    # production calls never carry this metadata key, so this is a no-op for them.
    _extra_pre_call_params = _room_meta_raw.get("pre_call_params")
    if isinstance(_extra_pre_call_params, dict):
        _pre_call_params = {**_pre_call_params, **_extra_pre_call_params}
    try:
        _pre_results = await run_lifecycle_functions(
            _functions, "pre_call", _pre_call_params,
            call_configured_function, call_state.setdefault("vars", {}),
        )
        if _pre_results:
            _log.info(f"[PRE-CALL] ran {len(_pre_results)} function(s): "
                      f"{[(r['name'], r['ok']) for r in _pre_results]} | vars={list(call_state['vars'])}")
    except Exception as _pre_ex:
        _log.warning(f"[PRE-CALL] hook error (ignored): {_pre_ex}")

    # 4. Build system instruction from lead (or base rules if no lead yet)
    system_instruction = build_system_prompt(
        _prefetched_lead, lang_key=_language, bot_config=_bot_config
    )
    # Inject pre-call dynamic variables into the prompt ({{var}} / {{vars.var}}).
    if call_state.get("vars"):
        system_instruction = interpolate_vars(system_instruction, call_state["vars"])

    # 5. RealtimeModel — same Gemini config as the Pipecat bot
    _selected_key = _next_gemini_key()
    _incr_key_inflight(_selected_key)
    _log.info(
        f"[LLM] Using Gemini key ...{_selected_key[-6:]} "
        f"({len(_GEMINI_LIVE_KEYS)} keys in pool, "
        f"inflight={_KEY_INFLIGHT.get(_selected_key, 1)})"
    )
    llm = google.realtime.RealtimeModel(
        model="gemini-3.1-flash-live-preview",
        voice="Aoede",
        instructions=system_instruction,
        temperature=_temperature,
        language="hi-IN",
        api_key=_selected_key,
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=_vad_start,
                end_of_speech_sensitivity=_vad_end,
                silence_duration_ms=_vad_silence_ms,
                prefix_padding_ms=_vad_prefix_ms,
            ),
        ),
    )

    # 6. Inner helpers (defined before event handlers so closures resolve at call time)

    async def _delete_room_safe(attempt: int = 1) -> None:
        lkapi = LiveKitAPI()
        try:
            await asyncio.wait_for(
                lkapi.room.delete_room(DeleteRoomRequest(room=room_name)),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            if attempt < 3:
                await asyncio.sleep(2)
                await _delete_room_safe(attempt + 1)
        except Exception as e:
            _log.error(f"[CLOSE] delete_room failed (attempt {attempt}): {e}")
        finally:
            await lkapi.aclose()

    async def _kick_caller_safe() -> None:
        """Remove only the SIP participant — ends the call for the user without deleting the room,
        keeping the agent alive to complete save_call_data before process exit."""
        if not _caller_identity or _RemoveParticipantRequest is None:
            return
        lkapi = LiveKitAPI()
        try:
            await lkapi.room.remove_participant(
                _RemoveParticipantRequest(room=room_name, identity=_caller_identity)
            )
        except Exception as e:
            _log.warning(f"[CLOSE] remove_participant failed: {e}")
        finally:
            await lkapi.aclose()

    async def save_call_data(status: str) -> None:
        if call_state["save_done"]:
            return
        call_state["save_done"] = True

        # Cancel timeout timer if still running
        _t = call_state.get("_timeout_task")
        if _t and not _t.done():
            _t.cancel()

        lead_id = call_state.get("record_id")
        _log.info(
            f"[SAVE_CALL] save_call_data called | status={status!r} | "
            f"record_id={lead_id!r} | call_id={call_state.get('call_id')!r} | "
            f"lead_record_present={bool(call_state.get('lead_record'))}"
        )
        # Discard any un-combined muted-window text — it was never processed by Gemini
        # and already lives in muted_transcript; don't pollute the main transcript.
        _muted_inject["text"] = ""
        # Flush any partial user turn that never received a final transcription
        if _pending_user_text and (
            not _live_transcript or _live_transcript[-1].get("text") != _pending_user_text
        ):
            _live_transcript.append({"role": "user", "text": _pending_user_text})
        # Sarvam STT fallback: if user speech is still absent after the Gemini drain,
        # transcribe the raw audio buffer as a last resort.
        # IMPORTANT: check BEFORE flushing the pending assistant turn — otherwise the
        # agent's half-formed response (appended below) makes last.role == "assistant"
        # and Sarvam fires even though Gemini already captured a user partial.
        if (
            status == "disconnected"
            and _user_audio["has_audio"]
            and (not _live_transcript or _live_transcript[-1].get("role") != "user")
        ):
            _sarvam_text = await _sarvam_stt_fallback()
            if _sarvam_text:
                _live_transcript.append({"role": "user", "text": _sarvam_text})
        # Flush any partial assistant turn that was cut mid-sentence (sniffer buffer)
        if _pending_assistant_text and (
            not _live_transcript
            or _live_transcript[-1].get("role") != "assistant"
            or _live_transcript[-1].get("text") != _pending_assistant_text
        ):
            _live_transcript.append({"role": "assistant", "text": _pending_assistant_text})
        transcript = _live_transcript if _live_transcript else build_transcript_from_session(session)

        _start = call_state.get("call_start_time")
        _end_time = time.time()
        _duration = round(_end_time - _start, 1) if _start else 0.0

        # Persist transcript + metadata to MongoDB; callback worker will pick it up
        _mongo_doc = {
            "lead_id": lead_id,
            "call_id": call_state.get("call_id"),
            "assistant_id": _assistant_id,
            "room_name": room_name,
            "status": status,
            "ended_naturally": call_state.get("ended_naturally"),
            "product_change": call_state.get("product_change"),
            "transcript": transcript,
            "muted_transcript": _muted_transcript_log,
            "lead_record": call_state.get("lead_record"),
            "sip_info": sip_info,
            "call_start_time": _start,
            "call_end_time": _end_time,
            "call_duration_sec": _duration,
            "greeting_retry": _greeting_retry_triggered,
            "gemini_connect_failed": _gemini_connect_failed,
            # Did the bot finish speaking the greeting before the call ended?
            # False = user hung up during/before greeting; True = greeting completed.
            "greeting_done": _greeting_done,
            # How many ms of user speech were detected after the greeting (Silero VAD).
            # Non-zero but below Sarvam threshold means the user spoke but STT couldn't
            # transcribe it — useful for analysis to distinguish "user responded briefly"
            # from "user was completely silent after greeting".
            "user_speech_ms": round(_user_audio.get("speech_ms", 0.0)),
            # True when Gemini's first turn was a connection probe ("क्या आप line पर हैं?")
            # instead of the product greeting — agent progression is unreliable for such calls.
            "wrong_opener_detected": _wrong_opener_detected,
            "turn_count": _turn_counter,
            "tagged": False,
            "tagged_at": None,
            "created_at": datetime.now(timezone.utc),
            # Bot's configured spoken language (LANG_CONFIGS key, e.g. "hi"/"en") — lets
            # callback_worker/analysis.py tailor its post-call analysis instructions to the
            # language actually spoken instead of assuming Hindi for every call.
            "language": _language,
        }
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _get_mongo_collection().insert_one(_mongo_doc))
            _log.info(f"[MONGO] Transcript saved | lead_id={lead_id!r} | call_id={call_state.get('call_id')!r}")
        except Exception as e:
            _log.error(f"[MONGO] insert failed: {e}")

        _lead = call_state.get("lead_record") or {}
        _search_ctx = _lead.get("search_context") or {}
        _product = (
            (_search_ctx.get("searched_product") or {}).get("product_name", "")
            or _search_ctx.get("searched_keyword", "")
            or _lead.get("catname", "")
        )
        _end_ts = datetime.now(timezone.utc).isoformat()
        _start_ts = datetime.fromtimestamp(_start, tz=timezone.utc).isoformat() if _start else None
        _transcripts = [
            {
                "id": idx + 1, "call_id": 0,
                "timestamp": _start_ts or _end_ts,
                "speaker": "user" if t["role"] in ("buyer", "user") else "assistant",
                "text": t.get("text", ""),
                "sentiment": "neutral", "confidence": 1.0,
                "created_at": _start_ts or _end_ts,
            }
            for idx, t in enumerate(transcript)
        ]
        call_log_payload = {
            "call_sid": call_state.get("call_id") or room_name,
            "stream_id": f"session-{int(time.time())}",
            "from_number": sip_info.get("caller_number", ""),
            "to_number": sip_info.get("dialed_number", ""),
            "start_time": _start_ts,
            "end_time": _end_ts,
            "duration_seconds": _duration,
            "recording_link": None,
            "organization_id": _bot_config.get("organization_id", ""),
            "assistant_id": _assistant_id,
            "status": "completed" if status == "completed" else "disconnected",
            "summary": "",
            "call_type": "inbound",
            "outcome": status,
            "transcripts": _transcripts,
            "meta_data": {
                "lead_id": call_state.get("record_id", ""),
                "lead_call_id": call_state.get("call_id", ""),
                "product": _product,
                "qna": [],
                "is_business": "",
                "rescheduled_to": "",
                "product_change": call_state.get("product_change") or {},
                "buyer_name": (_lead.get("buyer_details") or {}).get("buyer_name", ""),
                "buyer_city": (_lead.get("buyer_details") or {}).get("buyer_city", ""),
                "call_outcome_desc": "",
                "turn_count": _turn_counter,
                "no_user_response": (_turn_counter == 0 and status == "disconnected"),
            },
            "tags": (
                [status, "no_response"]
                if _turn_counter == 0 and status == "disconnected"
                else [status]
            ),
            "sentiment": "neutral",
        }
        await save_call_log_to_backend(call_log_payload)

        # Post-call custom functions — analytics / persistence after the conversation.
        # Runs with the transcript, outcome, product change and any pre-call/in-call vars.
        # Never raises: teardown must always complete.
        try:
            _post_params = {
                "lead_id": call_state.get("record_id") or "",
                "call_id": call_state.get("call_id") or room_name,
                "mobile": _room_mobile,
                "status": status,
                "duration_sec": _duration,
                "product": _product,
                "product_change": call_state.get("product_change") or {},
                "transcript": transcript,
                **(call_state.get("vars") or {}),
            }
            _post_results = await run_lifecycle_functions(
                _functions, "post_call", _post_params,
                call_configured_function, call_state.setdefault("vars", {}),
            )
            if _post_results:
                _log.info(f"[POST-CALL] ran {len(_post_results)} function(s): "
                          f"{[(r['name'], r['ok']) for r in _post_results]}")
        except Exception as _post_ex:
            _log.warning(f"[POST-CALL] hook error (ignored): {_post_ex}")

        for _p in _wav_paths:
            try:
                if Path(_p).exists():
                    os.unlink(_p)
            except OSError:
                pass
        _log.info(_SEP)
        _log.info(
            f"[CALL END] room={room_name} | status={status!r} | "
            f"duration={_duration}s | lead_id={lead_id!r}"
        )
        _log.info(_SEP)

    _save_done_event = asyncio.Event()

    async def _save_and_close(status: str) -> None:
        # Release this key's in-flight slot so future calls can reuse it.
        # _decr_key_inflight uses max(0, ...) so double-calls are harmless.
        _decr_key_inflight(_selected_key)
        # Snapshot save_done BEFORE the call so only the path that actually
        # runs save_call_data sets the done-event.  Without this the
        # disconnected-event (fired when we kick the caller) sets the event
        # early while the Mongo insert is still in flight.
        _was_done = call_state["save_done"]
        # For abrupt disconnects the Gemini STT pipeline is still draining —
        # user_input_transcribed (partials/finals) and conversation_item_added
        # (user role commit) both fire within this window.  session.aclose() MUST
        # come AFTER save_call_data so the STT pipeline stays alive long enough
        # for the user's last utterance to land in _live_transcript before the
        # snapshot.  Closing first would kill those events and lose the user turn.
        if status == "disconnected":
            # Give Gemini time to complete the dummy-turn generation cycle triggered
            # in _on_disconnect.  The cycle (send "." → Gemini generates → turn_complete
            # → _mark_current_generation_done → input_audio_transcription_completed) takes
            # ~2.25 s empirically (observed: save at T=2.0s, _on_item_added at T=2.258s).
            # 4.0 s gives 1.75 s margin for slow Gemini responses.
            await asyncio.sleep(4.0)
        await save_call_data(status)
        asyncio.create_task(_delete_room_safe())
        try:
            await session.aclose()
        except Exception:
            pass
        if not _was_done:
            _save_done_event.set()

    # Inactivity tracking
    _nudge_count = 0
    _nudge_in_progress = False   # True while bot is speaking an inactivity nudge
    # Guards against an unbounded silent loop: several rescue/suppression paths below
    # reset _nudge_count back to 0 and reschedule instead of nudging, whenever background
    # noise or an unresolved muted-capture buffer is present but no real user turn lands.
    # Without a cap, a call with persistent line noise (or a caller who never speaks
    # intelligibly) can sit silently resetting itself indefinitely — the bot never says
    # "क्या आप अभी line पर हैं?" and never ends the call, until the hard 300s call-duration
    # timeout eventually kicks the caller. _STALL_RESET_CAP bounds that to ~60s.
    _STALL_RESET_CAP = 6
    _stall_resets = 0
    _inactivity_task: asyncio.Task | None = None
    _call_ended = False

    async def _inactivity_timeout() -> None:
        nonlocal _nudge_count, _inactivity_task, _nudge_in_progress, _stall_resets
        # Nudge 1 at (first_rescue + first_nudge_gap) s (default 8 s),
        # nudge 2 at nudge_secs after (default 10 s), close at close_secs after nudge 2 (default 5 s).
        sleep_secs = _inactivity_close_secs if _nudge_count >= 2 else _inactivity_nudge_secs

        # Early Sarvam rescue checkpoint — only on the first timer cycle with no live
        # user turn yet.  Handles "user spoke right after greeting but Gemini missed
        # it": detect missed speech early and rescue, then fire the nudge after the
        # gap (default: 4s rescue + 4s gap = 8s total to first nudge).
        if _nudge_count == 0 and _greeting_done and _turn_counter == 0:
            await asyncio.sleep(_inactivity_first_rescue_secs)
            if _call_ended or _closing_triggered:
                return
            if _turn_counter > 0 or _pending_user_text:
                # User responded while we were sleeping — reset and let normal flow take over
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            if _user_audio["speech_ms"] > 200 and _stall_resets < _STALL_RESET_CAP:
                _stall_resets += 1
                _log.info(
                    f"[INACTIVITY] speech_ms={_user_audio['speech_ms']:.0f} at 4s — "
                    f"early Sarvam rescue (Gemini missed initial response) [stall {_stall_resets}/{_STALL_RESET_CAP}]"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                _turn_at_start_e = _turn_counter
                _speaking_at_start_e = _speaking_turns_completed
                async def _early_sarvam_rescue(
                    _t=_turn_at_start_e, _s=_speaking_at_start_e
                ) -> None:
                    nonlocal _muted_inject_sent_time
                    _buffered = _muted_inject.get("text", "")
                    if _buffered:
                        text = _buffered
                        _muted_inject["text"] = ""
                        _log.info(f"[SARVAM-RESCUE] early rescue — using buffered muted-capture: {text!r}")
                    else:
                        text = await _sarvam_stt_fallback(min_speech_ms=200, silero_min_ms=60)
                    if not text or _call_ended:
                        return
                    if _turn_counter > _t or _speaking_turns_completed > _s or _pending_user_text:
                        _log.info(f"[SARVAM-RESCUE] early rescue — already handled, skipping ({text!r})")
                        return
                    # Skip if muted-capture already injected this turn — dual injection confuses Gemini
                    _now_e = asyncio.get_event_loop().time()
                    if _muted_inject_sent_time > 0 and (_now_e - _muted_inject_sent_time) < 8.0:
                        _log.info(
                            f"[SARVAM-RESCUE] early rescue — muted-capture inject already sent "
                            f"{_now_e - _muted_inject_sent_time:.1f}s ago — skipping ({text!r})"
                        )
                        return
                    try:
                        _agent_s = getattr(session.agent_state, "value", None) or str(session.agent_state)
                        if _agent_s == "speaking":
                            _log.info(f"[SARVAM-RESCUE] early rescue — bot speaking, skipping ({text!r})")
                            return
                    except Exception:
                        pass
                    _log.info(f"[SARVAM-RESCUE] early rescue — Gemini missed — injecting: {text!r}")
                    try:
                        _rt._send_client_event(
                            types.LiveClientContent(
                                turns=[types.Content(parts=[types.Part(text=text)], role="user")],
                                turn_complete=True,
                            )
                        )
                        _muted_inject_sent_time = asyncio.get_event_loop().time()
                    except Exception as e:
                        _log.warning(f"[SARVAM-RESCUE] early rescue inject failed: {e}")
                asyncio.create_task(_early_sarvam_rescue())
                return
            # No early rescue needed — wait the remaining gap then nudge
            await asyncio.sleep(_inactivity_first_nudge_gap_secs)
        else:
            await asyncio.sleep(sleep_secs)

        _nudge_count += 1
        if _call_ended:
            return
        # Don't nudge before the greeting has played — the caller would hear
        # "क्या आप अभी line पर हैं?" as the very first thing if Gemini is slow
        # to start the greeting. Reset counter and reschedule; the greeting's
        # speaking-start event will cancel this task via _cancel_inactivity().
        if not _greeting_done:
            _nudge_count = 0
            _inactivity_task = asyncio.create_task(_inactivity_timeout())
            return
        if _nudge_count >= 3:
            _nudge_count = 0
            _inactivity_task = None
            # Race-condition guard: user may have spoken in the last instant before
            # this timer fired. Only suppress if speech was within 2 s — a wider window
            # would permanently prevent call-end for anyone who spoke earlier in the call.
            _now = asyncio.get_event_loop().time()
            _just_spoke = _last_user_turn_time > 0 and (_now - _last_user_turn_time) < 2.0
            if _just_spoke or _pending_user_text:
                _log.info(
                    f"[INACTIVITY] end suppressed — user just spoke "
                    f"(since={_now - _last_user_turn_time:.1f}s ago, pending={_pending_user_text!r})"
                )
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            _log.info("[INACTIVITY] extended silence — ending call directly")
            call_state["ended_naturally"] = True
            end_phrase = _lang_cfg.get("inactivity_end_phrase") or INACTIVITY_END_PHRASE
            await _speak_via_gemini(end_phrase, reason="inactivity-end")
            # _speak_via_gemini returns immediately after sending the directive.
            # Poll agent_state to wait for Gemini to start then fully finish
            # speaking before kicking, so the closing sentence isn't cut off.
            try:
                for _ in range(40):   # up to 4 s for Gemini to start speaking
                    if getattr(getattr(session, "agent_state", None), "value", "") == "speaking":
                        break
                    await asyncio.sleep(0.1)
                for _ in range(150):  # up to 15 s for speech to finish
                    if getattr(getattr(session, "agent_state", None), "value", "") != "speaking":
                        break
                    await asyncio.sleep(0.1)
                await asyncio.sleep(0.8)  # small buffer so the last syllable clears
            except Exception:
                await asyncio.sleep(4)
            await _kick_caller_safe()
            # No user turns means the caller never engaged — save as disconnected.
            _inactivity_status = "completed" if _turn_counter > 0 else "disconnected"
            asyncio.create_task(_save_and_close(_inactivity_status))
        else:
            # Skip nudge if user spoke very recently (race: timer fired as user was responding).
            # Use a 5s recency window — NOT _turn_counter > 0 which permanently suppresses nudges
            # after the first user turn, leaving mid-call silence unhandled.
            _now = asyncio.get_event_loop().time()
            _recent_user_speech = _last_user_turn_time > 0 and (_now - _last_user_turn_time) < 5.0
            if _recent_user_speech or _pending_user_text:
                _log.info(
                    f"[INACTIVITY] nudge suppressed — user spoke recently "
                    f"(since={_now - _last_user_turn_time:.1f}s ago, pending={_pending_user_text!r})"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            # Skip nudge if audio RMS shows active input but Gemini gave no transcript —
            # try Sarvam to rescue the missed utterance.
            if _user_audio["speech_ms"] > 200 and _stall_resets < _STALL_RESET_CAP:
                _stall_resets += 1
                _log.info(
                    f"[INACTIVITY] speech_ms={_user_audio['speech_ms']:.0f} — "
                    f"Gemini silent on live audio — attempting Sarvam rescue [stall {_stall_resets}/{_STALL_RESET_CAP}]"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                _turn_at_start = _turn_counter
                _speaking_at_start = _speaking_turns_completed
                async def _sarvam_rescue(
                    _t=_turn_at_start, _s=_speaking_at_start
                ) -> None:
                    nonlocal _muted_inject_sent_time
                    _buffered = _muted_inject.get("text", "")
                    if _buffered:
                        text = _buffered
                        _muted_inject["text"] = ""
                        _log.info(f"[SARVAM-RESCUE] using buffered muted-capture: {text!r}")
                    else:
                        text = await _sarvam_stt_fallback(min_speech_ms=200, silero_min_ms=60)
                    if not text or _call_ended:
                        return
                    if _turn_counter > _t:
                        _log.info(f"[SARVAM-RESCUE] Gemini caught up — skipping inject ({text!r})")
                        return
                    if _speaking_turns_completed > _s:
                        _log.info(f"[SARVAM-RESCUE] Bot already responded — skipping inject ({text!r})")
                        return
                    if _pending_user_text:
                        _log.info(f"[SARVAM-RESCUE] Gemini partial in flight — skipping inject ({text!r})")
                        return
                    # Skip if muted-capture already injected this turn — dual injection confuses Gemini
                    _now_r = asyncio.get_event_loop().time()
                    if _muted_inject_sent_time > 0 and (_now_r - _muted_inject_sent_time) < 8.0:
                        _log.info(
                            f"[SARVAM-RESCUE] muted-capture inject already sent "
                            f"{_now_r - _muted_inject_sent_time:.1f}s ago — skipping ({text!r})"
                        )
                        return
                    try:
                        _agent_s = getattr(session.agent_state, "value", None) or str(session.agent_state)
                        if _agent_s == "speaking":
                            _log.info(f"[SARVAM-RESCUE] Bot currently speaking — skipping inject ({text!r})")
                            return
                    except Exception:
                        pass
                    _log.info(f"[SARVAM-RESCUE] Gemini missed — injecting via Sarvam: {text!r}")
                    try:
                        _rt._send_client_event(
                            types.LiveClientContent(
                                turns=[types.Content(parts=[types.Part(text=text)], role="user")],
                                turn_complete=True,
                            )
                        )
                        _muted_inject_sent_time = asyncio.get_event_loop().time()
                    except Exception as e:
                        _log.warning(f"[SARVAM-RESCUE] inject failed: {e}")
                asyncio.create_task(_sarvam_rescue())
                return
            # Guard: a muted-capture text was injected recently (within 8 s) — Gemini is
            # likely still processing it.  Suppressing the nudge here prevents double-engagement
            # when the caller spoke during the greeting window and the post-greeting watchdog
            # is still running.  This race window grew when we shortened the first-nudge gap.
            _now2 = asyncio.get_event_loop().time()
            if (
                _muted_inject.get("text")
                or (_muted_inject_sent_time > 0 and (_now2 - _muted_inject_sent_time) < 8.0)
            ) and _stall_resets < _STALL_RESET_CAP:
                _stall_resets += 1
                _log.info(
                    "[INACTIVITY] nudge suppressed — muted-capture inject in flight/recent "
                    f"[stall {_stall_resets}/{_STALL_RESET_CAP}]"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            nudge = _lang_cfg.get("inactivity_phrase") or INACTIVITY_PHRASE
            _log.info(f"[INACTIVITY] {sleep_secs:.0f}s silence — nudge {_nudge_count}: {nudge!r}")
            _nudge_in_progress = True
            await _speak_via_gemini(nudge, reason="inactivity-nudge")
            # _on_agent_state fires _reset_inactivity() after bot finishes speaking.
            # _nudge_in_progress=True prevents that call from zeroing _nudge_count.

    def _reset_inactivity(from_user_speech: bool = False) -> None:
        nonlocal _nudge_count, _inactivity_task, _nudge_in_progress, _stall_resets
        if _call_ended:
            return
        if from_user_speech:
            # User genuinely spoke — clear everything, including the stall-reset guard.
            _nudge_count = 0
            _nudge_in_progress = False
            _stall_resets = 0
        elif not _nudge_in_progress:
            # Normal bot-speech-end event while no nudge is pending — reset counter.
            _nudge_count = 0
        # When _nudge_in_progress=True and from_user_speech=False (bot-speaking events
        # for the nudge itself), leave both _nudge_count and _nudge_in_progress intact
        # so the counter keeps accumulating toward the 2-nudge limit.
        had_task = _inactivity_task and not _inactivity_task.done()
        if had_task:
            _inactivity_task.cancel()
        _log.info(f"[INACTIVITY] timer reset (prev_task_cancelled={had_task})")
        _inactivity_task = asyncio.create_task(_inactivity_timeout())

    def _cancel_inactivity() -> None:
        nonlocal _inactivity_task
        if _inactivity_task and not _inactivity_task.done():
            _inactivity_task.cancel()
        _inactivity_task = None

    # Closing-phrase handler
    _closing_buffer = ""
    _closing_triggered = False
    _early_close_muting = False  # True once partial closing phrases appear — keeps mic muted
    _echo_guard_task: asyncio.Task | None = None
    _speaking_unmute_task: asyncio.Task | None = None  # 2 s delayed unmute for mid-turn interruptions

    # Turn-wise transcript tracking
    _turn_counter = 0
    _live_transcript: list = []  # real-time capture; avoids missing turns on abrupt disconnect
    _pending_user_text: str = ""     # last partial user transcription (may never get a final)
    _pending_assistant_text: str = ""  # last partial assistant turn being streamed (may be cut mid-sentence)

    # WAV file path for Sarvam STT fallback audio.
    # Each turn reset opens a new file segment so old audio is discarded.
    _wav_path = f"/tmp/caller_{room_name.replace('/', '_')[-40:]}_0.wav"
    _wav_paths = [_wav_path]   # mutable holder so inner coroutines share current path
    _wav_reset_flag = False    # set True by _on_user_spoke to trigger segment rotation
    _buffer_frozen = False     # set True on disconnect to stop writing post-hangup noise
    _user_audio: dict = {"speech_ms": 0.0, "nbytes": 0, "has_audio": False}
    # Raw PCM for the current mic-ON window — scored by Silero on Gemini FINAL to
    # reject background/noise captures before they enter the transcript.
    _current_window_pcm: bytearray = bytearray()
    # Texts rejected by Silero in _on_user_spoke; checked in _on_item_added so the
    # committed-item fallback path doesn't re-insert what Silero dropped.
    _silero_rejected_turns: set = set()
    # Audio captured from caller while mic is muted (bot speaking turn + post-hold).
    # Transcribed mid-call via Sarvam and re-injected to Gemini if substantive.
    _muted_capture: dict = {"frames": [], "speech_ms": 0.0}
    # Holds the last muted-window transcript (Sarvam capture while mic was OFF).
    # Never sent to Gemini — combined with the next live user FINAL for Mongo/analysis.
    _muted_inject: dict = {"text": ""}
    # Timestamp (loop time) when the post-greeting muted-capture inject was last sent to Gemini.
    # Used by Sarvam rescue functions to avoid sending a second competing injection.
    _muted_inject_sent_time: float = 0.0
    # Monotonic time when the last muted-capture Sarvam call returned empty.
    # Echo guard uses this: if Gemini fires a new speaking turn within 200 ms of this
    # timestamp the response is almost certainly a PSTN echo, not real user speech.
    _muted_capture_empty_time: float = 0.0
    # Monotonic time when the last muted-capture filler/hallucination was dropped.
    # Filler guard uses this: if Gemini fires a new speaking turn within 1.5 s of this
    # timestamp it heard the filler via the live audio path (after the 4 s unmute) and
    # should be interrupted — the user said nothing substantive.
    _muted_filler_dropped_time: float = 0.0
    # Cumulative log of every muted-window Sarvam transcript across the call.
    # Saved to Mongo as a separate field so the analysis LLM can see what the user
    # said during bot speaking turns even when those turns were discarded from _live_transcript.
    _muted_transcript_log: list = []
    _close_status = "completed"  # "completed", "not_interested", or "abusive"
    _abusive_detected = False    # set True when profanity is detected; prevents double-trigger

    async def _handle_close() -> None:
        nonlocal _call_ended
        _call_ended = True
        _cancel_inactivity()
        await asyncio.sleep(2)
        await _kick_caller_safe()
        asyncio.create_task(_save_and_close(_close_status))

    async def _consume_sniff(text_iter) -> None:
        """Consume one branch of a tee'd text_stream, muting the mic as soon as
        a partial closing phrase is detected in the streaming chunks.
        Also updates _pending_assistant_text with accumulated chunks so that an
        abrupt disconnect mid-sentence can still be captured in save_call_data."""
        nonlocal _early_close_muting, _pending_assistant_text
        buf = ""
        try:
            async for chunk in text_iter:
                buf += chunk
                _pending_assistant_text = buf
                if not _early_close_muting and not _closing_triggered:
                    buf_lower = buf.lower()
                    if any(m in buf_lower for m in (
                        "details मिल गईं",
                        "sellers आपसे contact",   # closing line only — not mid-call "relevant sellers से connect"
                        "sellers will contact",
                        "all details",
                    )):
                        _early_close_muting = True
                        _set_mic(False, reason="stream-closing-phrase")
                        _log.info(f"[STREAM-DETECT] Closing phrase in stream — mic muted | buf={buf!r}")
        except Exception:
            pass

    async def _buffer_user_audio(track: rtc.RemoteAudioTrack) -> None:
        """Stream caller audio directly into a WAV file for Sarvam STT fallback."""
        nonlocal _wav_reset_flag, _buffer_frozen, _current_window_pcm
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        segment = 0

        def _open_wav(path: str) -> wave.Wave_write:
            wf = wave.open(path, "wb")
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            return wf

        wf = _open_wav(_wav_paths[0])
        try:
            async for ev in stream:
                if _call_ended or _buffer_frozen:
                    break
                if _wav_reset_flag:
                    _wav_reset_flag = False
                    prev_speech_ms = _user_audio["speech_ms"]
                    old_path = _wav_paths[0]
                    wf.close()
                    try:
                        os.unlink(old_path)
                    except OSError:
                        pass
                    _user_audio["speech_ms"] = 0.0
                    _user_audio["nbytes"] = 0
                    _user_audio["has_audio"] = False
                    _current_window_pcm = bytearray()
                    segment += 1
                    new_path = f"/tmp/caller_{room_name.replace('/', '_')[-40:]}_{segment}.wav"
                    _wav_paths[0] = new_path
                    wf = _open_wav(new_path)
                    _log.info(
                        f"[AUDIO-BUF] WAV rotated → seg {segment} "
                        f"(prev speech_ms={prev_speech_ms:.0f} path={new_path})"
                    )
                chunk = bytes(ev.frame.data)
                samples = _array.array('h', chunk)
                n = len(samples)
                rms = (sum(s * s for s in samples) / n) ** 0.5 if n else 0.0
                if rms < _sarvam_min_rms:
                    continue
                frame_ms = len(chunk) / 2 / 16000 * 1000
                # Accumulate caller frames whenever mic is OFF so speech during bot's
                # turn or post-hold can be transcribed and re-injected to Gemini.
                if not _mic_enabled:
                    _muted_capture["frames"].append(chunk)
                    _muted_capture["speech_ms"] += frame_ms
                # Only write to the fallback WAV during user-response windows (mic ON).
                # Agent-speaking windows can bleed the bot's own TTS into the caller track
                # via SIP echo; writing those frames would make Sarvam transcribe the agent.
                if not _mic_enabled:
                    continue
                if _user_audio["nbytes"] >= _SARVAM_AUDIO_MAX_BYTES:
                    continue
                wf.writeframes(chunk)
                _current_window_pcm += chunk
                _user_audio["nbytes"] += len(chunk)
                _user_audio["speech_ms"] += frame_ms
                _user_audio["has_audio"] = True
        finally:
            wf.close()
            try:
                os.unlink(_wav_paths[0])
            except OSError:
                pass

    # Phrases that indicate the captured audio is a bystander talking to someone
    # else in the room, not addressing the bot. When any of these substrings appear
    # in an STT result the turn is dropped from _live_transcript so it doesn't
    # pollute the call analysis. The Gemini LLM still receives the audio (we cannot
    # intercept that), but the stored transcript stays clean.
    _BYSTANDER_SPEECH_MARKERS = [
        "यह लोग", "ये लोग", "इन लोगों", "यह लोगों",
        "these people", "this people",
        # Gemini labels background audio with "from behind" when caller's mic picks
        # up a third party speaking to someone else in the room.
        "पीछे से",
    ]
    # Gemini encodes rapid background repetition as hyphen-chained tokens, e.g.
    # "करो-करो-करो-करो" — three or more repetitions of the same word is noise.
    _BYSTANDER_HYPHEN_REPEAT_RE = re.compile(r'(\S+)-\1(?:-\1)+')

    def _is_bystander_speech(text: str) -> bool:
        normalized = unicodedata.normalize("NFC", text)
        if any(marker in normalized for marker in _BYSTANDER_SPEECH_MARKERS):
            return True
        if _BYSTANDER_HYPHEN_REPEAT_RE.search(normalized):
            return True
        return False

    # IVR / carrier auto-attendant / voicemail markers.
    # If the first transcript on a call matches any of these, the call is
    # talking to an IVR or busy-line — not a real buyer — and should be closed.
    _IVR_BUSY_MARKERS = [
        # Hindi
        "इस समय व्यस्त", "बाद में call", "बाद में कॉल", "नंबर अभी busy",
        "व्यस्त हैं", "available नहीं", "कृपया थोड़ी देर बाद",
        "स्विच ऑफ", "switch off", "switched off",
        # English (telco bilingual prompts)
        "currently busy", "please try later", "not reachable",
        "number you have dialed", "out of coverage",
        "call cannot be completed", "is not available",
        "thank you for calling", "press 1", "press 2",
        "for english press", "our working hours",
        # Additional English voicemail / hold-music phrases seen in production logs
        "please stay on the line", "stay on the line",
        "leave a message", "leave your message", "after the tone",
        "after the beep", "not available right now",
        "please leave", "record your message",
        "you have reached", "we will connect", "all lines are busy",
        # Devanagari transliterations of common English telco phrases
        "प्लीज ट्राई", "करेंटली बिजी", "इज नॉट अवेलेबल",
        "प्लीज रिप्लाई", "प्लीज लीव", "लीव ए मैसेज",
        "आफ्टर द टोन", "नॉट अवेलेबल", "नॉट रीचेबल",
        # Devanagari transliterations seen in production logs where Sarvam/Soniox
        # misread English voicemail/IVR audio as Devanagari phonetics
        "रीजन फॉर कॉलिंग", "रीज़न फॉर कॉलिंग",  # "reason for calling"
        "पर्सन इज अवेलेबल", "पर्सन यू आर ट्राइंग",  # "if this person is available / person you are trying to reach"
        "स्टे ऑन द लाइन",           # "stay on the line"
        "रिकॉर्ड योर मैसेज",        # "record your message"
        "पिक अप द कॉल",             # "pick up the call"
        "लीव योर मैसेज",            # "leave your message"
        "फिनिशड योर",               # "when you have finished your..." (end of voicemail prompt)
        "ट्राइंग टू रीच",           # "the person you are trying to reach"
        "ट्राइंग टू रीडायरेक्ट",   # garbled "trying to redirect/reach"
        "फोन आई है",                # garbled IVR: "this number has been dialled / the phone came"
        # Gujarati
        "व्यस्त छे", "थोड़ा क्षणों",
    ]

    def _is_ivr_message(text: str) -> bool:
        n = unicodedata.normalize("NFC", text).lower()
        return any(m.lower() in n for m in _IVR_BUSY_MARKERS)

    # Only tokens that Sarvam hallucinates from pure noise even after Silero VAD passes.
    # Legitimate one-word user responses (हाँ, yes, ok, …) are intentionally excluded —
    # Silero already gates real speech; anything it passes with a single substantive word
    # should not be dropped here.
    _SARVAM_FILLER_HALLUCINATIONS = {
        unicodedata.normalize("NFC", w) for w in {
            # Single-character / single-vowel glitches from line noise
            "a", "e", "o", "i",
            # Filler sounds that carry no intent
            "hmm", "hm", "हम्म",
        }
    }

    # Phrases that Gemini generates from near-silence or low-level ambient audio —
    # Gemini sometimes outputs counting/alphabet sequences as a calibration response
    # when it receives audio that is too quiet to resolve into real speech.
    _GEMINI_CALIBRATION_HALLUCINATIONS: set[str] = {
        unicodedata.normalize("NFC", p) for p in {
            "ए बी सी", "वन टू थ्री फोर", "वन टू थ्री", "वन टू",
            "a b c", "one two three four", "one two three",
        }
    }

    # Short noise tokens that Gemini transcribes from sub-350ms ambient sounds.
    # These are never valid product-qualification answers and are safe to reject
    # when speech_ms < 350ms AND the whole transcript is a single such token.
    _GEMINI_SHORT_NOISE_TOKENS: set[str] = {
        unicodedata.normalize("NFC", w) for w in {
            # Throat-clearing / breathing artifacts (never valid product answers)
            "हूं", "हूँ", "ऊं", "उम",
            "uh", "um", "ugh",
            # Junk syllables seen in production logs that are never valid answers
            "पाठ",   # "lesson" — would never be a product qualification answer
        }
    }

    # Bot-echo substrings: Sarvam/Soniox can pick up the bot's own voice echoing
    # through the caller's speakerphone during the muted greeting window.
    # Any muted-capture text containing one of these is our own audio — drop it.
    _BOT_ECHO_MARKERS = [
        "सिमरन बोल रही",   # "Simran bol rahi hoon" — bot identity line
        "simran bol",
    ]

    def _is_bot_echo(text: str) -> bool:
        n = unicodedata.normalize("NFC", text).lower()
        return any(m in n for m in _BOT_ECHO_MARKERS)

    def _normalize_stt_tokens(text: str) -> list[str]:
        """NFC-normalize, lowercase, strip punctuation, then split into tokens.
        Uses Unicode category checks (not \\w) so Devanagari combining marks
        (chandrabindu, maatra, virama) are preserved — otherwise "हाँ" becomes "ह"."""
        text = unicodedata.normalize("NFC", text).lower()
        # Keep letters (L), digits (N), combining marks (M); strip P/S/C.
        cleaned = "".join(
            c for c in text
            if unicodedata.category(c)[0] in ("L", "N", "M") or c.isspace()
        )
        return [t for t in cleaned.split() if t]

    async def _sarvam_stt_fallback(min_speech_ms: int | None = None, silero_min_ms: int | None = None) -> str | None:
        """Transcribe the WAV file written by _buffer_user_audio using Sarvam.
        Silero VAD gates the path — only invoked when genuine human voice is present.
        min_speech_ms overrides the speech_ms threshold (rescue paths use 200 ms).
        silero_min_ms overrides the Silero voiced_ms gate (rescue paths use 60 ms to
        catch monosyllabic Hindi words that have low voiced energy)."""
        if not _user_audio["has_audio"]:
            return None
        speech_ms = _user_audio["speech_ms"]
        _effective_min = min_speech_ms if min_speech_ms is not None else _sarvam_min_speech_ms
        if speech_ms < _effective_min:
            _log.info(f"[STT] skipped — speech_ms={speech_ms:.0f} < min={_effective_min}")
            return None
        wav_path = _wav_paths[0]
        wav_data: bytes | None = None
        if Path(wav_path).exists():
            try:
                with open(wav_path, "rb") as f:
                    wav_data = f.read()
            except Exception as e:
                _log.warning(f"[STT] WAV read error: {e}")
        if not wav_data and _current_window_pcm:
            # WAV file was deleted (buffer_frozen at disconnect) but in-memory PCM
            # still has the audio — wrap it in a WAV container and use that.
            _log.info(
                f"[STT] WAV file gone — using in-memory PCM "
                f"({len(_current_window_pcm)} bytes, speech_ms={speech_ms:.0f})"
            )
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(_current_window_pcm))
            wav_data = buf.getvalue()
        if not wav_data:
            return None
        # Silero VAD gate
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as wf:
                pcm_bytes = wf.readframes(wf.getnframes())
        except Exception:
            pcm_bytes = b""
        if pcm_bytes:
            voiced_ms = await asyncio.get_running_loop().run_in_executor(
                None, _silero_voiced_ms, pcm_bytes, _sarvam_silero_threshold
            )
            _silero_gate = silero_min_ms if silero_min_ms is not None else _sarvam_silero_min_speech_ms
            if voiced_ms < _silero_gate:
                _log.info(
                    f"[STT] Silero — no speech (voiced_ms={voiced_ms:.0f} < "
                    f"min={_silero_gate}), skipping"
                )
                return None
            _log.info(f"[STT] Silero — speech confirmed (voiced_ms={voiced_ms:.0f})")
        if not SARVAM_API_KEY:
            return None
        try:
            form = aiohttp.FormData()
            form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
            form.add_field("language_code", "hi-IN")
            form.add_field("model", "saaras:v3")
            form.add_field("mode", "transcribe")
            async with _get_http_session().post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": SARVAM_API_KEY},
                data=form,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    text = (result.get("transcript") or "").strip()
                    if text:
                        tokens = _normalize_stt_tokens(text)
                        if tokens and all(t in _SARVAM_FILLER_HALLUCINATIONS for t in tokens):
                            _log.info(
                                f"[SARVAM] dropped all-filler transcript {text!r} "
                                f"(speech_ms={speech_ms:.0f})"
                            )
                            return None
                        _log.info(
                            f"[STT] sarvam=ok: {text!r} "
                            f"(speech_ms={speech_ms:.0f})"
                        )
                        return text
                else:
                    body = await resp.text()
                    _log.warning(f"[SARVAM] STT failed: {resp.status} {body[:200]}")
        except Exception as e:
            _log.warning(f"[SARVAM] STT error: {e}")
        return None

    async def _transcribe_muted_period(frames: list, speech_ms: float) -> None:
        """Transcribe audio captured during a muted window (bot speaking turn + post-hold).
        Uses Sarvam for transcription. No Silero gate — we want everything the user said, even brief."""
        nonlocal _call_ended, _muted_capture_empty_time, _muted_filler_dropped_time
        if not frames:
            return
        if not SARVAM_API_KEY:
            _log.warning("[MUTED-CAPTURE] no STT API key set — skipping transcription")
            return
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                for f in frames:
                    wf.writeframes(f)
            buf.seek(0)
            wav_data = buf.read()
        except Exception as e:
            _log.warning(f"[MUTED-CAPTURE] WAV build error: {e}")
            return
        text = None
        _cascade_tag = "sarvam=empty"
        try:
            form = aiohttp.FormData()
            form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
            form.add_field("language_code", "hi-IN")
            form.add_field("model", "saaras:v3")
            form.add_field("mode", "transcribe")
            async with _get_http_session().post(
                SARVAM_STT_URL,
                headers={"api-subscription-key": SARVAM_API_KEY},
                data=form,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    text = (result.get("transcript") or "").strip() or None
                    _cascade_tag = "sarvam=ok" if text else "sarvam=empty"
                else:
                    body = await resp.text()
                    _log.warning(
                        f"[MUTED-CAPTURE] Sarvam STT failed: {resp.status} {body[:200]}"
                    )
        except Exception as e:
            _log.warning(f"[MUTED-CAPTURE] Sarvam error: {e}")
        if not text:
            _log.info(f"[MUTED-CAPTURE] empty transcript [{_cascade_tag}] (speech_ms={speech_ms:.0f})")
            _muted_capture_empty_time = asyncio.get_event_loop().time()
            return
        # IVR / voicemail check — captured during muted window (greeting)
        if _is_ivr_message(text):
            _log.info(f"[IVR] busy-line in muted-capture — ending call: {text!r}")
            _live_transcript.append({"role": "ivr", "text": text})
            if not _call_ended:
                _call_ended = True
                call_state["ended_naturally"] = True
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("ivr_detected"))
            return
        # Bot-echo: Sarvam/Soniox captured our own greeting voice echoing through the
        # caller's speakerphone — discard, it is not the user speaking.
        if _is_bot_echo(text):
            _log.info(f"[MUTED-CAPTURE] bot-echo discarded: {text!r}")
            return
        tokens = _normalize_stt_tokens(text)
        if tokens and all(t in _SARVAM_FILLER_HALLUCINATIONS for t in tokens):
            _log.info(f"[MUTED-CAPTURE] dropped all-filler {text!r} [{_cascade_tag}]")
            _muted_filler_dropped_time = asyncio.get_event_loop().time()
            return
        # Sarvam/Soniox hallucination: same token repeated 2+ times (e.g. "हाँ हाँ",
        # "हाँ हाँ हाँ") from background audio bleed or line noise.  A genuine
        # single-word confirmation arrives as one token, not a repetition.
        if len(tokens) >= 2 and len(set(tokens)) == 1:
            _log.info(
                f"[MUTED-CAPTURE] repeated-token hallucination {text!r} [{_cascade_tag}] — dropped"
            )
            _muted_filler_dropped_time = asyncio.get_event_loop().time()
            return
        _log.info(
            f"[MUTED-CAPTURE] captured user speech [{_cascade_tag}]: {text!r} "
            f"(speech_ms={speech_ms:.0f}) — buffered, not sent to Gemini"
        )
        _muted_inject["text"] = text
        _muted_transcript_log.append(text)

    # 7. Function tools
    # Tokens that in ANY conjugation indicate the caller manufactures/sells the product.
    # Gemini Live ASR mis-transcribes "plastic drum banate hain" → "stick drum banate hain"
    # but the "banate" token survives — this guard catches it before the API fires.
    _SELLER_MANUFACTURE_TOKENS = {
        "banate", "banaate", "banata", "banaata", "banati", "banaati",
        "बनाते", "बनाता", "बनाती", "बनाती", "बनाते हैं", "बनाता हूँ",
        "bechte", "bechta", "बेचते", "बेचता",
        "supplier", "manufacturer", "dealer", "distributor",
        "supply karte", "khud banate", "खुद बनाते",
    }

    @function_tool
    async def FetchCategorySchema(tool_ctx: RunContext, srchterm: str) -> dict:
        """Call when the buyer changes their product requirement mid-call.
        Pass the new product as a simple English search term (e.g. 'washing-machine', 'cctv')."""
        _log.info(f"[FetchCategorySchema] called with srchterm={srchterm!r}")

        # Hard guard: block if recent user speech contains manufacturer/seller signals.
        # This catches Gemini Live ASR errors where "plastic drum banate hain" gets
        # transcribed as "stick drum banate hain" — the seller token still survives.
        _recent = " ".join(
            t.get("text", "") for t in _live_transcript[-4:] if t.get("role") in ("user", "buyer")
        ).lower()
        if any(tok.lower() in _recent for tok in _SELLER_MANUFACTURE_TOKENS):
            _log.warning(
                f"[FetchCategorySchema] SELLER BLOCK — manufacture/sell token in recent turns "
                f"({_recent!r}). Returning seller_detected instead of category switch."
            )
            return {
                "seller_detected": True,
                "instruction": (
                    "The caller is a SELLER or MANUFACTURER, NOT a buyer. "
                    "Follow the CALLER IS A SELLER instructions: ask one confirmation "
                    "(\"अच्छा जी — तो आप [product] खुद बनाते / बेचते हैं?\"), then close warmly. "
                    "Do NOT ask any spec questions."
                ),
            }

        result = await _execute_function_call(
            "FetchCategorySchema", {"srchterm": srchterm},
            functions=_functions, call_state=call_state,
        )
        return result

    @function_tool
    async def FetchLead(tool_ctx: RunContext, lead_id: str = "", mobile: str = "") -> dict:
        """Fetch customer lead details from VoiceDesk MIS API. Pass lead_id or mobile."""
        _log.info(f"[FetchLead] called | lead_id={lead_id!r} | mobile={mobile!r}")
        result = await _execute_function_call(
            "FetchLead", {"lead_id": lead_id, "mobile": mobile},
            functions=_functions, call_state=call_state,
        )
        return result

    tools = [FetchCategorySchema, FetchLead] if _function_calling else []
    if _function_calling:
        _dynamic_tools = build_during_call_tools(_functions, _execute_function_call, call_state)
        if _dynamic_tools:
            logger.info(f"[FnCall] registered {len(_dynamic_tools)} custom during-call tool(s): "
                        f"{[t.info.name for t in _dynamic_tools]}")
            tools += _dynamic_tools
    agent = Agent(instructions=system_instruction, tools=tools)

    # 8. AgentSession
    session = AgentSession(llm=llm)

    # 9. Event handlers (replace Pipecat FrameProcessors)

    @session.on("conversation_item_added")
    def _on_item_added(ev) -> None:
        nonlocal _closing_buffer, _closing_triggered, _early_close_muting, _live_transcript, _pending_assistant_text, _close_status, _wrong_opener_detected
        item = ev.item if hasattr(ev, "item") else ev
        role = getattr(item, "role", None)
        role_str = role.value if hasattr(role, "value") else str(role) if role else ""
        text = (
            getattr(item, "text_content", None)
            or getattr(item, "text", None)
            or ""
        )
        # Capture user items committed to session history (fallback when
        # user_input_transcribed doesn't fire, e.g. Gemini realtime without
        # input_audio_transcription enabled)
        if role_str == "user":
            if text and text in _silero_rejected_turns:
                _log.info(f"[TRANSCRIPT] USER (committed): skipped — Silero-rejected {text!r}")
                _silero_rejected_turns.discard(text)
                return
            if text and not any(t["role"] == "user" and t["text"] == text for t in _live_transcript):
                _log.info(f"[TRANSCRIPT] USER (committed): {text!r}")
                _live_transcript.append({"role": "user", "text": text})
            return
        if role_str != "assistant" or _closing_triggered:
            return
        _closing_buffer += " " + text
        if text:
            _log.info(f"[TRANSCRIPT] Turn {_turn_counter} | AGENT: {text!r}")
            # Detect Gemini wrong opener: first agent turn is a connection probe instead of
            # the product greeting. Log loudly so it's visible in ops monitoring.
            if not _wrong_opener_detected and not _greeting_done and not _live_transcript:
                _text_lower = text.lower()
                _WRONG_OPENER_PHRASES_BOT = (
                    "क्या आप अभी line पर हैं", "क्या आप अभी लाइन पर हैं",
                    "kya aap abhi line par", "are you on the line",
                )
                if any(p in _text_lower for p in _WRONG_OPENER_PHRASES_BOT):
                    _wrong_opener_detected = True
                    _log.warning(f"[GREETING] Wrong opener detected — Gemini said {text!r} instead of product greeting")
            # Barge-in cleanup: if the user interrupted this bot turn mid-sentence,
            # _barge_in_fired is still True from that turn (reset to False only when
            # the NEXT speaking turn starts). A committed item that ends without
            # sentence-ending punctuation is a truncated partial — skip it.
            _last_char = text.rstrip()[-1] if text.rstrip() else ""
            _is_incomplete = _barge_in_fired and _last_char not in ("।", ".", "?", "!", "…")
            if _is_incomplete:
                _log.info(f"[BARGE-IN] Skipping interrupted partial bot turn: {text!r}")
                # Bot never finished speaking this turn — reset early-close flag so the
                # closing phrase (which the user never heard) doesn't lock out the mic.
                if _early_close_muting and not _closing_triggered:
                    _early_close_muting = False
                    _log.info("[BARGE-IN] Resetting early_close_muting — closing phrase was barged into")
            elif not _live_transcript or _live_transcript[-1] != {"role": "assistant", "text": text}:
                _asst_entry: dict = {"role": "assistant", "text": text}
                _live_transcript.append(_asst_entry)
        # Sniffer partial is superseded by the officially committed item — clear it.
        _pending_assistant_text = ""
        # Only run closing phrase detection if the bot actually finished speaking this turn
        # (not barged into). A closing phrase in a barged-into turn was never heard by the
        # user and must not end the call.
        if not _is_incomplete:
            if not _early_close_muting and not _closing_triggered:
                _buf_lower = _closing_buffer.lower()
                if any(m in _buf_lower for m in (
                    "details मिल गईं",   # start of Hindi closing line
                    "sellers आपसे contact",   # closing line only — not mid-call "relevant sellers से connect"
                    "sellers will contact",
                    "all details",        # English equivalent
                )):
                    _early_close_muting = True
                    _set_mic(False, reason="commit-closing-phrase")
                    _log.info("[CLOSE DETECT] Partial closing phrase detected in commit — mic muted")
            if _is_closing_phrase(_closing_buffer):
                _closing_triggered = True
                call_state["ended_naturally"] = True
                if _is_not_interested_close(text):
                    _close_status = "not_interested"
                    _log.info("[CLOSE DETECT] Not-interested close detected — status=not_interested")
                else:
                    _close_status = "completed"
                _log.info(f"[CLOSE DETECT] Closing phrase matched — status={_close_status!r} — scheduling end")
                _set_mic(False, reason="closing-phrase-matched")
                asyncio.create_task(_handle_close())

    @session.on("user_input_transcribed")
    def _on_user_spoke(ev) -> None:
        nonlocal _turn_counter, _live_transcript, _pending_user_text, _wav_reset_flag, _call_ended, _early_inject_done, _abusive_detected, _close_status
        # User spoke — reset inactivity timer (pass from_user_speech=True so nudge count clears)
        if not _call_ended:
            _reset_inactivity(from_user_speech=True)
        is_final = getattr(ev, "is_final", True)
        transcript_text = (
            getattr(ev, "transcript", None)
            or getattr(ev, "text", None)
            or ""
        ).strip()
        if not transcript_text:
            return
        # Snapshot agent state at the moment user speech arrives (for interruption diagnosis).
        _agent_state_now = ""
        try:
            _s = session.agent_state
            _agent_state_now = _s.value if hasattr(_s, "value") else str(_s)
        except Exception:
            _agent_state_now = "?"
        # Live speech arrived — discard any buffered muted-window text.
        # Remove from the Mongo log too: it was never sent to Gemini, so it shouldn't
        # influence analysis as if it were a user response.
        if _muted_inject["text"]:
            _log.info(
                f"[MUTED-CAPTURE] live speech arrived — discarding muted buffer "
                f"{_muted_inject['text']!r}"
            )
            if _muted_transcript_log and _muted_transcript_log[-1] == _muted_inject["text"]:
                _muted_transcript_log.pop()
            _muted_inject["text"] = ""
        muted_prefix = ""  # no longer combining
        if is_final:
            _turn_counter += 1
            speech_ms_now = _user_audio["speech_ms"]
            _log.info(
                f"[TRANSCRIPT] Turn {_turn_counter} | USER (FINAL): {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled} "
                f"speech_ms={speech_ms_now:.0f}"
            )
            if _call_ended:
                return
            # Zero-speech guard: if our local VAD captured no audio above the RMS
            # threshold in this window AND the PCM buffer is empty, Gemini is
            # transcribing audio that arrived before the current mic-ON window
            # (e.g. a sound that triggered the PARTIAL just before the mic was
            # muted for the bot's speaking turn). No real user audio → reject.
            if speech_ms_now == 0 and not _current_window_pcm:
                _log.info(
                    f"[GEMINI] Rejected zero-speech FINAL {transcript_text!r} "
                    f"— speech_ms=0 and window_pcm empty (pre-mute echo)"
                )
                _turn_counter -= 1
                if _live_transcript and _live_transcript[-1].get("role") == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                return
            _early_inject_done = False  # reset for next turn
            _had_partial = bool(_pending_user_text)
            _pending_user_text = ""
            # Gemini confirmed this turn — rotate WAV segment so next turn starts fresh
            _wav_reset_flag = True
            # Silero sanity-check: Gemini occasionally fires on background audio (TV,
            # nearby conversation). If Silero finds < min voiced ms in the window PCM,
            # discard the transcript rather than letting background noise reach Mongo.
            _voiced = 0  # initialised here so the noise filter below can reference it
            if _silero_session is not None and _current_window_pcm:
                _voiced = _silero_voiced_ms(
                    bytes(_current_window_pcm), _sarvam_silero_threshold
                )
                if _voiced < _sarvam_silero_min_speech_ms:
                    # Short monosyllabic words (e.g. "हां", "ना", "ओके") have very brief
                    # voiced frames and often fall below the Silero threshold. Gemini's
                    # own transcription is strong evidence — if speech_ms >= 150 ms AND
                    # Gemini produced a non-empty transcript, trust it over Silero here.
                    # GUARD: also require voiced_ms >= 60 OR voiced/speech ratio >= 8%.
                    # IVR carrier audio has very high speech_ms but almost zero voiced_ms
                    # (e.g. voiced=32ms in speech=1110ms = 2.9%) — reject that pattern.
                    _voiced_ratio = _voiced / speech_ms_now if speech_ms_now > 0 else 0.0
                    if speech_ms_now >= _gemini_silero_fallback_speech_ms and (
                        _voiced >= 60 or _voiced_ratio >= 0.08
                    ):
                        _log.info(
                            f"[GEMINI] Silero weak but speech_ms sufficient — accepting "
                            f"{transcript_text!r} (voiced_ms={_voiced:.0f} < "
                            f"min={_sarvam_silero_min_speech_ms}, speech_ms={speech_ms_now:.0f}, "
                            f"ratio={_voiced_ratio:.2%})"
                        )
                    else:
                        _log.info(
                            f"[GEMINI] Silero rejected FINAL {transcript_text!r} — "
                            f"voiced_ms={_voiced:.0f} < min={_sarvam_silero_min_speech_ms} "
                            f"(speech_ms={speech_ms_now:.0f}, ratio={_voiced_ratio:.2%})"
                        )
                        # Remove any partial placeholder that was already added for this turn
                        if _live_transcript and _live_transcript[-1]["role"] == "user":
                            _live_transcript.pop()
                        # Block _on_item_added from re-inserting this text
                        _silero_rejected_turns.add(transcript_text)
                        # Save to muted_transcript so there is at least a record in Mongo
                        # that the user said something (even if unconfirmed by Silero).
                        _muted_transcript_log.append(f"[low-confidence] {transcript_text}")
                        return
                _log.info(
                    f"[GEMINI] Silero confirmed FINAL (voiced_ms={_voiced:.0f})"
                )
            # Gemini calibration hallucination: Gemini outputs counting/alphabet
            # sequences from near-silence.  Exact-match so real user answers aren't hit.
            _norm_transcript = unicodedata.normalize("NFC", transcript_text.strip())
            if _norm_transcript in _GEMINI_CALIBRATION_HALLUCINATIONS:
                _log.info(f"[NOISE] Gemini calibration hallucination discarded: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                return
            # Short noise filter: single-token transcripts below 350 ms that are
            # known noise patterns (throat-clears, breathing, junk syllables).
            # "हाँ", "ना", "ओके" are NOT in the set so valid monosyllabics pass.
            # Guard: only reject if Silero found essentially no voiced frames (_voiced < 30).
            # A real "हूं"/"हूँ" from a user will have some voiced energy even if weak;
            # bg noise transcribed as these tokens typically has voiced_ms near 0.
            if speech_ms_now < 350:
                _short_toks = _normalize_stt_tokens(_norm_transcript)
                if (
                    len(_short_toks) == 1
                    and unicodedata.normalize("NFC", _short_toks[0]) in _GEMINI_SHORT_NOISE_TOKENS
                    and _voiced < 30
                ):
                    _log.info(
                        f"[NOISE] Short noise token rejected: {transcript_text!r} "
                        f"(speech_ms={speech_ms_now:.0f}, voiced_ms={_voiced:.0f})"
                    )
                    if _live_transcript and _live_transcript[-1]["role"] == "user":
                        _live_transcript.pop()
                    _silero_rejected_turns.add(transcript_text)
                    _muted_transcript_log.append(f"[noise-filtered] {transcript_text}")
                    return
            # Bystander filter: drop turns that are clearly a nearby person talking
            # to a third party, not to the bot (e.g. "यह लोग एक और किलो वाला…").
            if _is_bystander_speech(transcript_text):
                _log.info(f"[NOISE] Bystander speech discarded: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                return
            # IVR / busy-line filter: first live transcript matching a telco/voicemail
            # pattern means we dialled an IVR, not a real buyer — end the call immediately.
            if _is_ivr_message(transcript_text):
                _log.info(f"[IVR] busy-line/voicemail detected — ending call: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _live_transcript.append({"role": "ivr", "text": transcript_text})
                _silero_rejected_turns.add(transcript_text)
                if not _call_ended:
                    _call_ended = True  # set immediately — prevents closing-phrase from winning the race
                    call_state["ended_naturally"] = True
                    asyncio.create_task(_kick_caller_safe())
                    asyncio.create_task(_save_and_close("ivr_detected"))
                return
            # Abuse detection — end the call immediately without passing the text to Gemini.
            if _is_abusive_text(transcript_text) and not _call_ended and not _abusive_detected:
                _abusive_detected = True
                _close_status = "abusive"
                _log.warning(f"[ABUSE] Abusive language detected in FINAL — ending call: {transcript_text!r}")
                if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript[-1] = {"role": "user", "text": transcript_text}
                else:
                    _live_transcript.append({"role": "user", "text": transcript_text})
                _call_ended = True
                _cancel_inactivity()
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("abusive"))
                return
            # Replace the last entry only if it was a partial for THIS same turn.
            # After a barge-in the previous agent turn is skipped, leaving a completed
            # user entry as _live_transcript[-1].  Without the _had_partial guard that
            # completed entry would be silently overwritten by the new turn's final.
            _user_entry: dict = {"role": "user", "text": transcript_text}
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1] = _user_entry
            else:
                _live_transcript.append(_user_entry)
            # Start watchdog: if Gemini doesn't begin speaking within 8 s, re-inject.
            # When the FINAL arrives while the bot is already speaking (barge-in path),
            # do NOT inject immediately — that causes Gemini to queue a second generation
            # on top of the already-in-flight partial response, producing two consecutive
            # bot turns for a single user utterance.  Instead the speaking→listening
            # handler below restarts this watchdog with a 1 s window once the current
            # speaking turn ends, but only when the turn was short (micro-ack); a long
            # turn means Gemini already gave a full answer and no re-inject is needed.
            nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn, _stale_partial_task, _last_user_turn_time, _final_arrived_while_speaking
            _last_user_turn_time = asyncio.get_event_loop().time()
            # FINAL arrived — cancel the stale-partial watchdog (no longer needed)
            if _stale_partial_task and not _stale_partial_task.done():
                _stale_partial_task.cancel()
                _stale_partial_task = None
            _last_user_final_text = transcript_text
            _last_user_final_turn = _turn_counter
            # Track whether bot was already speaking when this FINAL arrived.
            # Used below to suppress the micro-ack watchdog restart: if the bot was
            # already mid-response to the PARTIAL (early-inject path), the FINAL is
            # just confirmation — no re-inject needed regardless of speaking duration.
            try:
                _cur_state = session.agent_state
                _cur_state_val = _cur_state.value if hasattr(_cur_state, "value") else str(_cur_state)
                _final_arrived_while_speaking = (_cur_state_val == "speaking")
            except Exception:
                _final_arrived_while_speaking = False
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
            _bot_resp_watchdog_task = asyncio.create_task(
                _bot_response_watchdog(transcript_text, _turn_counter,
                                       speaking_count_at_start=_speaking_turns_completed)
            )
        else:
            # Partial — keep the latest chunk in _pending_user_text; also put a
            # placeholder in _live_transcript so save_call_data sees it even if
            # the final never arrives (abrupt disconnect before Gemini finalises).
            _log.info(
                f"[TRANSCRIPT] PARTIAL | USER: {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled}"
            )
            # IVR check on PARTIAL: Gemini responds to partials in ~10ms, so by the
            # time the FINAL arrives the bot is already speaking. Catch it here instead.
            if _is_ivr_message(transcript_text) and not _call_ended:
                _log.info(f"[IVR] busy-line/voicemail detected in PARTIAL — ending call: {transcript_text!r}")
                _live_transcript.append({"role": "ivr", "text": transcript_text})
                _call_ended = True
                call_state["ended_naturally"] = True
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("ivr_detected"))
                return
            # Abuse detection on PARTIAL: catches abuse before EARLY-INJECT fires.
            if _is_abusive_text(transcript_text) and not _call_ended and not _abusive_detected:
                _abusive_detected = True
                _close_status = "abusive"
                _log.warning(f"[ABUSE] Abusive language detected in PARTIAL — ending call: {transcript_text!r}")
                _live_transcript.append({"role": "user", "text": transcript_text})
                _call_ended = True
                _cancel_inactivity()
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("abusive"))
                return
            _had_partial = bool(_pending_user_text)
            _pending_user_text = transcript_text
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1]["text"] = transcript_text
            else:
                _live_transcript.append({"role": "user", "text": transcript_text})

            # Reset stale-partial watchdog: if FINAL doesn't arrive within 6 s of the
            # last PARTIAL, force-inject current text to Gemini so the bot can respond.
            if _stale_partial_task and not _stale_partial_task.done():
                _stale_partial_task.cancel()
            if _greeting_done and not _call_ended and not _closing_triggered:
                _stale_partial_task = asyncio.create_task(
                    _stale_partial_watchdog(transcript_text, speaking_count_at_start=_speaking_turns_completed)
                )

            # Early-inject: for short monosyllabic responses (जी, हाँ, ना, ok…)
            # PARTIAL == FINAL 100% of the time. Inject now so Gemini has a
            # 400-600 ms head start on generating the real answer before the
            # actual FINAL arrives, eliminating the post-filler thinking gap.
            # Guards:
            #   1. speech_ms >= 150 — filters sub-100ms noise glitches; slow/soft
            #      speech still accumulates ≥150ms before Gemini fires PARTIAL
            #   2. agent in "listening" — barge-in path uses FINAL-INJECT instead
            _speech_ms_now = _user_audio["speech_ms"]
            if (
                not _early_inject_done
                and not _call_ended
                and _greeting_done
                and _agent_state_now == "listening"
                and _speech_ms_now >= 150
            ):
                _tokens = set(_normalize_stt_tokens(transcript_text))
                if _tokens and _tokens <= _SHORT_TERMINAL_TOKENS:
                    if _rt is not None and getattr(_rt, "_active_session", None) is not None:
                        try:
                            _rt._send_client_event(
                                types.LiveClientContent(
                                    turns=[types.Content(parts=[types.Part(text=transcript_text)], role="user")],
                                    turn_complete=True,
                                )
                            )
                            _early_inject_done = True
                            _log.info(
                                f"[EARLY-INJECT] Short terminal PARTIAL → Gemini: {transcript_text!r} "
                                f"(speech_ms={_speech_ms_now:.0f})"
                            )
                        except Exception as _ei_exc:
                            _log.warning(f"[EARLY-INJECT] failed: {_ei_exc}")

    _greeting_done = False
    _bot_has_spoken = False  # True once the agent first transitions to "speaking"
    _greeting_retry_triggered = False
    _gemini_connect_failed = False  # True when Gemini WebSocket never connected within 5 s
    _wrong_opener_detected = False  # True when Gemini's first turn was a probe ("क्या आप line पर हैं?") not a greeting
    _mic_enabled: bool = False  # mirrors the last value passed to set_audio_enabled
    _barge_in_fired: bool = False  # True once the 2s unmute task fires for this bot turn
    _bot_resp_watchdog_task: asyncio.Task | None = None  # cancelled when bot starts speaking
    _last_user_final_text: str = ""      # text of the most recent user FINAL turn
    _last_user_final_turn: int = 0       # _turn_counter value when watchdog was started
    _speaking_start_time: float = 0.0   # monotonic time when current speaking turn began
    _speaking_turns_completed: int = 0  # incremented each time speaking→listening fires
    _early_inject_done: bool = False     # True if this PARTIAL was already early-injected
    _stale_partial_task: asyncio.Task | None = None  # fires if PARTIAL goes 6s without FINAL
    _last_user_turn_time: float = 0.0  # monotonic time of last accepted user FINAL (for inactivity recency check)
    _final_arrived_while_speaking: bool = False  # True when FINAL arrives while bot is already speaking

    # Short terminal tokens: PARTIAL == FINAL for these 100% of the time.
    # Safe to treat the PARTIAL as FINAL and inject early so Gemini gets
    # a 400-600 ms head start on generating the real answer.
    _SHORT_TERMINAL_TOKENS: frozenset = frozenset(unicodedata.normalize("NFC", w) for w in {
        "जी", "हाँ", "हां", "हा", "ना", "नहीं", "नहि",
        "yes", "no", "ok", "okay", "हाँजी", "हांजी",
        "ठीक", "बिल्कुल", "सही", "sure", "bilkul",
    })

    async def _bot_response_watchdog(user_text: str, turn: int, timeout: float = 8.0, speaking_count_at_start: int = 0) -> None:
        """Re-inject the user's last turn if Gemini doesn't start speaking within `timeout` s.
        Handles silent Gemini failures where the model transcribed audio but produced
        no output — observed as 12+ second silences before user disconnects."""
        await asyncio.sleep(timeout)
        if _call_ended or _closing_triggered or _last_user_final_turn != turn:
            return
        # If a speaking turn completed after this watchdog was created, the bot already
        # gave a real answer (early-inject response) — re-injecting would duplicate it.
        if _speaking_turns_completed > speaking_count_at_start:
            return
        # If the bot is currently mid-speech, the answer is already in progress.
        # Re-injecting now queues a duplicate generation on top of the live turn.
        # This covers long bot responses (>= 8s) where _speaking_turns_completed hasn't
        # incremented yet when the watchdog fires — seen as triple-Q1 in Call 1.
        try:
            _agent_s = session.agent_state
            _agent_s_val = getattr(_agent_s, "value", None) or str(_agent_s)
            if _agent_s_val == "speaking":
                _log.info(
                    f"[GEMINI-WATCHDOG] Bot currently speaking — suppressing re-inject (turn={turn})"
                )
                return
        except Exception:
            pass
        if _rt is None or getattr(_rt, "_active_session", None) is None:
            return
        _log.warning(
            f"[GEMINI-WATCHDOG] No response to {user_text!r} in 8s — re-injecting (turn={turn})"
        )
        try:
            _rt._send_client_event(
                types.LiveClientContent(
                    turns=[types.Content(parts=[types.Part(text=user_text)], role="user")],
                    turn_complete=True,
                )
            )
        except Exception as e:
            _log.warning(f"[GEMINI-WATCHDOG] re-inject failed: {e}")

    async def _stale_partial_watchdog(text: str, timeout: float = 6.0, speaking_count_at_start: int = 0) -> None:
        """Force-inject a PARTIAL to Gemini if no FINAL arrives within `timeout` s.
        Covers the case where the user speaks continuously (no clear pause) so Soniox
        never commits a FINAL — the bot would stay silent until the caller hangs up."""
        nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn
        await asyncio.sleep(timeout)
        if _call_ended or _closing_triggered or not _greeting_done:
            return
        if _pending_user_text != text:
            return  # a newer partial already arrived; its timer handles it
        if not _rt or getattr(_rt, "_active_session", None) is None:
            return
        # If Gemini already started speaking in response to the user audio, injecting
        # the same text again would interrupt the live response and cause a duplicate
        # (agent starts saying something, gets cut off, repeats from scratch).
        try:
            _agent_s_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
            if _agent_s_val == "speaking":
                _log.info(f"[STALE-PARTIAL] Bot already speaking — suppressing re-inject for {text!r}")
                return
        except Exception:
            pass
        # If any bot speaking turn completed since the partial arrived, Gemini already
        # answered — re-injecting would generate a duplicate response.
        if _speaking_turns_completed > speaking_count_at_start:
            _log.info(f"[STALE-PARTIAL] Bot already responded (turns_completed={_speaking_turns_completed}) — suppressing re-inject for {text!r}")
            return
        _log.warning(
            f"[STALE-PARTIAL] No FINAL in {timeout:.0f}s — force-injecting: {text!r}"
        )
        try:
            _rt._send_client_event(
                types.LiveClientContent(
                    turns=[types.Content(parts=[types.Part(text=text)], role="user")],
                    turn_complete=True,
                )
            )
            _last_user_final_text = text
            _last_user_final_turn = _turn_counter
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
            _bot_resp_watchdog_task = asyncio.create_task(
                _bot_response_watchdog(text, _turn_counter,
                                       speaking_count_at_start=_speaking_turns_completed)
            )
        except Exception as e:
            _log.warning(f"[STALE-PARTIAL] force-inject failed: {e}")

    def _set_mic(enabled: bool, reason: str = "") -> None:
        nonlocal _mic_enabled
        _mic_enabled = enabled
        state_tag = "ON " if enabled else "OFF"
        reason_tag = f" [{reason}]" if reason else ""
        _log.info(f"[MIC] mic → {state_tag}{reason_tag}")
        try:
            if hasattr(session, "input") and hasattr(session.input, "set_audio_enabled"):
                session.input.set_audio_enabled(enabled)
        except Exception as e:
            _log.warning(f"[MIC] set_audio_enabled({enabled}) error: {e}")

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        nonlocal _echo_guard_task, _speaking_unmute_task, _greeting_done, _bot_has_spoken, _barge_in_fired, _bot_resp_watchdog_task, _speaking_start_time, _speaking_turns_completed, _muted_capture_empty_time, _muted_filler_dropped_time, _last_user_final_text, _final_arrived_while_speaking
        new_state = getattr(ev, "new_state", None)
        old_state = getattr(ev, "old_state", None)
        state_str = new_state.value if hasattr(new_state, "value") else str(new_state) if new_state else ""
        old_str   = old_state.value if hasattr(old_state, "value") else str(old_state) if old_state else "?"
        _log.info(
            f"[STATE] {old_str} → {state_str} | "
            f"mic={_mic_enabled} greeting_done={_greeting_done} "
            f"call_ended={_call_ended} closing={_closing_triggered}"
        )

        if state_str == "speaking":
            if _call_ended:
                return
            _bot_has_spoken = True
            _barge_in_fired = False  # reset at start of each bot turn
            _speaking_start_time = asyncio.get_event_loop().time()
            _cancel_inactivity()
            # Gemini started speaking — cancel the response watchdog and reset the
            # FINAL-while-speaking flag so a fresh turn starts with a clean slate.
            _final_arrived_while_speaking = False
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
                _bot_resp_watchdog_task = None
            # Cancel any in-flight hold task and any pending muted injection.
            if _echo_guard_task and not _echo_guard_task.done():
                _echo_guard_task.cancel()
                _log.info("[SPEAKING-MUTE] cancelled previous hold — new speaking turn")
            # Discard any buffered muted-window text — it was never processed by Gemini.
            # Also remove from Mongo log so it doesn't inflate user signal in analysis.
            if _muted_inject["text"]:
                _log.info(
                    f"[MUTED-CAPTURE] discarding uncombined muted text (not sent to Gemini): "
                    f"{_muted_inject['text']!r}"
                )
                if _muted_transcript_log and _muted_transcript_log[-1] == _muted_inject["text"]:
                    _muted_transcript_log.pop()
                _muted_inject["text"] = ""
            # Echo guard: if Gemini fires a new speaking turn within 200 ms of an empty
            # muted-capture result, it is almost certainly reacting to its own TTS echoing
            # back through the PSTN path — not real user speech. Interrupt immediately so
            # the user's actual response can be heard.
            _now_eg = asyncio.get_event_loop().time()
            if (_greeting_done
                    and _muted_capture_empty_time > 0
                    and (_now_eg - _muted_capture_empty_time) < 0.20):
                _log.warning(
                    f"[ECHO-GUARD] new speaking turn {(_now_eg - _muted_capture_empty_time)*1000:.0f}ms "
                    "after empty muted-capture — suspected TTS echo, interrupting"
                )
                _muted_capture_empty_time = 0.0
                _last_user_final_text = ""   # prevent stale watchdog re-inject after interrupt
                _barge_in_fired = True       # keeps mic ON through the post-speech-hold that follows
                session.interrupt()
                return
            _muted_capture_empty_time = 0.0
            # Filler guard: if Gemini fires a new speaking turn within 1.5 s of a dropped
            # filler/hallucination, it heard the filler via the live audio path (after the
            # 4 s unmute) — the user said nothing substantive. Interrupt so the user can
            # respond properly. 1.5 s covers Sarvam latency (~180 ms) + Gemini generation
            # time (~400-800 ms). Separate from echo guard to avoid changing its 200 ms window.
            if (_greeting_done
                    and _muted_filler_dropped_time > 0
                    and (_now_eg - _muted_filler_dropped_time) < 1.5):
                _log.warning(
                    f"[FILLER-GUARD] new speaking turn {(_now_eg - _muted_filler_dropped_time)*1000:.0f}ms "
                    "after dropped filler — Gemini heard filler via live path, interrupting"
                )
                _muted_filler_dropped_time = 0.0
                _last_user_final_text = ""
                _barge_in_fired = True
                session.interrupt()
                return
            _muted_filler_dropped_time = 0.0
            # Mute mic at the start of every bot speaking turn.
            # For mid-call turns: unmute after 4 s so the user can interrupt.
            # Greeting turn: unmute after 5 s so Gemini warms up to the audio
            # stream before the greeting ends, reducing post-greeting input latency.
            # Closing turn stays muted for the full turn.
            # Caller audio continues to flow into _buffer_user_audio (raw track is
            # unaffected) and is written to _muted_capture while _mic_enabled=False.
            _set_mic(False, reason="speaking-start")
            # Cancel any previous timer that didn't fire yet (new turn arrived faster).
            if _speaking_unmute_task and not _speaking_unmute_task.done():
                _speaking_unmute_task.cancel()
                _speaking_unmute_task = None
            if _greeting_done and not _closing_triggered and not _early_close_muting:
                async def _delayed_unmute() -> None:
                    nonlocal _barge_in_fired
                    await asyncio.sleep(4.0)
                    if not _call_ended and not _closing_triggered and not _early_close_muting:
                        _barge_in_fired = True
                        _set_mic(True, reason="4s-speaking-unmute")
                _speaking_unmute_task = asyncio.create_task(_delayed_unmute())
            elif not _greeting_done and not _closing_triggered:
                # Greeting turn early unmute: re-enable Gemini audio input at 4 s so
                # it is already processing the stream when the greeting finishes.
                # 4 s (was 3 s, before that 5 s) — greetings can be as short as ~4.5 s;
                # at 5 s the timer fired after the greeting ended for short greetings,
                # leaving Gemini with zero warm-up time and silently dropping the first user turn.
                async def _greeting_early_unmute() -> None:
                    await asyncio.sleep(4.0)
                    if not _greeting_done and not _call_ended:
                        _set_mic(True, reason="greeting-4s-early-unmute")
                _speaking_unmute_task = asyncio.create_task(_greeting_early_unmute())

        elif state_str in ("listening", "idle"):
            # Bot finished speaking — cancel the long watchdog.  If a FINAL is still
            # pending (arrived while the bot was mid-response to a partial), decide
            # whether to re-inject based on how long the bot spoke:
            #   • Short turn (< 3 s) → micro-ack (e.g. 'अच्छा जी') — Gemini didn't
            #     give a full answer; restart watchdog with 1 s so Gemini gets the
            #     FINAL quickly.
            #   • Long turn (≥ 3 s) → Gemini already gave a complete response to the
            #     partial; do NOT re-inject or we produce a duplicate bot turn.
            #   • Barge-in turn (any duration) — if speaking was long (≥ 3 s), Gemini
            #     already gave a complete answer via real-time audio even if the user
            #     interrupted. Re-injecting would produce a duplicate bot turn.
            #     The stale-partial watchdog handles any truly missed turn.
            speaking_duration = asyncio.get_event_loop().time() - _speaking_start_time
            _speaking_turns_completed += 1
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
                _bot_resp_watchdog_task = None
            # Only restart watchdog for true micro-acks (< 1.5 s TTS, e.g. "अच्छा जी").
            # Turns 1.5 s+ are complete responses (re-asks, questions) — re-injecting them
            # caused spurious double-responses and the fake 5-min timeout bug in Call 2/3.
            # Also skip if the FINAL arrived while the bot was already speaking: that means
            # the bot was already mid-response to the PARTIAL (early-inject path) and the
            # FINAL is just confirmation — re-injecting creates a duplicate second response.
            if (not _call_ended and not _closing_triggered
                    and _last_user_final_text
                    and speaking_duration < 1.5
                    and not _final_arrived_while_speaking):
                _bot_resp_watchdog_task = asyncio.create_task(
                    _bot_response_watchdog(_last_user_final_text, _last_user_final_turn, timeout=1.0,
                                           speaking_count_at_start=_speaking_turns_completed)
                )
            if _bot_has_spoken and not _greeting_done:
                _greeting_done = True
                _log.info(
                    f"[STATE] greeting_done → True (call_ended={_call_ended})"
                )
                # Transcribe any audio captured during the greeting window via Sarvam
                # so it lands in _muted_transcript_log (and hence Mongo).
                # Covers voicemail prompts, IVR menus, ambient speech that played
                # while the mic was muted for the bot's opening line.
                _greeting_captured_frames = _muted_capture["frames"][:]
                _greeting_captured_ms = _muted_capture["speech_ms"]
                _muted_capture["frames"].clear()
                _muted_capture["speech_ms"] = 0.0
                if _greeting_captured_frames:
                    _log.info(
                        f"[MUTED-CAPTURE] greeting window: {_greeting_captured_ms:.0f}ms "
                        "— spawning Sarvam transcription"
                    )
                    asyncio.create_task(
                        _transcribe_muted_period(_greeting_captured_frames, _greeting_captured_ms)
                    )
                    async def _post_greeting_inject() -> None:
                        # Wait for Sarvam to finish + buffer for live speech to arrive first.
                        # If the user already responded live, _turn_counter > 0 and we skip.
                        await asyncio.sleep(0.8)
                        if _call_ended or _closing_triggered or _turn_counter > 0:
                            return
                        text = _muted_inject.get("text", "")
                        if not text:
                            return
                        # Drop bare phone-pickup signals ("हाँ जी", "हाँ", "हेलो", "जी", etc.)
                        # captured mid-greeting. These are reflexive phone-answer responses,
                        # not product confirmations — injecting them causes Gemini to treat
                        # them as "yes I need the product" and skip to spec questions.
                        #
                        # Guard 1: duration — genuine answers to the requirement question take
                        # >800 ms; anything shorter is almost certainly a phone-pickup reflex.
                        if _greeting_captured_ms < 800:
                            _log.info(
                                f"[MUTED-CAPTURE] post-greeting inject skipped — too short "
                                f"({_greeting_captured_ms:.0f}ms): {text!r}"
                            )
                            if _muted_transcript_log and _muted_transcript_log[-1] == text:
                                _muted_transcript_log.pop()
                            _muted_inject["text"] = ""
                            return
                        # Guard 2: content — strip only Unicode punctuation/symbols (NOT [^\w]
                        # which incorrectly removes Devanagari matras/vowel-signs) so that
                        # "हाँ।" → "हाँ" and "हेलो।" → "हेलो" correctly match the token set.
                        _inject_tokens = {
                            unicodedata.normalize("NFC", "".join(
                                c for c in w.lower()
                                if unicodedata.category(c)[0] not in ("P", "S", "Z")
                            ))
                            for w in text.split() if w.strip()
                        }
                        _inject_tokens.discard("")
                        _BARE_GREETING_TOKENS = frozenset(unicodedata.normalize("NFC", w) for w in {
                            "हाँ", "हां", "हा", "जी", "हाँजी", "हांजी",
                            "हेलो", "hello", "हैलो", "hi", "हाय",
                            "haan", "ha", "han", "ji", "jee", "okay", "ok",
                            "हाँ", "हां", "बोलो", "bol", "bolo",
                            "बोला", "bola",  # past-tense "spoke" — reflexive phone-pickup, not a yes
                            "om",            # Gujarati/Marathi phone-answer greeting (Jai Shree Krishna)
                            "हो", "हो जी", "होजी", "हाँ हो",  # Marathi/regional reflexive "yes" pickup
                        })
                        if _inject_tokens and not (_inject_tokens - _BARE_GREETING_TOKENS):
                            _log.info(
                                f"[MUTED-CAPTURE] post-greeting inject skipped — bare phone-pickup signal: {text!r}"
                            )
                            if _muted_transcript_log and _muted_transcript_log[-1] == text:
                                _muted_transcript_log.pop()
                            _muted_inject["text"] = ""
                            return
                        if _rt is None or getattr(_rt, "_active_session", None) is None:
                            return
                        _muted_inject["text"] = ""
                        _log.info(f"[MUTED-CAPTURE] post-greeting inject → Gemini: {text!r}")
                        try:
                            _rt._send_client_event(
                                types.LiveClientContent(
                                    turns=[types.Content(parts=[types.Part(text=text)], role="user")],
                                    turn_complete=True,
                                )
                            )
                            nonlocal _muted_inject_sent_time
                            _muted_inject_sent_time = asyncio.get_event_loop().time()
                            # Watchdog: if Gemini doesn't start speaking within 3 s, re-inject.
                            # Handles silent Gemini failures after muted-capture injection.
                            # Snapshot speaking count so we don't re-inject if Gemini already responded.
                            _injected_text = text
                            _speaking_count_at_inject = _speaking_turns_completed
                            async def _post_greeting_watchdog() -> None:
                                await asyncio.sleep(3.0)
                                if _call_ended or _closing_triggered or _turn_counter > 0:
                                    return
                                # Bot already spoke at least once since inject — no need to re-inject.
                                if _speaking_turns_completed > _speaking_count_at_inject:
                                    return
                                _s = session.agent_state
                                _s_val = _s.value if hasattr(_s, "value") else str(_s)
                                if _s_val == "speaking":
                                    return
                                _log.warning(
                                    f"[GEMINI-WATCHDOG] post-greeting inject no response in 3s "
                                    f"— re-injecting {_injected_text!r}"
                                )
                                try:
                                    _rt._send_client_event(
                                        types.LiveClientContent(
                                            turns=[types.Content(parts=[types.Part(text=_injected_text)], role="user")],
                                            turn_complete=True,
                                        )
                                    )
                                except Exception as _ex:
                                    _log.warning(f"[GEMINI-WATCHDOG] post-greeting re-inject failed: {_ex}")
                            asyncio.create_task(_post_greeting_watchdog())
                        except Exception as e:
                            _log.warning(f"[MUTED-CAPTURE] post-greeting inject failed: {e}")
                    asyncio.create_task(_post_greeting_inject())
                if not _call_ended:
                    _set_mic(True, reason="greeting-complete")
                    _log.info("[MIC] Greeting complete — mic enabled")
            elif _greeting_done and _bot_has_spoken and not _call_ended and not _closing_triggered:
                # Post-speech hold: brief window after each bot turn to absorb TTS tail.
                # If the 2 s unmute already fired, mic is ON here — re-mute for the hold
                # so the WAV rotation and muted-capture flush still happen cleanly.
                # Bot finished speaking — cancel 2 s unmute timer if it hasn't fired yet.
                if _speaking_unmute_task and not _speaking_unmute_task.done():
                    _speaking_unmute_task.cancel()
                    _speaking_unmute_task = None
                if not _barge_in_fired:
                    _set_mic(False, reason="post-speech-hold-start")
                else:
                    _log.info("[POST-SPEECH-HOLD] barge-in already fired — mic stays ON during hold")

                if _echo_guard_task and not _echo_guard_task.done():
                    _echo_guard_task.cancel()
                    _log.info("[POST-SPEECH-HOLD] cancelled stale guard — starting new")

                async def _post_speech_hold() -> None:
                    _log.info(
                        f"[POST-SPEECH-HOLD] started (hold={_post_speech_hold_ms} ms)"
                    )
                    hold_ran_to_completion = False
                    try:
                        # Mic is already OFF from the speaking branch — no need to re-mute.
                        await asyncio.sleep(_post_speech_hold_ms / 1000)
                        hold_ran_to_completion = True
                        if not _call_ended and not _closing_triggered and not _early_close_muting:
                            if not _barge_in_fired:
                                _log.info(
                                    f"[POST-SPEECH-HOLD] expired ({_post_speech_hold_ms} ms) — "
                                    "enabling mic"
                                )
                                _set_mic(True, reason="post-speech-hold-expired")
                            else:
                                _log.info(
                                    f"[POST-SPEECH-HOLD] expired ({_post_speech_hold_ms} ms) — "
                                    "mic already ON (barge-in was active)"
                                )
                            _wav_reset_flag = True
                        else:
                            _log.info(
                                f"[POST-SPEECH-HOLD] expired — mic stays muted "
                                f"(call_ended={_call_ended} closing={_closing_triggered} "
                                f"early_mute={_early_close_muting})"
                            )
                    except asyncio.CancelledError:
                        _log.info("[POST-SPEECH-HOLD] cancelled — new speaking turn started")
                    finally:
                        # Snapshot and clear the muted-window audio. Spawn Sarvam
                        # transcription if the caller said anything substantive.
                        captured_ms = _muted_capture["speech_ms"]
                        captured_frames = _muted_capture["frames"][:]
                        _muted_capture["frames"].clear()
                        _muted_capture["speech_ms"] = 0.0
                        if (
                            captured_frames
                            and not _call_ended
                        ):
                            _log.info(
                                f"[MUTED-CAPTURE] {captured_ms:.0f} ms of speech captured — "
                                "spawning Sarvam transcription"
                            )
                            asyncio.create_task(
                                _transcribe_muted_period(captured_frames, captured_ms)
                            )

                _echo_guard_task = asyncio.create_task(_post_speech_hold())
            # Bot finished speaking — restart inactivity timer
            if not _call_ended and not _closing_triggered:
                _reset_inactivity()
        elif state_str == "thinking":
            # Gemini Realtime never emits "thinking" — no-op, just log.
            pass
        else:
            _log.info(f"[STATE] unhandled state {state_str!r} — no action taken")

    # 10. Subscribe to caller audio for Sarvam STT fallback buffering.
    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(track, pub, participant) -> None:
        if isinstance(track, rtc.RemoteAudioTrack):
            asyncio.create_task(_buffer_user_audio(track))

    # Start session — disable close_on_disconnect so the process stays alive
    # long enough for save_call_data (Mongo insert + call-log POST) to finish.
    try:
        await session.start(
            room=ctx.room,
            agent=agent,
            room_options=_RoomOptionsCls(close_on_disconnect=False),
        )
    except Exception as _start_exc:
        _exc_str = str(_start_exc).lower()
        _key_tag = f"key=...{_selected_key[-6:]}"
        if "409" in _exc_str or "conflict" in _exc_str or "aborted" in _exc_str:
            _mark_key_409(_selected_key)
            _decr_key_inflight(_selected_key)
            _log.error(
                f"[GEMINI-ERR-409] concurrent session limit hit — {_key_tag} cooled {_KEY_COOLDOWN_SECS:.0f}s | {_start_exc}"
            )
        elif "429" in _exc_str or "resource_exhausted" in _exc_str or "quota" in _exc_str or "rate" in _exc_str:
            _log.error(f"[GEMINI-ERR-429] rate limit / quota exceeded — {_key_tag} | {_start_exc}")
        elif "401" in _exc_str or "unauthenticated" in _exc_str:
            _log.error(f"[GEMINI-ERR-401] invalid or missing API key — {_key_tag} | {_start_exc}")
        elif "403" in _exc_str or "permission_denied" in _exc_str or "permission denied" in _exc_str:
            _log.error(f"[GEMINI-ERR-403] key lacks permission / Live API not enabled — {_key_tag} | {_start_exc}")
        elif "404" in _exc_str or "not_found" in _exc_str or "not found" in _exc_str:
            _log.error(f"[GEMINI-ERR-404] model not found or deprecated — {_key_tag} | {_start_exc}")
        elif "400" in _exc_str or "invalid_argument" in _exc_str or "bad request" in _exc_str:
            _log.error(f"[GEMINI-ERR-400] bad request / invalid config — {_key_tag} | {_start_exc}")
        elif "503" in _exc_str or "unavailable" in _exc_str:
            _log.error(f"[GEMINI-ERR-503] service unavailable / overloaded — {_key_tag} | {_start_exc}")
        elif "504" in _exc_str or "deadline_exceeded" in _exc_str or "timed out" in _exc_str:
            _log.error(f"[GEMINI-ERR-504] connection timed out — {_key_tag} | {_start_exc}")
        elif "500" in _exc_str or "internal" in _exc_str:
            _log.error(f"[GEMINI-ERR-500] internal server error — {_key_tag} | {_start_exc}")
        elif "499" in _exc_str or "cancelled" in _exc_str:
            _log.error(f"[GEMINI-ERR-499] connection cancelled by client — {_key_tag} | {_start_exc}")
        else:
            _log.error(f"[GEMINI-ERR-UNKNOWN] session.start() failed — {_key_tag} | {_start_exc}")
        raise

    # Record call start immediately after session connects — before any lead
    # fetch awaits — so _save_and_close always computes a real duration.
    call_state["call_start_time"] = time.time()

    # Force Gemini to speak the greeting immediately on connect by sending a
    # LiveClientContent with a placeholder user turn and turn_complete=True.
    # This replicates what generate_reply() does internally, bypassing the
    # mutable_chat_context capability gate that blocks generate_reply() for
    # Gemini 3.1.  ActivityStart/ActivityEnd are ignored in automatic-AAD mode.
    _rt = getattr(session._activity, "_rt_session", None) if session._activity else None
    if _rt is not None:
        # Swap the framework's generation_created listener so we can tee each
        # message's text_stream.  Our sniffer branch detects partial closing phrases
        # from the streaming chunks — before the item is committed — and mutes the mic
        # while the LLM is still speaking, preventing VAD-triggered interruptions.
        _orig_gen_handler = getattr(session._activity, "_on_generation_created", None)
        if _orig_gen_handler is not None:
            def _gen_created_with_sniff(ev) -> None:
                orig_stream = ev.message_stream

                async def _wrapped_messages():
                    async for msg in orig_stream:
                        t = _aio_tee(msg.text_stream, 2)
                        msg.text_stream = t[0]   # framework consumes this branch
                        asyncio.create_task(_consume_sniff(t[1]))
                        yield msg

                ev.message_stream = _wrapped_messages()
                _orig_gen_handler(ev)

            _rt.off("generation_created", _orig_gen_handler)
            _rt.on("generation_created", _gen_created_with_sniff)

        async def _speak_via_gemini(phrase: str, *, reason: str) -> None:
            """Force Gemini to speak `phrase` verbatim. Mirrors _trigger_greeting's send pattern."""
            if _rt is None or getattr(_rt, "_active_session", None) is None:
                _log.warning(f"[FORCE-SPEAK] {reason}: no active rt session, skipping")
                return
            directive = (
                "[SYSTEM DIRECTIVE — do not echo this bracketed text]\n"
                "Speak the following line exactly, in the script and language already given, "
                "and say nothing else. Do not add commentary, do not translate, do not paraphrase:\n"
                f"{phrase}"
            )
            try:
                _rt._send_client_event(
                    types.LiveClientContent(
                        turns=[types.Content(parts=[types.Part(text=directive)], role="user")],
                        turn_complete=True,
                    )
                )
                _log.info(f"[FORCE-SPEAK] {reason}: sent directive")
            except Exception as e:
                _log.warning(f"[FORCE-SPEAK] {reason} failed: {e}")

        async def _trigger_greeting() -> None:
            nonlocal _greeting_done, _bot_has_spoken, _greeting_retry_triggered, _gemini_connect_failed
            for _ in range(50):  # wait up to 5 s for the Gemini websocket connection
                async with _rt._session_lock:
                    connected = _rt._active_session is not None
                if connected:
                    break
                await asyncio.sleep(0.1)
            else:
                _log.warning("[GREETING] Gemini did not connect within 5 s; skipping trigger")
                _gemini_connect_failed = True
                return
            await asyncio.sleep(0.2)  # let initial chat-history replay finish
            _rt._send_client_event(
                types.LiveClientContent(
                    turns=[types.Content(parts=[types.Part(text=".")], role="user")],
                    turn_complete=True,
                )
            )
            # Mute the mic NOW — Gemini has received the trigger and will generate
            # the greeting. We mute here (not at session.start) to avoid disrupting
            # the Gemini session during its initialization phase.
            # The mic is re-enabled by _on_agent_state when greeting finishes.
            if not _call_ended:
                _set_mic(False, reason="greeting-trigger")
                _log.info("[MIC] Muted after greeting trigger — awaiting greeting completion")

            # Fallback: if Gemini silently fails to produce the greeting (e.g. "no active
            # generation" race), _on_agent_state never fires → mic stays muted forever.
            # After 12 s with no greeting, retry once by resending the "." trigger.
            # If it still hasn't played after another 12 s, force-unmute as last resort.
            # 12 s (not 8 s) — the greeting audio itself takes ~8-9 s; 8 s was firing
            # ~900 ms before the greeting finished, causing false greeting_retry=True.
            await asyncio.sleep(12)
            if not _greeting_done and not _call_ended:
                _greeting_retry_triggered = True
                _log.warning("[GREETING] No greeting after 12 s — retrying trigger")
                try:
                    _rt._send_client_event(
                        types.LiveClientContent(
                            turns=[types.Content(parts=[types.Part(text=".")], role="user")],
                            turn_complete=True,
                        )
                    )
                    _log.info("[GREETING] Retry trigger sent")
                except Exception as e:
                    _log.warning(f"[GREETING] Retry trigger failed: {e}")
                await asyncio.sleep(12)
            if not _greeting_done and not _call_ended:
                _greeting_done = True
                _bot_has_spoken = True
                _set_mic(True, reason="greeting-timeout-force-unmute")
                _log.warning("[MIC] Greeting not complete after 24 s — force-enabling mic")

        asyncio.create_task(_trigger_greeting())

    # 11. Read SIP info from participant attributes
    participant = next(iter(ctx.room.remote_participants.values()), None)
    if participant:
        attrs = dict(participant.attributes or {})
        sip_info["caller_number"] = attrs.get("sip.phoneNumber") or ""
        sip_info["dialed_number"] = attrs.get("sip.trunkPhoneNumber") or ""
        if not sip_info["caller_number"]:
            ident = participant.identity or ""
            if ident.lower().startswith("sip_"):
                sip_info["caller_number"] = ident[4:]
        if not sip_info["caller_number"]:
            m = re.search(r'__(\d{10,12})_', room_name)
            if m:
                sip_info["caller_number"] = m.group(1)
        _log.info(
            f"[SIP] caller={sip_info['caller_number']!r} | dialed={sip_info['dialed_number']!r}"
        )

    _caller_identity: str = participant.identity if participant else ""

    # 12. Resolve lead for greeting (use pre-fetched, or build fallback)
    record = call_state.get("lead_record")
    caller_mobile = normalize_mobile(sip_info["caller_number"]) if sip_info["caller_number"] else _room_mobile

    if not record and caller_mobile:
        record = await fetch_lead(mobile=caller_mobile, mis_api_base=_mis_api_base)
        if record:
            call_state["record_id"] = record.get("_id") or record.get("ref_id")
            call_state["call_id"] = _room_meta_raw.get("call_id") or record.get("call_id") or room_name
            call_state["lead_record"] = record

    if not record:
        _srchterm = _room_meta_raw.get("srchterm", "")
        if _srchterm:
            record = await _build_sample_from_search(
                srchterm=_srchterm,
                buyer_name=_room_meta_raw.get("buyer_name", "Customer"),
                category_api=_category_change_api,
                lead_id=_room_meta_raw.get("lead_id", "") or "test_lead",
            )
            if record:
                call_state["record_id"] = "test_lead"
                call_state["call_id"] = _room_meta_raw.get("call_id") or room_name
                call_state["lead_record"] = record

    if not record:
        _buyer_name_fallback = _room_meta_raw.get("buyer_name", "Customer")
        record = {
            "_id": f"fallback_{caller_mobile or 'unknown'}",
            "call_id": _room_meta_raw.get("call_id") or room_name,
            "buyer_details": {
                "buyer_name": _buyer_name_fallback,
                "buyer_number": caller_mobile or "",
                "buyer_city": _room_meta_raw.get("city", ""),
                "is_business": 0,
            },
            "search_context": {
                "searched_keyword": _room_meta_raw.get("srchterm", ""),
                "searched_product": {
                    "product_name": _room_meta_raw.get("srchterm", ""),
                    "product_id": "", "attributes": {},
                },
            },
            "catname": _room_meta_raw.get("catname", ""),
            "qualification_schema": {},
        }
        call_state["record_id"] = record["_id"]
        call_state["call_id"] = record["call_id"]
        call_state["lead_record"] = record
        _log.info(f"[CALL SETUP] Using fallback lead for mobile={caller_mobile!r}")

    # 16. 5-minute hard call timeout
    _DEFAULT_TIMEOUT_MSG = (
        _lang_cfg.get("timeout_message")
        or "जी, मुझे सिर्फ 5 मिनट तक बात करने की permission है. जो भी details मिली हैं, sellers जल्द ही आपसे contact करेंगे. आपका समय देने के लिए धन्यवाद. अलविदा!"
    )
    async def _call_timeout() -> None:
        await asyncio.sleep(_max_call_duration)
        if call_state.get("ended_naturally") or call_state.get("save_done"):
            return
        _log.info(f"[TIMEOUT] {_max_call_duration}s limit reached — ending call")
        _pc = (_bot_config.get("prompt_config") or {}).get("timeout_message", "").strip()
        timeout_msg = _pc or _DEFAULT_TIMEOUT_MSG
        call_state["ended_naturally"] = True
        _cancel_inactivity()
        await _speak_via_gemini(timeout_msg, reason="timeout")
        try:
            for _ in range(40):   # up to 4 s for Gemini to start speaking
                if getattr(getattr(session, "agent_state", None), "value", "") == "speaking":
                    break
                await asyncio.sleep(0.1)
            for _ in range(150):  # up to 15 s for speech to finish
                if getattr(getattr(session, "agent_state", None), "value", "") != "speaking":
                    break
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.8)
        except Exception:
            await asyncio.sleep(4)
        await _kick_caller_safe()
        asyncio.create_task(_save_and_close("completed"))

    call_state["_timeout_task"] = asyncio.create_task(_call_timeout())

    # 17. Start inactivity timer (first nudge after greeting)
    _reset_inactivity()

    # 18. Participant disconnect handler
    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p: rtc.RemoteParticipant) -> None:
        nonlocal _call_ended, _buffer_frozen
        _cancel_inactivity()
        if call_state["ended_naturally"]:
            return
        _buffer_frozen = True  # stop buffering immediately so post-hangup noise stays out of WAV
        _call_ended = True
        # Force Gemini to finalise any in-flight user turn immediately.
        # Without this, Gemini waits for silence_duration_ms of silence to
        # detect end-of-speech — but the audio stream is already gone.
        # A non-empty turns payload is required: empty turns=[] does NOT trigger
        # a Gemini generation cycle, so server_content.input_transcription never
        # fires and the user's last utterance is silently discarded.
        # Using a dummy user turn (same pattern as _trigger_greeting) forces Gemini
        # into a generation cycle, which emits server_content.input_transcription
        # → _mark_current_generation_done → input_audio_transcription_completed
        # (is_final=True) with the user's buffered speech, before save_call_data runs.
        #
        # GUARD: only flush if the mic was actually live at some point after greeting.
        # During the greeting window the mic is muted — Gemini has no buffered user
        # audio to flush. Sending "." without this guard causes Gemini to generate a
        # spurious response that lands in the transcript and corrupts analysis.
        if _rt is not None and getattr(_rt, "_active_session", None) is not None and _greeting_done:
            try:
                _rt._send_client_event(
                    types.LiveClientContent(
                        turns=[types.Content(parts=[types.Part(text=".")], role="user")],
                        turn_complete=True,
                    )
                )
            except Exception:
                pass
        asyncio.create_task(_save_and_close("disconnected"))

    # Keep entrypoint alive until save_call_data + callback finish.
    # Prevents the event loop from shutting down before the HTTP POST
    # when the caller hangs up or we kick them after the closing phrase.
    try:
        await asyncio.wait_for(_save_done_event.wait(), timeout=_max_call_duration + 120)
    except asyncio.TimeoutError:
        _log.warning("[SAVE_CALL] Timed out waiting for save to complete — process will exit")


# ---------------------------------------------------------------------------
# Worker entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="voice-bot-voicedesk-gemini-live-unused",
            num_idle_processes=3,
        )
    )
