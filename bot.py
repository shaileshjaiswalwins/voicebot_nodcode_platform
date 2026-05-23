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
import io
import json
import logging as _logging
import os
import re
import sys
import time
import unicodedata
import wave
from copy import deepcopy
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

from voicebot_platform.config_store import fetch_active_bot_config
from voicebot_platform.observability import recorder as _observability

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
    filter=lambda rec: not rec["extra"].get("caller"),  # call logs go via grouped CALL BLOCK only
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
    elif "[LATENCY]" in msg:
        c, e = "<white>", "</white>"
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

# The Google plugin reads GOOGLE_API_KEY; reuse the existing GEMINI_LIVE_API_KEY.
if not os.environ.get("GOOGLE_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ.get("GEMINI_LIVE_API_KEY", "")

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
MIS_API_BASE = "http://192.168.8.67:8000"
CATEGORY_CHANGE_API = f"{MIS_API_BASE}/leads/ai-lead-qualify/search"
IST = timezone(timedelta(hours=5, minutes=30))

MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = os.getenv("VOICEBOT_PLATFORM_DB") or os.getenv("MONGO_DB", "ai_voice_bot_management")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "tbl_ai_vb_call_transcripts")

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

        "━━━ FIXED RULES — THESE NEVER FLEX ━━━\n\n"
        "These are business rules. No exceptions:\n\n"
        "1. Never name brands, recommend products, give prices, or share opinions.\n"
        "2. One question per response — never combine or skip.\n"
        "3. Never advance to the next question until the current one has a valid answer.\n"
        "4. Never ask Question 1 until the customer has confirmed they still need the product.\n"
        "5. After every deflection or diversion — ALWAYS re-ask the current question. Always.\n\n"

        "━━━ DEFLECTION — MANDATORY BUT NATURAL ━━━\n\n"
        "When someone directly asks for a brand recommendation, price estimate, or product comparison:\n"
        "→ You MUST deflect. No exception.\n"
        "→ You MUST re-ask the current question immediately after. No exception.\n\n"
        "Vary the phrasing — don't say the same line every time:\n"
        "  • \"Haan jee, brands ke baare mein sellers better bata payenge — [current question]?\"\n"
        "  • \"Price ke liye sellers se directly poochna theek rahega — [current question]?\"\n"
        "  • \"Main product expert nahi hoon, yeh sellers ke saath decide kar sakte hain — [current question]?\"\n"
        "  • \"Woh toh aap sellers se pooch sakte hain — abhi main bas requirements note kar rahi hoon. [current question]?\"\n\n"
        "Keep deflections short and warm. The re-ask is not optional.\n\n"
        "Do NOT use the deflection for:\n"
        "  • A user naming a brand IN their answer (e.g. \"Samsung chahiye\") → valid answer (a), accept it\n"
        "  • Gibberish or random words → \"Samajh nahi aaya, [re-ask]?\"\n"
        "  • Frustration or rudeness → empathize briefly, close warmly\n"
        "  • An answer that doesn't match options → re-ask naturally per ANSWER VALIDATION\n\n"

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
        "• English timeout line (use ONLY when language has been switched to English): 'I only have permission to talk for 5 minutes. The sellers will contact you soon based on what we discussed. Thank you for your time. Goodbye!'\n\n"

        "TONE\n\n"
        "Warm, natural, efficient — like a helpful person doing their job, not a machine.\n"
        "Every response: 1 acknowledgement + 1 question. Keep it to roughly 15–25 words.\n"
        "If a thought needs a few more words to land naturally, use them — don't clip awkwardly.\n"
        "Vary your acknowledgements every turn. Don't repeat the same opener.\n"
        "Always end with a question.\n\n"

        "CONVERSATION FLOW\n\n"
        "Step 1 — Opening (HARD GATE — do not skip)\n"
        "Say the opening line from CALL CONTEXT exactly. Then stop and wait.\n"
        "Do not ask Question 1 until the customer confirms they need the product.\n\n"
        "YES (haan, bilkul, theek hai, chahiye, etc.):\n"
        "→ Bridge: \"Achha jee, aapko sahi sellers se connect karaane ke liye thodi details chahiye.\" → Ask Q1.\n\n"
        "NO:\n"
        "→ \"Koi aur product dekh rahe hain?\"\n"
        "→ Different product → treat as product change\n"
        "→ Nothing needed → \"Theek hai jee, koi baat nahi. Future mein zaroorat ho toh Justdial pe call kar sakte hain. Dhanyavaad.\" → stop\n\n"
        "Unclear / partial / side question:\n"
        "→ Read intent. If clearly interested: bridge and ask Q1.\n"
        "→ If unclear: \"Jee, toh kya aapko [product] chahiye?\"\n"
        "→ Q1 gate: do not pass until explicit confirmation.\n\n"
        "Step 2 — Questions\n"
        "Strictly in order. One per turn. No skipping, no combining.\n"
        "If buyer proactively covers multiple questions — great, pick up from where they left off.\n\n"
        "Step 3 — Confirm before closing\n"
        "Once EVERY question has an answer (even \"Not Sure\"), do ONE confirmation turn.\n"
        "Read back the collected answers as a compact summary and ask if it's correct:\n"
        "  Example: \"तो confirm करते हैं — [Q1 answer], [Q2 answer], [Q3 answer] — सही समझा ना?\"\n"
        "Keep it to one natural sentence. List only the answers, not the question labels.\n\n"
        "After the confirmation:\n"
        "  • User confirms (हाँ / yes / bilkul / sahi hai / theek hai) → go to Step 4.\n"
        "  • User corrects one thing → \"Okay, तो [corrected value] — और बाकी सब ठीक है?\" → update and go to Step 4.\n"
        "  • User corrects multiple things → update all, confirm the corrected values once more, then go to Step 4.\n"
        "Do this confirmation ONCE only — never loop more than one correction round.\n\n"
        "Step 4 — Closing\n"
        "Only after confirmation is done:\n"
        "\"ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.\" then stop — do not add anything after.\n\n"

        "━━━ ANSWER VALIDATION — STRICT ━━━\n\n"
        "A question is only answered when the user gives ONE of:\n"
        "  (a) Option match — paraphrase OK if intent is clear (\"cement\" = Cement Plastering; \"wall\" = Wall Plastering)\n"
        "      NOT valid: sarcastic or indirect remarks (\"paidal lene aa jaana\" ≠ pickup)\n"
        "  (b) Recognizable number — digits or unambiguous Hindi number words — for quantity questions only\n"
        "  (c) Clear amount or range — for budget questions only\n"
        "  (d) Explicit Not Sure: \"pata nahi\" / \"not sure\" / \"kuch bhi chalega\" / \"no preference\" / \"decide nahi kiya\"\n\n"
        "If NONE of (a)–(d):\n"
        "→ Do not echo, confirm, or advance.\n"
        "→ Re-ask ONCE naturally, with options/unit reminder.\n"
        "→ If still invalid: mark Not Sure and move on. Never re-ask a third time.\n\n"
        "Don't overthink clear matches. If intent is obvious, accept it and move on.\n"
        "Never echo a value back unless it's validated per (a)–(d).\n\n"

        "SPECIFIC SITUATIONS\n\n"
        "Difference between options:\n"
        "One neutral factual sentence — no opinion. Re-ask with all options.\n"
        "Example: \"Split AC mein indoor aur outdoor dono hote hain, window AC ek unit hoti hai. Toh kaun sa chahiye — split, window, ya centralised?\"\n\n"
        "Brand preference question:\n"
        "Ask: \"Koi brand preference hai, ya kuch bhi chalega?\"\n"
        "Never list brands. Accept any brand name or 'no preference'.\n"
        "Unknown brand: \"Samajh gaya — aise preference wale sellers se connect karayenge.\" Move on.\n\n"
        "Budget question:\n"
        "Accept a number or range only. Re-ask once if vague, then Not Sure.\n\n"
        "Quantity question:\n"
        "Accept digits or clear Hindi number words only.\n"
        "STT often mis-transcribes Hindi numbers (\"सौ\" → \"To\", \"चार\" → \"For\") — if a non-number English word appears on a quantity question, treat as unclear and re-ask.\n\n"
        "Product change mid-call:\n"
        "\"Aapko [original] chahiye ya [new product]?\" — wait for answer.\n\n"
        "Off-topic / irrelevant:\n"
        "Brief warm acknowledge, then re-ask: \"Haan — toh [current question]?\"\n"
        "Persistent off-topic loop (3+ times): \"Main sirf requirements note kar rahi hoon — [current question]?\"\n\n"
        "Not interested: \"Theek hai jee, koi baat nahi. Future mein zaroorat ho toh Justdial pe call kar sakte hain. Dhanyavaad.\" → stop\n"
        "Rude or hang-up: same warm close immediately\n"
        "Reschedule: \"Theek hai jee, [time] pe baat karte hain.\" → stop\n\n"
        "Mid-conversation hello / connection check:\n"
        "If the user says \"hello\", \"हेलो\", \"हाय\", \"are you there\", \"hello hello\", or similar AFTER the call has already started:\n"
        "→ DO NOT re-introduce yourself. DO NOT say \"हाँ जी, आपको मेरी आवाज़ आ रही है?\"\n"
        "→ Simply say \"हाँ जी\" and immediately re-ask the current unanswered question.\n"
        "→ Example: \"हाँ जी — तो [current question]?\"\n\n"

        "━━━ PRE-RESPONSE CHECKLIST ━━━\n\n"
        "□ VALIDATION GATE: Did the user's last turn satisfy (a)–(d) for the current question?\n"
        "   NO → do not echo, do not advance. Re-ask once (or Not Sure if already re-asked).\n"
        "   YES → acknowledge the validated answer, ask the next question.\n"
        "□ Am I asking exactly one question?\n"
        "□ About to name a brand / give a price / give an opinion? → STOP. Deflect + re-ask.\n"
        "□ Did I just deflect? → Did I include the re-ask? (If not, add it.)\n"
        "□ Is every question answered? (If not, do NOT close and do NOT confirm.)\n"
        "□ Have I done the confirmation turn and the user confirmed (or corrected)? (If not, do confirmation first.)\n"
        "□ Is my language natural, warm, and varied from last turn?"
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
    "gemini_start_sensitivity": "START_SENSITIVITY_LOW",
    "gemini_end_sensitivity": "END_SENSITIVITY_LOW",
    "gemini_silence_duration_ms": 1800,
    "gemini_prefix_padding_ms": 300,
    "max_call_duration": 300,
    "sarvam_min_rms": 600,
    "sarvam_min_speech_ms": 500,
    "sarvam_min_speech_ms_singleword": 1500,
    "sarvam_silero_threshold": 0.5,
    "sarvam_silero_min_speech_ms": 150,
    "gemini_silero_min_speech_ms": 50,
    "post_speech_hold_ms": 800,
    "filler_message": ["अच्छा,", "हाँ,", "जी,", "तो,", "ठीक है,"],
    "function_filler_message": ["एक moment जी,", "जी, देख रही हूँ,"],
}


async def fetch_bot_config(assistant_id: str) -> dict | None:
    """Fetch the active published bot config from MongoDB."""
    try:
        return fetch_active_bot_config(assistant_id)
    except Exception as e:
        logger.warning(f"[CONFIG] active config lookup failed for assistant_id={assistant_id!r}: {e}")
        return None


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
            records = data.get("results", {}).get("data", [])
            if records:
                record = records[0]
                logger.info(
                    f"[API FETCH] {resp.status} OK — "
                    f"lead_id={record.get('_id')} | catname={record.get('catname')} | "
                    f"buyer={record.get('buyer_details', {}).get('buyer_name')}"
                )
                return record
    except Exception as e:
        logger.error(f"[API FETCH] fetch_lead failed: {e}")
    return None


async def _build_sample_from_search(srchterm: str, buyer_name: str, category_api: str, lead_id: str = "test_lead") -> dict | None:
    if not category_api:
        return None
    try:
        params = {"lead_id": lead_id, "search_term": srchterm}
        async with aiohttp.ClientSession() as sess:
            async with sess.get(category_api, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
        async with aiohttp.ClientSession() as sess:
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
                return False
            return True
    except Exception as e:
        logger.error(f"[CALL LOG] Failed to save: {e}")
        return False



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
        lines.append(f"{n + 1}. Is this product needed for business or personal use?  (yes/no)")
        lines.append(f"   → If YES: ask \"{n + 2}. What is your business name?\" then \"{n + 3}. What is your city?\"")
        lines.append(f"   → If NO or unclear: skip to closing")

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
            "If they reconfirm the original product: continue without calling the function.\n"
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
  - If the buyer proactively mentions business/shop earlier in the call, skip this question and directly ask business name and city at that point.

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


def _is_closing_phrase(text: str) -> bool:
    normalized = _dedup_words(text or "").lower()
    return any(marker.lower() in normalized for marker in _CLOSE_MARKERS)


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
    _is_test_session = bool(_room_meta_raw.get("test_session"))
    _caller_key = _room_mobile or room_name[-15:]
    _log = logger.bind(caller=_caller_key)

    # Buffer this call's logs in memory; flushed as a grouped block to the daily file at call end
    _call_log_buffer: list[str] = []
    _call_sink_id = logger.add(
        _call_log_buffer.append,
        level="INFO",
        format=_log_format,
        enqueue=False,
        filter=lambda rec, _k=_caller_key: rec["extra"].get("caller") == _k,
    )

    _SEP = "═" * 68
    _log.info(_SEP)
    _log.info(
        f"[CALL START] room={room_name} | lead_id={_lead_id_meta!r} | mobile={_room_mobile!r}"
    )
    _log.info(_SEP)

    _early_lead_task: asyncio.Task | None = None
    if (_lead_id_meta or _room_mobile) and not (_is_test_session and _room_meta_raw.get("srchterm")):
        _early_lead_task = asyncio.ensure_future(
            fetch_lead(lead_id=_lead_id_meta, mobile=_room_mobile, mis_api_base=MIS_API_BASE)
        )

    await ctx.connect()
    await ctx.wait_for_participant()

    # 2. Resolve bot config and settings
    _assistant_id = _room_meta_raw.get("assistant_id", "")
    _bc = await fetch_bot_config(_assistant_id) if _assistant_id else None
    _bot_config: dict = _bc or _HARDCODED_BOT_CONFIG
    _config_snapshot = deepcopy(_bot_config)
    _bot_id = _bot_config.get("bot_id", "")
    _bot_version_id = _bot_config.get("bot_version_id", "")
    _bot_version = _bot_config.get("bot_version")
    _campaign_id = _room_meta_raw.get("campaign_id", "")

    _observability.event(
        "call_started",
        {
            "room_name": room_name,
            "assistant_id": _assistant_id,
            "bot_id": _bot_id,
            "bot_version_id": _bot_version_id,
            "bot_version": _bot_version,
            "campaign_id": _campaign_id,
            "lead_id": _lead_id_meta,
            "mobile": _room_mobile,
        },
    )

    _prefetched_lead = await _early_lead_task if _early_lead_task is not None else None

    _api_urls = _bot_config.get("api_urls") or {}
    _mis_api_base        = _api_urls.get("mis_api_base") or MIS_API_BASE
    _category_change_api = _api_urls.get("category_change_api") or CATEGORY_CHANGE_API
    _model              = _bot_config.get("model") or "gemini-3.1-flash-live-preview"
    _voice              = _bot_config.get("voice") or "Aoede"
    _language           = _bot_config.get("language") or "hindi"
    _livekit_language   = _bot_config.get("livekit_language") or "hi-IN"
    _sarvam_language    = _bot_config.get("sarvam_language") or _livekit_language
    _temperature        = float(_bot_config.get("temperature") or 0.4)
    _vad_start          = _bot_config.get("gemini_start_sensitivity") or "START_SENSITIVITY_LOW"
    _vad_end            = _bot_config.get("gemini_end_sensitivity")   or "END_SENSITIVITY_LOW"
    _vad_silence_ms     = int(_bot_config.get("gemini_silence_duration_ms") or 1500)
    _vad_prefix_ms      = int(_bot_config.get("gemini_prefix_padding_ms")   or 100)
    _max_call_duration  = int(_bot_config.get("max_call_duration") or 300)
    _sarvam_min_rms                  = int(_bot_config.get("sarvam_min_rms") or 600)
    _sarvam_min_speech_ms            = int(_bot_config.get("sarvam_min_speech_ms") or 500)
    _sarvam_min_speech_ms_singleword = int(_bot_config.get("sarvam_min_speech_ms_singleword") or 1500)
    _sarvam_silero_threshold         = float(_bot_config.get("sarvam_silero_threshold") or 0.5)
    _sarvam_silero_min_speech_ms     = int(_bot_config.get("sarvam_silero_min_speech_ms") or 400)
    _gemini_silero_min_speech_ms     = int(_bot_config.get("gemini_silero_min_speech_ms") or 50)
    _post_speech_hold_ms             = int(_bot_config.get("post_speech_hold_ms") or 800)
    _early_mute_recovery_grace_ms    = int(_bot_config.get("early_mute_recovery_grace_ms") or 2500)
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
    llm = google.realtime.RealtimeModel(
        model=_model,
        voice=_voice,
        instructions=system_instruction,
        temperature=_temperature,
        language=_livekit_language,
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
            pass
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
        # Flush any muted-window Sarvam transcript that never got combined with live speech.
        if _muted_inject["text"]:
            _live_transcript.append({"role": "user", "text": _muted_inject["text"]})
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
            "bot_id": _bot_id,
            "bot_version_id": _bot_version_id,
            "bot_version": _bot_version,
            "campaign_id": _campaign_id,
            "config_snapshot": _config_snapshot,
            "room_name": room_name,
            "status": status,
            "ended_naturally": call_state.get("ended_naturally"),
            "product_change": call_state.get("product_change"),
            "transcript": transcript,
            "lead_record": call_state.get("lead_record"),
            "sip_info": sip_info,
            "call_start_time": _start,
            "call_end_time": _end_time,
            "call_duration_sec": _duration,
            "greeting_retry": _greeting_retry_triggered,
            "tagged": False,
            "tagged_at": None,
            "created_at": datetime.utcnow(),
        }
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _get_mongo_collection().insert_one(_mongo_doc))
            _log.info(f"[MONGO] Transcript saved | lead_id={lead_id!r} | call_id={call_state.get('call_id')!r}")
            _observability.event(
                "transcript_saved",
                {
                    "room_name": room_name,
                    "call_id": call_state.get("call_id"),
                    "lead_id": lead_id,
                    "bot_id": _bot_id,
                    "bot_version_id": _bot_version_id,
                    "campaign_id": _campaign_id,
                    "status": status,
                    "call_duration_sec": _duration,
                },
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
        _end_ts = datetime.utcnow().isoformat()
        _start_ts = datetime.utcfromtimestamp(_start).isoformat() if _start else None
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
            "bot_id": _bot_id,
            "bot_version_id": _bot_version_id,
            "campaign_id": _campaign_id,
            "status": "completed" if status == "completed" else "disconnected",
            "summary": "",
            "call_type": "outbound",
            "outcome": status,
            "transcripts": _transcripts,
            "meta_data": {
                "lead_id": call_state.get("record_id", ""),
                "lead_call_id": call_state.get("call_id", ""),
                "bot_id": _bot_id,
                "bot_version_id": _bot_version_id,
                "bot_version": _bot_version,
                "campaign_id": _campaign_id,
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
        _callback_ok = await save_call_log_to_backend(call_log_payload)
        _observability.event(
            "callback_sent",
            {
                "room_name": room_name,
                "call_id": call_state.get("call_id"),
                "lead_id": lead_id,
                "bot_id": _bot_id,
                "bot_version_id": _bot_version_id,
                "campaign_id": _campaign_id,
                "status": "completed" if _callback_ok else "failed",
                "callback_target": f"{BACKEND_URL}/backend/api/call-logs",
            },
        )
        _observability.event(
            "call_ended",
            {
                "room_name": room_name,
                "call_id": call_state.get("call_id"),
                "lead_id": lead_id,
                "bot_id": _bot_id,
                "bot_version_id": _bot_version_id,
                "campaign_id": _campaign_id,
                "status": status,
                "call_duration_sec": _duration,
            },
        )

        _log.info(_SEP)
        _log.info(
            f"[CALL END] room={room_name} | status={status!r} | "
            f"duration={_duration}s | lead_id={lead_id!r}"
        )
        _log.info(_SEP)
        logger.remove(_call_sink_id)

        # Append the full buffered call as a grouped block to the daily log file
        _daily_log = os.path.join(_LOG_DIR, f"{datetime.utcnow().strftime('%Y-%m-%d')}.log")
        _block_sep = "═" * 68
        _block_header = (
            f"\n{_block_sep}\n"
            f"[CALL BLOCK] lead_id={lead_id!r} | mobile={_room_mobile!r} | room={room_name}\n"
            f"{_block_sep}\n"
        )
        try:
            with open(_daily_log, "a", encoding="utf-8") as _f:
                _f.write(_block_header)
                _f.writelines(_call_log_buffer)
                _f.write(f"{_block_sep}\n\n")
        except Exception as _be:
            _log.warning(f"[CALL BLOCK] Could not write grouped block: {_be}")

    _save_done_event = asyncio.Event()

    async def _save_and_close(status: str) -> None:
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
        asyncio.ensure_future(_delete_room_safe())
        try:
            await session.aclose()
        except Exception:
            pass
        if not _was_done:
            _save_done_event.set()

    # Inactivity tracking
    _nudge_count = 0
    _inactivity_task: asyncio.Task | None = None
    _call_ended = False

    async def _inactivity_timeout() -> None:
        nonlocal _nudge_count, _inactivity_task
        await asyncio.sleep(15.0)
        _nudge_count += 1
        if _call_ended:
            return
        if _nudge_count >= 2:
            _nudge_count = 0
            _inactivity_task = None
            _log.info("[INACTIVITY] 30 s of silence — ending call directly")
            call_state["ended_naturally"] = True
            end_phrase = INACTIVITY_END_PHRASE
            try:
                await session.say(end_phrase, allow_interruptions=False)
            except Exception:
                pass
            await asyncio.sleep(2)
            await _kick_caller_safe()
            asyncio.ensure_future(_save_and_close("completed"))
        else:
            nudge = INACTIVITY_PHRASE
            _log.info(f"[INACTIVITY] 15 s nudge — saying: {nudge!r}")
            try:
                await session.say(nudge, allow_interruptions=True)
            except Exception:
                pass
            _inactivity_task = asyncio.create_task(_inactivity_timeout())

    def _reset_inactivity() -> None:
        nonlocal _nudge_count, _inactivity_task
        if _call_ended:
            return
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

    # Closing-phrase handler
    _closing_buffer = ""
    _closing_triggered = False
    _early_close_muting = False  # True once partial closing phrases appear — keeps mic muted
    _echo_guard_task: asyncio.Task | None = None

    # Turn-wise transcript + end-to-end latency tracking
    _turn_counter = 0
    _user_turn_time: float | None = None  # timestamp when user transcript arrived
    _first_user_audio_reported = False
    _first_agent_response_reported = False
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

    async def _handle_close() -> None:
        nonlocal _call_ended
        _call_ended = True
        _cancel_inactivity()
        await asyncio.sleep(2)
        await _kick_caller_safe()
        asyncio.ensure_future(_save_and_close("completed"))

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
                        "relevant sellers आपसे contact",
                        "sellers will contact",
                        "relevant sellers will contact",
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
                    wf.close()
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

    _GREETING_TOKENS = {
        unicodedata.normalize("NFC", w) for w in {
            "hello", "हेलो", "हैलो", "हलो",
            "hi", "hey", "हाय",
            "namaste", "namaskar", "नमस्ते", "नमस्कार",
        }
    }

    def _muted_is_substantive(text: str) -> bool:
        """Return True only if the muted-capture text carries real intent beyond
        greetings and filler — safe to inject to Gemini as a user turn."""
        if not text:
            return False
        tokens = _normalize_stt_tokens(text)
        if not tokens:
            return False
        if all(t in _SARVAM_FILLER_HALLUCINATIONS for t in tokens):
            return False
        if all(t in _GREETING_TOKENS or t in _SARVAM_FILLER_HALLUCINATIONS for t in tokens):
            return False
        return True

    async def _sarvam_stt_fallback() -> str | None:
        """Transcribe the WAV file written by _buffer_user_audio via Sarvam STT.
        Silero VAD gates the call — Sarvam is only invoked when genuine human voice
        is detected in the WAV, preventing noise / TV / echo from being transcribed."""
        if not _user_audio["has_audio"] or not SARVAM_API_KEY:
            return None
        speech_ms = _user_audio["speech_ms"]
        if speech_ms < _sarvam_min_speech_ms:
            _log.info(f"[SARVAM] skipped — speech_ms={speech_ms:.0f} < min={_sarvam_min_speech_ms}")
            return None
        wav_path = _wav_paths[0]
        if not Path(wav_path).exists():
            return None
        try:
            with open(wav_path, "rb") as f:
                wav_data = f.read()
            # Silero VAD gate — read PCM frames from WAV and score for human voice.
            try:
                with wave.open(io.BytesIO(wav_data), "rb") as wf:
                    pcm_bytes = wf.readframes(wf.getnframes())
            except Exception:
                pcm_bytes = b""
            if pcm_bytes:
                voiced_ms = await asyncio.get_event_loop().run_in_executor(
                    None, _silero_voiced_ms, pcm_bytes, _sarvam_silero_threshold
                )
                if voiced_ms < _sarvam_silero_min_speech_ms:
                    _log.info(
                        f"[SARVAM] Silero — no speech detected "
                        f"(voiced_ms={voiced_ms:.0f} < min={_sarvam_silero_min_speech_ms}), skipping"
                    )
                    return None
                _log.info(f"[SARVAM] Silero — speech confirmed (voiced_ms={voiced_ms:.0f})")
            form = aiohttp.FormData()
            form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
            form.add_field("language_code", _sarvam_language)
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
                        _log.info(f"[SARVAM] Fallback STT: {text!r} (speech_ms={speech_ms:.0f})")
                        return text
                else:
                    body = await resp.text()
                    _log.warning(f"[SARVAM] STT failed: {resp.status} {body[:200]}")
        except Exception as e:
            _log.warning(f"[SARVAM] STT error: {e}")
        return None

    async def _transcribe_muted_period(frames: list, speech_ms: float) -> None:
        """Transcribe audio captured during a muted window (bot speaking turn + post-hold)
        via Sarvam. Silero VAD gates the call so TV / background audio is rejected before
        the Sarvam API is hit."""
        if not SARVAM_API_KEY or not frames:
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
            # Silero VAD gate — extract PCM and score for human voice.
            try:
                with wave.open(io.BytesIO(wav_data), "rb") as wf:
                    pcm_bytes = wf.readframes(wf.getnframes())
            except Exception:
                pcm_bytes = b""
            if pcm_bytes:
                voiced_ms = await asyncio.get_event_loop().run_in_executor(
                    None, _silero_voiced_ms, pcm_bytes, _sarvam_silero_threshold
                )
                if voiced_ms < _sarvam_silero_min_speech_ms:
                    _log.info(
                        f"[MUTED-CAPTURE] Silero — no speech detected "
                        f"(voiced_ms={voiced_ms:.0f} < min={_sarvam_silero_min_speech_ms}), skipping"
                    )
                    return
                _log.info(f"[MUTED-CAPTURE] Silero — speech confirmed (voiced_ms={voiced_ms:.0f})")
            form = aiohttp.FormData()
            form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
            form.add_field("language_code", _sarvam_language)
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
                    if not text:
                        _log.info(
                            f"[MUTED-CAPTURE] Sarvam returned empty transcript "
                            f"(speech_ms={speech_ms:.0f})"
                        )
                        return
                    tokens = _normalize_stt_tokens(text)
                    if tokens and all(t in _SARVAM_FILLER_HALLUCINATIONS for t in tokens):
                        _log.info(
                            f"[MUTED-CAPTURE] dropped all-filler {text!r} "
                            f"(speech_ms={speech_ms:.0f})"
                        )
                        return
                    _log.info(
                        f"[MUTED-CAPTURE] captured user speech: {text!r} "
                        f"(speech_ms={speech_ms:.0f}) — buffered, not sent to Gemini"
                    )
                    # Buffer only — never injected to Gemini.
                    # _on_user_spoke combines this with the next live FINAL for Mongo.
                    _muted_inject["text"] = text
                else:
                    body = await resp.text()
                    _log.warning(
                        f"[MUTED-CAPTURE] Sarvam STT failed: {resp.status} {body[:200]}"
                    )
        except Exception as e:
            _log.warning(f"[MUTED-CAPTURE] error: {e}")

    # 7. Function tools
    @function_tool
    async def FetchCategorySchema(tool_ctx: RunContext, srchterm: str) -> dict:
        """Call when the buyer changes their product requirement mid-call.
        Pass the new product as a simple English search term (e.g. 'washing-machine', 'cctv')."""
        _log.info(f"[FetchCategorySchema] called with srchterm={srchterm!r}")
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
        nonlocal _closing_buffer, _closing_triggered, _early_close_muting, _first_agent_response_reported, _live_transcript, _pending_assistant_text
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
            _live_transcript.append({"role": "assistant", "text": text})
            if not _first_agent_response_reported:
                _first_agent_response_reported = True
                _observability.event(
                    "first_model_response",
                    {
                        "room_name": room_name,
                        "call_id": call_state.get("call_id"),
                        "bot_id": _bot_id,
                        "bot_version_id": _bot_version_id,
                        "campaign_id": _campaign_id,
                    },
                )
        # Sniffer partial is superseded by the officially committed item — clear it.
        _pending_assistant_text = ""
        # Early mute: partial closing phrases are unique to the wrap-up line — mute
        # immediately so the user cannot interrupt before the full phrase is committed.
        if not _early_close_muting and not _closing_triggered:
            _buf_lower = _closing_buffer.lower()
            if any(m in _buf_lower for m in (
                "details मिल गईं",
                "relevant sellers आपसे contact",
                "sellers will contact",
                "relevant sellers will contact",
            )):
                _early_close_muting = True
                _set_mic(False, reason="commit-closing-phrase")
                _log.info("[CLOSE DETECT] Partial closing phrase detected in commit — mic muted")
        if _is_closing_phrase(_closing_buffer):
            _closing_triggered = True
            call_state["ended_naturally"] = True
            _log.info(f"[CLOSE DETECT] Closing phrase matched — scheduling end")
            _set_mic(False, reason="closing-phrase-matched")
            asyncio.create_task(_handle_close())

    @session.on("user_input_transcribed")
    def _on_user_spoke(ev) -> None:
        nonlocal _first_user_audio_reported, _turn_counter, _user_turn_time, _live_transcript, _pending_user_text, _wav_reset_flag
        # User spoke — reset inactivity timer
        if not _call_ended:
            _reset_inactivity()
        is_final = getattr(ev, "is_final", True)
        transcript_text = (
            getattr(ev, "transcript", None)
            or getattr(ev, "text", None)
            or ""
        ).strip()
        if not transcript_text:
            return
        if not _first_user_audio_reported:
            _first_user_audio_reported = True
            _observability.event(
                "first_audio_received",
                {
                    "room_name": room_name,
                    "call_id": call_state.get("call_id"),
                    "bot_id": _bot_id,
                    "bot_version_id": _bot_version_id,
                    "campaign_id": _campaign_id,
                },
            )
        # Snapshot agent state at the moment user speech arrives (for interruption diagnosis).
        _agent_state_now = ""
        try:
            _s = session.agent_state
            _agent_state_now = _s.value if hasattr(_s, "value") else str(_s)
        except Exception:
            _agent_state_now = "?"
        # Live speech arrived — discard any buffered muted-window text.
        # (Muted text is only saved to Mongo when NO live speech follows.)
        if _muted_inject["text"]:
            _log.info(
                f"[MUTED-CAPTURE] live speech arrived — discarding muted buffer "
                f"{_muted_inject['text']!r}"
            )
            _muted_inject["text"] = ""
        muted_prefix = ""  # no longer combining
        if is_final:
            _user_turn_time = time.time()
            _turn_counter += 1
            speech_ms_now = _user_audio["speech_ms"]
            _log.info(
                f"[TRANSCRIPT] Turn {_turn_counter} | USER (FINAL): {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled} "
                f"speech_ms={speech_ms_now:.0f}"
            )
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
                if _voiced < _gemini_silero_min_speech_ms:
                    _log.info(
                        f"[GEMINI] Silero rejected FINAL {transcript_text!r} — "
                        f"voiced_ms={_voiced:.0f} < min={_gemini_silero_min_speech_ms} "
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
            # Replace the last entry if it was a partial for this same turn
            if _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1]["text"] = transcript_text
            else:
                _live_transcript.append({"role": "user", "text": transcript_text})
        else:
            # Partial — keep the latest chunk in _pending_user_text; also put a
            # placeholder in _live_transcript so save_call_data sees it even if
            # the final never arrives (abrupt disconnect before Gemini finalises).
            _log.info(
                f"[TRANSCRIPT] PARTIAL | USER: {transcript_text!r} | "
                f"agent_state={_agent_state_now} mic={_mic_enabled}"
            )
            _pending_user_text = transcript_text
            if _live_transcript and _live_transcript[-1]["role"] == "user":
                _live_transcript[-1]["text"] = transcript_text
            else:
                _live_transcript.append({"role": "user", "text": transcript_text})

    _greeting_done = False
    _bot_has_spoken = False  # True once the agent first transitions to "speaking"
    _greeting_retry_triggered = False
    _mic_enabled: bool = False  # mirrors the last value passed to set_audio_enabled
    _speaking_start_time: float | None = None  # wall-clock when current speaking turn started

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
        nonlocal _echo_guard_task, _greeting_done, _bot_has_spoken, _user_turn_time, _speaking_start_time
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
            _speaking_start_time = time.time()
            if _user_turn_time is not None:
                latency_ms = round((time.time() - _user_turn_time) * 1000)
                _log.info(f"[LATENCY] Turn {_turn_counter} | E2E: {latency_ms} ms")
                _user_turn_time = None
            _cancel_inactivity()
            # Cancel any in-flight hold task and any pending muted injection.
            if _echo_guard_task and not _echo_guard_task.done():
                _echo_guard_task.cancel()
                _log.info("[SPEAKING-MUTE] cancelled previous hold — new speaking turn")
            # If there's a buffered muted transcript that never got combined (no live
            # speech arrived before the next bot turn), save it to _live_transcript now
            # so it's visible in Mongo, then clear.
            if _muted_inject["text"]:
                _log.info(
                    f"[MUTED-CAPTURE] flushing uncombined muted text to transcript: "
                    f"{_muted_inject['text']!r}"
                )
                _live_transcript.append({"role": "user", "text": _muted_inject["text"]})
                _muted_inject["text"] = ""
            # Mute mic for the ENTIRE bot speaking turn (no timed unmute).
            # The listening branch unmutes via post-speech-hold.
            # Caller audio continues to flow into _buffer_user_audio (raw track is
            # unaffected) and is written to _muted_capture while _mic_enabled=False.
            _set_mic(False, reason="speaking-start")

        elif state_str in ("listening", "idle"):
            if _bot_has_spoken and not _greeting_done:
                # Greeting completed — unmute mic immediately and discard any
                # audio captured during the greeting (bot intro only, not a real turn).
                _greeting_done = True
                _muted_capture["frames"].clear()
                _muted_capture["speech_ms"] = 0.0
                if not _call_ended:
                    _set_mic(True, reason="greeting-complete")
                    _log.info("[MIC] Greeting complete — mic enabled")
            elif _greeting_done and _bot_has_spoken and not _call_ended and not _closing_triggered:
                # Post-speech hold: mic stays OFF for a brief window after each bot turn.
                # Purpose: absorb TTS audio tail + prevent instant hello-check loops.
                # Any caller speech during bot's turn + this hold was already written into
                # _muted_capture (because _mic_enabled was False) — Sarvam transcribes it
                # and re-injects substantive replies to Gemini after the window closes.
                _speaking_start_time = None

                if _echo_guard_task and not _echo_guard_task.done():
                    _echo_guard_task.cancel()
                    _log.info("[POST-SPEECH-HOLD] cancelled stale guard — starting new")

                async def _post_speech_hold() -> None:
                    nonlocal _early_close_muting
                    _log.info(
                        f"[POST-SPEECH-HOLD] started (hold={_post_speech_hold_ms} ms)"
                    )
                    hold_ran_to_completion = False
                    try:
                        # Mic is already OFF from the speaking branch — no need to re-mute.
                        await asyncio.sleep(_post_speech_hold_ms / 1000)
                        hold_ran_to_completion = True
                        if not _call_ended and not _closing_triggered and not _early_close_muting:
                            _log.info(
                                f"[POST-SPEECH-HOLD] expired ({_post_speech_hold_ms} ms) — "
                                "enabling mic"
                            )
                            _set_mic(True, reason="post-speech-hold-expired")
                            # Rotate WAV so the fallback only sees audio from THIS response
                            # window, not contaminated audio from earlier turns.
                            _wav_reset_flag = True
                        elif _early_close_muting and not _closing_triggered and not _call_ended:
                            # False-positive early-mute recovery: a partial closing phrase matched
                            # during streaming but the full closing line never committed, meaning
                            # the agent was speaking mid-conversation.  Re-enable the mic and give
                            # any muted-capture audio a chance to reach Gemini.
                            _early_close_muting = False
                            _log.info(
                                "[EARLY-MUTE-RECOVERY] Closing didn't commit after partial match — "
                                "clearing early_mute, re-enabling mic"
                            )
                            _set_mic(True, reason="early-mute-recovery")
                            _wav_reset_flag = True
                            _reset_inactivity()

                            async def _grace_period_inject() -> None:
                                """Wait for live speech to arrive. If none does and the muted
                                capture is substantive, inject it to Gemini as a user turn."""
                                await asyncio.sleep(_early_mute_recovery_grace_ms / 1000)
                                if _call_ended or _closing_triggered:
                                    return
                                text = _muted_inject.get("text", "")
                                if not text:
                                    return  # live speech already arrived and discarded it
                                if not _muted_is_substantive(text):
                                    _log.info(
                                        f"[EARLY-MUTE-INJECT] dropping non-substantive "
                                        f"muted text {text!r}"
                                    )
                                    _muted_inject["text"] = ""
                                    return
                                _rt_now = (
                                    getattr(session._activity, "_rt_session", None)
                                    if session._activity else None
                                )
                                if _rt_now is None:
                                    _log.warning("[EARLY-MUTE-INJECT] no realtime session — cannot inject")
                                    return
                                try:
                                    _rt_now._send_client_event(
                                        types.LiveClientContent(
                                            turns=[types.Content(
                                                parts=[types.Part(text=text)],
                                                role="user",
                                            )],
                                            turn_complete=True,
                                        )
                                    )
                                    _log.info(
                                        f"[EARLY-MUTE-INJECT] injected muted user turn → "
                                        f"Gemini: {text!r}"
                                    )
                                    _live_transcript.append({"role": "user", "text": text})
                                    _muted_inject["text"] = ""
                                except Exception as _inj_e:
                                    _log.warning(f"[EARLY-MUTE-INJECT] send_client_event failed: {_inj_e}")

                            asyncio.create_task(_grace_period_inject())
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
                            captured_ms >= _sarvam_min_speech_ms
                            and captured_frames
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
            asyncio.ensure_future(_buffer_user_audio(track))

    # Start session — disable close_on_disconnect so the process stays alive
    # long enough for save_call_data (Mongo insert + call-log POST) to finish.
    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=_RoomOptionsCls(close_on_disconnect=False),
    )

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

        async def _trigger_greeting() -> None:
            nonlocal _greeting_done, _bot_has_spoken, _greeting_retry_triggered
            for _ in range(50):  # wait up to 5 s for the Gemini websocket connection
                async with _rt._session_lock:
                    connected = _rt._active_session is not None
                if connected:
                    break
                await asyncio.sleep(0.1)
            else:
                _log.warning("[GREETING] Gemini did not connect within 5 s; skipping trigger")
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
            # Retry the greeting trigger once; force-unmute only if retry also fails.
            await asyncio.sleep(8)
            if not _greeting_done and not _call_ended:
                _greeting_retry_triggered = True
                _log.warning("[GREETING] Gemini did not complete greeting within 8 s — retrying trigger")
                _rt._send_client_event(
                    types.LiveClientContent(
                        turns=[types.Content(parts=[types.Part(text=".")], role="user")],
                        turn_complete=True,
                    )
                )
                await asyncio.sleep(8)
                if not _greeting_done and not _call_ended:
                    _greeting_done = True
                    _bot_has_spoken = True
                    _set_mic(True, reason="greeting-retry-timeout")
                    _log.warning("[MIC] Greeting retry also failed — force-enabling mic")

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

    call_state["call_start_time"] = time.time()

    # 12. Resolve lead for greeting (use pre-fetched, or build fallback)
    record = call_state.get("lead_record")
    caller_mobile = normalize_mobile(sip_info["caller_number"]) if sip_info["caller_number"] else _room_mobile

    if not record and caller_mobile and not (_is_test_session and _room_meta_raw.get("srchterm")):
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
                call_state["record_id"] = record.get("_id") or _room_meta_raw.get("lead_id") or "test_lead"
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
        or "Thank you for your time. The relevant sellers will contact you soon. Goodbye!"
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
        try:
            await session.say(timeout_msg, allow_interruptions=False)
        except Exception:
            pass
        await asyncio.sleep(2)
        await _kick_caller_safe()
        asyncio.ensure_future(_save_and_close("completed"))

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
        if _rt is not None and getattr(_rt, "_active_session", None) is not None:
            try:
                _rt._send_client_event(
                    types.LiveClientContent(
                        turns=[types.Content(parts=[types.Part(text=".")], role="user")],
                        turn_complete=True,
                    )
                )
            except Exception:
                pass
        asyncio.ensure_future(_save_and_close("disconnected"))

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
