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
import sys
import time
import unicodedata
import wave
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from loguru import logger

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
_LOG_DIR = os.path.join(os.environ.get("BOT_LOG_DIR", "/home/yogeshv_10011835/voicebot_nodcode_platform/logs/"), _BOT_PORT)
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

_KEY_INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gemini_key_index")

# Per-key concurrency tracking (in-process; shared across all concurrent entrypoint coroutines).
_KEY_COOLDOWN_UNTIL: dict[str, float] = {}   # key -> epoch when it becomes usable again
_KEY_COOLDOWN_SECS = 60.0
_KEY_INFLIGHT: dict[str, int] = {}           # key -> number of active sessions using it


def _mark_key_409(key: str) -> None:
    """Quarantine a key that returned 409 for _KEY_COOLDOWN_SECS seconds."""
    _KEY_COOLDOWN_UNTIL[key] = time.time() + _KEY_COOLDOWN_SECS
    _log.info(f"[GEMINI-409] key=...{key[-6:]} cooling {_KEY_COOLDOWN_SECS:.0f}s")


def _incr_key_inflight(key: str) -> None:
    _KEY_INFLIGHT[key] = _KEY_INFLIGHT.get(key, 0) + 1


def _decr_key_inflight(key: str) -> None:
    _KEY_INFLIGHT[key] = max(0, _KEY_INFLIGHT.get(key, 0) - 1)


def _next_gemini_key() -> str:
    """Pick the least-loaded available key; skip keys that are in 409-cooldown."""
    if len(_GEMINI_LIVE_KEYS) == 1:
        return _GEMINI_LIVE_KEYS[0]
    now = time.time()
    available = [k for k in _GEMINI_LIVE_KEYS if _KEY_COOLDOWN_UNTIL.get(k, 0) <= now]
    if not available:
        # All keys cooled — pick soonest-to-recover rather than crashing the call
        available = sorted(_GEMINI_LIVE_KEYS, key=lambda k: _KEY_COOLDOWN_UNTIL.get(k, 0))
        _log.warning(
            f"[GEMINI] All {len(_GEMINI_LIVE_KEYS)} keys in cooldown — "
            f"using soonest-ready ...{available[0][-6:]}"
        )
    chosen = min(available, key=lambda k: _KEY_INFLIGHT.get(k, 0))
    return chosen

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

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

SONIOX_API_KEY = os.getenv("SONIOX_API_KEY", "")
# REST endpoint for one-shot file transcription — verify at https://soniox.com/docs
SONIOX_STT_URL = "https://api.soniox.com/v1/transcribe"

_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


import numpy as _np
from pymongo import MongoClient as _MongoClient

_mongo_client: _MongoClient | None = None


def _get_mongo_collection():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = _MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB][MONGO_COLLECTION]


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
        "You are Simran, a warm and efficient call center agent calling from Justdial. The customer recently searched for a product on Justdial. Your job is to ask them a short set of qualification questions — one at a time — so Justdial can connect them with the right sellers.\n\n"
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
        "→ Answer naturally and briefly: \"मैं Justdial से Simran बोल रही हूँ जी.\"\n"
        "→ Then re-ask current question.\n\n"
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
        "CRITICAL: If the call has NOT been explicitly switched to English by the caller, ALWAYS use the Hindi timeout/closing lines — even if you heard English words from an IVR or voicemail system.\n\n"

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
        "→ Nothing needed → \"ठीक है जी, कोई बात नहीं. Future में ज़रूरत हो तो Justdial पे call कर सकते हैं. धन्यवाद.\" → stop\n\n"
        "Unclear / partial / side question:\n"
        "→ Read intent. If clearly interested: bridge and ask Q1.\n"
        "→ If unclear: \"जी, तो क्या आपको [product] चाहिए?\"\n"
        "→ Q1 gate: do not pass until explicit confirmation.\n\n"
        "Unintelligible / garbled / clearly not a yes-no response:\n"
        "→ Do NOT treat silence, noise, STT gibberish, or an unrelated fragment as a yes.\n"
        "→ Re-ask the opening once: \"जी, तो क्या आपको [product] चाहिए?\"\n"
        "→ If still no clear answer after one re-ask → \"ठीक है जी, कोई बात नहीं. Future में ज़रूरत हो तो Justdial पे call कर सकते हैं. धन्यवाद.\" → stop.\n\n"
        "Step 2 — Questions\n"
        "In order. One per turn. No skipping, no combining.\n"
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
        "  • Quantity field: answer is a word that cannot be a number — 'kal', 'haan', 'achha', 'theek' → re-ask once with unit reminder\n"
        "    (STT mis-transcribes Hindi numbers: 'सौ' → 'So'/'To', 'चार' → 'For', 'दस' → 'बस'/'das'/'dash', 'तीन' → 'teen'/'tin', 'पाँच' → 'punch'/'panch' — if an English word or Devanagari word appears that looks like a mis-transcribed number, accept it as that number rather than re-asking)\n"
        "  • Budget field: clearly non-numeric and not a 'not sure' variant — re-ask once\n"
        "  • Answer is an obvious non-answer — sarcasm, a counter-question about something unrelated, gibberish\n"
        "  • Sarcastic/indirect: 'paidal lene aa jaana' ≠ delivery/pickup — re-ask\n\n"
        "When probing: re-ask once, naturally, different phrasing each time, short options reminder.\n"
        "If still unclear after one probe → mark Not Sure, move on. NEVER a third ask. This is a hard rule.\n"
        "COUNTING: Each question gets maximum 2 attempts total (1 original ask + 1 re-ask). After that, Not Sure, next question. No exceptions, no matter how important the answer seems.\n"
        "Never echo an answer back to 'confirm' it. Valid answer → acknowledge and continue.\n\n"

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
        "Accept any digit or Hindi number word. If the answer is a non-numeric word that cannot be a number — re-ask once with the unit.\n\n"
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

        "GRIEVANCE / COMPLAINT (CRITICAL):\n"
        "Signals — caller mentions a complaint, defective product, seller not responding, delivery not received, refund, repair, service not given, or anything framed as a complaint or problem with a past purchase:\n"
        "  'complaint hai', 'shikayat hai', 'complaint darj karni hai', 'problem aa rahi hai',\n"
        "  'kaam nahi kar raha', 'band ho gaya', 'nahi bheja', 'call nahi kar raha', 'jawab nahi deta',\n"
        "  'wapas karna hai', 'refund chahiye', 'repair karni hai', 'service nahi mili'.\n"
        "→ Do NOT try to log, register, or handle the complaint. You are a lead qualification agent — not a complaint handler.\n"
        "→ Acknowledge briefly and redirect in one sentence: \"जी, complaints के लिए आपको Justdial की website पर जाकर Customer Care section में contact करना होगा — वहाँ पूरी मदद मिलेगी.\"\n"
        "→ Then close warmly: \"आपके time के लिए धन्यवाद.\" → stop.\n"
        "→ Do NOT ask any qualification questions after a grievance signal.\n\n"

        "PERSISTENT OFF-TOPIC (buyer keeps avoiding the question):\n"
        "→ First off-topic: engage briefly with their point, then re-ask.\n"
        "→ Second off-topic on same question: re-ask once more, different phrasing.\n"
        "→ Third time with no answer: accept Not Sure, move on. Never loop more than twice on any question.\n\n"

        "━━━ HIGH-QUANTITY → BUSINESS GATE (HARD RULE) ━━━\n\n"
        "If the buyer answers a QUANTITY question with a number ≥ 100 of a BULK or COUNT unit — treat as BUSINESS automatically. Do NOT ask \"business या personal?\" ever for this caller.\n\n"
        "BULK / COUNT units where ≥ 100 triggers the gate:\n"
        "  pieces / pcs / units / numbers / sets / boxes / cartons / packets / bags / dozen / rolls\n"
        "  kg / kilogram / litre / liter / ton / tonne / quintal / bori / nag / sack / drum\n\n"
        "SMALL-MEASURE units — NEVER trigger the gate regardless of number:\n"
        "  gram / gm / g / milligram / mg / ml / millilitre / cc / cm / mm / inch / feet / metre — these are personal-scale measures.\n"
        "  Example: \"100 gram\", \"500 ml\", \"200 gm\" → do NOT treat as business. Run normal gate.\n\n"
        "Action when triggered — EXACT SEQUENCE, no deviations:\n"
        "  1. Acknowledge plainly without echoing the number: \"इतनी quantity — business के लिए होगी।\" (vary wording each call)\n"
        "     NEVER say the number back — say 'इतनी quantity' or 'इतनी बड़ी requirement', NOT '1000 kg — समझ गई'.\n"
        "  2. Immediately ask: \"आपके business का नाम क्या है?\"\n"
        "  3. Wait for the answer. Then ask: \"और कौन से city में?\"\n"
        "  4. Wait for the answer. Then continue with the remaining qualification questions from the schema, one at a time.\n"
        "  5. After ALL qualification questions are done, say the closing line.\n\n"
        "CRITICAL: Business name and city are asked RIGHT AFTER the gate triggers — not at the end. Then qualification questions resume normally.\n"
        "CRITICAL: Skip the \"business या personal?\" question for the rest of this call. It has been answered by context.\n\n"
        "If quantity is BELOW 100 of a bulk/count unit (e.g. \"5 piece\", \"50 kg\", \"10 boxes\"), OR if the unit is a small-measure (gram, ml, etc.):\n"
        "  → run the normal \"business या personal?\" gate.\n\n"
        "Phrases that ALSO trigger the gate (regardless of number or unit):\n"
        "  English: \"wholesale\", \"bulk\", \"shop\", \"factory\", \"warehouse\", \"godown\", \"B2B\", \"resale\",\n"
        "           \"retail sale\", \"food service\", \"catering\", \"restaurant\", \"hotel\", \"canteen\", \"office\", \"commercial\", \"hospital\", \"school\", \"institution\".\n"
        "  Hindi: \"कैटरिंग\", \"रिटेल\", \"रिटेल सेल\", \"फूड सर्विस\", \"रेस्टोरेंट\", \"होटल\", \"दुकान\", \"फैक्ट्री\", \"ऑफिस\", \"थोक\", \"होलसेल\", \"B2B\", \"रिसेल\", \"कैंटीन\", \"अस्पताल\", \"स्कूल\".\n\n"
        "RADIO QUESTION RULE: If a qualification question offers options and the buyer picks a clearly commercial one\n"
        "(retail sale / food service / catering / wholesale / resale / supply / distribution / restaurant / hotel / canteen / institutional — or their Hindi equivalents),\n"
        "treat it as a business trigger. 'Personal consumption' / 'ghar ke liye' / 'khud ke liye' / 'personal use' are the ONLY non-business options.\n\n"

        "Product change mid-call:\n"
        "\"आपको [original] चाहिए या [new product]?\" — wait for answer.\n\n"
        "Off-topic / irrelevant:\n"
        "Brief warm acknowledge, then re-ask: \"हाँ — तो [current question]?\"\n"
        "Persistent off-topic loop (3+ times): \"मैं सिर्फ requirements note कर रही हूँ — [current question]?\"\n\n"
        "Not interested: \"ठीक है जी, कोई बात नहीं. Future में ज़रूरत हो तो Justdial पे call कर सकते हैं. धन्यवाद.\" → stop\n"
        "Rude or hang-up: same warm close immediately\n"
        "Reschedule: \"ठीक है जी, [time] पे बात करते हैं.\" → stop\n"
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
    "initial_message": "हेलो, मैं Simran बोल रही हूँ Justdial से — आपको {product} की requirement है ना?",
    "call_end_text": "ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.",
    "function_calling": True,
    "functions": [
        {
            "name": "FetchLead",
            "description": "Fetch customer lead details from Justdial MIS API at call start.",
            "url": "http://192.168.14.101:3006/leads/ai-lead-qualify/mis",
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
    "gemini_end_sensitivity": "END_SENSITIVITY_HIGH",
    "gemini_silence_duration_ms": 800,
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


async def fetch_bot_config(assistant_id: str) -> dict | None:
    """Return hardcoded bot config (no HTTP call)."""
    return _HARDCODED_BOT_CONFIG


# ---------------------------------------------------------------------------
# Language tables
# ---------------------------------------------------------------------------

HINDI_LANG_CONFIG = {
    "name": "Hindi",
    "timeout_message": "जी, details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए धन्यवाद.",
    "lang_notes": (
        "LANGUAGE NOTES — HINDI\n\n"
        "INPUT: The buyer typically speaks Hindi, Hinglish, or Indian-accented English. If audio is unclear and no explicit language-switch has happened, assume Hindi. If the buyer clearly speaks in English or explicitly requests a language change, honour it — refer to LANGUAGE SWITCHING rules above.\n\n"
        "STYLE: Natural spoken Hinglish — how a real person talks on a call. Conversational, warm, never formal or literary.\n"
        "  Good: 'हाँ जी', 'अच्छा', 'ठीक है', 'samajh gaya', 'okay jee'\n"
        "  Avoid: 'आपकी बात सुनकर खुशी हुई', 'मैं आपकी सहायता के लिए यहाँ हूँ'\n\n"
        "FILLERS — sprinkle naturally, don't force them:\n"
        "अच्छा, हाँ, जी, तो, ठीक है — use when they fit the moment. Vary them. Don't start every response the same way.\n\n"
        "TTS: Write 'डेढ़ ton' not '1.5 ton'. Write 'ढाई ton' not '2.5 ton'.\n\n"
        "NEVER use these overly formal words:\n"
        "शयनकक्ष, बैठक कक्ष, कार्यालय, स्थापित, आवश्यकता, पर्याप्त, उपयुक्त, उचित, सूचित, प्राप्त, विवरण, अनुसार, सुविधाजनक"
    ),
}

INACTIVITY_PHRASE = "क्या आप अभी line पर हैं?"
INACTIVITY_END_PHRASE = "जी, कोई response नहीं आया, इसलिए मैं call समाप्त कर रही हूँ. अगर future में आपको किसी भी तरह की requirement हो, तो आप Justdial पर कभी भी call कर सकते हैं. धन्यवाद."

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
    if lead_id:
        params = f"lead_id={lead_id}&page=1&limit=1&ai_partner=inh-suny-bot&fromdate={today}&todate={today}"
    elif mobile:
        params = f"mobile={mobile}&page=1&limit=1&ai_partner=inh-suny-bot&fromdate={today}&todate={today}"
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
    logger.info(f"[FnCall] {method} {url} | args={merged}")

    try:
        sess = _get_http_session()
        if method == "GET":
            async with sess.get(url, params=merged, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = json.loads(await resp.text())
        else:
            async with sess.request(method, url, json=merged, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = json.loads(await resp.text())

        if fn_name == "FetchCategorySchema":
            schema = result.get("results", {}).get("search_result", {}) if isinstance(result, dict) else {}
            questions = schema.get("question", [])
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
    url = (func_config.get("url") or "").strip()
    if not url:
        return None
    method = (func_config.get("method") or "GET").upper()
    headers = func_config.get("headers") or {}
    merged = {**dict(func_config.get("query_params") or {}), **runtime_params}
    merged = {k: v for k, v in merged.items() if v}
    query_string = "&".join(f"{k}={v}" for k, v in merged.items())
    full_url = f"{url}?{query_string}" if query_string else url
    logger.info(f"[FUNC CALL] {method} {full_url}")
    try:
        session = _get_http_session()
        if method == "GET":
            async with session.get(full_url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                return await resp.json(content_type=None)
        else:
            body = func_config.get("custom_body") or {}
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = {}
            body = {**body, **runtime_params}
            if func_config.get("body_format") == "form":
                async with session.post(full_url, headers=headers, data=body, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    return await resp.json(content_type=None)
            else:
                async with session.post(full_url, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=8)) as resp:
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

    if is_business == "":
        n = len(questions)
        lines.append(f"")
        lines.append(f"[BUSINESS GATE — applies mid-call, not at end]")
        lines.append(f"If gate triggers during Q1–Q{n} (qty ≥ 100 bulk/count unit, or business keyword):")
        lines.append(f"  → Acknowledge plainly, then ask 'आपके business का नाम क्या है?' → then 'और कौन से city में?' → then resume remaining questions.")
        lines.append(f"If gate does NOT trigger:")
        lines.append(f"  → After Q{n}, ask 'एक बात और — क्या यह business के लिए है?'")
        lines.append(f"  → If YES: ask business name → city → closing.")
        lines.append(f"  → If NO/personal: go straight to closing.")

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


def build_system_prompt(record: dict | None, lang_key: str | None = None, bot_config: dict | None = None) -> str:
    _bc = bot_config or {}
    _pc = _bc.get("prompt_config") or {}
    if _bc.get("system_prompt"):
        base_prompt = _bc["system_prompt"]
    elif _PROMPT_FILE.exists():
        base_prompt = _PROMPT_FILE.read_text(encoding="utf-8")
    else:
        base_prompt = _bc.get("system_prompt", "You are Simran, a product qualification agent for Justdial.")

    cfg = _load_prompt_config()
    language_name = cfg.get("language_name") or HINDI_LANG_CONFIG["name"]

    if _pc.get("script_rule"):
        script_rule = _pc["script_rule"]
    elif cfg.get("script_rule"):
        script_rule = cfg["script_rule"]
    else:
        script_rule = f"Every word MUST be in {language_name} script ONLY."

    lang_notes = HINDI_LANG_CONFIG.get("lang_notes", "")
    lang_notes_block = f"\n\nLANGUAGE NOTES\n\n{lang_notes}\n" if lang_notes else ""

    base = (
        base_prompt.replace("{script_rule}", script_rule).replace("{language_name}", language_name)
        + lang_notes_block
    )

    _functions_cfg = _bc.get("functions") or []
    _has_fetch_schema = any(f.get("name") == "FetchCategorySchema" for f in _functions_cfg)
    if _bc.get("function_calling") and _has_fetch_schema:
        base += (
            "\n\n━━━ PRODUCT CHANGE — TOOL RULE (MANDATORY) ━━━\n\n"
            "You have access to the FetchCategorySchema function.\n"
            "When the user confirms they want a DIFFERENT product:\n"
            "  1. Ask once to confirm: \"जी, आपको [original] चाहिए या [new product]?\"\n"
            "  2. As soon as they say YES / हां / confirm: call FetchCategorySchema(srchterm=\"<new product in English>\") IMMEDIATELY.\n"
            "     Do NOT continue asking questions from the old schema.\n"
            "  3. When the function returns: say the 'instruction' field, then ask Question 1 from the new schema.\n"
            "If they reconfirm the original product: continue without calling the function.\n\n"
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
    product = search.get("searched_product", {})

    name = buyer.get("buyer_name", "customer")
    keyword = search.get("searched_keyword", "")
    product_name = keyword or product.get("product_name", "")
    questions = schema.get("question", [])
    is_business = buyer.get("is_business", "")

    mandatory_opening = (
        f"हेलो, मैं Simran बोल रही हूँ Justdial से — "
        f"आपको {product_name} की requirement है ना?"
    )

    questions_block = build_questions_text(schema, is_business=is_business if is_business == "" else None)
    mapping_block = "\n" + _build_question_phrase_rules(questions) + "\n"

    closing_instruction = (
        _bc.get("call_end_text")
        or _pc.get("closing_instruction")
        or cfg.get("closing_instruction")
        or "After all questions are answered, close the call warmly."
    )

    business_prompt_section = ""
    if is_business == "":
        business_prompt_section = f"""
━━━ BUSINESS USE — CONVERSATIONAL HANDLING ━━━

BUSINESS GATE — triggers when buyer gives a high-quantity answer or uses a business keyword DURING qualification questions.

Triggers:
  • Quantity ≥ 100 of a BULK or COUNT unit: pieces / pcs / units / sets / boxes / cartons / packets / bags / dozen / rolls / kg / litre / ton / quintal / bori / nag / sack / drum
  • NOT triggered by small-measure units: gram / gm / mg / ml / cc / cm / mm / inch / feet — "500 ml" or "100 gram" is personal-scale.
  • Also triggers on keywords (any quantity):
      English: "wholesale", "bulk", "shop", "dukaan", "factory", "warehouse", "godown", "B2B", "resale", "retail sale", "food service", "catering", "restaurant", "hotel", "canteen", "office", "commercial", "hospital", "school", "institution"
      Hindi: "कैटरिंग", "रिटेल", "रिटेल सेल", "फूड सर्विस", "रेस्टोरेंट", "होटल", "दुकान", "फैक्ट्री", "ऑफिस", "थोक", "होलसेल", "रिसेल", "कैंटीन", "अस्पताल", "स्कूल"
  • RADIO OPTION RULE: If a qualification question offers options and the buyer picks a clearly commercial one (retail sale / food service / catering / wholesale / restaurant / hotel / canteen / institutional — or Hindi equivalents like कैटरिंग / रिटेल / फूड सर्विस) — treat as business trigger. The ONLY non-business options are: "personal consumption", "ghar ke liye", "khud ke liye", "personal use", "पर्सनल", "खुद के लिए", "घर के लिए".

EXACT SEQUENCE when gate triggers — follow this order, no deviations:
  1. Acknowledge plainly: "इतनी quantity — business के लिए होगी।" (vary wording, NEVER echo the number)
  2. Ask immediately: "आपके business का नाम क्या है?"
  3. Wait for answer. Then ask: "और कौन से city में?"
  4. Wait for answer. Then continue the remaining qualification questions one at a time.
  5. After ALL qualification questions are answered, say the closing line.

NEVER ask "business या personal?" — the gate has already answered it.
NEVER put business name/city at the end — they are asked RIGHT WHEN THE GATE TRIGGERS, before continuing other questions.

─────────────────────────────────────────────────
If the skip condition is NOT triggered (quantity < 100, no business keywords):

After ALL qualification questions are answered, ask naturally:
  "एक बात और — क्या यह {product_name} business के लिए चाहिए आपको?"

IF the buyer says YES (हाँ / हां / ji / bilkul / yes / business ke liye):
  - Warmly acknowledge: "अच्छा, business के लिए — ज़रूर!"
  - Ask business name: "आपके business का नाम क्या है?"
  - After they answer, ask city: "और आपका business किस city में है?"
  - Then close the call.

IF the buyer says NO (नहीं / personal / ghar ke liye / khud ke liye):
  - Accept naturally and move straight to closing. Do NOT ask business name or city.

IF the buyer is unclear or doesn't respond properly:
  - Re-ask once: "जी, मतलब क्या यह किसी business या shop के लिए है?"
  - If still unclear: accept as unknown and proceed to closing.

TONE RULES for this section:
  - Keep it light and quick — these are 2 extra questions, not an interrogation.
  - Do NOT announce "ab main business ke baare mein poochhungi" — just ask naturally after the last qualification question.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

    lead_section = f"""
━━━ CALL CONTEXT ━━━

Customer: {name}
Product search: {keyword}
Product: {product_name}

━━━ MANDATORY OPENING ━━━
Your VERY FIRST utterance MUST be EXACTLY this line, word-for-word, no additions, no preamble, no translation:

{mandatory_opening}

Speak it immediately. Do not wait for the customer to say anything.

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
    return base + lead_section + mapping_block


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
    "justdial पे call",        # trailing phrase in not-interested close only
    "justdial pe call",
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
    _assistant_id = _room_meta_raw.get("assistant_id", "")
    _bc = await fetch_bot_config(_assistant_id) if _assistant_id else None
    _bot_config: dict = _bc or _HARDCODED_BOT_CONFIG

    _prefetched_lead = await _early_lead_task if _early_lead_task is not None else None

    _api_urls = _bot_config.get("api_urls") or {}
    _mis_api_base        = _api_urls.get("mis_api_base") or MIS_API_BASE
    _category_change_api = _api_urls.get("category_change_api") or CATEGORY_CHANGE_API
    _language           = "hindi"
    _temperature        = float(_bot_config.get("temperature") or 0.4)
    _vad_start          = _bot_config.get("gemini_start_sensitivity") or "START_SENSITIVITY_HIGH"
    _vad_end            = _bot_config.get("gemini_end_sensitivity")   or "END_SENSITIVITY_HIGH"
    _vad_silence_ms     = int(_bot_config.get("gemini_silence_duration_ms") or 1500)
    _vad_prefix_ms      = int(_bot_config.get("gemini_prefix_padding_ms")   or 100)
    _max_call_duration  = 300
    _sarvam_min_rms                  = int(_bot_config.get("sarvam_min_rms") or 600)
    _sarvam_min_speech_ms            = int(_bot_config.get("sarvam_min_speech_ms") or 500)
    _sarvam_min_speech_ms_singleword = int(_bot_config.get("sarvam_min_speech_ms_singleword") or 1500)
    _sarvam_silero_threshold          = float(_bot_config.get("sarvam_silero_threshold") or 0.5)
    _sarvam_silero_min_speech_ms      = int(_bot_config.get("sarvam_silero_min_speech_ms") or 400)
    _gemini_silero_fallback_speech_ms = int(_bot_config.get("gemini_silero_fallback_speech_ms") or 150)
    _post_speech_hold_ms             = int(_bot_config.get("post_speech_hold_ms") or 800)
    _functions: list[dict] = _bot_config.get("functions") or []
    _function_calling   = bool(_bot_config.get("function_calling", False)) and bool(_functions)
    _lang_cfg           = HINDI_LANG_CONFIG

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

    # 4. Build system instruction from lead (or base rules if no lead yet)
    system_instruction = build_system_prompt(
        _prefetched_lead, lang_key=_language, bot_config=_bot_config
    )

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
            "tagged": False,
            "tagged_at": None,
            "created_at": datetime.now(timezone.utc),
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
        _end_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        _start_ts = (datetime.fromtimestamp(_start, tz=timezone.utc) - timedelta(days=1)).isoformat() if _start else None
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
            },
            "tags": [status],
            "sentiment": "neutral",
        }
        await save_call_log_to_backend(call_log_payload)

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
    _inactivity_task: asyncio.Task | None = None
    _call_ended = False

    async def _inactivity_timeout() -> None:
        nonlocal _nudge_count, _inactivity_task, _nudge_in_progress
        # Nudge 1 at 10 s, nudge 2 at 10 s after, close 5 s after nudge 2 (25 s total)
        sleep_secs = 5.0 if _nudge_count >= 2 else 10.0
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
            # this timer fired. If any live transcript exists or a partial is pending,
            # reset and let the normal flow continue — don't end a live conversation.
            if _turn_counter > 0 or _pending_user_text:
                _log.info(
                    f"[INACTIVITY] end suppressed — user just spoke "
                    f"(turn_counter={_turn_counter}, pending={_pending_user_text!r})"
                )
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            _log.info("[INACTIVITY] extended silence — ending call directly")
            call_state["ended_naturally"] = True
            end_phrase = INACTIVITY_END_PHRASE
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
            # Skip nudge if user just spoke (race: timer fired as user was responding)
            if _turn_counter > 0 or _pending_user_text:
                _log.info(
                    f"[INACTIVITY] nudge suppressed — user just spoke "
                    f"(turn_counter={_turn_counter}, pending={_pending_user_text!r})"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            # Skip nudge if audio RMS shows active input — caller on noisy line
            if _user_audio["speech_ms"] > 200:
                _log.info(
                    f"[INACTIVITY] speech_ms={_user_audio['speech_ms']:.0f} — "
                    "active audio detected, skipping nudge"
                )
                _nudge_count = 0
                _inactivity_task = asyncio.create_task(_inactivity_timeout())
                return
            nudge = INACTIVITY_PHRASE
            _log.info(f"[INACTIVITY] {sleep_secs:.0f}s silence — nudge {_nudge_count}: {nudge!r}")
            _nudge_in_progress = True
            await _speak_via_gemini(nudge, reason="inactivity-nudge")
            # _on_agent_state fires _reset_inactivity() after bot finishes speaking.
            # _nudge_in_progress=True prevents that call from zeroing _nudge_count.

    def _reset_inactivity(from_user_speech: bool = False) -> None:
        nonlocal _nudge_count, _inactivity_task, _nudge_in_progress
        if _call_ended:
            return
        if from_user_speech:
            # User genuinely spoke — clear everything.
            _nudge_count = 0
            _nudge_in_progress = False
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
    # Cumulative log of every muted-window Sarvam transcript across the call.
    # Saved to Mongo as a separate field so the analysis LLM can see what the user
    # said during bot speaking turns even when those turns were discarded from _live_transcript.
    _muted_transcript_log: list = []
    _close_status = "completed"  # "completed" or "not_interested"; set before _handle_close runs

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

    async def _soniox_transcribe(wav_bytes: bytes, context: str = "") -> str | None:
        """Call Soniox REST file-transcription. Returns transcript on success,
        None on any failure (HTTP error, empty body, timeout).
        Caller falls back to Sarvam on None."""
        if not SONIOX_API_KEY:
            return None
        form = aiohttp.FormData()
        form.add_field("file", wav_bytes, filename="audio.wav", content_type="audio/wav")
        # TODO: confirm exact model name from https://soniox.com/docs
        form.add_field("model", "stt-async-preview")
        form.add_field("enable_language_identification", "true")
        if context:
            form.add_field("context", context)
        try:
            async with _get_http_session().post(
                SONIOX_STT_URL,
                headers={"Authorization": f"Bearer {SONIOX_API_KEY}"},
                data=form,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _log.warning(
                        f"[SONIOX] HTTP {resp.status} — falling back to Sarvam | {body[:100]}"
                    )
                    return None
                result = await resp.json()
                # Soniox returns {"text": "..."} or {"transcript": "..."} — handle both
                text = (
                    result.get("text") or result.get("transcript") or ""
                ).strip()
                if not text:
                    _log.info("[SONIOX] empty transcript — falling back to Sarvam")
                    return None
                return text
        except Exception as e:
            _log.warning(f"[SONIOX] error: {e} — falling back to Sarvam")
            return None

    async def _sarvam_stt_fallback() -> str | None:
        """Transcribe the WAV file written by _buffer_user_audio.
        Tries Soniox first (multilingual, domain-biased); falls back to Sarvam.
        Silero VAD gates both paths — only invoked when genuine human voice is present."""
        if not _user_audio["has_audio"]:
            return None
        speech_ms = _user_audio["speech_ms"]
        if speech_ms < _sarvam_min_speech_ms:
            _log.info(f"[STT] skipped — speech_ms={speech_ms:.0f} < min={_sarvam_min_speech_ms}")
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
        # Silero VAD gate — applies to both Soniox and Sarvam paths.
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as wf:
                pcm_bytes = wf.readframes(wf.getnframes())
        except Exception:
            pcm_bytes = b""
        if pcm_bytes:
            voiced_ms = await asyncio.get_running_loop().run_in_executor(
                None, _silero_voiced_ms, pcm_bytes, _sarvam_silero_threshold
            )
            if voiced_ms < _sarvam_silero_min_speech_ms:
                _log.info(
                    f"[STT] Silero — no speech (voiced_ms={voiced_ms:.0f} < "
                    f"min={_sarvam_silero_min_speech_ms}), skipping"
                )
                return None
            _log.info(f"[STT] Silero — speech confirmed (voiced_ms={voiced_ms:.0f})")
        # Build context for Soniox domain biasing
        _lead = call_state.get("lead_record") or {}
        _catname = _lead.get("catname", "")
        _buyer_name = (_lead.get("buyer_details") or {}).get("buyer_name", "")
        _ctx = f"{_catname} {_buyer_name}".strip()
        # Try Soniox first
        text = await _soniox_transcribe(wav_data, context=_ctx)
        if text is not None:
            if _is_ivr_message(text):
                _log.info(f"[IVR] busy-line in Soniox fallback — dropping {text!r}")
                return None
            tokens = _normalize_stt_tokens(text)
            if tokens and all(t in _SARVAM_FILLER_HALLUCINATIONS for t in tokens):
                _log.info(f"[SONIOX] dropped all-filler {text!r}")
                return None
            _log.info(f"[STT-CASCADE] soniox=ok: {text!r} (speech_ms={speech_ms:.0f})")
            return text
        # Soniox failed — fall back to Sarvam
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
                            f"[STT-CASCADE] soniox=miss, sarvam=ok: {text!r} "
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
        Tries Soniox first (multilingual + domain-biased); falls back to Sarvam.
        No Silero gate here — we want everything the user said, even brief."""
        nonlocal _call_ended
        if not frames:
            return
        if not SONIOX_API_KEY and not SARVAM_API_KEY:
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
        # Build Soniox context from lead data for domain vocabulary biasing
        _lead = call_state.get("lead_record") or {}
        _catname = _lead.get("catname", "")
        _buyer_name = (_lead.get("buyer_details") or {}).get("buyer_name", "")
        _ctx = f"{_catname} {_buyer_name}".strip()
        # Try Soniox first
        text = await _soniox_transcribe(wav_data, context=_ctx)
        _cascade_tag = "soniox=ok"
        if text is None:
            # Fall back to Sarvam
            _cascade_tag = "soniox=miss"
            if SARVAM_API_KEY:
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
                            _cascade_tag = "soniox=miss,sarvam=ok" if text else "soniox=miss,sarvam=empty"
                        else:
                            body = await resp.text()
                            _log.warning(
                                f"[MUTED-CAPTURE] Sarvam STT failed: {resp.status} {body[:200]}"
                            )
                except Exception as e:
                    _log.warning(f"[MUTED-CAPTURE] Sarvam error: {e}")
        if not text:
            _log.info(f"[MUTED-CAPTURE] empty transcript [{_cascade_tag}] (speech_ms={speech_ms:.0f})")
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
            return
        # Sarvam/Soniox hallucination: same token repeated 2+ times (e.g. "हाँ हाँ",
        # "हाँ हाँ हाँ") from background audio bleed or line noise.  A genuine
        # single-word confirmation arrives as one token, not a repetition.
        if len(tokens) >= 2 and len(set(tokens)) == 1:
            _log.info(
                f"[MUTED-CAPTURE] repeated-token hallucination {text!r} [{_cascade_tag}] — dropped"
            )
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
        """Fetch customer lead details from Justdial MIS API. Pass lead_id or mobile."""
        _log.info(f"[FetchLead] called | lead_id={lead_id!r} | mobile={mobile!r}")
        result = await _execute_function_call(
            "FetchLead", {"lead_id": lead_id, "mobile": mobile},
            functions=_functions, call_state=call_state,
        )
        return result

    tools = [FetchCategorySchema, FetchLead] if _function_calling else []
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
                if _is_not_interested_close(_closing_buffer):
                    _close_status = "not_interested"
                    _log.info("[CLOSE DETECT] Not-interested close detected — status=not_interested")
                else:
                    _close_status = "completed"
                _log.info(f"[CLOSE DETECT] Closing phrase matched — status={_close_status!r} — scheduling end")
                _set_mic(False, reason="closing-phrase-matched")
                asyncio.create_task(_handle_close())

    @session.on("user_input_transcribed")
    def _on_user_spoke(ev) -> None:
        nonlocal _turn_counter, _live_transcript, _pending_user_text, _wav_reset_flag, _call_ended
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
            _had_partial = bool(_pending_user_text)
            _pending_user_text = ""
            # Gemini confirmed this turn — rotate WAV segment so next turn starts fresh
            _wav_reset_flag = True
            # Silero sanity-check: Gemini occasionally fires on background audio (TV,
            # nearby conversation). If Silero finds < min voiced ms in the window PCM,
            # discard the transcript rather than letting background noise reach Mongo.
            if _silero_session is not None and _current_window_pcm:
                _voiced = _silero_voiced_ms(
                    bytes(_current_window_pcm), _sarvam_silero_threshold
                )
                if _voiced < _sarvam_silero_min_speech_ms:
                    # Short monosyllabic words (e.g. "हां", "ना", "ओके") have very brief
                    # voiced frames and often fall below the Silero threshold. Gemini's
                    # own transcription is strong evidence — if speech_ms >= 150 ms AND
                    # Gemini produced a non-empty transcript, trust it over Silero here.
                    if speech_ms_now >= _gemini_silero_fallback_speech_ms:
                        _log.info(
                            f"[GEMINI] Silero weak but speech_ms sufficient — accepting "
                            f"{transcript_text!r} (voiced_ms={_voiced:.0f} < "
                            f"min={_sarvam_silero_min_speech_ms}, speech_ms={speech_ms_now:.0f})"
                        )
                    else:
                        _log.info(
                            f"[GEMINI] Silero rejected FINAL {transcript_text!r} — "
                            f"voiced_ms={_voiced:.0f} < min={_sarvam_silero_min_speech_ms} "
                            f"(speech_ms={speech_ms_now:.0f})"
                        )
                        # Remove any partial placeholder that was already added for this turn
                        if _live_transcript and _live_transcript[-1]["role"] == "user":
                            _live_transcript.pop()
                        # Block _on_item_added from re-inserting this text
                        _silero_rejected_turns.add(transcript_text)
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
            if speech_ms_now < 350:
                _short_toks = _normalize_stt_tokens(_norm_transcript)
                if (
                    len(_short_toks) == 1
                    and unicodedata.normalize("NFC", _short_toks[0]) in _GEMINI_SHORT_NOISE_TOKENS
                ):
                    _log.info(
                        f"[NOISE] Short noise token rejected: {transcript_text!r} "
                        f"(speech_ms={speech_ms_now:.0f})"
                    )
                    if _live_transcript and _live_transcript[-1]["role"] == "user":
                        _live_transcript.pop()
                    _silero_rejected_turns.add(transcript_text)
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
            nonlocal _bot_resp_watchdog_task, _last_user_final_text, _last_user_final_turn
            _last_user_final_text = transcript_text
            _last_user_final_turn = _turn_counter
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
            _bot_resp_watchdog_task = asyncio.create_task(
                _bot_response_watchdog(transcript_text, _turn_counter)
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
            _had_partial = bool(_pending_user_text)
            _pending_user_text = transcript_text
            if _had_partial and _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1]["text"] = transcript_text
            else:
                _live_transcript.append({"role": "user", "text": transcript_text})

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

    async def _bot_response_watchdog(user_text: str, turn: int) -> None:
        """Re-inject the user's last turn if Gemini doesn't start speaking within 8 s.
        Handles silent Gemini failures where the model transcribed audio but produced
        no output — observed as 12+ second silences before user disconnects."""
        await asyncio.sleep(8.0)
        if _call_ended or _closing_triggered or _last_user_final_turn != turn:
            return
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
        nonlocal _echo_guard_task, _speaking_unmute_task, _greeting_done, _bot_has_spoken, _barge_in_fired, _bot_resp_watchdog_task
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
            _bot_has_spoken = True
            _barge_in_fired = False  # reset at start of each bot turn
            _cancel_inactivity()
            # Gemini started speaking — cancel the response watchdog.
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
                # Greeting turn early unmute: re-enable Gemini audio input at 3 s so
                # it is already processing the stream when the greeting finishes.
                # 3 s (was 5 s) — greetings can be as short as ~4.5 s; at 5 s the timer
                # fired after the greeting ended for short greetings, leaving Gemini with
                # zero warm-up time and silently dropping the first user turn.
                async def _greeting_early_unmute() -> None:
                    await asyncio.sleep(3.0)
                    if not _greeting_done and not _call_ended:
                        _set_mic(True, reason="greeting-3s-early-unmute")
                _speaking_unmute_task = asyncio.create_task(_greeting_early_unmute())

        elif state_str in ("listening", "idle"):
            # Bot finished speaking — cancel watchdog if it was started by a FINAL
            # that arrived while the bot was already responding to the partial.
            if _bot_resp_watchdog_task and not _bot_resp_watchdog_task.done():
                _bot_resp_watchdog_task.cancel()
                _bot_resp_watchdog_task = None
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
                        await asyncio.sleep(2.5)
                        if _call_ended or _closing_triggered or _turn_counter > 0:
                            return
                        text = _muted_inject.get("text", "")
                        if not text:
                            return
                        # Drop bare phone-pickup signals ("हाँ जी", "हाँ", "हेलो", "जी", etc.)
                        # captured mid-greeting. These are reflexive phone-answer responses,
                        # not product confirmations — injecting them causes Gemini to treat
                        # them as "yes I need the product" and skip to spec questions.
                        _inject_tokens = {
                            unicodedata.normalize("NFC", re.sub(r"[^\w]", "", w.lower()))
                            for w in text.split() if w.strip()
                        }
                        _BARE_GREETING_TOKENS = frozenset(unicodedata.normalize("NFC", w) for w in {
                            "हाँ", "हां", "हा", "जी", "हाँजी", "हांजी",
                            "हेलो", "hello", "हैलो", "hi", "हाय",
                            "haan", "ha", "han", "ji", "jee", "okay", "ok",
                            "हाँ", "हां", "बोलो", "bol", "bolo",
                        })
                        if _inject_tokens and not (_inject_tokens - _BARE_GREETING_TOKENS):
                            _log.info(
                                f"[MUTED-CAPTURE] post-greeting inject skipped — bare phone-pickup signal: {text!r}"
                            )
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
            agent_name="voice-bot-justdial",
            num_idle_processes=3,
        )
    )
