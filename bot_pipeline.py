#!/usr/bin/env python3
"""
Pipeline (STT → LLM → TTS) version of the Justdial voice bot.

Unlike bot.py (which uses Gemini Live s2s / RealtimeModel), this file runs a
classic three-stage pipeline:
  STT:  sarvam.STT (saaras:v3 transcribe, flush_signal)     [needs SARVAM_API_KEY]
  LLM:  google.LLM (Gemini 3.1 Flash Lite)                 [needs GEMINI_API_KEY]
  TTS:  sarvam.TTS (bulbul:v3, simran, mp3/22050)           [needs SARVAM_API_KEY]

All genuine business logic (config fetch, prompt building, transcript save, IVR
detection, closing-phrase detection, function tools, muted-window audio capture,
4s barge-in timing, echo/filler guards) is preserved and mostly imported from bot.py.

Only the realtime I/O layer changes:
  • RealtimeModel                            →  AgentSession(stt, llm, tts, turn_detection="stt")
  • _rt._send_client_event(LiveClientContent) →  session.say() / session.generate_reply()
  • Greeting trigger (24s retry dance)       →  session.say(greeting_text) [mute kept]
  • _speak_via_gemini(phrase)               →  session.say(phrase)

Required env vars:
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
  GEMINI_API_KEY   — Gemini API key for the LLM (gemini-3.1-flash-lite)
  SARVAM_API_KEY   — Sarvam API key (STT saaras:v3, TTS bulbul:v3, muted-window batch STT)
  BACKEND_URL, MONGO_URI          — call-log save (same as bot.py)

Run:  python bot_pipeline.py start
"""

import asyncio
import io
import json
import os
import re
import sys
import time
import unicodedata
import wave
from datetime import datetime, timezone

from dotenv import load_dotenv
from loguru import logger

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AudioConfig,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.voice.room_io import RoomOptions as _RoomOptionsCls
from livekit.api import DeleteRoomRequest, LiveKitAPI

try:
    from livekit.api import RoomParticipantIdentity as _RemoveParticipantRequest
except ImportError:
    _RemoveParticipantRequest = None

import aiohttp

from livekit.plugins import google, sarvam

# ---------------------------------------------------------------------------
# Import all reusable module-level helpers from bot.py
# (these are top-level in bot.py and only touch module globals — safe)
# ---------------------------------------------------------------------------
from bot import (
    # Config / data helpers
    fetch_bot_config,
    normalize_mobile,
    fetch_lead,
    _build_sample_from_search,
    _execute_function_call,
    call_configured_function,
    save_call_log_to_backend,
    build_system_prompt,
    build_transcript_from_session,
    _dedup_words,
    # Detections
    _is_closing_phrase,
    _is_not_interested_close,
    _is_abusive_text,
    _silero_voiced_ms,
    # Constants
    _HARDCODED_BOT_CONFIG,
    MIS_API_BASE,
    CATEGORY_CHANGE_API,
    BACKEND_URL,
    MONGO_URI,
    MONGO_DB,
    MONGO_COLLECTION,
    IST,
    HINDI_LANG_CONFIG,
    INACTIVITY_PHRASE,
    INACTIVITY_END_PHRASE,
    _get_mongo_collection,
)

load_dotenv(override=True)

# Dev API (mirrors bot_dev.py) — overrides the live URLs imported from bot.py
MIS_API_BASE = "http://192.168.14.101:3006"
CATEGORY_CHANGE_API = f"{MIS_API_BASE}/leads/ai-lead-qualify/search"

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"

# ---------------------------------------------------------------------------
# Langfuse — optional observability (no-ops cleanly if creds are absent)
# ---------------------------------------------------------------------------
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

_langfuse_client = None
if LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY:
    try:
        from langfuse import Langfuse as _Langfuse
        from langfuse.types import TraceContext as _LFTraceContext

        _langfuse_client = _Langfuse(
            secret_key=LANGFUSE_SECRET_KEY,
            public_key=LANGFUSE_PUBLIC_KEY,
            host=LANGFUSE_BASE_URL,
        )

        class _LFTraceShim:
            """Thin v2-compat wrapper over Langfuse v3/v4 start_observation API.

            v2: langfuse.trace() → returns object with .span()/.update()
            v4: langfuse.start_observation(as_type="trace") + TraceContext for children
            """

            def __init__(self, obs, client):
                self._obs = obs
                self._client = client
                self._ctx = _LFTraceContext(
                    trace_id=obs.trace_id,
                    parent_span_id=obs.id,
                )

            def span(self, *, name, input=None, output=None, metadata=None, **kw):
                return self._client.start_observation(
                    name=name,
                    as_type="span",
                    trace_context=self._ctx,
                    input=input,
                    output=output,
                    metadata=metadata,
                )

            def update(self, *, output=None, metadata=None, **kw):
                self._obs.update(output=output, metadata=metadata)

            def end(self):
                self._obs.end()

    except Exception as _lf_err:
        logger.warning(f"[LANGFUSE] init failed — traces disabled: {_lf_err}")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
_BOT_PORT = os.environ.get("BOT_PIPELINE_PORT", os.environ.get("BOT_PORT", "8082"))
_LOG_DIR = os.path.join(
    os.environ.get("BOT_LOG_DIR", "/home/yogeshv_10011835/voicebot_nodcode_platform/logs/"),
    f"{_BOT_PORT}_pipeline",
)
os.makedirs(_LOG_DIR, exist_ok=True)


def _log_format(record: dict) -> str:
    caller = record["extra"].get("caller", "")
    caller_col = f"{caller:<15} | " if caller else (" " * 17)
    return "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | " + caller_col + "{message}\n"


# File handler — everything, rotated daily
logger.add(
    os.path.join(_LOG_DIR, "{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    compression="gz",
    level="INFO",
    enqueue=True,
    format=_log_format,
)

# Console handler — only the signals that matter for live monitoring
_CONSOLE_KEYWORDS = (
    "[TRANSCRIPT]", "[MUTED-CAPTURE]", "[LATENCY]",
    "[CALL START]", "[CALL END]", "[GREETING]",
    "[IVR]", "[CLOSE DETECT]", "[INACTIVITY]",
)


def _console_filter(record: dict) -> bool:
    if record["level"].no >= 30:   # WARNING / ERROR / CRITICAL always shown
        return True
    return any(kw in record["message"] for kw in _CONSOLE_KEYWORDS)


logger.remove(0)   # remove loguru's default stderr handler
logger.add(
    sys.stderr,
    level="INFO",
    filter=_console_filter,
    format=_log_format,
    colorize=False,
)


# ---------------------------------------------------------------------------
# Prewarm: nothing to preload — Sarvam STT handles turn detection internally
# ---------------------------------------------------------------------------
def prewarm_fnc(proc) -> None:
    pass


# ---------------------------------------------------------------------------
# Cached TTS hold-message frames (pre-synthesized once per process)
# ---------------------------------------------------------------------------
_HOLD_FRAMES: list[rtc.AudioFrame] = []

async def _preload_hold_message(tts_instance) -> None:
    """Synthesize the hold phrase once and cache frames for all tool calls."""
    global _HOLD_FRAMES
    if _HOLD_FRAMES:
        return
    try:
        frames: list[rtc.AudioFrame] = []
        async for event in tts_instance.synthesize("एक क्षण रुकिए, मैं अभी चेक करती हूँ।"):
            frames.append(event.frame)
        _HOLD_FRAMES = frames
        logger.info(f"[HOLD-TTS] Preloaded {len(_HOLD_FRAMES)} frames for tool hold message")
    except Exception as e:
        logger.warning(f"[HOLD-TTS] Preload failed (will skip hold messages): {e}")


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------
async def entrypoint(ctx: JobContext):  # noqa: C901
    room_name = ctx.job.room.name
    try:
        _room_meta_raw = json.loads(ctx.job.room.metadata or "{}")
    except (json.JSONDecodeError, TypeError):
        _room_meta_raw = {}

    # ── 1. Kick off lead pre-fetch immediately (runs while connect + wait happen) ──
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

    # ── 2. Resolve bot config and settings ──
    _assistant_id = _room_meta_raw.get("assistant_id", "")
    _bc = await fetch_bot_config(_assistant_id) if _assistant_id else None
    _bot_config: dict = _bc or _HARDCODED_BOT_CONFIG

    _prefetched_lead = await _early_lead_task if _early_lead_task is not None else None

    _api_urls = _bot_config.get("api_urls") or {}
    _mis_api_base = _api_urls.get("mis_api_base") or MIS_API_BASE
    _category_change_api = _api_urls.get("category_change_api") or CATEGORY_CHANGE_API
    _language = "hindi"
    _temperature = float(_bot_config.get("temperature") or 0.4)
    _max_call_duration = 300
    # Pipeline mode: higher threshold than bot.py defaults — filters TTS echo
    # and background IVR music that would otherwise pass the muted-capture gate.
    _silero_threshold = float(_bot_config.get("silero_threshold") or 0.6)
    _silero_min_speech_ms = int(_bot_config.get("silero_min_speech_ms") or 1000)
    _post_speech_hold_ms = int(_bot_config.get("post_speech_hold_ms") or 400)
    _inactivity_first_rescue_secs = float(_bot_config.get("inactivity_first_rescue_secs") or 4.0)
    _inactivity_first_nudge_gap_secs = float(_bot_config.get("inactivity_first_nudge_gap_secs") or 4.0)
    _inactivity_nudge_secs = float(_bot_config.get("inactivity_nudge_secs") or 10.0)
    _inactivity_close_secs = float(_bot_config.get("inactivity_close_secs") or 5.0)
    _functions: list[dict] = _bot_config.get("functions") or []
    _function_calling = bool(_bot_config.get("function_calling", False)) and bool(_functions)
    _lang_cfg = HINDI_LANG_CONFIG

    # ── 3. Per-call state ──
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
    # _caller_identity is set at section 11 (after session.start) but must exist
    # in this scope now so _kick_caller_safe() closure can read it at call time.
    _caller_identity = ""

    # ── Langfuse trace (no-op if client is None) ──
    _lf_trace = None
    if _langfuse_client:
        try:
            _lf_obs = _langfuse_client.start_observation(
                name="voice-call",
                as_type="trace",
                metadata={
                    "room": room_name,
                    "mobile": _room_mobile,
                    "assistant_id": _assistant_id,
                    "call_id": call_state["call_id"],
                    "model_llm": "gemini-3.1-flash-lite",
                    "model_stt": "saaras:v3-codemix",
                    "model_tts": "bulbul:v3-simran",
                },
            )
            _lf_trace = _LFTraceShim(_lf_obs, _langfuse_client)
        except Exception as _lf_ex:
            _log.warning(f"[LANGFUSE] trace creation failed: {_lf_ex}")

    if _prefetched_lead:
        call_state["record_id"] = _prefetched_lead.get("_id") or _prefetched_lead.get("ref_id")
        call_state["call_id"] = (
            _room_meta_raw.get("call_id", "")
            or _prefetched_lead.get("call_id", "")
            or room_name
        )
        call_state["lead_record"] = _prefetched_lead

    # ── 4. Build system instruction from lead (or base rules if no lead yet) ──
    system_instruction = build_system_prompt(
        _prefetched_lead, lang_key=_language, bot_config=_bot_config
    )

    # Log the exact qualification questions the backend returned for this lead, so
    # the set the bot is supposed to ask is visible in the call logs. Source: the
    # FetchLead API response → results.search_result.question array, stored on the
    # lead record as qualification_schema["question"]. NOTE: the bot also asks
    # hardcoded steps on top of these (product confirmation + business name + city
    # via the BUSINESS GATE), so total asks > backend question count by design.
    try:
        _qs = ((_prefetched_lead or {}).get("qualification_schema") or {}).get("question") or []
        _q_texts = [q.get("text", "").strip() for q in _qs]
        _log.info(
            f"[QUESTIONS] backend returned {len(_qs)} qualification question(s) "
            f"for catname={(_prefetched_lead or {}).get('catname')!r}: {_q_texts}"
        )
    except Exception as _q_ex:
        _log.warning(f"[QUESTIONS] could not read qualification_schema: {_q_ex}")

    # ── 5. Pipeline plugins: Sarvam STT → Gemini LLM → Sarvam TTS ──
    # STT: saaras:v3 transcribe with flush_signal — Sarvam handles turn detection
    # TTS: bulbul:v3 simran — mp3/22050 (official guide defaults)
    # LLM: gemini-3.1-flash-lite
    _gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    _log.info(f"[LLM] Using GEMINI_API_KEY ...{_gemini_api_key[-6:] if _gemini_api_key else 'NOT SET'}")

    stt = sarvam.STT(
        language="hi-IN",
        model="saaras:v3",
        mode="transcribe",
        api_key=SARVAM_API_KEY or None,
        flush_signal=True,
    )
    llm = google.LLM(
        model="gemini-3.1-flash-lite",
        api_key=_gemini_api_key or None,
        temperature=_temperature,
    )
    tts = sarvam.TTS(
        target_language_code="hi-IN",
        model="bulbul:v3",
        speaker="simran",
        api_key=SARVAM_API_KEY or None,
        # linear16 at 22050Hz (Sarvam native rate, no internal downsampling).
        # No MP3 frame-boundary artifacts in streaming mode. LiveKit handles
        # the 22050→48000→8000 SIP chain with its own high-quality resampler.
        speech_sample_rate=22050,
        output_audio_codec="linear16",
        temperature=0.75,
        pace=1.0,
        send_completion_event=True,
    )

    # Pre-synthesize hold message (no-op if already cached from a previous call)
    await _preload_hold_message(tts)

    # ── 6. Inner helpers ──

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
        """Remove only the SIP participant — ends the call for the user without deleting the room."""
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

        _t = call_state.get("_timeout_task")
        if _t and not _t.done():
            _t.cancel()

        lead_id = call_state.get("record_id")
        _log.info(
            f"[SAVE_CALL] save_call_data called | status={status!r} | "
            f"record_id={lead_id!r} | call_id={call_state.get('call_id')!r} | "
            f"lead_record_present={bool(call_state.get('lead_record'))}"
        )
        # Discard un-combined muted-window text
        _muted_inject["text"] = ""
        # Flush any partial user turn that never received a final transcription
        if _pending_user_text and (
            not _live_transcript or _live_transcript[-1].get("text") != _pending_user_text
        ):
            _live_transcript.append({"role": "user", "text": _pending_user_text})
        # In pipeline mode _pending_assistant_text is always "" (no stream sniffer),
        # but keep the flush guard for safety.
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
            # Pipeline-mode metadata (no Gemini Live quirks)
            "greeting_retry": False,
            "gemini_connect_failed": False,
            "greeting_done": _greeting_done,
            "user_speech_ms": round(_muted_capture.get("speech_ms", 0.0)),
            "wrong_opener_detected": False,  # deterministic TTS greeting, never wrong
            "turn_count": _turn_counter,
            "avg_response_latency_ms": (
                round(sum(_response_latencies) / len(_response_latencies))
                if _response_latencies else 0
            ),
            "response_latencies_ms": _response_latencies,
            "tagged": False,
            "tagged_at": None,
            "created_at": datetime.now(timezone.utc),
        }
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _get_mongo_collection().insert_one(_mongo_doc))
            _log.info(
                f"[MONGO] Transcript saved | lead_id={lead_id!r} | call_id={call_state.get('call_id')!r}"
            )
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

        _avg_latency_ms = (
            round(sum(_response_latencies) / len(_response_latencies))
            if _response_latencies else 0
        )

        # Finalise Langfuse trace
        if _lf_trace:
            try:
                _lf_trace.update(
                    output={
                        "status": status,
                        "turn_count": _turn_counter,
                        "duration_sec": _duration,
                        "avg_response_latency_ms": _avg_latency_ms,
                        "response_latencies_ms": _response_latencies,
                        "ended_naturally": call_state.get("ended_naturally"),
                    },
                    metadata={
                        "lead_id": str(lead_id or ""),
                        "call_id": call_state.get("call_id", ""),
                        "mobile": _room_mobile,
                    },
                )
                _lf_trace.end()
                _langfuse_client.flush()
            except Exception as _lf_ex:
                _log.warning(f"[LANGFUSE] flush failed: {_lf_ex}")

        _log.info(_SEP)
        _log.info(
            f"[CALL END] room={room_name} | status={status!r} | "
            f"duration={_duration}s | lead_id={lead_id!r} | "
            f"avg_latency={_avg_latency_ms}ms over {len(_response_latencies)} turn(s)"
        )
        _log.info(_SEP)

    _save_done_event = asyncio.Event()

    async def _save_and_close(status: str) -> None:
        _was_done = call_state["save_done"]
        # Pipeline STT finalizes user turns quickly — no Gemini flush delay needed.
        # Keep a 1 s buffer on disconnect so any in-flight STT event lands.
        if status == "disconnected":
            await asyncio.sleep(1.0)
        await save_call_data(status)
        asyncio.create_task(_delete_room_safe())
        try:
            await _bg_audio.aclose()
        except Exception:
            pass
        try:
            await session.aclose()
        except Exception:
            pass
        if not _was_done:
            _save_done_event.set()

    # ── Inactivity tracking ──
    _nudge_count = 0
    _nudge_in_progress = False
    _inactivity_task: asyncio.Task | None = None
    _call_ended = False

    async def _inactivity_timeout() -> None:
        nonlocal _nudge_count, _inactivity_task, _nudge_in_progress
        sleep_secs = _inactivity_close_secs if _nudge_count >= 2 else _inactivity_nudge_secs

        if _nudge_count == 0 and _greeting_done and _turn_counter == 0:
            await asyncio.sleep(_inactivity_first_rescue_secs)
            if _call_ended or _closing_triggered:
                return
            if _turn_counter > 0 or _pending_user_text:
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            # Check if a muted-window transcription landed (from post-greeting STT)
            if _muted_inject.get("text"):
                _log.info(
                    f"[INACTIVITY] early check — muted-capture text buffered, letting inject flow"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            await asyncio.sleep(_inactivity_first_nudge_gap_secs)
        else:
            await asyncio.sleep(sleep_secs)

        _nudge_count += 1
        if _call_ended:
            return
        if not _greeting_done:
            _nudge_count = 0
            _inactivity_task = asyncio.create_task(_inactivity_timeout())
            return
        if _nudge_count >= 3:
            _nudge_count = 0
            _inactivity_task = None
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
            end_phrase = INACTIVITY_END_PHRASE
            try:
                # session.say returns a SpeechHandle; await it to wait for full playout
                await session.say(end_phrase, allow_interruptions=False)
                await asyncio.sleep(0.5)
            except Exception:
                await asyncio.sleep(4)
            await _kick_caller_safe()
            _inactivity_status = "completed" if _turn_counter > 0 else "disconnected"
            asyncio.create_task(_save_and_close(_inactivity_status))
        else:
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
            _now2 = asyncio.get_event_loop().time()
            if _muted_inject.get("text") or (
                _muted_inject_sent_time > 0 and (_now2 - _muted_inject_sent_time) < 8.0
            ):
                _log.info("[INACTIVITY] nudge suppressed — muted-capture inject in flight/recent")
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            nudge = INACTIVITY_PHRASE
            _log.info(f"[INACTIVITY] {sleep_secs:.0f}s silence — nudge {_nudge_count}: {nudge!r}")
            _nudge_in_progress = True
            # Fire-and-forget: agent_state "speaking" → "listening" will call _reset_inactivity()
            try:
                session.say(nudge, allow_interruptions=True)
            except Exception as e:
                _log.warning(f"[INACTIVITY] nudge say() failed: {e}")

    def _reset_inactivity(from_user_speech: bool = False) -> None:
        nonlocal _nudge_count, _inactivity_task, _nudge_in_progress
        if _call_ended:
            return
        if from_user_speech:
            _nudge_count = 0
            _nudge_in_progress = False
        elif not _nudge_in_progress:
            _nudge_count = 0
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

    # ── Closing-phrase + transcript state ──
    _closing_buffer = ""
    _closing_triggered = False
    _early_close_muting = False
    _echo_guard_task: asyncio.Task | None = None
    _speaking_unmute_task: asyncio.Task | None = None

    _turn_counter = 0
    _live_transcript: list = []
    _pending_user_text: str = ""
    _pending_assistant_text: str = ""  # kept for safety (always "" in pipeline mode)

    _current_window_pcm: bytearray = bytearray()
    _silero_rejected_turns: set = set()
    _muted_capture: dict = {"frames": [], "speech_ms": 0.0}
    _muted_inject: dict = {"text": ""}
    _muted_inject_sent_time: float = 0.0
    _muted_capture_empty_time: float = 0.0
    _muted_filler_dropped_time: float = 0.0
    # Set when a FINAL is rejected as noise (Silero/calibration/short-noise/
    # bystander/babble). LiveKit's turn_detection="stt" still auto-fires an LLM
    # reply off the rejected FINAL; this timestamp lets the speaking-start guard
    # kill that spurious turn. Reset on every legitimate reply path so only the
    # implicit noise-driven auto-reply reaches the guard with it set.
    _silero_rejected_time: float = 0.0
    # Consecutive Silero rejections (reset whenever a turn is accepted). If Silero
    # starts rejecting genuine speech in a sustained streak (degraded audio path /
    # model failure), the guard must stop interrupting — otherwise it mutes the
    # whole call. Real conversations don't produce long runs of pure noise turns.
    _consecutive_silero_rejects: int = 0
    _muted_transcript_log: list = []
    _close_status = "completed"
    _abusive_detected = False

    async def _handle_close() -> None:
        nonlocal _call_ended
        _call_ended = True
        _cancel_inactivity()
        await asyncio.sleep(2)
        await _kick_caller_safe()
        asyncio.create_task(_save_and_close(_close_status))

    # ── IVR / voicemail detection (not importable from bot.py — defined inside entrypoint there) ──
    _IVR_BUSY_MARKERS = [
        # Hindi
        "इस समय व्यस्त", "बाद में call", "बाद में कॉल", "नंबर अभी busy",
        "व्यस्त हैं", "available नहीं", "कृपया थोड़ी देर बाद",
        "स्विच ऑफ", "switch off", "switched off",
        # English
        "currently busy", "please try later", "not reachable",
        "number you have dialed", "out of coverage",
        "call cannot be completed", "is not available",
        "thank you for calling", "press 1", "press 2",
        "for english press", "our working hours",
        "please stay on the line", "stay on the line",
        "leave a message", "leave your message", "after the tone",
        "after the beep", "not available right now",
        "please leave", "record your message",
        "you have reached", "we will connect", "all lines are busy",
        # Devanagari transliterations
        "प्लीज ट्राई", "करेंटली बिजी", "इज नॉट अवेलेबल",
        "प्लीज रिप्लाई", "प्लीज लीव", "लीव ए मैसेज",
        "आफ्टर द टोन", "नॉट अवेलेबल", "नॉट रीचेबल",
        "रीजन फॉर कॉलिंग", "रीज़न फॉर कॉलिंग",
        "पर्सन इज अवेलेबल", "पर्सन यू आर ट्राइंग",
        "स्टे ऑन द लाइन", "रिकॉर्ड योर मैसेज",
        "पिक अप द कॉल", "लीव योर मैसेज",
        "फिनिशड योर", "ट्राइंग टू रीच", "ट्राइंग टू रीडायरेक्ट",
        "फोन आई है",
        # Gujarati
        "व्यस्त छे", "थोड़ा क्षणों",
    ]

    def _is_ivr_message(text: str) -> bool:
        n = unicodedata.normalize("NFC", text).lower()
        return any(m.lower() in n for m in _IVR_BUSY_MARKERS)

    # Filler hallucinations for muted-capture filtering
    _STT_FILLER_TOKENS = {
        unicodedata.normalize("NFC", w) for w in {
            "a", "e", "o", "i",
            "hmm", "hm", "हम्म",
        }
    }

    # Calibration hallucinations (STT calibration output from near-silence)
    _GEMINI_CALIBRATION_HALLUCINATIONS: set[str] = {
        unicodedata.normalize("NFC", p) for p in {
            "ए बी सी", "वन टू थ्री फोर", "वन टू थ्री", "वन टू",
            "a b c", "one two three four", "one two three",
        }
    }

    # Short noise tokens (single-token transcripts from sub-350ms ambient sounds)
    _GEMINI_SHORT_NOISE_TOKENS: set[str] = {
        unicodedata.normalize("NFC", w) for w in {
            "हूं", "हूँ", "ऊं", "उम",
            "uh", "um", "ugh",
            "पाठ",
        }
    }

    # Bot-echo markers: Sarvam capturing our own TTS via speakerphone echo
    _BOT_ECHO_MARKERS = [
        "सिमरन बोल रही",
        "simran bol",
    ]

    def _is_bot_echo(text: str) -> bool:
        n = unicodedata.normalize("NFC", text).lower()
        return any(m in n for m in _BOT_ECHO_MARKERS)

    _BYSTANDER_SPEECH_MARKERS = [
        "यह लोग", "ये लोग", "इन लोगों", "यह लोगों",
        "these people", "this people",
        "पीछे से",
    ]
    _BYSTANDER_HYPHEN_REPEAT_RE = re.compile(r'(\S+)-\1(?:-\1)+')
    # Space-separated repetition: catches STT babble like "पास पास पास पास पास पास"
    # (same word repeated 4+ times with spaces — not covered by the hyphen regex)
    _SPACE_REPEAT_RE = re.compile(r'(?:^|\s)(\S{2,})(?:\s+\1){3,}(?=\s|$)', re.UNICODE)

    def _is_bystander_speech(text: str) -> bool:
        normalized = unicodedata.normalize("NFC", text)
        if any(marker in normalized for marker in _BYSTANDER_SPEECH_MARKERS):
            return True
        if _BYSTANDER_HYPHEN_REPEAT_RE.search(normalized):
            return True
        return False

    def _is_repetitive_babble(text: str) -> bool:
        """Return True when STT produces 4+ consecutive repetitions of the same word."""
        normalized = unicodedata.normalize("NFC", text)
        return bool(_SPACE_REPEAT_RE.search(normalized))

    def _normalize_stt_tokens(text: str) -> list[str]:
        text = unicodedata.normalize("NFC", text).lower()
        cleaned = "".join(
            c for c in text
            if unicodedata.category(c)[0] in ("L", "N", "M") or c.isspace()
        )
        return [t for t in cleaned.split() if t]

    # Seller/manufacturer guard tokens (for FetchCategorySchema)
    _SELLER_MANUFACTURE_TOKENS = {
        "banate", "banaate", "banata", "banaata", "banati", "banaati",
        "बनाते", "बनाता", "बनाती", "बनाते हैं", "बनाता हूँ",
        "bechte", "bechta", "बेचते", "बेचता",
    }

    # Short terminal tokens: PARTIAL == FINAL 100% of the time for these
    _SHORT_TERMINAL_TOKENS: frozenset = frozenset(unicodedata.normalize("NFC", w) for w in {
        "जी", "हाँ", "हां", "हा", "ना", "नहीं", "नहि",
        "yes", "no", "ok", "okay", "हाँजी", "हांजी",
        "ठीक", "बिल्कुल", "सही", "sure", "bilkul",
    })

    # Impatience-signal tokens: user says these while bot is "thinking".
    # Passing them through causes livekit-agents to cancel the LLM generation
    # on every occurrence — creating a thinking→listening loop.
    _THINKING_FILLER_TOKENS: frozenset = frozenset(unicodedata.normalize("NFC", w) for w in {
        "हेलो", "हैलो", "हेल्लो", "hello", "hi", "हाय",
        "हाँ", "हां", "हा", "जी", "ok", "okay", "ओके",
        "हाँजी", "हांजी", "हेलो।", "hello।",
    })

    # ── Speaking / watchdog state ──
    _greeting_done = False
    _bot_has_spoken = False
    _mic_enabled: bool = False
    _barge_in_fired: bool = False
    _bot_resp_watchdog_task: asyncio.Task | None = None
    _kb_auto_stop_task: asyncio.Task | None = None
    _last_user_final_text: str = ""
    _last_user_final_turn: int = 0
    _speaking_start_time: float = 0.0
    _speaking_turns_completed: int = 0
    _early_inject_done: bool = False
    _stale_partial_task: asyncio.Task | None = None
    _last_user_turn_time: float = 0.0
    _final_arrived_while_speaking: bool = False

    # ── Latency tracking ──
    _last_user_final_time: float = 0.0   # user FINAL → bot speaking start (E2E)
    _first_partial_time: float = 0.0     # first PARTIAL → FINAL (STT latency)
    _thinking_start_time: float = 0.0    # thinking state start → speaking (LLM+TTS TTFB)
    _response_latencies: list = []        # E2E ms per completed turn

    # ── _buffer_user_audio: captures caller audio for muted-window STT + Silero gating ──
    # The pipeline STT plugin sees only frames when mic is ON (session.input.set_audio_enabled).
    # This coroutine subscribes directly to the raw track so we can capture frames while the
    # mic is muted (bot speaking) and feed them to batch Google STT after the bot turn ends.
    async def _buffer_user_audio(track: rtc.RemoteAudioTrack) -> None:
        nonlocal _current_window_pcm
        _MAX_WINDOW = 160_000  # ~5 s of 16-bit 16 kHz mono — enough for Silero gate
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        async for ev in stream:
            if _call_ended:
                break
            chunk = bytes(ev.frame.data)
            frame_ms = len(chunk) / 2 / 16_000 * 1000
            if not _mic_enabled:
                # Muted window: accumulate for Google STT batch transcription
                _muted_capture["frames"].append(chunk)
                _muted_capture["speech_ms"] += frame_ms
            else:
                # Live window: rolling last ~5 s for the Silero gate on FINAL STT
                # events. Must be a ROLLING window (keep the most recent audio),
                # not fill-then-drop: the mic unmutes ~4 s into a ~10 s bot turn
                # (4s-speaking-unmute for barge-in), so bot-echo/line-silence
                # accumulates first. A fixed cap that dropped new audio filled the
                # window with that echo and starved Silero of the user's actual
                # speech (voiced_ms=0 → false rejection). Trimming the front keeps
                # the user's words, which are always the most recent samples.
                _current_window_pcm += chunk
                _overflow = len(_current_window_pcm) - _MAX_WINDOW
                if _overflow > 0:
                    del _current_window_pcm[:_overflow]

    async def _transcribe_muted_period(frames: list, speech_ms: float) -> None:
        """Transcribe audio captured during a muted window using Sarvam batch STT.

        Frames were collected by _buffer_user_audio while session.input was disabled.
        We wrap them in a WAV and POST to the Sarvam HTTP STT endpoint (saaras:v3 codemix),
        then inject the result via generate_reply().
        """
        nonlocal _call_ended, _muted_capture_empty_time, _muted_filler_dropped_time
        if not frames:
            return
        if not SARVAM_API_KEY:
            _log.warning("[MUTED-CAPTURE] SARVAM_API_KEY not set — skipping transcription")
            return
        # Build WAV in memory
        buf = io.BytesIO()
        try:
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
        text: str | None = None
        try:
            form = aiohttp.FormData()
            form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
            form.add_field("language_code", "hi-IN")
            form.add_field("model", "saaras:v3")
            form.add_field("mode", "codemix")
            async with aiohttp.ClientSession() as _http:
                async with _http.post(
                    SARVAM_STT_URL,
                    headers={"api-subscription-key": SARVAM_API_KEY},
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        text = (result.get("transcript") or "").strip() or None
                    else:
                        body = await resp.text()
                        _log.warning(
                            f"[MUTED-CAPTURE] Sarvam STT {resp.status}: {body[:200]}"
                        )
        except Exception as e:
            _log.warning(f"[MUTED-CAPTURE] Sarvam STT error: {e}")
            _muted_capture_empty_time = asyncio.get_event_loop().time()
            return

        if not text:
            _log.info(f"[MUTED-CAPTURE] empty transcript [sarvam=empty] (speech_ms={speech_ms:.0f})")
            _muted_capture_empty_time = asyncio.get_event_loop().time()
            return
        # IVR check on muted-capture (greeting window captures voicemail)
        if _is_ivr_message(text):
            _log.info(f"[IVR] busy-line in muted-capture — ending call: {text!r}")
            _live_transcript.append({"role": "ivr", "text": text})
            if not _call_ended:
                _call_ended = True
                call_state["ended_naturally"] = True
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("ivr_detected"))
            return
        if _is_bot_echo(text):
            _log.info(f"[MUTED-CAPTURE] bot-echo discarded: {text!r}")
            return
        tokens = _normalize_stt_tokens(text)
        if tokens and all(t in _STT_FILLER_TOKENS for t in tokens):
            _log.info(f"[MUTED-CAPTURE] dropped all-filler {text!r} [sarvam=filler]")
            _muted_filler_dropped_time = asyncio.get_event_loop().time()
            return
        # Single-token repeat (e.g. "हाँ हाँ हाँ") OR pair-repeat babble
        # (e.g. "हाँ जी हाँ जी हाँ जी" — 2 unique tokens repeating ≥ 4 times total)
        if len(tokens) >= 4 and len(set(tokens)) <= 2:
            _log.info(
                f"[MUTED-CAPTURE] repeated-token hallucination {text!r} — dropped"
            )
            _muted_filler_dropped_time = asyncio.get_event_loop().time()
            return
        # Space-repetition babble (4+ repetitions of same word: "पास पास पास पास")
        if _is_repetitive_babble(text):
            _log.info(f"[MUTED-CAPTURE] space-repetition babble dropped: {text!r}")
            _muted_filler_dropped_time = asyncio.get_event_loop().time()
            return
        _log.info(
            f"[MUTED-CAPTURE] captured user speech: {text!r} "
            f"(speech_ms={speech_ms:.0f}) — buffered"
        )
        _muted_inject["text"] = text
        _muted_transcript_log.append(text)

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

    async def _bot_response_watchdog(
        user_text: str,
        turn: int,
        timeout: float = 8.0,
        speaking_count_at_start: int = 0,
    ) -> None:
        """Re-inject the user's last turn if the bot doesn't start speaking within `timeout` s."""
        await asyncio.sleep(timeout)
        if _call_ended or _closing_triggered or _last_user_final_turn != turn:
            return
        if _speaking_turns_completed > speaking_count_at_start:
            return
        try:
            _agent_s_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
            # Suppress when a generation is already in flight. Both "thinking"
            # (LLM generating) and "speaking" (TTS playing) mean a reply is coming;
            # re-injecting stacks a duplicate turn. Only re-inject from a quiescent
            # state (listening/idle) where nothing is actually responding.
            if _agent_s_val in ("thinking", "speaking"):
                _log.info(
                    f"[LLM-WATCHDOG] Bot currently {_agent_s_val} — suppressing re-inject (turn={turn})"
                )
                return
        except Exception:
            pass
        _log.warning(
            f"[LLM-WATCHDOG] No response to {user_text!r} in {timeout:.0f}s — re-injecting (turn={turn})"
        )
        try:
            session.generate_reply(user_input=user_text)
        except Exception as e:
            _log.warning(f"[LLM-WATCHDOG] re-inject failed: {e}")

    async def _stale_partial_watchdog(
        text: str,
        timeout: float = 6.0,
        speaking_count_at_start: int = 0,
    ) -> None:
        """Force-inject a PARTIAL to the LLM if no FINAL arrives within `timeout` s."""
        nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn
        await asyncio.sleep(timeout)
        if _call_ended or _closing_triggered or not _greeting_done:
            return
        if _pending_user_text != text:
            return
        try:
            _agent_s_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
            # Suppress while a generation is in flight (thinking = LLM generating,
            # speaking = TTS playing); re-injecting either way stacks a duplicate.
            if _agent_s_val in ("thinking", "speaking"):
                _log.info(f"[STALE-PARTIAL] Bot already {_agent_s_val} — suppressing re-inject for {text!r}")
                return
        except Exception:
            pass
        if _speaking_turns_completed > speaking_count_at_start:
            _log.info(
                f"[STALE-PARTIAL] Bot already responded (turns_completed={_speaking_turns_completed}) "
                f"— suppressing re-inject for {text!r}"
            )
            return
        _log.warning(f"[STALE-PARTIAL] No FINAL in {timeout:.0f}s — force-injecting: {text!r}")
        try:
            session.generate_reply(user_input=text)
            _last_user_final_text = text
            _last_user_final_turn = _turn_counter
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
            _bot_resp_watchdog_task = asyncio.create_task(
                _bot_response_watchdog(
                    text, _turn_counter,
                    speaking_count_at_start=_speaking_turns_completed,
                )
            )
        except Exception as e:
            _log.warning(f"[STALE-PARTIAL] force-inject failed: {e}")

    # ── 7. Function tools ──
    def _play_hold_message(tool_ctx: RunContext):
        """Fire-and-forget hold message using pre-cached frames. Returns the SpeechHandle."""
        if not _HOLD_FRAMES:
            return None
        async def _cached_audio():
            for frame in _HOLD_FRAMES:
                yield frame
        return tool_ctx.session.say(
            "एक क्षण रुकिए, मैं अभी चेक करती हूँ।",
            audio=_cached_audio(),
            add_to_chat_ctx=False,
        )

    @function_tool
    async def FetchCategorySchema(tool_ctx: RunContext, srchterm: str) -> dict:
        """Call when the buyer changes their product requirement mid-call.
        Pass the new product as a simple English search term (e.g. 'washing-machine', 'cctv')."""
        _log.info(f"[FetchCategorySchema] called with srchterm={srchterm!r}")
        _recent = " ".join(
            t.get("text", "") for t in _live_transcript[-4:] if t.get("role") in ("user", "buyer")
        ).lower()
        if any(tok.lower() in _recent for tok in _SELLER_MANUFACTURE_TOKENS):
            _log.warning(
                f"[FetchCategorySchema] SELLER BLOCK — manufacture/sell token in recent turns "
                f"({_recent!r}). Returning seller_detected."
            )
            return {
                "seller_detected": True,
                "instruction": (
                    "The caller is a SELLER or MANUFACTURER, NOT a buyer. "
                    "Follow the CALLER IS A SELLER instructions: ask one confirmation, then close warmly. "
                    "Do NOT ask any spec questions."
                ),
            }
        hold_handle = _play_hold_message(tool_ctx)
        result = await _execute_function_call(
            "FetchCategorySchema", {"srchterm": srchterm},
            functions=_functions, call_state=call_state,
        )
        if hold_handle and not hold_handle.interrupted and not hold_handle.done():
            hold_handle.interrupt()
        return result

    @function_tool
    async def FetchLead(tool_ctx: RunContext, lead_id: str = "", mobile: str = "") -> dict:
        """Fetch customer lead details from Justdial MIS API. Pass lead_id or mobile."""
        _log.info(f"[FetchLead] called | lead_id={lead_id!r} | mobile={mobile!r}")
        result = await _execute_function_call(
            "FetchLead", {"lead_id": lead_id, "mobile": mobile},
            functions=_functions, call_state=call_state,
        )
        return result

    # ── 8. Agent + AgentSession ──
    # Latency: prepend a very short Hindi acknowledgment so the first TTS chunk
    # is tiny (e.g. "जी") and plays within ~100ms, while the full answer follows.
    # The 4-second barge-in unmute timer is unchanged, so the bot cannot be
    # interrupted during the first 4 seconds of any reply.
    _LATENCY_HINT = (
        "\n\nRESPONSE SPEED RULE (mandatory): Start every reply with a 1–3 word Hindi "
        "acknowledgment ONLY — e.g. 'जी,', 'हाँ,', 'बिल्कुल,', 'ठीक है,' — on its own "
        "before the full answer. Never skip this opener. This is required so the caller "
        "hears audio immediately while the rest of the response is still being generated."
    )
    system_instruction = system_instruction + _LATENCY_HINT

    tools = [FetchCategorySchema, FetchLead] if _function_calling else []
    agent = Agent(instructions=system_instruction, tools=tools)
    # No VAD — Sarvam STT with flush_signal handles speech start/end events natively.
    # turn_detection="stt" tells AgentSession to trust Sarvam's speech boundaries.
    # min_endpointing_delay=0.05 — LLM starts 50ms after STT FINAL (was 70ms).
    session = AgentSession(
        stt=stt, llm=llm, tts=tts,
        turn_detection="stt",
        min_endpointing_delay=0.05,
    )

    # ── 9. Event handlers ──

    @session.on("conversation_item_added")
    def _on_item_added(ev) -> None:
        nonlocal _closing_buffer, _closing_triggered, _early_close_muting, _live_transcript
        nonlocal _pending_assistant_text, _close_status
        item = ev.item if hasattr(ev, "item") else ev
        role = getattr(item, "role", None)
        role_str = role.value if hasattr(role, "value") else str(role) if role else ""
        text = (
            getattr(item, "text_content", None)
            or getattr(item, "text", None)
            or ""
        )
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
            # Barge-in cleanup: skip incomplete partial bot turns
            _last_char = text.rstrip()[-1] if text.rstrip() else ""
            _is_incomplete = _barge_in_fired and _last_char not in ("।", ".", "?", "!", "…")
            if _is_incomplete:
                _log.info(f"[BARGE-IN] Skipping interrupted partial bot turn: {text!r}")
                if _early_close_muting and not _closing_triggered:
                    _early_close_muting = False
                    _log.info("[BARGE-IN] Resetting early_close_muting — closing phrase was barged into")
            elif not _live_transcript or _live_transcript[-1] != {"role": "assistant", "text": text}:
                _live_transcript.append({"role": "assistant", "text": text})
        _pending_assistant_text = ""
        if not _is_incomplete:
            # Partial closing-phrase detection (replaces _consume_sniff stream sniffer).
            # In pipeline mode, conversation_item_added fires after LLM generation completes.
            # The _delayed_unmute task (4s) checks _early_close_muting at fire time, so
            # setting it here before 4s keeps the mic muted through the closing turn.
            if not _early_close_muting and not _closing_triggered:
                _buf_lower = _closing_buffer.lower()
                if any(m in _buf_lower for m in (
                    "details मिल गईं",
                    "sellers आपसे contact",
                    "sellers will contact",
                    "all details",
                )):
                    _early_close_muting = True
                    _set_mic(False, reason="commit-closing-phrase")
                    _log.info("[CLOSE DETECT] Partial closing phrase detected in commit — mic muted")
            # Closing detection. Evaluate the CURRENT turn (text), not the
            # accumulated _closing_buffer: a polite "शुक्रिया"/"धन्यवाद" in an
            # earlier turn must not end a later one. And a turn that still asks a
            # question is mid-conversation, NOT a close — bare thank-you words are
            # in _CLOSE_MARKERS and the bot uses them politely before its next
            # question. Only a strong "sellers will contact / details मिल गईं"
            # signal forces a close regardless of phrasing.
            _strong_close = any(m in text.lower() for m in (
                "details मिल गईं",
                "sellers आपसे contact",
                "sellers will contact",
                "all details",
            ))
            if _is_closing_phrase(text) and (_strong_close or "?" not in text):
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
            elif _is_closing_phrase(text):
                _log.info(
                    f"[CLOSE DETECT] closing marker in a mid-conversation question turn "
                    f"— ignoring: {text!r}"
                )

    @session.on("user_input_transcribed")
    def _on_user_spoke(ev) -> None:
        nonlocal _turn_counter, _live_transcript, _pending_user_text, _current_window_pcm
        nonlocal _call_ended, _early_inject_done, _abusive_detected, _close_status
        nonlocal _first_partial_time, _silero_rejected_turns, _silero_rejected_time
        nonlocal _consecutive_silero_rejects
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
        _agent_state_now = ""
        try:
            _s = session.agent_state
            _agent_state_now = _s.value if hasattr(_s, "value") else str(_s)
        except Exception:
            _agent_state_now = "?"
        # Discard buffered muted-window text when live speech arrives
        if _muted_inject["text"]:
            _log.info(
                f"[MUTED-CAPTURE] live speech arrived — discarding muted buffer "
                f"{_muted_inject['text']!r}"
            )
            if _muted_transcript_log and _muted_transcript_log[-1] == _muted_inject["text"]:
                _muted_transcript_log.pop()
            _muted_inject["text"] = ""
        if is_final:
            # Pre-filter: FINAL arriving while bot is speaking with mic disabled and barge-in
            # not yet fired means this is STT pipeline lag — audio buffered during the prior
            # thinking period (when mic was ON) delivered ~100-200ms after mic is disabled at
            # speaking-start. This is NOT user speech; discard before turning the counter.
            # Add to _silero_rejected_turns so conversation_item_added also skips it,
            # preventing the pipeline from triggering a second LLM generate_reply.
            if _agent_state_now == "speaking" and not _mic_enabled and not _barge_in_fired:
                _log.info(
                    f"[STT] FINAL during muted-speaking (pre-barge-in) — "
                    f"pipeline-delay noise discarded: {transcript_text!r}"
                )
                _silero_rejected_turns.add(transcript_text)
                return
            _turn_counter += 1
            speech_ms_now = len(_current_window_pcm) / 2 / 16_000 * 1000
            _log.info(
                f"[TRANSCRIPT] Turn {_turn_counter} | USER (FINAL): {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled} "
                f"speech_ms={speech_ms_now:.0f}"
            )
            # STT Langfuse span
            if _lf_trace and _first_partial_time > 0:
                try:
                    _stt_latency_ms = (asyncio.get_event_loop().time() - _first_partial_time) * 1000
                    _lf_trace.span(
                        name=f"stt-turn-{_turn_counter}",
                        input={"speech_ms": round(speech_ms_now)},
                        output={"transcript": transcript_text},
                        metadata={
                            "latency_ms": round(_stt_latency_ms),
                            "model": "saaras:v3-codemix",
                        },
                    ).end()
                except Exception:
                    pass
                _first_partial_time = 0.0
            if _call_ended:
                return
            _early_inject_done = False
            _had_partial = bool(_pending_user_text)
            _pending_user_text = ""
            _pcm_snapshot = bytes(_current_window_pcm)  # save before reset
            _current_window_pcm = bytearray()  # reset window for next turn
            # Silero sanity-check on the captured PCM
            _voiced = 0
            if _pcm_snapshot:
                _voiced = _silero_voiced_ms(_pcm_snapshot, _silero_threshold)
                if _voiced < _silero_min_speech_ms:
                    _voiced_ratio = _voiced / speech_ms_now if speech_ms_now > 0 else 0.0
                    _distinct_words = len(set(_normalize_stt_tokens(transcript_text)))
                    if _voiced >= 60 or _voiced_ratio >= 0.08:
                        _log.info(
                            f"[STT] Silero weak but trace speech present — accepting "
                            f"{transcript_text!r} (voiced_ms={_voiced:.0f}, ratio={_voiced_ratio:.2%})"
                        )
                    elif _distinct_words >= 3:
                        # Silero found ~zero voiced energy — typically the user spoke
                        # over/right after the bot's audio, so the window is echo-polluted
                        # or quiet. But a coherent 3+ distinct-word phrase is almost
                        # certainly real speech; Sarvam STT does not fabricate that from
                        # line noise. Trust the STT here so genuine answers aren't dropped
                        # (which forces the bot to re-ask). Short/single-token and repeated
                        # hallucinations stay strictly gated, and the calibration /
                        # short-noise / bystander / babble filters below still apply.
                        _log.info(
                            f"[STT] Silero near-zero but multi-word coherent phrase — "
                            f"trusting STT: {transcript_text!r} "
                            f"(distinct_words={_distinct_words}, voiced_ms={_voiced:.0f})"
                        )
                    else:
                        _log.info(
                            f"[STT] Silero rejected FINAL {transcript_text!r} — "
                            f"voiced_ms={_voiced:.0f} < min={_silero_min_speech_ms}"
                        )
                        if _live_transcript and _live_transcript[-1]["role"] == "user":
                            _live_transcript.pop()
                        _silero_rejected_turns.add(transcript_text)
                        _silero_rejected_time = asyncio.get_event_loop().time()
                        _consecutive_silero_rejects += 1
                        _muted_transcript_log.append(f"[low-confidence] {transcript_text}")
                        return
                    _consecutive_silero_rejects = 0  # weak-but-accepted counts as a real turn
                _log.info(f"[STT] Silero confirmed FINAL (voiced_ms={_voiced:.0f})")
                _consecutive_silero_rejects = 0
            # Calibration hallucination filter
            _norm_transcript = unicodedata.normalize("NFC", transcript_text.strip())
            if _norm_transcript in _GEMINI_CALIBRATION_HALLUCINATIONS:
                _log.info(f"[NOISE] Calibration hallucination discarded: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                _silero_rejected_time = asyncio.get_event_loop().time()
                return
            # Short noise filter (speech_ms_now was computed before window reset)
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
                    _silero_rejected_time = asyncio.get_event_loop().time()
                    _muted_transcript_log.append(f"[noise-filtered] {transcript_text}")
                    return
            # Bystander filter
            if _is_bystander_speech(transcript_text):
                _log.info(f"[NOISE] Bystander speech discarded: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                _silero_rejected_time = asyncio.get_event_loop().time()
                return
            # Space-repetition babble filter (e.g. "पास पास पास पास पास पास" from STT)
            if _is_repetitive_babble(transcript_text):
                _log.info(f"[NOISE] Repetitive-word babble discarded: {transcript_text!r}")
                if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                elif _live_transcript and _live_transcript[-1] == {"role": "user", "text": transcript_text}:
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                _silero_rejected_time = asyncio.get_event_loop().time()
                _muted_transcript_log.append(f"[babble-filtered] {transcript_text}")
                return
            # IVR / busy-line detection
            if _is_ivr_message(transcript_text):
                _log.info(f"[IVR] busy-line/voicemail detected — ending call: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1].get("role") == "user":
                    _live_transcript.pop()
                _live_transcript.append({"role": "ivr", "text": transcript_text})
                _silero_rejected_turns.add(transcript_text)
                if not _call_ended:
                    _call_ended = True
                    call_state["ended_naturally"] = True
                    asyncio.create_task(_kick_caller_safe())
                    asyncio.create_task(_save_and_close("ivr_detected"))
                return
            # Abuse detection
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
            # Thinking-state impatience guard: if the bot is already generating a
            # reply and the user says a short filler like "हेलो" or "हाँ", don't
            # treat it as a new real turn. livekit-agents will still internally
            # cancel the LLM (we can't prevent that), but we suppress it from our
            # transcript and watchdogs so the bot eventually responds to the LAST
            # real user turn instead of getting stuck in a thinking→listening loop.
            if (
                _agent_state_now == "thinking"
                and not _call_ended
                and not _closing_triggered
                and _greeting_done
            ):
                _toks = set(_normalize_stt_tokens(transcript_text))
                if _toks and _toks <= (_SHORT_TERMINAL_TOKENS | _THINKING_FILLER_TOKENS):
                    _log.info(
                        f"[BARGE-THINK] Filler during thinking suppressed "
                        f"(won't displace pending LLM turn): {transcript_text!r}"
                    )
                    if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                        _live_transcript.pop()
                    elif _live_transcript and _live_transcript[-1] == {"role": "user", "text": transcript_text}:
                        _live_transcript.pop()
                    _silero_rejected_turns.add(transcript_text)
                    return

            # Real turn accepted — clear the noise flag so the auto-reply for
            # THIS turn is not mistaken for a spurious one by the speaking guard.
            _silero_rejected_time = 0.0
            _consecutive_silero_rejects = 0
            _user_entry: dict = {"role": "user", "text": transcript_text}
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1] = _user_entry
            else:
                _live_transcript.append(_user_entry)
            # Response watchdog
            nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn
            nonlocal _stale_partial_task, _last_user_turn_time, _final_arrived_while_speaking
            nonlocal _last_user_final_time
            _last_user_turn_time = asyncio.get_event_loop().time()
            _last_user_final_time = _last_user_turn_time  # latency clock starts here
            if _stale_partial_task and not _stale_partial_task.done():
                _stale_partial_task.cancel()
                _stale_partial_task = None
            _last_user_final_text = transcript_text
            _last_user_final_turn = _turn_counter
            try:
                _cur_state_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
                _final_arrived_while_speaking = (_cur_state_val == "speaking")
            except Exception:
                _final_arrived_while_speaking = False
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
            _bot_resp_watchdog_task = asyncio.create_task(
                _bot_response_watchdog(
                    transcript_text, _turn_counter,
                    speaking_count_at_start=_speaking_turns_completed,
                )
            )
        else:
            # Partial transcript
            _log.info(
                f"[TRANSCRIPT] PARTIAL | USER: {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled}"
            )
            # IVR check on PARTIAL (pipeline STT may respond before FINAL)
            if _is_ivr_message(transcript_text) and not _call_ended:
                _log.info(f"[IVR] busy-line/voicemail in PARTIAL — ending call: {transcript_text!r}")
                _live_transcript.append({"role": "ivr", "text": transcript_text})
                _call_ended = True
                call_state["ended_naturally"] = True
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("ivr_detected"))
                return
            # Abuse detection on PARTIAL
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
            if not _had_partial:
                # First partial of this turn — start STT clock
                _first_partial_time = asyncio.get_event_loop().time()
            _pending_user_text = transcript_text
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1]["text"] = transcript_text
            else:
                _live_transcript.append({"role": "user", "text": transcript_text})
            # Stale-partial watchdog
            if _stale_partial_task and not _stale_partial_task.done():
                _stale_partial_task.cancel()
            if _greeting_done and not _call_ended and not _closing_triggered:
                _stale_partial_task = asyncio.create_task(
                    _stale_partial_watchdog(
                        transcript_text, speaking_count_at_start=_speaking_turns_completed,
                    )
                )
            # Early-inject for short terminal tokens (PARTIAL == FINAL)
            _speech_ms_now = len(_current_window_pcm) / 2 / 16_000 * 1000
            if (
                not _early_inject_done
                and not _call_ended
                and _greeting_done
                and _agent_state_now == "listening"
                and _speech_ms_now >= 150
            ):
                _tokens = set(_normalize_stt_tokens(transcript_text))
                if _tokens and _tokens <= _SHORT_TERMINAL_TOKENS:
                    try:
                        _silero_rejected_time = 0.0  # explicit real-turn reply
                        session.generate_reply(user_input=transcript_text)
                        _early_inject_done = True
                        _log.info(
                            f"[EARLY-INJECT] Short terminal PARTIAL → LLM: {transcript_text!r} "
                            f"(speech_ms={_speech_ms_now:.0f})"
                        )
                    except Exception as _ei_exc:
                        _log.warning(f"[EARLY-INJECT] failed: {_ei_exc}")

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        nonlocal _echo_guard_task, _speaking_unmute_task, _greeting_done, _bot_has_spoken
        nonlocal _barge_in_fired, _bot_resp_watchdog_task, _speaking_start_time
        nonlocal _thinking_start_time
        nonlocal _speaking_turns_completed, _muted_capture_empty_time, _muted_filler_dropped_time
        nonlocal _last_user_final_text, _final_arrived_while_speaking, _silero_rejected_time
        nonlocal _consecutive_silero_rejects
        nonlocal _kb_handle, _kb_auto_stop_task
        new_state = getattr(ev, "new_state", None)
        old_state = getattr(ev, "old_state", None)
        state_str = new_state.value if hasattr(new_state, "value") else str(new_state) if new_state else ""
        old_str = old_state.value if hasattr(old_state, "value") else str(old_state) if old_state else "?"
        _log.info(
            f"[STATE] {old_str} → {state_str} | "
            f"mic={_mic_enabled} greeting_done={_greeting_done} "
            f"call_ended={_call_ended} closing={_closing_triggered}"
        )

        if state_str == "speaking":
            if _call_ended:
                return
            _bot_has_spoken = True
            _barge_in_fired = False
            _speaking_start_time = asyncio.get_event_loop().time()
            _cancel_inactivity()
            # Keyboard overlap: play a short KEYBOARD_TYPING2 burst at the exact moment
            # TTS audio starts so there is NO silence gap between thinking sound and voice.
            # KEYBOARD_TYPING2 is a short clip that fades naturally while the first TTS
            # sentence plays — giving a smooth keyboard→voice crossfade.
            try:
                if _kb_auto_stop_task and not _kb_auto_stop_task.done():
                    _kb_auto_stop_task.cancel()
                    _kb_auto_stop_task = None
                if _kb_handle and not _kb_handle.done():
                    _kb_handle.stop()
                _kb_handle = _bg_audio.play(
                    AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.40)
                )
            except Exception:
                pass
            # Latency: time from user FINAL to first bot audio
            nonlocal _last_user_final_time, _response_latencies
            if _last_user_final_time > 0 and _greeting_done:
                _latency_ms = (_speaking_start_time - _last_user_final_time) * 1000
                _last_user_final_time = 0.0
                _response_latencies.append(_latency_ms)
                _avg_ms = sum(_response_latencies) / len(_response_latencies)
                _turn_n = len(_response_latencies)
                _log.info(
                    f"[LATENCY] response={_latency_ms:.0f}ms | "
                    f"avg={_avg_ms:.0f}ms over {_turn_n} turn(s)"
                )
                # Langfuse spans: E2E + LLM breakdown
                if _lf_trace:
                    try:
                        # E2E: user FINAL → first bot audio
                        _lf_trace.span(
                            name=f"e2e-turn-{_turn_n}",
                            input={"user": _last_user_final_text},
                            metadata={
                                "latency_ms": round(_latency_ms),
                                "avg_latency_ms": round(_avg_ms),
                                "turn": _turn_n,
                            },
                        ).end()
                        # LLM + TTS TTFB: thinking state start → first bot audio
                        if _thinking_start_time > 0:
                            _llm_ms = (_speaking_start_time - _thinking_start_time) * 1000
                            _lf_trace.span(
                                name=f"llm-tts-ttfb-turn-{_turn_n}",
                                input={"user": _last_user_final_text},
                                metadata={
                                    "latency_ms": round(_llm_ms),
                                    "model": "gemini-3.1-flash-lite",
                                },
                            ).end()
                            _thinking_start_time = 0.0
                    except Exception:
                        pass
            _final_arrived_while_speaking = False
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
                _bot_resp_watchdog_task = None
            if _echo_guard_task and not _echo_guard_task.done():
                _echo_guard_task.cancel()
                _log.info("[SPEAKING-MUTE] cancelled previous hold — new speaking turn")
            if _muted_inject["text"]:
                _log.info(
                    f"[MUTED-CAPTURE] discarding uncombined muted text (not sent to LLM): "
                    f"{_muted_inject['text']!r}"
                )
                if _muted_transcript_log and _muted_transcript_log[-1] == _muted_inject["text"]:
                    _muted_transcript_log.pop()
                _muted_inject["text"] = ""
            _now_eg = asyncio.get_event_loop().time()
            # Silero-reject guard: this speaking turn was auto-fired by LiveKit's
            # turn_detection="stt" off a FINAL we rejected as noise. _on_user_spoke
            # can suppress the transcript but cannot stop the auto-reply, so kill the
            # spurious turn here before any audio plays. A real accepted turn or any
            # explicit generate_reply resets _silero_rejected_time, so only the
            # implicit noise-driven reply reaches here with the flag set.
            if (
                _greeting_done
                and _silero_rejected_time > 0
                and (_now_eg - _silero_rejected_time) < 4.0
            ):
                if _consecutive_silero_rejects >= 2:
                    # Sustained rejection streak — Silero is likely wrong about real
                    # speech (degraded audio path). Stop interrupting so the call
                    # isn't muted; let the bot speak.
                    _log.warning(
                        f"[SILERO-GUARD] {_consecutive_silero_rejects} consecutive rejects "
                        "— Silero likely misfiring, allowing bot to speak"
                    )
                    _silero_rejected_time = 0.0
                else:
                    _log.warning(
                        f"[SILERO-GUARD] speaking turn {(_now_eg - _silero_rejected_time)*1000:.0f}ms "
                        "after Silero-rejected FINAL — spurious auto-reply, interrupting"
                    )
                    _silero_rejected_time = 0.0
                    _last_user_final_text = ""
                    _barge_in_fired = True
                    session.interrupt()
                    return
            # Echo guard: new speaking turn within 200ms of empty muted-capture
            if (
                _greeting_done
                and _muted_capture_empty_time > 0
                and (_now_eg - _muted_capture_empty_time) < 0.20
            ):
                _log.warning(
                    f"[ECHO-GUARD] new speaking turn {(_now_eg - _muted_capture_empty_time)*1000:.0f}ms "
                    "after empty muted-capture — suspected TTS echo, interrupting"
                )
                _muted_capture_empty_time = 0.0
                _last_user_final_text = ""
                _barge_in_fired = True
                session.interrupt()
                return
            _muted_capture_empty_time = 0.0
            # Filler guard: new speaking turn within 1.5s of a dropped filler
            if (
                _greeting_done
                and _muted_filler_dropped_time > 0
                and (_now_eg - _muted_filler_dropped_time) < 1.5
            ):
                _log.warning(
                    f"[FILLER-GUARD] new speaking turn {(_now_eg - _muted_filler_dropped_time)*1000:.0f}ms "
                    "after dropped filler — suspected filler response, interrupting"
                )
                _muted_filler_dropped_time = 0.0
                _last_user_final_text = ""
                _barge_in_fired = True
                session.interrupt()
                return
            _muted_filler_dropped_time = 0.0
            # Mute mic at the start of every bot speaking turn.
            # Mid-call turns: unmute after 4s for barge-in.
            # Greeting turn: unmute early at 4s so pipeline STT is warm when greeting ends.
            # Closing turn: stays muted (no 4s timer).
            _set_mic(False, reason="speaking-start")
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
                async def _greeting_early_unmute() -> None:
                    await asyncio.sleep(4.0)
                    if not _greeting_done and not _call_ended:
                        _set_mic(True, reason="greeting-4s-early-unmute")
                _speaking_unmute_task = asyncio.create_task(_greeting_early_unmute())

        elif state_str in ("listening", "idle"):
            speaking_duration = asyncio.get_event_loop().time() - _speaking_start_time
            _speaking_turns_completed += 1
            # TTS duration Langfuse span (speaking_start → speaking_end)
            if _lf_trace and _greeting_done and speaking_duration > 0.1:
                try:
                    _lf_trace.span(
                        name=f"tts-turn-{_speaking_turns_completed}",
                        metadata={
                            "duration_ms": round(speaking_duration * 1000),
                            "model": "bulbul:v3-simran",
                        },
                    ).end()
                except Exception:
                    pass
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
                _bot_resp_watchdog_task = None
            if (
                not _call_ended and not _closing_triggered
                and _last_user_final_text
                and speaking_duration < 1.5
                and not _final_arrived_while_speaking
            ):
                _bot_resp_watchdog_task = asyncio.create_task(
                    _bot_response_watchdog(
                        _last_user_final_text, _last_user_final_turn, timeout=1.0,
                        speaking_count_at_start=_speaking_turns_completed,
                    )
                )
            if _bot_has_spoken and not _greeting_done:
                _greeting_done = True
                _log.info(f"[STATE] greeting_done → True (call_ended={_call_ended})")
                # Transcribe any audio captured during the greeting window via Sarvam
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
                        await asyncio.sleep(0.8)
                        if _call_ended or _closing_triggered or _turn_counter > 0:
                            return
                        text = _muted_inject.get("text", "")
                        if not text:
                            return
                        if _greeting_captured_ms < 800:
                            _log.info(
                                f"[MUTED-CAPTURE] post-greeting inject skipped — too short "
                                f"({_greeting_captured_ms:.0f}ms): {text!r}"
                            )
                            if _muted_transcript_log and _muted_transcript_log[-1] == text:
                                _muted_transcript_log.pop()
                            _muted_inject["text"] = ""
                            return
                        _inject_tokens = {
                            unicodedata.normalize("NFC", "".join(
                                c for c in w.lower()
                                if unicodedata.category(c)[0] not in ("P", "S", "Z")
                            ))
                            for w in text.split() if w.strip()
                        }
                        _inject_tokens.discard("")
                        _BARE_GREETING_TOKENS = frozenset(
                            unicodedata.normalize("NFC", w) for w in {
                                "हाँ", "हां", "हा", "जी", "हाँजी", "हांजी",
                                "हेलो", "hello", "हैलो", "hi", "हाय",
                                "haan", "ha", "han", "ji", "jee", "okay", "ok",
                                "हाँ", "हां", "बोलो", "bol", "bolo",
                                "बोला", "bola",
                                "om",
                                "हो", "हो जी", "होजी", "हाँ हो",
                            }
                        )
                        if _inject_tokens and not (_inject_tokens - _BARE_GREETING_TOKENS):
                            _log.info(
                                f"[MUTED-CAPTURE] post-greeting inject skipped — bare phone-pickup signal: {text!r}"
                            )
                            if _muted_transcript_log and _muted_transcript_log[-1] == text:
                                _muted_transcript_log.pop()
                            _muted_inject["text"] = ""
                            return
                        _muted_inject["text"] = ""
                        _log.info(f"[MUTED-CAPTURE] post-greeting inject → LLM: {text!r}")
                        try:
                            session.generate_reply(user_input=text)
                            nonlocal _muted_inject_sent_time
                            _muted_inject_sent_time = asyncio.get_event_loop().time()
                            # Watchdog: if LLM doesn't start speaking within 3s, re-inject
                            _injected_text = text
                            _speaking_count_at_inject = _speaking_turns_completed

                            async def _post_greeting_watchdog() -> None:
                                await asyncio.sleep(3.0)
                                if _call_ended or _closing_triggered or _turn_counter > 0:
                                    return
                                if _speaking_turns_completed > _speaking_count_at_inject:
                                    return
                                try:
                                    _s_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
                                    if _s_val == "speaking":
                                        return
                                except Exception:
                                    pass
                                _log.warning(
                                    f"[LLM-WATCHDOG] post-greeting inject no response in 3s "
                                    f"— re-injecting {_injected_text!r}"
                                )
                                try:
                                    session.generate_reply(user_input=_injected_text)
                                except Exception as _ex:
                                    _log.warning(f"[LLM-WATCHDOG] post-greeting re-inject failed: {_ex}")

                            asyncio.create_task(_post_greeting_watchdog())
                        except Exception as e:
                            _log.warning(f"[MUTED-CAPTURE] post-greeting inject failed: {e}")

                    asyncio.create_task(_post_greeting_inject())
                if not _call_ended:
                    _set_mic(True, reason="greeting-complete")
                    _log.info("[MIC] Greeting complete — mic enabled")
            elif _greeting_done and _bot_has_spoken and not _call_ended and not _closing_triggered:
                # Post-speech hold: brief window after each bot turn to absorb TTS tail
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
                    _log.info(f"[POST-SPEECH-HOLD] started (hold={_post_speech_hold_ms} ms)")
                    try:
                        await asyncio.sleep(_post_speech_hold_ms / 1000)
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
                        else:
                            _log.info(
                                f"[POST-SPEECH-HOLD] expired — mic stays muted "
                                f"(call_ended={_call_ended} closing={_closing_triggered} "
                                f"early_mute={_early_close_muting})"
                            )
                    except asyncio.CancelledError:
                        _log.info("[POST-SPEECH-HOLD] cancelled — new speaking turn started")
                    finally:
                        captured_ms = _muted_capture["speech_ms"]
                        captured_frames = _muted_capture["frames"][:]
                        _muted_capture["frames"].clear()
                        _muted_capture["speech_ms"] = 0.0
                        if captured_frames and not _call_ended:
                            _log.info(
                                f"[MUTED-CAPTURE] {captured_ms:.0f} ms of speech captured — "
                                "spawning Sarvam transcription"
                            )
                            asyncio.create_task(
                                _transcribe_muted_period(captured_frames, captured_ms)
                            )

                _echo_guard_task = asyncio.create_task(_post_speech_hold())
            if not _call_ended and not _closing_triggered:
                _reset_inactivity()

        elif state_str == "thinking":
            # LLM is generating — pause inactivity timer (bot is actively responding)
            _cancel_inactivity()
            _thinking_start_time = asyncio.get_event_loop().time()
            # Keyboard only when the user just spoke (listening → thinking).
            # Skip for speaking → thinking (watchdog re-inject, tool-call second pass)
            # so the user doesn't hear keyboard noise with no new user turn.
            if old_str == "listening":
                try:
                    if _kb_auto_stop_task and not _kb_auto_stop_task.done():
                        _kb_auto_stop_task.cancel()
                    if _kb_handle and not _kb_handle.done():
                        _kb_handle.stop()
                    _kb_handle = _bg_audio.play(
                        AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.55)
                    )
                    # Auto-stop after 4 s so long tool calls don't loop the sound
                    async def _auto_stop_kb(_h=_kb_handle) -> None:
                        await asyncio.sleep(4.0)
                        try:
                            if _h and not _h.done():
                                _h.stop()
                        except Exception:
                            pass
                    _kb_auto_stop_task = asyncio.create_task(_auto_stop_kb())
                except Exception:
                    pass
        else:
            _log.info(f"[STATE] unhandled state {state_str!r} — no action taken")

    # ── 10. Subscribe to caller audio for muted-window capture + Sarvam fallback ──
    _buffering_track_sids: set = set()

    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(track, pub, participant) -> None:
        if isinstance(track, rtc.RemoteAudioTrack) and track.sid not in _buffering_track_sids:
            _buffering_track_sids.add(track.sid)
            asyncio.create_task(_buffer_user_audio(track))

    # SIP audio tracks may be subscribed before the handler above is registered.
    # Scan already-subscribed remote tracks so _buffer_user_audio always starts.
    for _rp in ctx.room.remote_participants.values():
        for _rpub in _rp.track_publications.values():
            if (
                isinstance(_rpub.track, rtc.RemoteAudioTrack)
                and _rpub.track.sid not in _buffering_track_sids
            ):
                _buffering_track_sids.add(_rpub.track.sid)
                asyncio.create_task(_buffer_user_audio(_rpub.track))

    # ── Background audio ──
    # thinking_sound is NOT passed here so we control keyboard playback manually
    # in _on_agent_state. This lets us play a short overlap burst at the start of
    # speaking so keyboard → TTS is seamless instead of keyboard → silence → TTS.
    _bg_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.50),
    )
    _kb_handle = None  # tracks the current keyboard play handle

    # ── Start session ──
    try:
        await session.start(
            room=ctx.room,
            agent=agent,
            room_options=_RoomOptionsCls(close_on_disconnect=False),
        )
    except Exception as _start_exc:
        _exc_str = str(_start_exc).lower()
        _key_tag = f"key=...{_gemini_api_key[-6:] if _gemini_api_key else 'NOT SET'}"
        if "409" in _exc_str or "conflict" in _exc_str or "aborted" in _exc_str:
            _log.error(f"[LLM-ERR-409] concurrent session limit hit — {_key_tag} | {_start_exc}")
        elif "429" in _exc_str or "resource_exhausted" in _exc_str or "quota" in _exc_str:
            _log.error(f"[LLM-ERR-429] rate limit / quota exceeded — {_key_tag} | {_start_exc}")
        elif "401" in _exc_str or "unauthenticated" in _exc_str:
            _log.error(f"[LLM-ERR-401] invalid or missing API key — {_key_tag} | {_start_exc}")
        elif "403" in _exc_str or "permission_denied" in _exc_str:
            _log.error(f"[LLM-ERR-403] key lacks permission — {_key_tag} | {_start_exc}")
        elif "404" in _exc_str or "not_found" in _exc_str:
            _log.error(f"[LLM-ERR-404] model not found — {_key_tag} | {_start_exc}")
        elif "503" in _exc_str or "unavailable" in _exc_str:
            _log.error(f"[LLM-ERR-503] service unavailable — {_key_tag} | {_start_exc}")
        else:
            _log.error(f"[LLM-ERR-UNKNOWN] session.start() failed — {_key_tag} | {_start_exc}")
        raise

    await _bg_audio.start(room=ctx.room, agent_session=session)

    call_state["call_start_time"] = time.time()

    # ── 11. Read SIP info from participant attributes ──
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
    _caller_identity = participant.identity if participant else ""

    # ── 12. Resolve lead for greeting text ──
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

    # ── 13. Build greeting text from lead (replaces the Gemini "." trigger) ──
    def _build_greeting(rec: dict) -> str:
        """Build the deterministic opening line from lead data (mirrors mandatory_opening in bot.py)."""
        _company = (rec.get("buyer_details") or {}).get("company_name", "").strip()
        _sc = (rec.get("search_context") or {})
        _product_name = (
            (_sc.get("searched_product") or {}).get("product_name", "")
            or _sc.get("searched_keyword", "")
            or rec.get("catname", "")
            or "product"
        )
        if _company:
            return (
                f"हेलो, {_company}? "
                f"जी, मैं Simran बोल रही हूँ Justdial से — "
                f"आपको {_product_name} की requirement है ना?"
            )
        return (
            f"हेलो, मैं Simran बोल रही हूँ Justdial से — "
            f"आपको {_product_name} की requirement है ना?"
        )

    _greeting_text = _build_greeting(record)
    _log.info(f"[GREETING] Text: {_greeting_text!r}")

    # ── 14. Hard call timeout (5 min) ──
    _DEFAULT_TIMEOUT_MSG = (
        _lang_cfg.get("timeout_message")
        or "जी, मुझे सिर्फ 5 मिनट तक बात करने की permission है. जो भी details मिली हैं, sellers जल्द ही आपसे contact करेंगे. आपका समय देने के लिए धन्यवाद. अलविदा!"
    )

    async def _call_timeout() -> None:
        await asyncio.sleep(_max_call_duration)
        if call_state.get("ended_naturally") or call_state.get("save_done"):
            return
        _log.info(f"[TIMEOUT] {_max_call_duration}s limit reached — ending call")
        _pc_msg = (_bot_config.get("prompt_config") or {}).get("timeout_message", "").strip()
        timeout_msg = _pc_msg or _DEFAULT_TIMEOUT_MSG
        call_state["ended_naturally"] = True
        _cancel_inactivity()
        try:
            # await session.say: waits for TTS to fully play out before kicking
            await session.say(timeout_msg, allow_interruptions=False)
            await asyncio.sleep(0.5)
        except Exception:
            await asyncio.sleep(4)
        await _kick_caller_safe()
        asyncio.create_task(_save_and_close("completed"))

    call_state["_timeout_task"] = asyncio.create_task(_call_timeout())

    # ── 15. Start inactivity timer ──
    _reset_inactivity()

    # ── 16. Participant disconnect handler ──
    @ctx.room.on("participant_disconnected")
    def _on_disconnect(p: rtc.RemoteParticipant) -> None:
        nonlocal _call_ended
        _cancel_inactivity()
        if call_state["ended_naturally"]:
            return
        _call_ended = True
        # In pipeline mode the STT plugin finalizes user turns on disconnect quickly —
        # no need for the Gemini "." flush hack. A short sleep in _save_and_close is enough.
        asyncio.create_task(_save_and_close("disconnected"))

    # ── 17. Speak greeting (replaces the _trigger_greeting / LiveClientContent dance) ──
    # Mute mic BEFORE session.say so muted-window audio capture starts immediately.
    # The agent_state "speaking" handler schedules the 4s early-unmute automatically.
    _set_mic(False, reason="greeting-start")
    _log.info("[GREETING] Starting greeting via session.say()")
    try:
        await session.say(_greeting_text, allow_interruptions=True)
        _log.info("[GREETING] session.say() completed (TTS playout done)")
    except Exception as _greet_exc:
        _log.warning(f"[GREETING] session.say() error: {_greet_exc}")
    finally:
        # Ensure greeting state is set and mic is restored if the agent_state
        # handler hasn't done so yet (e.g. session.say errored before playout)
        if not _greeting_done:
            _greeting_done = True
            _bot_has_spoken = True
            _log.info("[GREETING] Marking greeting_done=True in finally block")
        if not _call_ended and not _mic_enabled:
            _set_mic(True, reason="greeting-finally-unmute")

    # ── Keep entrypoint alive until save_call_data finishes ──
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
            prewarm_fnc=prewarm_fnc,
            agent_name="voice-bot-justdial-fallback",
            port=8082,
            num_idle_processes=3,
        )
    )
