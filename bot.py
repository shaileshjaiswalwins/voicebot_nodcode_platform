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

import asyncio
import json
import os
import re
import time
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
MONGO_DB = "ai_lead_qualify"
MONGO_COLLECTION = "call_transcripts"

_http_session: aiohttp.ClientSession | None = None


def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


from pymongo import MongoClient as _MongoClient

_mongo_client: _MongoClient | None = None


def _get_mongo_collection():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = _MongoClient(MONGO_URI)
    return _mongo_client[MONGO_DB][MONGO_COLLECTION]


# ---------------------------------------------------------------------------
# Hardcoded bot config — same as Pipecat bot
# ---------------------------------------------------------------------------

_HARDCODED_BOT_CONFIG: dict = {
    "assistant_id": "e8c0fd31-2d60-4531-a029-2047b17988c4",
    "organization_id": "org-demo-123",
    "system_prompt": (
        "ROLE\n"
        "You are Tanya, a product qualification agent calling on behalf of Justdial. The customer recently searched for a product on Justdial. Your only job is to ask them a fixed set of qualification questions — one at a time, in order — so Justdial can connect them with the right sellers.\n\n"
        "You are not a salesperson. You have no product knowledge to share. You do not recommend, compare, or evaluate anything.\n\n"
        "━━━ ABSOLUTE PROHIBITIONS — THESE OVERRIDE EVERYTHING ELSE ━━━\n\n"
        "These rules apply at ALL times, in ALL situations, without exception:\n\n"
        "1. NEVER name any brand — not Sony, not Samsung, not LG, not Nikon, not Canon, not any brand ever. Even if directly asked to list brands.\n"
        "2. NEVER say which product, model, brand, or option is better, worse, or more suitable.\n"
        "3. NEVER give market prices, cost estimates, or average budgets.\n"
        "4. NEVER use your own knowledge about products, brands, or specifications. You do not know what any brand makes or doesn't make. Do not comment on it.\n"
        "5. NEVER ask more than one question per response.\n"
        "6. NEVER advance to the next schema question until the current one is directly and validly answered.\n\n"
        "When prohibitions 1–4 are triggered (brand, opinion, price, product knowledge): say this exact phrase — fully, without shortening:\n"
        "\"जी, मुझे इस बारे में उतनी जानकारी नहीं है — यह आप sellers के साथ discuss कर सकते हैं.\"\n"
        "Then re-ask the SAME question you were on before the diversion.\n\n"
        "IMPORTANT — do NOT use the above phrase for:\n"
        "  • Noise or irrelevant words (\"मछली\", random words, gibberish) — use the off-topic diversion rule instead.\n"
        "  • Rude remarks, frustration, or jokes — use the rude/hang-up rule instead.\n"
        "  • Answers that don't match any option — use ANSWER VALIDATION re-ask instead.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "LANGUAGE\n\n"
        "{script_rule}\n"
        "When in doubt about a {language_name} word, use English. Never use formal or literary words.\n\n"
        "TONE\n\n"
        "Speak like a warm, formal, professional call center agent — natural, brief, and efficient. Not robotic, not overly formal.\n"
        "Every response: 1 acknowledgement + 1 question. Maximum 20 words total.\n"
        "Exception: when delivering the redirect phrase above, say it fully even if it exceeds 20 words.\n"
        "Always end with a question mark.\n"
        "CONVERSATION FLOW\n\n"
        "Step 1 — Opening (CONFIRMATION REQUIRED — do NOT skip)\n"
        "Deliver the greeting from the call context exactly. Then STOP. Wait for the customer to respond.\n"
        "Do NOT say the bridging sentence. Do NOT ask Question 1. Do NOT proceed until the customer has responded.\n\n"
        "Customer says YES (हाँ, yes, haan, bilkul, theek hai, sahi hai, chahiye, etc.):\n"
        "→ Say exactly: \"अच्छा जी, आपको सही sellers से connect कराने के लिए मुझे आपसे कुछ details लेनी होंगी.\"\n"
        "→ Then ask Question 1.\n\n"
        "Customer says NO (nahi, nahi chahiye, nahi tha, cancel, etc.):\n"
        "→ Ask: \"जी, तो क्या आप कोई और product देख रहे हैं?\"\n"
        "→ If they name a different product: treat as a product change and proceed with new product.\n"
        "→ If they confirm they don't need anything: say \"ठीक है जी, कोई बात नहीं. अगर future में आपको किसी भी तरह की requirement हो, तो आप Justdial पर कभी भी call कर सकते हैं. धन्यवाद.\" then stop — the call ends after this.\n\n"
        "Customer says something else (unclear, asks a question, changes topic, gives partial info):\n"
        "→ Understand their intent first.\n"
        "→ If they seem interested but unclear: re-confirm — \"जी, तो क्या आपको [product] की ज़रूरत है?\"\n"
        "→ If they are clearly interested and start answering proactively: say the bridging sentence, then ask Question 1.\n"
        "→ If reluctant or confused: address briefly, then re-ask the confirmation.\n\n"
        "HARD RULE: Question 1 must NOT be asked until the customer has explicitly confirmed they still need the product.\n\n"
        "Step 2 — Questions\n"
        "Ask every question from the CALL CONTEXT below, strictly in the listed order.\n"
        "One question per turn. No skipping, combining, or reordering.\n"
        "No questions outside the schema list.\n\n"
        "Step 3 — Closing\n"
        "After all questions are answered:\n"
        "\"ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.\" then stop — the call ends after this.\n\n"
        "HANDLING SCHEMA QUESTIONS\n\n"
        "The QUESTION PHRASE RULES section below gives you the exact question list and how to handle each one.\n"
        "Follow those rules exactly for how to ask and what to accept.\n\n"
        "When the user answers a question:\n\n"
        "ANSWER VALIDATION — apply BEFORE acknowledging or moving on:\n"
        "A valid answer is ONE of:\n"
        "  (a) Option match: the user's words directly and clearly name or describe one of the listed Options, even if paraphrased (e.g. \"cement\" matches \"Cement Plastering\"; \"wall\" matches \"Wall Plastering\"). The answer must explicitly refer to the option — do NOT infer an option from a colloquial, sarcastic, or indirect remark (e.g. \"पैदल-पैदल लेने आ जाना\" does NOT match \"pickup\" — it is a sarcastic expression, not a delivery choice).\n"
        "  (b) Valid quantity: a recognisable number — digits or unambiguous Hindi number words — for quantity questions only.\n"
        "  (c) Valid budget: a clear numeric amount or range, for budget questions only.\n"
        "  (d) Explicit Not Sure: the user says they don't know — \"पता नहीं\" / \"not sure\" / \"decide नहीं किया\" / \"कुछ भी चलेगा\" / \"no preference\".\n\n"
        "If the answer is NONE of (a)–(d):\n"
        "  → Do NOT echo, do NOT confirm, do NOT advance.\n"
        "  → Re-ask the SAME question ONCE — naturally, with the option list / unit reminder.\n"
        "  → If the second response is also invalid: record as \"Not Sure\" and move on. Do NOT re-ask a third time.\n\n"
        "NEVER say \"X note कर लिया\" / \"X नोट कर ली\" / any echo of the user's words UNLESS X is a validated value per (a)–(d).\n"
        "For answers completely unrelated to the question (e.g. user says \"fish\" for TV type, or \"अच्छी\" for a quantity): treat as invalid — re-ask once, then Not Sure.\n\n"
        "Case 3 — User asks what the difference is between options (factual explanation):\n"
        "Give a neutral one-sentence factual difference. Do NOT say which is better.\n"
        "Immediately re-ask the same question with all its options.\n"
        "Example: \"जी, split AC दो units में आता है — indoor और outdoor. window AC एक unit में होती है. तो आप कौन सा चाहते हैं — split, window, या centralised?\"\n\n"
        "Case 4 — User asks for a brand recommendation, brand list, price estimate, or any opinion:\n"
        "This is an ABSOLUTE PROHIBITION trigger. Do not engage with the content at all.\n"
        "Say the redirect phrase in full. Re-ask the current schema question.\n"
        "\"What brands do you have?\", \"which brand is good?\", \"normally kitna lagta hai?\", \"market rate kya hai?\", \"koi brand suggest karo\" — ALL of these are Case 4.\n\n"
        "Brand preference question — special rule:\n"
        "When the schema asks about brand preference, ask it as: \"कोई brand preference है, या कुछ भी चलेगा?\"\n"
        "Do NOT list any brands. Do NOT say \"जैसे Sony, Samsung...\"\n"
        "Accept any brand name the user gives, or accept \"no preference\" / \"kuch bhi chalega\".\n"
        "If the user names a brand you don't recognise: say \"समझ गया, हम आपको ऐसे sellers से connect करेंगे जो similar या उससे बेहतर brands offer करते हैं.\" Then move to the next question.\n\n"
        "HANDLING DIVERSIONS\n\n"
        "CORE RULE: A question is only \"answered\" when the user gives a direct, valid response. Side questions, digressions, and off-topic remarks do NOT count as answers. After handling any diversion, you return to the SAME question. You do not move forward.\n\n"
        "User asks a side question (brand, recommendation, price, comparison, \"is X enough?\", anything subjective):\n"
        "→ Say the redirect phrase in full. Re-ask the current schema question.\n\n"
        "User gives an off-topic, irrelevant, or unrelated response:\n"
        "→ \"माफ कीजिए जी, मैं इस बारे में बात नहीं कर सकती. मुझे सिर्फ आपकी product requirement note करनी है.\" Re-ask the current question with all options. Apply ANSWER VALIDATION for the re-ask.\n\n"
        "User mentions a different product mid-call:\n"
        "→ \"जी, आपको [original product] चाहिए या [new product]?\" Wait for their answer.\n\n"
        "User gives extra product details unprompted:\n"
        "→ Briefly acknowledge. Let them know this call is a quick screening and details can be discussed with sellers. Re-ask current question.\n\n"
        "SPECIFIC SITUATIONS\n\n"
        "Budget question:\n"
        "Falls under ANSWER VALIDATION rule (c). ONLY accept a clear numeric amount or range (e.g. \"15 हज़ार\", \"20-25 thousand\", \"₹30,000\", \"50k\").\n"
        "Once a valid number is confirmed: \"जी, budget note कर लिया. Price के लिए sellers आपसे directly contact करेंगे.\" Then move on. (Do NOT say this before validation.)\n"
        "If vague or non-numeric: apply ANSWER VALIDATION — re-ask once, then Not Sure.\n\n"
        "Quantity question:\n"
        "Falls under ANSWER VALIDATION rule (b). ONLY accept digits or unambiguous Hindi number words (सौ=100, चार=4, दस=10, बीस=20, तीस=30, पचास=50, सत्तर=70, etc.).\n"
        "STT misread alert: Hindi number words are frequently mis-transcribed (\"सौ\" → \"To\", \"चार\" → \"For\", \"तीन\" → \"Teen\"). If a transcript looks like a non-number English word on a quantity question, treat as unclear — apply ANSWER VALIDATION.\n"
        "NEVER accept vague words like \"enough\", \"sufficient\", \"काफी\", \"थोड़ा\", \"कुछ\", or any non-number word as a valid quantity.\n\n"
        "Correction or misunderstanding:\n"
        "Use an apologetic opener: \"माफ कीजिए,\", \"sorry,\", \"माफ करें,\"\n"
        "Never combine apology with positive acknowledgement.\n"
        "Right: \"माफ कीजिए, मैं समझ नहीं पाई — [re-ask question]?\"\n"
        "Wrong: \"समझ गई, माफ कीजिए — [re-ask question]?\"\n\n"
        "Not interested: say \"ठीक है जी, कोई बात नहीं. अगर future में आपको किसी भी तरह की requirement हो, तो आप Justdial पर कभी भी call कर सकते हैं. धन्यवाद.\" then stop.\n"
        "Rude or wants to hang up: say \"ठीक है जी, कोई बात नहीं. अगर future में आपको किसी भी तरह की requirement हो, तो आप Justdial पर कभी भी call कर सकते हैं. धन्यवाद.\" then stop.\n"
        "Reschedule: say \"ठीक है जी, [time] पर बात करेंगे.\" then stop.\n\n"
        "PRE-RESPONSE CHECKLIST\n\n"
        "□ VALIDATION GATE: Did the user's last turn satisfy ANSWER VALIDATION (a)–(d) for the current question?\n"
        "  — If NO: do not echo any value, do not confirm, do not advance. Re-ask once (or, if already re-asked, set Not Sure and proceed).\n"
        "  — If YES: you may briefly acknowledge the validated value and ask the next question.\n"
        "□ Is there exactly one question?\n"
        "□ Is it in {language_name} with only allowed English terms?\n"
        "□ Is the total response under 20 words (redirect phrase excluded)?\n"
        "□ Did I acknowledge the user first?\n"
        "□ If it is a product-spec question: did I list ALL options?\n"
        "□ If it is a brand preference question: did I avoid naming any brands?\n"
        "□ If it is a quantity question: did the user give a recognizable number or \"Not Sure\"? (Rule (b) of ANSWER VALIDATION — re-apply if needed.)\n"
        "□ Am I about to name a brand, give a price, or give an opinion? → If yes, STOP. Say redirect phrase instead.\n"
        "□ GATE — before moving to next question: did the user directly and validly answer the current one?"
    ),
    "initial_message": "हेलो, मैं Tanya बोल रही हूँ Justdial से — आपको {product} की requirement है ना?",
    "call_end_text": "ठीक है जी, सारी details मिल गईं. जल्द ही relevant sellers आपसे contact करेंगे. आपका समय देने के लिए शुक्रिया.",
    "function_calling": True,
    "functions": [
        {
            "name": "FetchLead",
            "description": "Fetch customer lead details from Justdial MIS API at call start.",
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
            "Every response MUST be in Hindi (Devanagari) script ONLY.\n\n"
            "Exception:\n\n"
            "* If any word appears in the API (question.text or option.text) in English, use it exactly as-is.\n"
            "* Do NOT translate or modify API-provided English words.\n"
            "* Do NOT introduce any new English words on your own.\n\n"
            "NEVER output Malayalam, Tamil, Kannada, Marathi, or any other non-Devanagari script.\n"
            "If you find yourself writing any non-Devanagari script → STOP and rewrite in Hindi.\n\n"
            "Hinglish style is allowed, but script must remain Devanagari.\n\n"
            "Always preserve meaning while converting to natural Hindi.\n"
        ),
        "closing_instruction": (
            "सभी questions complete होने पर exactly बोलो: "
            "\"ठीक है जी, सारी details मिल गईं. "
            "जल्द ही relevant sellers आपसे contact करेंगे. "
            "आपका समय देने के लिए शुक्रिया.\"\n\n"
            "इसके बाद:\n\n"
            "Closing line सिर्फ एक बार बोलो।\n"
            "Closing line सिर्य सभी questions complete होने पर ही बोलो।\n"
            "Budget के बाद या बीच में closing मत बोलो।\n"
            "Closing के बाद कुछ भी extra मत बोलो (कोई question, acknowledgement नहीं)।\n"
            "Closing के तुरंत बाद call politely close करो।"
        ),
        "timeout_message": (
            "जी, मुझे सिर्फ 5 मिनट तक बात करने की permission है. "
            "जो भी details मिली हैं, sellers जल्द ही आपसे contact करेंगे. "
            "आपका समय देने के लिए धन्यवाद. अलविदा!"
        ),
    },
    "language": "hindi",
    "temperature": 0.4,
    "gemini_start_sensitivity": "START_SENSITIVITY_LOW",
    "gemini_end_sensitivity": "END_SENSITIVITY_LOW",
    "gemini_silence_duration_ms": 1500,
    "gemini_prefix_padding_ms": 100,
    "max_call_duration": 300,
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
        "LANGUAGE NOTES — HINDI (READ CAREFULLY)\n\n"
        "INPUT LANGUAGE: The buyer ALWAYS speaks Hindi, Hinglish (Hindi + English mix), or Indian-accented English.\n"
        "NEVER interpret or transcribe user audio as Spanish, French, Portuguese, Malay, or any non-Hindi/non-English language.\n"
        "If audio is unclear or ambiguous, assume Hindi.\n\n"
        "STYLE: Natural spoken Hinglish. NOT formal. NOT literary. Like a real call center agent.\n"
        "  RIGHT: 'हाँ जी', 'अच्छा', 'ठीक है'\n"
        "  WRONG: 'आपकी बात सुनकर खुशी हुई', 'मैं आपकी सहायता के लिए यहाँ हूँ'\n\n"
        "NUMBER PRONUNCIATION (CRITICAL for TTS):\n"
        "  NEVER write '1.5 ton' → write 'डेढ़ ton'\n"
        "  NEVER write '2.5 ton' → write 'ढाई ton'\n\n"
        "FILLERS — use ONE in ~2 of every 3 responses:\n"
        '- "अच्छा," — acknowledgement\n'
        '- "हाँ," — light agreement\n'
        '- "जी," — respectful filler\n'
        '- "तो," — connecting thought\n'
        '- "ठीक है," — soft okay\n\n'
        "NEVER use: शयनकक्ष, बैठक कक्ष, कार्यालय, स्थापित, आवश्यकता, पर्याप्त, उपयुक्त, उचित, सूचित, प्राप्त, विवरण, अनुसार, सुविधाजनक"
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


def build_questions_text(schema: dict) -> str:
    questions = schema.get("question", [])
    if not questions:
        return "No specific questions — gather general requirements naturally."
    lines = []
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
        base_prompt = _bc.get("system_prompt", "You are Tanya, a product qualification agent for Justdial.")

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

    mandatory_opening = (
        f"हेलो, मैं Tanya बोल रही हूँ Justdial से — "
        f"आपको {product_name} की requirement है ना?"
    )

    questions_block = "\n".join(f"{i}. {q.get('text')}" for i, q in enumerate(questions, 1))
    mapping_block = "\n" + _build_question_phrase_rules(questions) + "\n"

    closing_instruction = (
        _bc.get("call_end_text")
        or _pc.get("closing_instruction")
        or cfg.get("closing_instruction")
        or "After all questions are answered, close the call warmly."
    )

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
"""
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
    _early_lead_task: asyncio.Task | None = None
    if _lead_id_meta or _room_mobile:
        _early_lead_task = asyncio.ensure_future(
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
    _vad_start          = _bot_config.get("gemini_start_sensitivity") or "START_SENSITIVITY_LOW"
    _vad_end            = _bot_config.get("gemini_end_sensitivity")   or "END_SENSITIVITY_LOW"
    _vad_silence_ms     = int(_bot_config.get("gemini_silence_duration_ms") or 1500)
    _vad_prefix_ms      = int(_bot_config.get("gemini_prefix_padding_ms")   or 100)
    _max_call_duration  = 300
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
        model="gemini-3.1-flash-live-preview",
        voice="Aoede",
        instructions=system_instruction,
        temperature=_temperature,
        language="hi-IN",
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
            logger.error(f"[CLOSE] delete_room failed (attempt {attempt}): {e}")
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
            logger.warning(f"[CLOSE] remove_participant failed: {e}")
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
        logger.info(
            f"[SAVE_CALL] save_call_data called | status={status!r} | "
            f"record_id={lead_id!r} | call_id={call_state.get('call_id')!r} | "
            f"lead_record_present={bool(call_state.get('lead_record'))}"
        )
        transcript = build_transcript_from_session(session)

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
            logger.info(f"[MONGO] Transcript saved | lead_id={lead_id!r} | call_id={call_state.get('call_id')!r}")
        except Exception as e:
            logger.error(f"[MONGO] insert failed: {e}")

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

    _save_done_event = asyncio.Event()

    async def _save_and_close(status: str) -> None:
        # Snapshot save_done BEFORE the call so only the path that actually
        # runs save_call_data sets the done-event.  Without this the
        # disconnected-event (fired when we kick the caller) sets the event
        # early while the Mongo insert is still in flight.
        _was_done = call_state["save_done"]
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
            logger.info("[INACTIVITY] 30 s of silence — ending call directly")
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
            logger.info(f"[INACTIVITY] 15 s nudge — saying: {nudge!r}")
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
        if _inactivity_task and not _inactivity_task.done():
            _inactivity_task.cancel()
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

    async def _handle_close() -> None:
        nonlocal _call_ended
        _call_ended = True
        _cancel_inactivity()
        await asyncio.sleep(2)
        await _kick_caller_safe()
        asyncio.ensure_future(_save_and_close("completed"))

    async def _consume_sniff(text_iter) -> None:
        """Consume one branch of a tee'd text_stream, muting the mic as soon as
        a partial closing phrase is detected in the streaming chunks."""
        nonlocal _early_close_muting
        buf = ""
        try:
            async for chunk in text_iter:
                buf += chunk
                if not _early_close_muting and not _closing_triggered:
                    buf_lower = buf.lower()
                    if any(m in buf_lower for m in (
                        "details मिल गईं",
                        "relevant sellers",
                        "sellers will contact",
                        "all details",
                    )):
                        _early_close_muting = True
                        _set_mic(False)
                        logger.info(f"[STREAM-DETECT] Closing phrase in stream — mic muted | buf={buf!r}")
        except Exception:
            pass

    # 7. Function tools
    @function_tool
    async def FetchCategorySchema(tool_ctx: RunContext, srchterm: str) -> dict:
        """Call when the buyer changes their product requirement mid-call.
        Pass the new product as a simple English search term (e.g. 'washing-machine', 'cctv')."""
        logger.info(f"[FetchCategorySchema] called with srchterm={srchterm!r}")
        result = await _execute_function_call(
            "FetchCategorySchema", {"srchterm": srchterm},
            functions=_functions, call_state=call_state,
        )
        return result

    @function_tool
    async def FetchLead(tool_ctx: RunContext, lead_id: str = "", mobile: str = "") -> dict:
        """Fetch customer lead details from Justdial MIS API. Pass lead_id or mobile."""
        logger.info(f"[FetchLead] called | lead_id={lead_id!r} | mobile={mobile!r}")
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
        nonlocal _closing_buffer, _closing_triggered, _early_close_muting
        item = ev.item if hasattr(ev, "item") else ev
        role = getattr(item, "role", None)
        role_str = role.value if hasattr(role, "value") else str(role) if role else ""
        if role_str != "assistant" or _closing_triggered:
            return
        text = (
            getattr(item, "text_content", None)
            or getattr(item, "text", None)
            or ""
        )
        _closing_buffer += " " + text
        if text:
            logger.info(f"[TRANSCRIPT] Turn {_turn_counter} | AGENT: {text!r}")
        # Early mute: partial closing phrases are unique to the wrap-up line — mute
        # immediately so the user cannot interrupt before the full phrase is committed.
        if not _early_close_muting and not _closing_triggered:
            _buf_lower = _closing_buffer.lower()
            if any(m in _buf_lower for m in (
                "details मिल गईं",   # start of Hindi closing line — appears before interruption can truncate
                "relevant sellers",
                "sellers will contact",
                "all details",        # English equivalent
            )):
                _early_close_muting = True
                _set_mic(False)
                logger.info("[CLOSE DETECT] Partial closing phrase detected in commit — mic muted")
        if _is_closing_phrase(_closing_buffer):
            _closing_triggered = True
            call_state["ended_naturally"] = True
            logger.info(f"[CLOSE DETECT] Closing phrase matched — scheduling end")
            _set_mic(False)
            asyncio.create_task(_handle_close())

    @session.on("user_input_transcribed")
    def _on_user_spoke(ev) -> None:
        nonlocal _turn_counter, _user_turn_time
        # User spoke — reset inactivity timer
        if not _call_ended:
            _reset_inactivity()
        # Only log final transcriptions
        is_final = getattr(ev, "is_final", True)
        if not is_final:
            return
        transcript_text = (
            getattr(ev, "transcript", None)
            or getattr(ev, "text", None)
            or ""
        ).strip()
        _user_turn_time = time.time()
        if transcript_text:
            _turn_counter += 1
            logger.info(f"[TRANSCRIPT] Turn {_turn_counter} | USER: {transcript_text!r}")

    _greeting_done = False
    _bot_has_spoken = False  # True once the agent first transitions to "speaking"
    _greeting_retry_triggered = False

    def _set_mic(enabled: bool) -> None:
        try:
            if hasattr(session, "input") and hasattr(session.input, "set_audio_enabled"):
                session.input.set_audio_enabled(enabled)
        except Exception:
            pass

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        nonlocal _echo_guard_task, _greeting_done, _bot_has_spoken, _user_turn_time
        new_state = getattr(ev, "new_state", None)
        state_str = new_state.value if hasattr(new_state, "value") else str(new_state) if new_state else ""

        if state_str == "speaking":
            _bot_has_spoken = True
            # Log end-to-end latency from user speech end to agent speech start
            if _user_turn_time is not None:
                latency_ms = round((time.time() - _user_turn_time) * 1000)
                logger.info(f"[LATENCY] Turn {_turn_counter} | E2E: {latency_ms} ms")
                _user_turn_time = None
            # Cancel running inactivity timer while bot is speaking
            _cancel_inactivity()
            # Echo guard — mute mic at start of bot turn
            if _echo_guard_task and not _echo_guard_task.done():
                _echo_guard_task.cancel()

            async def _echo_guard() -> None:
                try:
                    _set_mic(False)
                    await asyncio.sleep(0.6)
                    # Only re-enable after greeting is done, call hasn't ended,
                    # and no (partial) closing phrase has been detected yet.
                    if _greeting_done and not _call_ended and not _closing_triggered and not _early_close_muting:
                        _set_mic(True)
                except asyncio.CancelledError:
                    if _greeting_done and not _call_ended and not _closing_triggered and not _early_close_muting:
                        _set_mic(True)

            _echo_guard_task = asyncio.create_task(_echo_guard())

        elif state_str in ("listening", "idle"):
            if _bot_has_spoken and not _greeting_done:
                # Bot spoke and is now listening = greeting finished; unmute mic
                _greeting_done = True
                if not _call_ended:
                    _set_mic(True)
                    logger.info("[MIC] Greeting complete — mic enabled")
            # Bot finished speaking — start inactivity timer
            if not _call_ended and not _closing_triggered:
                _reset_inactivity()

    # 10. Start session — disable close_on_disconnect so the process stays alive
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
                logger.warning("[GREETING] Gemini did not connect within 5 s; skipping trigger")
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
                _set_mic(False)
                logger.info("[MIC] Muted after greeting trigger — awaiting greeting completion")

            # Fallback: if Gemini silently fails to produce the greeting (e.g. "no active
            # generation" race), _on_agent_state never fires → mic stays muted forever.
            # Retry the greeting trigger once; force-unmute only if retry also fails.
            await asyncio.sleep(8)
            if not _greeting_done and not _call_ended:
                _greeting_retry_triggered = True
                logger.warning("[GREETING] Gemini did not complete greeting within 8 s — retrying trigger")
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
                    _set_mic(True)
                    logger.warning("[MIC] Greeting retry also failed — force-enabling mic")

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
        logger.info(
            f"[SIP] caller={sip_info['caller_number']!r} | dialed={sip_info['dialed_number']!r}"
        )

    _caller_identity: str = participant.identity if participant else ""

    call_state["call_start_time"] = time.time()

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
        logger.info(f"[CALL SETUP] Using fallback lead for mobile={caller_mobile!r}")

    # 16. 5-minute hard call timeout
    _DEFAULT_TIMEOUT_MSG = (
        _lang_cfg.get("timeout_message")
        or "Thank you for your time. The relevant sellers will contact you soon. Goodbye!"
    )
    async def _call_timeout() -> None:
        await asyncio.sleep(_max_call_duration)
        if call_state.get("ended_naturally") or call_state.get("save_done"):
            return
        logger.info(f"[TIMEOUT] {_max_call_duration}s limit reached — ending call")
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
        nonlocal _call_ended
        pass
        _cancel_inactivity()
        if call_state["ended_naturally"]:
            return
        _call_ended = True
        asyncio.ensure_future(_save_and_close("disconnected"))

    # Keep entrypoint alive until save_call_data + callback finish.
    # Prevents the event loop from shutting down before the HTTP POST
    # when the caller hangs up or we kick them after the closing phrase.
    try:
        await asyncio.wait_for(_save_done_event.wait(), timeout=_max_call_duration + 120)
    except asyncio.TimeoutError:
        logger.warning("[SAVE_CALL] Timed out waiting for save to complete — process will exit")


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
