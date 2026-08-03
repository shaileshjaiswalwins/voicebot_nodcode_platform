#!/usr/bin/env python3
"""
Parameterised standalone pipeline voice bot.

Full copy of bot_dev.py with every hardcoded value driven by an env var.
No monkey-patching — all parameters are wired in directly.

─── Infrastructure ───────────────────────────────────────────────────────────
  BOT_PORT            HTTP worker port              (default: 8085)
  AGENT_NAME          LiveKit agent name            (default: voice-bot-param)
  NUM_IDLE_PROCESSES  warm worker pool size         (default: 2)

─── API Keys ─────────────────────────────────────────────────────────────────
  SARVAM_API_KEY      Sarvam key for STT + TTS
  GEMINI_API_KEY      Gemini key for LLM

─── Backend / MIS URLs ───────────────────────────────────────────────────────
  MIS_API_BASE        MIS backend base URL          (default: http://192.168.14.101:3006)
  BACKEND_URL         Call-log save URL             (default: http://localhost:8000)

─── MongoDB (DB isolation) ───────────────────────────────────────────────────
  MONGO_URI           MongoDB connection string     (default: mongodb://192.168.13.65:27017)
  MONGO_DB            Database name                 (default: ai_lead_qualify)
  MONGO_COLLECTION    Collection name               (default: call_transcripts)

─── Language ─────────────────────────────────────────────────────────────────
  LANGUAGE_KEY        Prompt language key           (default: hindi)
                      Passed as lang_key to build_system_prompt
  STT_LANGUAGE_CODE   BCP-47 code for STT + batch  (default: hi-IN)
  TTS_LANGUAGE_CODE   BCP-47 code for TTS output   (default: hi-IN)

─── Models ───────────────────────────────────────────────────────────────────
  LLM_MODEL           Gemini model id               (default: gemini-3.1-flash-lite)
  STT_MODEL           Sarvam STT model              (default: saaras:v3)
  TTS_MODEL           Sarvam TTS model              (default: bulbul:v3)

─── Temperature ──────────────────────────────────────────────────────────────
  LLM_TEMPERATURE     LLM temperature override      (default: from bot_config or 0.4)
  TTS_TEMPERATURE     TTS temperature               (default: 0.75)

─── System Prompt ────────────────────────────────────────────────────────────
  SYSTEM_PROMPT_EXTRA Extra instructions appended after the built system prompt

Run:
  ./start_param.sh
  # or directly:
  MONGO_DB=ai_lead_qualify_test GEMINI_API_KEY=... uv run python bot_dev_param.py start
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

from pipeline_providers import build_llm, build_stt, build_tts
from workflow_engine import run_workflow_call

# ---------------------------------------------------------------------------
# ── Env-var config block — all parameters live here ──────────────────────────
# ---------------------------------------------------------------------------

load_dotenv(override=True)

# Infrastructure
_PORT               = int(os.getenv("BOT_PORT", "8085"))
_AGENT_NAME         = os.getenv("AGENT_NAME", os.getenv("LIVEKIT_AGENT_NAME", "voice-bot-justdial-live-2"))
_NUM_IDLE_PROCESSES = int(os.getenv("NUM_IDLE_PROCESSES", "2"))

# Language
_LANGUAGE_KEY      = os.getenv("LANGUAGE_KEY", "hindi")
_STT_LANGUAGE_CODE = os.getenv("STT_LANGUAGE_CODE", "hi-IN")
_TTS_LANGUAGE_CODE = os.getenv("TTS_LANGUAGE_CODE", "hi-IN")

# Models
_LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
_STT_MODEL = os.getenv("STT_MODEL", "saaras:v3")
_TTS_MODEL = os.getenv("TTS_MODEL", "bulbul:v3")

# Temperature (None means "fall back to bot_config value")
_LLM_TEMPERATURE_ENV = os.getenv("LLM_TEMPERATURE")
_TTS_TEMPERATURE     = float(os.getenv("TTS_TEMPERATURE", "0.75"))

# Persona gender → Sarvam bulbul voice. Both ids verified against the Sarvam speaker list.
_SPEAKER_BY_GENDER = {
    "female": os.getenv("TTS_SPEAKER_FEMALE", "simran"),
    "male":   os.getenv("TTS_SPEAKER_MALE", "amit"),
}

# Appended to the system prompt for a male agent. The base prompt (bot.py) hardcodes a
# "female, use feminine forms" rule; this overrides it late so masculine forms win. Only
# added when the mapped agent is male — female agents keep the base prompt untouched.
_GENDER_OVERRIDE_MALE = (
    "\n\n━━━ GENDER OVERRIDE (MANDATORY — REPLACES ANY EARLIER GENDER RULE) ━━━\n\n"
    "You are MALE. This OVERRIDES every earlier statement that the agent is female. "
    "Every first-person verb and adjective MUST use MASCULINE Hindi forms:\n"
    "  ✓ बोल रहा हूँ   ✗ बोल रही हूँ\n"
    "  ✓ समझ गया      ✗ समझ गई\n"
    "  ✓ करूंगा        ✗ करूंगी\n"
    "  ✓ देख रहा हूँ    ✗ देख रही हूँ\n"
    "Never use a feminine self-reference, even in informal speech or identity answers."
)

# System prompt extra
_SYSTEM_PROMPT_EXTRA = os.getenv("SYSTEM_PROMPT_EXTRA", "")

# MongoDB
_MONGO_URI        = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
_MONGO_DB         = os.getenv("MONGO_DB", "ai_lead_qualify")
_MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "call_transcripts")

# Backend URL (call-log save)
_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Import reusable helpers from bot.py — then override DB / URL globals below
# ---------------------------------------------------------------------------
from bot import (
    # Config / data helpers
    fetch_bot_config,
    record_fallback_event,
    FALLBACK_REASON_NO_IDS,
    normalize_mobile,
    fetch_lead,
    _build_sample_from_search,
    _execute_function_call,
    call_configured_function,
    _get_http_session,
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
    CATEGORY_CHANGE_API as _BOT_CATEGORY_CHANGE_API,
    IST,
    HINDI_LANG_CONFIG,
    INACTIVITY_PHRASE,
    INACTIVITY_END_PHRASE,
    _get_mongo_collection as _bot_get_mongo_collection,
)
import bot as _bot_module
from agent_resolver import render_greeting, resolve_agent_config
from custom_functions import (
    apply_store_variables,
    build_http_call,
    interpolate_vars,
    resolve_timeout_seconds,
    run_lifecycle_functions,
)
from custom_function_tools import build_during_call_tools

# Override bot.py globals so all calls in this process use the parameterised values
_bot_module.MONGO_URI        = _MONGO_URI
_bot_module.MONGO_DB         = _MONGO_DB
_bot_module.MONGO_COLLECTION = _MONGO_COLLECTION
_bot_module.BACKEND_URL      = _BACKEND_URL
_bot_module._mongo_client    = None  # force client rebuild with new URI on first call

# Convenience aliases (used throughout entrypoint below)
MONGO_URI        = _MONGO_URI
MONGO_DB         = _MONGO_DB
MONGO_COLLECTION = _MONGO_COLLECTION

def _get_mongo_collection():
    return _bot_module._get_mongo_collection()


# Dev API — overrides the live URLs imported from bot.py
MIS_API_BASE        = os.getenv("MIS_API_BASE", "http://192.168.14.101:3006")
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
            """Thin v2-compat wrapper over Langfuse v3/v4 start_observation API."""

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
_BOT_PORT = os.environ.get("BOT_PIPELINE_PORT", os.environ.get("BOT_PORT", str(_PORT)))
_LOG_DIR = os.path.join(
    os.environ.get("BOT_LOG_DIR", "/home/yogeshv_10011835/voicebot_nodcode_platform/logs/"),
    f"{_BOT_PORT}_pipeline",
)
os.makedirs(_LOG_DIR, exist_ok=True)


def _log_format(record: dict) -> str:
    caller = record["extra"].get("caller", "")
    caller_col = f"{caller:<15} | " if caller else (" " * 17)
    return "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | " + caller_col + "{message}\n"


logger.add(
    os.path.join(_LOG_DIR, "{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    compression="gz",
    level="INFO",
    enqueue=True,
    format=_log_format,
)

_CONSOLE_KEYWORDS = (
    "[TRANSCRIPT]", "[MUTED-CAPTURE]", "[LATENCY]",
    "[CALL START]", "[CALL END]", "[GREETING]",
    "[IVR]", "[CLOSE DETECT]", "[INACTIVITY]",
)


def _console_filter(record: dict) -> bool:
    if record["level"].no >= 30:
        return True
    return any(kw in record["message"] for kw in _CONSOLE_KEYWORDS)


logger.remove(0)
logger.add(
    sys.stderr,
    level="INFO",
    filter=_console_filter,
    format=_log_format,
    colorize=False,
)


# ---------------------------------------------------------------------------
# Prewarm
# ---------------------------------------------------------------------------
def prewarm_fnc(proc) -> None:
    pass


# ---------------------------------------------------------------------------
# Cached TTS hold-message frames
# ---------------------------------------------------------------------------
_HOLD_FRAMES: list[rtc.AudioFrame] = []

async def _preload_hold_message(tts_instance) -> None:
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
    _bot_id_meta = _room_meta_raw.get("bot_id", "") or _assistant_id
    _test_version_meta = _room_meta_raw.get("test_bot_version_id", "")
    # fetch_bot_config(bot_id, test_version) loads a specific version's config — used by the
    # dashboard test flow, which passes both in room metadata. Live number-attached calls
    # instead resolve config via the number→bot mapping (resolve_agent_config, below).
    if _bot_id_meta and _test_version_meta:
        _bc, _fallback_reason = await fetch_bot_config(_bot_id_meta, _test_version_meta)
    else:
        _bc, _fallback_reason = None, FALLBACK_REASON_NO_IDS
    _bot_config: dict = _bc or _HARDCODED_BOT_CONFIG
    if _bc is None:
        record_fallback_event(
            room_name=room_name, bot_id=_bot_id_meta, test_bot_version_id=_test_version_meta,
            reason=_fallback_reason, worker=_AGENT_NAME,
        )
        _log.warning(f"[CONFIG] Falling back to hardcoded assistant — reason={_fallback_reason!r}")

    _prefetched_lead = await _early_lead_task if _early_lead_task is not None else None

    # ── Workflow bots (visual graph, backend/routers/workflow_bots.py) bypass the
    # rest of this fixed-assistant entrypoint entirely — run_workflow_call builds
    # its own AgentSession from bot_config["workflow"] and drives the whole call. ──
    if (_bot_config or {}).get("bot_type") == "workflow":
        await run_workflow_call(
            ctx, _bot_config, _prefetched_lead, room_name=room_name,
        )
        return

    _api_urls = _bot_config.get("api_urls") or {}
    _mis_api_base = _api_urls.get("mis_api_base") or MIS_API_BASE
    _category_change_api = _api_urls.get("category_change_api") or CATEGORY_CHANGE_API
    _language = _LANGUAGE_KEY
    _temperature = float(
        _LLM_TEMPERATURE_ENV if _LLM_TEMPERATURE_ENV is not None
        else (_bot_config.get("temperature") or 0.4)
    )
    _max_call_duration = 300
    _silero_threshold = float(_bot_config.get("silero_threshold") or 0.6)
    _silero_min_speech_ms = int(_bot_config.get("silero_min_speech_ms") or 1000)
    _post_speech_hold_ms = int(_bot_config.get("post_speech_hold_ms") or 400)
    _inactivity_first_rescue_secs = float(_bot_config.get("inactivity_first_rescue_secs") or 4.0)
    _inactivity_first_nudge_gap_secs = float(_bot_config.get("inactivity_first_nudge_gap_secs") or 4.0)
    _inactivity_nudge_secs = float(_bot_config.get("inactivity_nudge_secs") or 10.0)
    _inactivity_close_secs = float(_bot_config.get("inactivity_close_secs") or 5.0)
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
    _caller_identity = ""

    # ── Langfuse trace ──
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
                    "model_llm": _LLM_MODEL,
                    "model_stt": f"{_STT_MODEL}-codemix",
                    "model_tts": _TTS_MODEL,
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

    # Resolve the mapped agent up front so its gender drives the prompt + TTS voice below.
    # dialed_number is still empty this early; resolution falls back to the room prefix,
    # which is the path that actually works on these trunks anyway. Reused for the greeting
    # at step 13 so prompt, voice, and greeting all agree on one config.
    _mapped_config = None
    try:
        _mapped_config = resolve_agent_config(room_name=room_name)
    except Exception as _agent_exc:
        _log.warning(f"[AGENT] early lookup failed: {_agent_exc}")
    # Fall back to the metadata-loaded config (dashboard test calls, which have no number
    # mapping) so persona/greeting reflect the chosen bot, not the hardcoded default.
    _persona_gender = ((_mapped_config or _bot_config or {}).get("persona_gender") or "female").lower()
    _log.info(f"[AGENT] persona_gender={_persona_gender!r}")

    # ── 3c. Custom functions (from the bot's saved config) ──
    # Source functions from the mapped agent config (number→bot→published version) for live
    # calls, falling back to the metadata-resolved config. This is where the dashboard-saved
    # custom functions live (config.functions).
    _fn_config = _mapped_config or _bot_config
    _functions: list[dict] = list(_fn_config.get("functions") or [])
    _function_calling = bool(_fn_config.get("function_calling", False)) and bool(_functions)

    # Pre-call custom functions: run before the greeting; their store_variables land in
    # call_state["vars"] for {{var}} interpolation into the prompt AND the opening line.
    # Guarded so a misconfigured API never fails the call.
    # Seed the call's variable bag with the identifiers both lifecycle hooks and during-call
    # tools need. setdefault, not assignment: a pre_call function's store_variables may later
    # overwrite these, and re-seeding must never clobber that.
    _call_vars = call_state.setdefault("vars", {})
    _call_vars.setdefault("lead_id", _lead_id_meta or (call_state.get("record_id") or ""))
    _call_vars.setdefault("mobile", _room_mobile)
    _call_vars.setdefault("call_id", call_state.get("call_id") or room_name)

    _pre_call_params = {
        "lead_id": _call_vars["lead_id"],
        "mobile": _call_vars["mobile"],
        "call_id": _call_vars["call_id"],
    }
    # Test-call only: tester-seeded overrides for query_params this bot's own pre_call
    # functions define beyond the fixed lead_id/mobile/call_id trio above (dashboard's
    # dynamic "Test Call Parameters" modal). Real production calls never carry this
    # metadata key, so this is a no-op for them.
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

    # ── 4. Build system instruction ──
    system_instruction = build_system_prompt(
        _prefetched_lead, lang_key=_language, bot_config=_bot_config
    )
    if _persona_gender == "male":
        system_instruction = system_instruction + _GENDER_OVERRIDE_MALE
    if _SYSTEM_PROMPT_EXTRA:
        system_instruction = system_instruction + "\n\n" + _SYSTEM_PROMPT_EXTRA

    # Fill {{variables}} from pre_call functions into the system prompt.
    if call_state.get("vars"):
        system_instruction = interpolate_vars(system_instruction, call_state["vars"])

    try:
        _sch = (_prefetched_lead or {}).get("qualification_schema") or {}
        _qs = _sch.get("question") or []
        _log.info(
            f"[QUESTIONS] backend returned {len(_qs)} qualification question(s) "
            f"for catname={(_prefetched_lead or {}).get('catname')!r}"
        )
        for _i, _q in enumerate(_qs, 1):
            _log.info(f"[QUESTIONS]   Q{_i}: {_q.get('text', '').strip()!r}")
        if _qs:
            _log.info(
                f"[QUESTIONS] raw API search_result JSON: "
                f"{json.dumps(_sch, ensure_ascii=False)}"
            )
    except Exception as _q_ex:
        _log.warning(f"[QUESTIONS] could not read qualification_schema: {_q_ex}")

    # ── 5. Pipeline plugins: provider-selectable via pipeline_providers.py, so a bot's
    # stt_provider/tts_provider/llm_provider (Deepgram, ElevenLabs, our own IndicF5 "justdial"
    # TTS, OpenAI, ...) actually take effect here — previously this block hardcoded Sarvam
    # STT / Gemini LLM / Sarvam TTS inline and never read those fields at all. A bot with no
    # provider fields set still gets exactly the same hardcoded stack (see
    # tests/test_pipeline_providers.py's backward-compatibility tests). ──
    _gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    _log.info(f"[LLM] Using GEMINI_API_KEY ...{_gemini_api_key[-6:] if _gemini_api_key else 'NOT SET'}")
    _log.info(f"[CONFIG] language={_language!r} stt={_STT_LANGUAGE_CODE!r} tts={_TTS_LANGUAGE_CODE!r} "
              f"llm_model={_LLM_MODEL!r} stt_model={_STT_MODEL!r} tts_model={_TTS_MODEL!r} "
              f"llm_temp={_temperature} tts_temp={_TTS_TEMPERATURE} "
              f"mongo_db={_MONGO_DB!r} mongo_collection={_MONGO_COLLECTION!r}")

    # This env's legacy hardcoded defaults (language/model/speaker) fill in only where the
    # bot's own config doesn't already set them, so existing bots/env overrides keep working.
    _provider_cfg = dict(_bot_config)
    _provider_cfg["temperature"] = _temperature
    _provider_cfg.setdefault("stt_language", _STT_LANGUAGE_CODE)
    _provider_cfg.setdefault("stt_model", _STT_MODEL)
    _provider_cfg.setdefault("tts_language", _TTS_LANGUAGE_CODE)
    _provider_cfg.setdefault("tts_model", _TTS_MODEL)
    _provider_cfg.setdefault("llm_model", _LLM_MODEL)
    if not _provider_cfg.get("tts_voice"):
        _provider_cfg["tts_voice"] = _SPEAKER_BY_GENDER.get(_persona_gender, "simran")
    if (_provider_cfg.get("tts_provider") or "sarvam").lower() == "sarvam":
        _provider_cfg.setdefault("tts_options", {})
        _provider_cfg["tts_options"] = {"temperature": _TTS_TEMPERATURE, **_provider_cfg["tts_options"]}

    stt = build_stt(_provider_cfg)
    llm = build_llm(_provider_cfg)
    tts = build_tts(_provider_cfg)

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
        _muted_inject["text"] = ""
        if _pending_user_text and (
            not _live_transcript or _live_transcript[-1].get("text") != _pending_user_text
        ):
            _live_transcript.append({"role": "user", "text": _pending_user_text})
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
        _avg_latency_ms = (
            round(sum(_response_latencies) / len(_response_latencies))
            if _response_latencies else 0
        )

        _log.info(_SEP)
        _log.info(
            f"[CALL END] room={room_name} | status={status!r} | "
            f"duration={_duration}s | lead_id={lead_id!r} | "
            f"avg_latency={_avg_latency_ms}ms over {len(_response_latencies)} turn(s)"
        )
        _log.info(_SEP)

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
            "greeting_retry": False,
            "gemini_connect_failed": False,
            "greeting_done": _greeting_done,
            "user_speech_ms": round(_muted_capture.get("speech_ms", 0.0)),
            "wrong_opener_detected": False,
            "turn_count": _turn_counter,
            "avg_response_latency_ms": (
                round(sum(_response_latencies) / len(_response_latencies))
                if _response_latencies else 0
            ),
            "response_latencies_ms": _response_latencies,
            "tagged": False,
            "tagged_at": None,
            "created_at": datetime.now(timezone.utc),
            # record which param config was active for this call
            "_param_config": {
                "language": _language,
                "stt_language_code": _STT_LANGUAGE_CODE,
                "tts_language_code": _TTS_LANGUAGE_CODE,
                "llm_model": _LLM_MODEL,
                "stt_model": _STT_MODEL,
                "tts_model": _TTS_MODEL,
                "llm_temperature": _temperature,
                "tts_temperature": _TTS_TEMPERATURE,
                "mongo_db": _MONGO_DB,
                "mongo_collection": _MONGO_COLLECTION,
            },
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

    _save_done_event = asyncio.Event()

    async def _save_and_close(status: str) -> None:
        _was_done = call_state["save_done"]
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
    _pending_assistant_text: str = ""

    _current_window_pcm: bytearray = bytearray()
    _silero_rejected_turns: set = set()
    _muted_capture: dict = {"frames": [], "speech_ms": 0.0}
    _muted_inject: dict = {"text": ""}
    _muted_inject_sent_time: float = 0.0
    _muted_capture_empty_time: float = 0.0
    _muted_filler_dropped_time: float = 0.0
    _silero_rejected_time: float = 0.0
    _consecutive_silero_rejects: int = 0
    _muted_transcript_log: list = []
    _close_status = "completed"
    _abusive_detected = False
    _pipeline_delay_buffer: str = ""

    async def _handle_close() -> None:
        nonlocal _call_ended
        _call_ended = True
        _cancel_inactivity()
        await asyncio.sleep(2)
        await _kick_caller_safe()
        asyncio.create_task(_save_and_close(_close_status))

    # ── IVR / voicemail detection ──
    _IVR_BUSY_MARKERS = [
        "इस समय व्यस्त", "बाद में call", "बाद में कॉल", "नंबर अभी busy",
        "व्यस्त हैं", "available नहीं", "कृपया थोड़ी देर बाद",
        "स्विच ऑफ", "switch off", "switched off",
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
        "प्लीज ट्राई", "करेंटली बिजी", "इज नॉट अवेलेबल",
        "प्लीज रिप्लाई", "प्लीज लीव", "लीव ए मैसेज",
        "आफ्टर द टोन", "नॉट अवेलेबल", "नॉट रीचेबल",
        "रीजन फॉर कॉलिंग", "रीज़न फॉर कॉलिंग",
        "पर्सन इज अवेलेबल", "पर्सन यू आर ट्राइंग",
        "स्टे ऑन द लाइन", "रिकॉर्ड योर मैसेज",
        "पिक अप द कॉल", "लीव योर मैसेज",
        "फिनिशड योर", "ट्राइंग टू रीच", "ट्राइंग टू रीडायरेक्ट",
        "फोन आई है",
        "व्यस्त छे", "थोड़ा क्षणों",
    ]

    def _is_ivr_message(text: str) -> bool:
        n = unicodedata.normalize("NFC", text).lower()
        return any(m.lower() in n for m in _IVR_BUSY_MARKERS)

    _STT_FILLER_TOKENS = {
        unicodedata.normalize("NFC", w) for w in {
            "a", "e", "o", "i",
            "hmm", "hm", "हम्म",
        }
    }

    _GEMINI_CALIBRATION_HALLUCINATIONS: set[str] = {
        unicodedata.normalize("NFC", p) for p in {
            "ए बी सी", "वन टू थ्री फोर", "वन टू थ्री", "वन टू",
            "a b c", "one two three four", "one two three",
        }
    }

    _GEMINI_SHORT_NOISE_TOKENS: set[str] = {
        unicodedata.normalize("NFC", w) for w in {
            "हूं", "हूँ", "ऊं", "उम",
            "uh", "um", "ugh",
            "पाठ",
        }
    }

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
    _SPACE_REPEAT_RE = re.compile(r'(?:^|\s)(\S{2,})(?:\s+\1){3,}(?=\s|$)', re.UNICODE)

    def _is_bystander_speech(text: str) -> bool:
        normalized = unicodedata.normalize("NFC", text)
        if any(marker in normalized for marker in _BYSTANDER_SPEECH_MARKERS):
            return True
        if _BYSTANDER_HYPHEN_REPEAT_RE.search(normalized):
            return True
        return False

    def _is_repetitive_babble(text: str) -> bool:
        normalized = unicodedata.normalize("NFC", text)
        return bool(_SPACE_REPEAT_RE.search(normalized))

    def _normalize_stt_tokens(text: str) -> list[str]:
        text = unicodedata.normalize("NFC", text).lower()
        cleaned = "".join(
            c for c in text
            if unicodedata.category(c)[0] in ("L", "N", "M") or c.isspace()
        )
        return [t for t in cleaned.split() if t]

    _SELLER_MANUFACTURE_TOKENS = {
        "banate", "banaate", "banata", "banaata", "banati", "banaati",
        "बनाते", "बनाता", "बनाती", "बनाते हैं", "बनाता हूँ",
        "bechte", "bechta", "बेचते", "बेचता",
    }

    _SHORT_TERMINAL_TOKENS: frozenset = frozenset(unicodedata.normalize("NFC", w) for w in {
        "जी", "हाँ", "हां", "हा", "ना", "नहीं", "नहि",
        "yes", "no", "ok", "okay", "हाँजी", "हांजी",
        "ठीक", "बिल्कुल", "सही", "sure", "bilkul",
    })

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
    _last_user_final_time: float = 0.0
    _first_partial_time: float = 0.0
    _thinking_start_time: float = 0.0
    _response_latencies: list = []

    async def _buffer_user_audio(track: rtc.RemoteAudioTrack) -> None:
        nonlocal _current_window_pcm
        _MAX_WINDOW = 160_000
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        async for ev in stream:
            if _call_ended:
                break
            chunk = bytes(ev.frame.data)
            frame_ms = len(chunk) / 2 / 16_000 * 1000
            if not _mic_enabled:
                _muted_capture["frames"].append(chunk)
                _muted_capture["speech_ms"] += frame_ms
            else:
                _current_window_pcm += chunk
                _overflow = len(_current_window_pcm) - _MAX_WINDOW
                if _overflow > 0:
                    del _current_window_pcm[:_overflow]

    async def _transcribe_muted_period(frames: list, speech_ms: float) -> None:
        nonlocal _call_ended, _muted_capture_empty_time, _muted_filler_dropped_time
        if not frames:
            return
        if not SARVAM_API_KEY:
            _log.warning("[MUTED-CAPTURE] SARVAM_API_KEY not set — skipping transcription")
            return
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
            form.add_field("language_code", _STT_LANGUAGE_CODE)
            form.add_field("model", _STT_MODEL)
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
        if len(tokens) >= 8 and len(set(tokens)) <= 2:
            _log.info(
                f"[MUTED-CAPTURE] repeated-token hallucination {text!r} — dropped"
            )
            _muted_filler_dropped_time = asyncio.get_event_loop().time()
            return
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
        await asyncio.sleep(timeout)
        if _call_ended or _closing_triggered or _last_user_final_turn != turn:
            return
        if _speaking_turns_completed > speaking_count_at_start:
            return
        try:
            _agent_s_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
            if _agent_s_val in ("thinking", "speaking"):
                _log.info(
                    f"[LLM-WATCHDOG] Bot currently {_agent_s_val} — suppressing re-inject (turn={turn})"
                )
                return
        except Exception as e:
            _log.warning(
                f"[LLM-WATCHDOG] Could not read agent_state — suppressing re-inject (turn={turn}): {e}"
            )
            return
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
        nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn
        await asyncio.sleep(timeout)
        if _call_ended or _closing_triggered or not _greeting_done:
            return
        if _pending_user_text != text:
            return
        try:
            _agent_s_val = getattr(session.agent_state, "value", None) or str(session.agent_state)
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
    def _interpolate_cfg(value, variables: dict):
        """Recursively fill {{var}} placeholders in a config value (str / dict / list)."""
        if isinstance(value, str):
            return interpolate_vars(value, variables)
        if isinstance(value, dict):
            return {k: _interpolate_cfg(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [_interpolate_cfg(v, variables) for v in value]
        return value

    async def _execute_custom_function_call(
        fn_name: str, fn_args: dict, functions: list[dict], call_state: dict
    ) -> dict:
        """During-call twin of call_configured_function.

        bot.py's _execute_function_call hand-rolls its request and drops `custom_body`
        entirely, so a dashboard-configured static body never reaches the API. This routes
        during-call tools through the same build_http_call/timeout/store_variables path the
        pre_call hook uses, and adds {{var}} interpolation so a URL, header or body field can
        reference identifiers and values stored by earlier functions.

        Built-ins (FetchLead / FetchCategorySchema) deliberately keep bot.py's handler — it
        does product-change bookkeeping this must not duplicate.
        """
        fn_cfg = next((f for f in functions or [] if f.get("name") == fn_name), None)
        if not fn_cfg:
            return {"error": f"Function {fn_name!r} not configured"}

        variables = call_state.setdefault("vars", {})
        cfg = dict(fn_cfg)

        # Legacy configs stored custom_body as a JSON string; normalize before merging.
        body = cfg.get("custom_body")
        if isinstance(body, str):
            try:
                cfg["custom_body"] = json.loads(body)
            except Exception:
                cfg["custom_body"] = {}

        for field in ("url", "headers", "query_params", "custom_body"):
            if field in cfg:
                cfg[field] = _interpolate_cfg(cfg[field], variables)

        # Unlike the pre_call path, falsy args are NOT filtered out: the LLM passing
        # quantity=0 mid-call means zero, not "unset".
        call = build_http_call(cfg, _interpolate_cfg(fn_args or {}, variables))
        timeout = aiohttp.ClientTimeout(total=resolve_timeout_seconds(cfg, default=8.0))
        _log.info(
            f"[FnCall/custom] {call['method']} {call['url']} | args={fn_args} "
            f"| timeout={timeout.total}s"
        )
        try:
            http = _get_http_session()
            async with http.request(
                call["method"], call["url"], headers=call["headers"],
                params=call["params"], json=call["json"], data=call["data"], timeout=timeout,
            ) as resp:
                result = await resp.json(content_type=None)
        except Exception as exc:
            _log.error(f"[FnCall/custom] {fn_name} failed: {exc}")
            return {"error": str(exc)}

        stored = apply_store_variables(cfg.get("store_variables"), result, variables)
        if stored:
            _log.info(f"[FnCall/custom] {fn_name} stored vars: {list(stored)}")
        return result

    def _play_hold_message(tool_ctx: RunContext):
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
        try:
            _sch = (call_state.get("lead_record") or {}).get("qualification_schema") or {}
            _qs = _sch.get("question") or []
            _log.info(
                f"[QUESTIONS] FetchCategorySchema(srchterm={srchterm!r}) → "
                f"{result.get('total_questions', len(_qs))} question(s) for "
                f"product={result.get('product', '?')!r}"
            )
            for _i, _q in enumerate(_qs, 1):
                _log.info(f"[QUESTIONS]   Q{_i}: {_q.get('text', '').strip()!r}")
            _log.info(
                f"[QUESTIONS] raw API search_result JSON: "
                f"{json.dumps(_sch, ensure_ascii=False)}"
            )
        except Exception as _q_ex:
            _log.warning(f"[QUESTIONS] could not read FetchCategorySchema questions: {_q_ex}")
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
    _LATENCY_HINT = (
        "\n\nRESPONSE SPEED RULE (mandatory): Start every reply with a 1–3 word Hindi "
        "acknowledgment ONLY — e.g. 'जी,', 'हाँ,', 'बिल्कुल,', 'ठीक है,' — on its own "
        "before the full answer. Never skip this opener. This is required so the caller "
        "hears audio immediately while the rest of the response is still being generated."
    )
    system_instruction = system_instruction + _LATENCY_HINT

    tools = [FetchCategorySchema, FetchLead] if _function_calling else []

    # Register user-defined during_call custom functions as LLM tools (from config.functions).
    # Gated on _functions alone, NOT _function_calling: that flag toggles the built-in MIS
    # tools above, and a bot with custom functions but the flag off used to register nothing
    # while its pre_call hooks still ran — silently, with no log line to explain it.
    if _functions:
        _dynamic_tools = build_during_call_tools(_functions, _execute_custom_function_call, call_state)
        if _dynamic_tools:
            _log.info(f"[FnCall] registered {len(_dynamic_tools)} custom during-call tool(s): "
                      f"{[t.info.name for t in _dynamic_tools]}")
            tools += _dynamic_tools
        else:
            _log.info(f"[FnCall] no during-call tools registered from {len(_functions)} "
                      f"configured function(s) — check trigger/enabled/name")

    _TTS_SPEAKABLE_RE = re.compile(r"[A-Za-z0-9ऀ-ॿ]")

    class _TTSSanitizingAgent(Agent):
        async def tts_node(self, text, model_settings):
            async def _clean():
                depth = 0
                dropped: list[str] = []
                async for chunk in text:
                    if not chunk:
                        continue
                    keep, drop = [], []
                    for ch in chunk:
                        if ch == "(":
                            depth += 1
                            drop.append(ch)
                        elif ch == ")":
                            drop.append(ch)
                            if depth > 0:
                                depth -= 1
                        elif depth > 0:
                            drop.append(ch)
                        else:
                            keep.append(ch)
                    cleaned = "".join(keep)
                    if cleaned and (cleaned.isspace() or _TTS_SPEAKABLE_RE.search(cleaned)):
                        yield cleaned
                    elif cleaned:
                        drop.append(cleaned)
                    if drop:
                        dropped.append("".join(drop))
                if dropped:
                    _log.info(f"[TTS-SANITIZE] removed non-speakable text from TTS: {''.join(dropped)!r}")

            async for frame in Agent.default.tts_node(self, _clean(), model_settings):
                yield frame

    agent = _TTSSanitizingAgent(instructions=system_instruction, tools=tools)
    session = AgentSession(
        stt=stt, llm=llm, tts=tts,
        turn_detection="stt",
        min_endpointing_delay=0.05,
    )

    # ── 9. Event handlers ──

    @session.on("error")
    def _on_session_error(event) -> None:
        err = event.error
        tag = {
            "tts_error": "[TTS-ERROR]",
            "stt_error": "[STT-ERROR]",
            "llm_error": "[LLM-ERROR]",
        }.get(getattr(err, "type", ""), "[PIPELINE-ERROR]")
        status = "retry" if getattr(err, "recoverable", False) else "EXHAUSTED (all retries failed)"
        _log.error(f"{tag} {status}: {getattr(err, 'error', err)}")

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
        if _muted_inject["text"]:
            _log.info(
                f"[MUTED-CAPTURE] live speech arrived — discarding muted buffer "
                f"{_muted_inject['text']!r}"
            )
            if _muted_transcript_log and _muted_transcript_log[-1] == _muted_inject["text"]:
                _muted_transcript_log.pop()
            _muted_inject["text"] = ""
        if is_final:
            if _agent_state_now == "speaking" and not _mic_enabled and not _barge_in_fired:
                _pd_tokens = _normalize_stt_tokens(transcript_text) if transcript_text else []
                _pd_distinct = len(set(_pd_tokens))
                if _pd_distinct >= 2 and not _pipeline_delay_buffer:
                    _log.info(
                        f"[STT] FINAL during muted-speaking — buffering for post-speak inject: "
                        f"{transcript_text!r} ({_pd_distinct} distinct words)"
                    )
                    _pipeline_delay_buffer = transcript_text
                else:
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
            if _lf_trace and _first_partial_time > 0:
                try:
                    _stt_latency_ms = (asyncio.get_event_loop().time() - _first_partial_time) * 1000
                    _lf_trace.span(
                        name=f"stt-turn-{_turn_counter}",
                        input={"speech_ms": round(speech_ms_now)},
                        output={"transcript": transcript_text},
                        metadata={
                            "latency_ms": round(_stt_latency_ms),
                            "model": f"{_STT_MODEL}-codemix",
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
            _pcm_snapshot = bytes(_current_window_pcm)
            _current_window_pcm = bytearray()
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
                    _consecutive_silero_rejects = 0
                _log.info(f"[STT] Silero confirmed FINAL (voiced_ms={_voiced:.0f})")
                _consecutive_silero_rejects = 0
            _norm_transcript = unicodedata.normalize("NFC", transcript_text.strip())
            if _norm_transcript in _GEMINI_CALIBRATION_HALLUCINATIONS:
                _log.info(f"[NOISE] Calibration hallucination discarded: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                _silero_rejected_time = asyncio.get_event_loop().time()
                return
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
            if _is_bystander_speech(transcript_text):
                _log.info(f"[NOISE] Bystander speech discarded: {transcript_text!r}")
                if _live_transcript and _live_transcript[-1]["role"] == "user":
                    _live_transcript.pop()
                _silero_rejected_turns.add(transcript_text)
                _silero_rejected_time = asyncio.get_event_loop().time()
                return
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

            _silero_rejected_time = 0.0
            _consecutive_silero_rejects = 0
            _user_entry: dict = {"role": "user", "text": transcript_text}
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1] = _user_entry
            else:
                _live_transcript.append(_user_entry)
            nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn
            nonlocal _stale_partial_task, _last_user_turn_time, _final_arrived_while_speaking
            nonlocal _last_user_final_time
            _last_user_turn_time = asyncio.get_event_loop().time()
            _last_user_final_time = _last_user_turn_time
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
            _log.info(
                f"[TRANSCRIPT] PARTIAL | USER: {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled}"
            )
            if _is_ivr_message(transcript_text) and not _call_ended:
                _log.info(f"[IVR] busy-line/voicemail in PARTIAL — ending call: {transcript_text!r}")
                _live_transcript.append({"role": "ivr", "text": transcript_text})
                _call_ended = True
                call_state["ended_naturally"] = True
                asyncio.create_task(_kick_caller_safe())
                asyncio.create_task(_save_and_close("ivr_detected"))
                return
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
                _first_partial_time = asyncio.get_event_loop().time()
            _pending_user_text = transcript_text
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1]["text"] = transcript_text
            else:
                _live_transcript.append({"role": "user", "text": transcript_text})
            if _stale_partial_task and not _stale_partial_task.done():
                _stale_partial_task.cancel()
            if _greeting_done and not _call_ended and not _closing_triggered:
                _stale_partial_task = asyncio.create_task(
                    _stale_partial_watchdog(
                        transcript_text, speaking_count_at_start=_speaking_turns_completed,
                    )
                )
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
                        _silero_rejected_time = 0.0
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
        nonlocal _pipeline_delay_buffer
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
                if _lf_trace:
                    try:
                        _lf_trace.span(
                            name=f"e2e-turn-{_turn_n}",
                            input={"user": _last_user_final_text},
                            metadata={
                                "latency_ms": round(_latency_ms),
                                "avg_latency_ms": round(_avg_ms),
                                "turn": _turn_n,
                            },
                        ).end()
                        if _thinking_start_time > 0:
                            _llm_ms = (_speaking_start_time - _thinking_start_time) * 1000
                            _lf_trace.span(
                                name=f"llm-tts-ttfb-turn-{_turn_n}",
                                input={"user": _last_user_final_text},
                                metadata={
                                    "latency_ms": round(_llm_ms),
                                    "model": _LLM_MODEL,
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
            if (
                _greeting_done
                and _silero_rejected_time > 0
                and (_now_eg - _silero_rejected_time) < 4.0
            ):
                if _consecutive_silero_rejects >= 2:
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
            if (
                _greeting_done
                and _muted_filler_dropped_time > 0
                and (_now_eg - _muted_filler_dropped_time) < 0.8
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
            if old_str == "speaking":
                _speaking_turns_completed += 1
                if _pipeline_delay_buffer and not _barge_in_fired and not _call_ended and not _closing_triggered:
                    _inject_text = _pipeline_delay_buffer
                    _pipeline_delay_buffer = ""
                    _log.info(f"[STT] injecting pipeline-delay buffer after speak: {_inject_text!r}")
                    try:
                        session.generate_reply(user_input=_inject_text)
                    except Exception as _e:
                        _log.warning(f"[STT] pipeline-delay inject failed: {_e}")
                else:
                    _pipeline_delay_buffer = ""
                if _lf_trace and _greeting_done and speaking_duration > 0.1:
                    try:
                        _lf_trace.span(
                            name=f"tts-turn-{_speaking_turns_completed}",
                            metadata={
                                "duration_ms": round(speaking_duration * 1000),
                                "model": _TTS_MODEL,
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
            elif (
                old_str == "thinking"
                and not _call_ended and not _closing_triggered
                and _last_user_final_text
            ):
                if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                    _bot_resp_watchdog_task.cancel()
                _bot_resp_watchdog_task = asyncio.create_task(
                    _bot_response_watchdog(
                        _last_user_final_text, _last_user_final_turn, timeout=1.0,
                        speaking_count_at_start=_speaking_turns_completed,
                    )
                )
                _log.info(
                    f"[LLM-WATCHDOG] generation cancelled mid-thinking — fast re-inject "
                    f"scheduled (turn={_last_user_final_turn}): {_last_user_final_text!r}"
                )
            if _bot_has_spoken and not _greeting_done:
                _greeting_done = True
                _log.info(f"[STATE] greeting_done → True (call_ended={_call_ended})")
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
                        _muted_inject["text"] = ""
                        _log.info(f"[MUTED-CAPTURE] post-greeting inject → LLM: {text!r}")
                        try:
                            session.generate_reply(user_input=text)
                            nonlocal _muted_inject_sent_time
                            _muted_inject_sent_time = asyncio.get_event_loop().time()
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
            _cancel_inactivity()
            _thinking_start_time = asyncio.get_event_loop().time()
            if old_str == "listening":
                try:
                    if _kb_auto_stop_task and not _kb_auto_stop_task.done():
                        _kb_auto_stop_task.cancel()
                    if _kb_handle and not _kb_handle.done():
                        _kb_handle.stop()
                    _kb_handle = _bg_audio.play(
                        AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.55)
                    )
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

    # ── 10. Subscribe to caller audio ──
    _buffering_track_sids: set = set()

    @ctx.room.on("track_subscribed")
    def _on_track_subscribed(track, pub, participant) -> None:
        if isinstance(track, rtc.RemoteAudioTrack) and track.sid not in _buffering_track_sids:
            _buffering_track_sids.add(track.sid)
            asyncio.create_task(_buffer_user_audio(track))

    for _rp in ctx.room.remote_participants.values():
        for _rpub in _rp.track_publications.values():
            if (
                isinstance(_rpub.track, rtc.RemoteAudioTrack)
                and _rpub.track.sid not in _buffering_track_sids
            ):
                _buffering_track_sids.add(_rpub.track.sid)
                asyncio.create_task(_buffer_user_audio(_rpub.track))

    # ── Background audio ──
    _bg_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.50),
    )
    _kb_handle = None

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

    # ── 11. Read SIP info ──
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

    # ── 12. Resolve lead ──
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

    # ── 13. Build greeting ──
    # A dashboard-created agent mapped to this number owns the opening line. Nothing
    # mapped (or platform DB unreachable) falls back to the built-in persona below.
    def _build_greeting(rec: dict) -> str:
        return "हेलो, मैं Simran बोल रही हूँ Justdial से।"

    def _product_for(rec: dict) -> str:
        sc = (rec or {}).get("search_context") or {}
        return (
            (sc.get("searched_product") or {}).get("product_name")
            or sc.get("searched_keyword")
            or (rec or {}).get("catname")
            or ""
        )

    # Reuse the config resolved early at step 4 (it drove the prompt + voice) so the
    # greeting matches. Dashboard test calls (bot_id/test_bot_version_id in room metadata,
    # _bc truthy) resolve a specific draft/published version with its own initial_message —
    # that must win over the live number→bot mapping, which is empty for a WebRTC test room
    # and was silently falling through to the hardcoded built-in persona's opening line even
    # though the system prompt itself was already using the real bot's config. _mapped_config
    # is None when no agent is mapped → built-in persona.
    _greeting_text = ""
    if _bc:
        _greeting_text = render_greeting(_bot_config, product=_product_for(record))
        _log.info(
            f"[AGENT] dashboard test config persona={_bot_config.get('agent_name')!r} "
            f"org={_bot_config.get('organization_name')!r} gender={_persona_gender!r}"
        )

    if not _greeting_text and _mapped_config:
        _greeting_text = render_greeting(_mapped_config, product=_product_for(record))
        _log.info(
            f"[AGENT] mapped agent persona={_mapped_config.get('agent_name')!r} "
            f"org={_mapped_config.get('organization_name')!r} gender={_persona_gender!r}"
        )

    if not _greeting_text:
        _greeting_text = _build_greeting(record)
        _log.info("[AGENT] no config greeting — built-in persona")

    # Fill {{variables}} from pre_call custom functions into the opening line (render_greeting
    # only fills the single-brace {agent_name}/{organization_name}/{product} tokens; the
    # double-brace {{customer_name}}/{{city}}/... come from call_state["vars"]).
    if call_state.get("vars"):
        _greeting_text = interpolate_vars(_greeting_text, call_state["vars"])

    _log.info(f"[GREETING] Text: {_greeting_text!r}")

    # ── 14. Hard call timeout ──
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
        asyncio.create_task(_save_and_close("disconnected"))

    # ── 17. Speak greeting ──
    _set_mic(False, reason="greeting-start")
    _log.info("[GREETING] Starting greeting via session.say()")
    _greeting_cancelled = False
    try:
        await session.say(_greeting_text, allow_interruptions=True)
        _log.info("[GREETING] session.say() completed (TTS playout done)")
    except asyncio.CancelledError:
        _greeting_cancelled = True
        _log.warning("[GREETING] session.say() cancelled — job runner cancelled entrypoint (room disconnected during TTS stall?)")
        raise
    except Exception as _greet_exc:
        _log.warning(f"[GREETING] session.say() error: {_greet_exc}")
    finally:
        if not _greeting_done:
            _greeting_done = True
            _bot_has_spoken = True
            _log.info(f"[GREETING] Marking greeting_done=True in finally block{' (cancelled)' if _greeting_cancelled else ''}")
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
            agent_name=_AGENT_NAME,
            port=_PORT,
            num_idle_processes=_NUM_IDLE_PROCESSES,
        )
    )
