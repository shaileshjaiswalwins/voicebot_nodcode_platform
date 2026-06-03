"""Post-call Gemini analysis — ported verbatim from bot.py."""

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger

from .config import GEMINI_API_KEY

IST = timezone(timedelta(hours=5, minutes=30))


def _strip_punct(s: str) -> str:
    """Strip punctuation/separators while keeping Unicode letters AND combining marks.

    Python's \\w strips Devanagari vowel marks (category Mn) because they are
    not 'word characters' in the regex sense. This breaks matching for scripts
    like Devanagari where vowels are combining diacritics (e.g. हेलो→हल via \\w).
    We keep categories L (letters), M (marks/combining), N (numbers) instead.
    """
    return "".join(
        c for c in unicodedata.normalize("NFC", s.lower())
        if unicodedata.category(c)[0] in ("L", "M", "N")
    )


# Individual NFC-normalised lowercase words that indicate a bare call-presence signal
# rather than product engagement. Derived by tokenising all greeting/acknowledgement
# phrases. Used in two places:
#   1. Muted-transcript-only check → if all muted words are in this set → Short Hangup
#   2. Live-transcript check → if all user turns contain only these tokens AND the agent
#      never progressed past the greeting → Short Hangup
_BARE_CALL_SIGNAL_TOKENS: frozenset = frozenset(
    unicodedata.normalize("NFC", word.lower())
    for phrase in {
        # hello / hi variants
        "hello", "हेलो", "helo", "halo", "हैलो", "hi", "हाय",
        # Gujarati/Marathi phone-answer greeting — "Om" / "Jai Shree Krishna" shortened
        # "Om Hello" is purely a phone-pickup reflex, not product confirmation
        "om", "ом",
        # haan / ji / yes variants
        "हाँ", "हां", "haan", "ha", "han", "ji", "jee",
        # Devanagari "yes" (STT sometimes transcribes English "yes" in Devanagari script)
        "यस",
        # Devanagari "all right" — common Hindi phone-filler / acknowledgement
        "ऑल राइट", "ऑल",
        # Phone-answer honorifics — "सर", "मैडम" etc. appearing alone or with bare ack
        "सर", "sir", "मैडम", "madam", "ma'am",
        # "go ahead / speak" — call-answering phrases, NOT product confirmation
        "haan bolo", "ha bolo", "हाँ बोलो", "हां बोलो",
        "bolo", "बोलो", "bol", "बोल",
        # okay / fine
        "okay", "ok",
        # stall / hold phrases
        "ek second", "एक second", "एक सेकंड",
        "hold on", "ruko",
        # identity questions
        "kaun", "कौन", "kaun hai", "कौन है",
        # common call-acknowledgement fillers (achha / theek / sahi)
        "acha", "achha", "accha", "achcha",
        "अच्छा", "अच्छे",               # Devanagari achha — GP-4 listed, was missing
        "theek", "thik",
        "ठीक", "ठीक है", "ठीक है जी",  # Devanagari theek — was missing
        "haan ji", "ji haan",
        "जी", "जी हाँ", "हाँ जी", "जी हां", "हां जी",  # Devanagari ji variants
        "sahi", "सही",                  # Devanagari sahi
        # exclamations / filler — NOT product confirmation
        "वाह", "वाह वाह", "wah", "arrey", "अरे",
        # function word "है" appearing alone is a filler, not substantive content
        # e.g. "हाँ हाँ है है" from Sarvam rescue injections on silence
        "है",
        # Marathi/regional reflexive phone-pickup "yes" — not product confirmation
        "हो", "हो जी", "होजी", "हाँ हो",
    }
    for word in phrase.split()
    if word.strip()
)

# Tokens that mean "tell me what this call is about" — the caller is seeking context,
# NOT confirming they need the product. Used to extend the all-bare-signals guard so
# that an "इनफो" / "info" response doesn't fall through to LLM as a potential Enriched.
_INFO_REQUEST_TOKENS: frozenset = frozenset(
    unicodedata.normalize("NFC", w.lower())
    for w in {
        "info", "इनफो",
        "information",
        "jankari", "jankaari", "जानकारी",
    }
)

# Phrases Gemini sometimes generates as its FIRST turn instead of the real greeting.
# When detected, agent progression cannot be used to infer product confirmation.
_WRONG_OPENER_PHRASES: tuple[str, ...] = (
    "क्या आप अभी line पर हैं",
    "क्या आप अभी लाइन पर हैं",
    "kya aap abhi line par hain",
    "kya aap line par hain",
    "क्या आप line पर हैं",
    "are you on the line",
)

DISPOSITION_MAP: dict[str, str] = {
    "Short Hangup":                      "The call ended with no product discussion — the customer said nothing at all, OR gave only a bare call-acknowledgment (e.g. hello, haan, hold on, ek second) and disconnected before any product topic was raised.",
    "Voicemail":                        "The call went to the recipient's voicemail instead of connecting directly.",
    "Wrong Number":                     "The number dialed does not belong to the intended customer.",
    "Approved":                         "The customer confirmed the product and answered ALL specification questions.",
    "Enriched":                         "The customer confirmed the product and answered at least one (but not all) specification questions with valid specific values.",
    "Interested":                       "The customer confirmed they need the product but answered ZERO specification questions with valid specific values, OR showed clear positive interest (engaged meaningfully, asked follow-up questions, showed enthusiasm) without answering any spec questions. Covers both explicit product confirmation with zero specs and positive-but-unconfirmed engagement.",
    "Not Interested":                   "The customer clearly stated they are not interested or do not need the product.",
    "Could Not Confirm":                "The customer was uncertain or did not confirm whether they still need the product — includes vague/non-committal responses, mid-conversation disconnections where no product confirmation was obtained, and cases where the call dropped before any meaningful product exchange.",
    "Alternate Number":                 "The customer provided a different or alternate contact number.",
    "Already Spoken":                   "The customer has already discussed or interacted about the requirement with JD or the seller, OR the customer's requirement has already been fulfilled.",
    "Will do it Myself":                "The customer still has the requirement but will source/handle it themselves without JD's help — they explicitly declined seller connections (e.g. 'मैं खुद देख लूँगा', 'I'll manage it myself'). The need exists; only JD's assistance is rejected. Distinct from Not Interested.",
    "Call Rescheduled":                 "The customer asked to call at a specific date and time.",
    "Seller Intent":                    "The caller is a seller or vendor trying to offer their own products/services — they are NOT a buyer with a requirement. They may want to list on JustDial or pitch their business. This is the opposite of a buyer lead.",
    "Abusive Lead":                     "The recipient exhibited abusive or inappropriate behavior during the call.",
    "DNC Client : Don't Call Further":  "The customer explicitly requested not to be contacted again.",
    "Other Cases":                      "The call outcome does not fit into any predefined categories.",
    "Technical Issue - Call Connected": "The call connected but was disrupted by technical issues.",
    "Language Issue":                   "Communication was not possible due to a language mismatch.",
}

_VALID_OUTCOMES = set(DISPOSITION_MAP.keys())


def status_to_outcome(status: str) -> str:
    return "Could Not Confirm"


def fuzzy_match_opt_id(answ: str, options: list[dict]) -> str | None:
    """Return the option id whose text best matches answ, handling STT digit-drops."""
    if not answ or not options:
        return None
    normalized = re.sub(r"\s+", " ", answ.strip().lower())
    answ_digits = re.sub(r"[^0-9]", "", normalized)
    for opt in options:
        opt_text = (opt.get("text") or "").strip().lower()
        if normalized == opt_text:
            return opt.get("id")
    if answ_digits:
        for opt in options:
            opt_text = (opt.get("text") or "").strip().lower()
            opt_digits = re.sub(r"[^0-9]", "", opt_text)
            if opt_digits and opt_digits.endswith(answ_digits) and len(opt_digits) > len(answ_digits):
                return opt.get("id")
    return None


def fallback_analysis(status: str) -> dict:
    outcome = status_to_outcome(status)
    return {
        "call_outcome": outcome,
        "call_outcome_description": DISPOSITION_MAP.get(outcome, ""),
        "call_summary": "", "is_business": "", "qna": [],
        "product_change": {}, "rescheduled_to": "",
    }


async def generate_call_analysis(
    transcript: list[dict],
    base_status: str,
    schema: dict,
    http_session: aiohttp.ClientSession,
    model: str = "gemini-3.1-flash-lite",
    muted_transcript: list[str] | None = None,
    gemini_connect_failed: bool = False,
    duration_secs: float | None = None,
    greeting_done: bool = True,
    user_speech_ms: int = 0,
    wrong_opener_detected: bool = False,
) -> dict:
    if gemini_connect_failed:
        return {
            "call_outcome": "Technical Issue - Call Connected",
            "call_outcome_description": DISPOSITION_MAP["Technical Issue - Call Connected"],
            "call_summary": "Gemini realtime WebSocket failed to connect — bot was silent, no greeting was spoken.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }
    if not transcript:
        return fallback_analysis(base_status)

    # --- Deterministic pre-LLM guards (saves cost + prevents model misclassification) ---

    # Any turn explicitly tagged role="ivr" means the call was answered by an automated system.
    if any(t.get("role") == "ivr" for t in transcript):
        return {
            "call_outcome": "Voicemail",
            "call_outcome_description": DISPOSITION_MAP["Voicemail"],
            "call_summary": "Call was answered by an automated IVR system, not a live person.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    user_turns = [t for t in transcript if t.get("role") == "user"]
    non_empty_user_turns = [t for t in user_turns if (t.get("text") or "").strip()]

    # Tokens that count as product confirmation when they appear as the buyer's
    # ONLY or FIRST substantive response to the opening product question.
    _CONFIRMATION_TOKENS = {
        "हाँ", "हां", "ha", "han", "haan", "yes", "ji", "jee",
        "bilkul", "zaroor", "theek", "ठीक", "okay", "ok",
        "good", "गुड", "sure", "right", "correct", "हा",
    }

    # No real user speech captured. Check how far the agent progressed before deciding.
    # The bot never advances to the next question without a valid answer — so agent turn
    # count tells us whether the buyer actually spoke (STT just failed to capture it).
    _agent_turns_with_text = [
        t for t in transcript
        if t.get("role") == "assistant" and (t.get("text") or "").strip()
    ]
    # Detect Gemini wrong-opener: when Gemini generates a connection-probe as its first
    # turn instead of the product greeting, agent progression is unreliable — the bot may
    # have advanced on ambient noise or background conversation, not product confirmation.
    _first_agent_text_lower = (_agent_turns_with_text[0].get("text") or "").lower() if _agent_turns_with_text else ""
    # Also detect a truncated greeting: agent started with "हेलो…" but the TTS was cut
    # before the product question ("requirement है ना?" / "चाहिए?" / "चाहिए थे?").
    # In that case the user's "हाँ/जी" was a response to an incomplete utterance, NOT to
    # the product question — agent progression cannot be used to infer product confirmation.
    _greeting_start = any(
        _first_agent_text_lower.startswith(p)
        for p in ("हेलो", "hello", "helo", "नमस्ते", "namaste")
    )
    _greeting_has_product_q = any(
        kw in _first_agent_text_lower
        for kw in (
            "requirement", "है ना", "चाहिए", "chahiye", "zaroorat",
            # new 2-step greeting: Step 1a
            "देख रहे", "dekh rahe", "dekh rhe",
        )
    )
    _truncated_greeting = _greeting_start and not _greeting_has_product_q
    _wrong_opener = (
        wrong_opener_detected
        or any(p.lower() in _first_agent_text_lower for p in _WRONG_OPENER_PHRASES)
    )
    # Check whether any user-side signal exists at all (live transcript OR muted capture).
    _has_any_user_signal = bool(non_empty_user_turns) or bool(
        muted_transcript and any((m or "").strip() for m in muted_transcript)
    )
    # Compute early — needed both by the pre-LLM bare-signal guard (below) and by the
    # _first_turn_is_confirmation check later.  The bot never asks spec questions without
    # product confirmation, so ≥2 agent turns with at least one live user turn means the
    # buyer genuinely progressed past the greeting.
    _agent_progressed = bool(non_empty_user_turns) and len(_agent_turns_with_text) >= 2
    if not non_empty_user_turns:
        if not _has_any_user_signal:
            # Zero user speech from any source.
            # Distinguish by whether the greeting actually completed and whether any
            # sub-threshold audio was detected (user spoke but STT couldn't transcribe).
            if not greeting_done:
                _summary = "User disconnected before or during the agent greeting — no response at all."
            elif user_speech_ms > 0:
                _summary = (
                    f"Greeting completed. User spoke briefly (~{user_speech_ms}ms, below STT threshold) "
                    "then disconnected — no transcribable response captured."
                )
            else:
                _summary = "No user response recorded — call ended after agent greeting only."
            return {
                "call_outcome": "Short Hangup",
                "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
                "call_summary": _summary,
                "is_business": "", "business_city": "", "business_name": "",
                "qna": [], "product_change": {}, "rescheduled_to": "",
            }
        # Muted transcript has content — check if it's only greetings/acknowledgements.
        # A bare "hello" / "haan bolo" / "haan" during the bot's opening turn is not
        # product confirmation.  Normalise to NFC so Devanagari from different STT
        # engines compares correctly regardless of Unicode composition form.
        def _nfc(s: str) -> str:
            return unicodedata.normalize("NFC", s)
        _muted_words = {
            _nfc(w.strip(".,!? ।").lower())
            for m in (muted_transcript or [])
            # Strip STT confidence tags like "[low-confidence]" before tokenising
            for w in re.sub(r'\[.*?\]', '', _nfc(m or '')).split()
            if w.strip(".,!? ।")
        }
        if _muted_words and not (_muted_words - _BARE_CALL_SIGNAL_TOKENS):
            if greeting_done and user_speech_ms > 0:
                _summary = (
                    f"Greeting completed. User acknowledged with a greeting during the bot turn "
                    f"then spoke briefly (~{user_speech_ms}ms, below STT threshold) and disconnected "
                    "— no product confirmation obtained."
                )
            elif not greeting_done:
                _summary = "User acknowledged with a greeting during the bot turn then disconnected before greeting completed — no product confirmation obtained."
            else:
                _summary = "User responded with a greeting or acknowledgement only — no product confirmation or engagement obtained."
            return {
                "call_outcome": "Short Hangup",
                "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
                "call_summary": _summary,
                "is_business": "", "business_city": "", "business_name": "",
                "qna": [], "product_change": {}, "rescheduled_to": "",
            }
        # Muted transcript has substantive content but no live user turns — STT failed on live mic
        # but user did speak during muted window. Fall through to LLM with context.

    # Hard IVR signals — checked across ALL turns and muted_transcript, position doesn't matter.
    # These phrases never appear in genuine human speech, so any occurrence means IVR answered.
    _HARD_IVR_SIGNALS = [
        # DTMF prompts
        "press 1", "press 2", "press 3", "press 4", "press 5",
        "press 6", "press 7", "press 8", "press 9", "press 0",
        "dial 1", "dial 2", "dial 3",
        "for english press", "hindi ke liye", "हिंदी के लिए दबाएं",
        "please press", "kindly press",
        # Automated queuing / unavailability
        "all our representatives are busy", "all agents are busy",
        "our executives are busy", "all our executives are busy",
        "currently busy", "please hold the line",
        "your call is important to us",
        "estimated wait time",
        "you are number", "in the queue",
        # Automated connection notices
        "your call is being connected", "apka call connect",
        "connecting your call",
        "this call may be recorded for quality",
        "this call is being recorded for training",
    ]

    _all_text = " ".join(
        (t.get("text") or "").lower() for t in transcript
    )
    _muted_text = " ".join((m or "").lower() for m in (muted_transcript or []))
    _full_text = f"{_all_text} {_muted_text}"
    if any(sig in _full_text for sig in _HARD_IVR_SIGNALS):
        return {
            "call_outcome": "Voicemail",
            "call_outcome_description": DISPOSITION_MAP["Voicemail"],
            "call_summary": "Call was answered by an automated IVR system, not a live person.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    _VOICEMAIL_SIGNALS_PRE = [
        "leave a message", "leave your message", "please leave a message",
        "after the tone", "at the beep",
        "you have reached", "you've reached",
        "unable to take your call", "cannot take your call",
        "not available to take your call",
        "record your message", "record a message",
        "mailbox is full", "mailbox full",
        "voice mail recording", "voicemail recording",
        "finished recording hang up", "when you have finished recording",
    ]
    _HOLD_MUSIC_SIGNALS_PRE = [
        "put your call on hold",
        "placed your call on hold",
        "has put your call on hold",
        "please stay on the line",
        "stay on the line",
        "पुट योर कॉल ऑन होल्ड",           # transliterated English in Hindi script
        "होल्ड पर राख्यो छे",              # Gujarati hold-music phrase
        "hold par rakho chhe",
    ]

    # Only short-circuit if the signal appears before any real user response.
    # If hold/voicemail text appears after a real conversation, let the LLM decide.
    _GREETING_TOKENS = {"hello", "हेलो", "helo", "halo", "हैलो"}
    seen_substantive_user_turn = False
    for turn in transcript:
        text_lower = (turn.get("text") or "").lower()
        if turn.get("role") in ("user", "buyer"):
            words = {w.strip(".,!? ").lower() for w in (turn.get("text") or "").split() if w.strip()}
            if words - _GREETING_TOKENS:
                seen_substantive_user_turn = True
        if seen_substantive_user_turn:
            continue
        if any(sig in text_lower for sig in _VOICEMAIL_SIGNALS_PRE):
            return {
                "call_outcome": "Voicemail",
                "call_outcome_description": DISPOSITION_MAP["Voicemail"],
                "call_summary": "Call was answered by voicemail or automated IVR system.",
                "is_business": "", "business_city": "", "business_name": "",
                "qna": [], "product_change": {}, "rescheduled_to": "",
            }
        if any(sig in text_lower for sig in _HOLD_MUSIC_SIGNALS_PRE):
            return {
                "call_outcome": "Could Not Confirm",
                "call_outcome_description": DISPOSITION_MAP["Could Not Confirm"],
                "call_summary": "Caller placed the bot on hold; no product confirmation was obtained.",
                "is_business": "", "business_city": "", "business_name": "",
                "qna": [], "product_change": {}, "rescheduled_to": "",
            }

    # Pre-LLM: detect agent's not-interested closing phrase.
    # The bot emits "कोई बात नहीं जी, future में ज़रूरत हो तो Justdial पे call कर सकते हैं"
    # most commonly when the buyer rejected the product, but also (incorrectly) when the
    # buyer is a seller/distributor or has already spoken to a seller. Bypass the short-circuit
    # for those cases so the LLM can assign the correct outcome.
    _NI_AGENT_MARKERS = [
        "कोई बात नहीं",
        "future में ज़रूरत",
        "future mein zaroorat",
        "justdial पे call",
        "justdial pe call",
        "ज़रूरत हो तो",
        "zaroorat ho toh",
    ]
    _last_agent_text = (_agent_turns_with_text[-1].get("text") or "").lower() if _agent_turns_with_text else ""
    # Guard: the approved closing also contains "कोई बात नहीं जी" as a filler before
    # "sellers आपको सब guide कर लेंगे / सारी details मिल गईं / relevant sellers".
    # Only fire the NI short-circuit when the approved closing markers are absent.
    _approved_closing_present = (
        "सारी details मिल गईं" in _last_agent_text
        or "सारी डिटेल्स मिल गई" in _last_agent_text   # Devanagari variant emitted by bot
        or "relevant sellers" in _last_agent_text
    )
    # Bypass patterns: if user turns contain seller-side signals, fall through to LLM so it
    # can classify as Seller Intent instead of Not Interested.
    _NI_SELLER_BYPASS = [
        "खुद डिस्ट्रीब्यूट", "khud distribute", "hum distribute", "हम डिस्ट्रीब्यूट",
        "apne aap distribute",
        "खुद बेचते", "khud bechte", "hum bechte", "हम बेचते",
        "हम सप्लायर", "hum supplier", "supplier hain", "supplier hai",
        "हम manufacturer", "hum manufacturer", "manufacturer hain", "manufacturer hai",
        "हम बनाते", "hum banate",
        "खुद supply", "hum supply", "हम supply करते",
        "हम dealer", "hum dealer", "dealer hain", "dealer hai",
        "हम distributor", "hum distributor", "distributor hain", "distributor hai",
        "हम vendor", "hum vendor", "vendor hain", "vendor hai",
        "खुद produce", "hum produce", "हम produce",
        "खुद इंक्वायरी", "khud inquiry", "khud enquiry",
    ]
    # Bypass patterns: if user turns contain already-spoken signals, fall through to LLM so
    # it can classify as Already Spoken instead of Not Interested.
    _NI_ALREADY_SPOKEN_BYPASS = [
        "बातचीत हो गई", "baatcheet ho gayi", "baat cheet ho gayi",
        "बात हो गई", "baat ho gayi", "बात हो चुकी", "baat ho chuki",
        "बात कर दी", "baat kar di", "बात कर ली", "baat kar li",
        "कॉल आ गया था", "call aa gaya tha", "call aaya tha",
        "उनका कॉल", "unka call",
        "already spoken", "already baat", "already hua",
        "already ho gaya", "already le liya", "already purchase",
        "already connected", "already deal",
        "kaam ho gaya", "काम हो गया", "khatam ho gaya", "खत्म हो गया",
        "pura ho gaya", "पूरा हो गया", "poora ho gaya",
        "le liya", "ले लिया",
        "kisi ne baat ki", "किसी ने बात की",
        "seller ne call", "seller ka call", "seller se baat",
        "idar se baat", "इधर से बात", "idhar se baat",
        "sorted", "ho gaya kaam",
    ]
    _user_text_ni_check = unicodedata.normalize("NFC", " ".join(
        (t.get("text") or "").lower() for t in non_empty_user_turns
    ))
    _ni_seller_bypass = any(
        unicodedata.normalize("NFC", p.lower()) in _user_text_ni_check
        for p in _NI_SELLER_BYPASS
    )
    _ni_already_spoken_bypass = any(
        unicodedata.normalize("NFC", p.lower()) in _user_text_ni_check
        for p in _NI_ALREADY_SPOKEN_BYPASS
    )
    if (
        not _approved_closing_present
        and any(m.lower() in _last_agent_text for m in _NI_AGENT_MARKERS)
        and not _ni_seller_bypass
        and not _ni_already_spoken_bypass
    ):
        return {
            "call_outcome": "Not Interested",
            "call_outcome_description": DISPOSITION_MAP["Not Interested"],
            "call_summary": "Agent responded with not-interested closing — buyer did not confirm the product requirement.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # Pre-LLM: detect job-seeking caller intent.
    # When the buyer explicitly states they are looking for a job/employment, this call is
    # not a product inquiry. Short-circuit to Not Interested before the LLM can misclassify
    # the engagement (e.g. treating "naukri chahiye" as product confirmation).
    # Only scan user turns — the bot itself never uses job-seeking phrases.
    _JOB_SEEKING_PATTERNS = [
        # Hindi — "नौकरी" exclusively means "job/employment"
        "नौकरी चाहिए", "नोकरी चाहिए",
        "नौकरी है", "नोकरी है",
        "नौकरी मिलेगी", "नोकरी मिलेगी",
        "नौकरी दे", "नोकरी दे",
        "नौकरी ढूंढ", "नोकरी ढूंढ",
        "नौकरी के लिए", "नोकरी के लिए",
        "नौकरी मिलना", "नोकरी मिलना",
        "नौकरी लगवा", "नोकरी लगवा",
        # Devanagari "जॉब" — STT commonly transcribes "job" in Devanagari script
        "जॉब चाहिए", "जॉब मिलेगा", "जॉब मिलेगी",
        "जॉब के लिए", "जॉब्स के लिए",
        "जॉब ढूंढ", "जॉब दिला", "जॉब करना",
        "जॉब करना था", "जॉब करना है",
        "जॉब मिलना", "जॉब लगवा",
        # Devanagari "अप्लाई" — STT transcription of "apply"
        "अप्लाई करना है", "अप्लाई करना था",
        "अप्लाई करना चाहता", "अप्लाई करना चाहती",
        "अप्लाई कर सकते", "अप्लाई कैसे",
        # Mixed-script apply patterns
        "apply करना है", "apply करना था",
        "apply karna hai", "apply karna tha",
        "apply karna chahta", "apply karna chahti",
        "job ke liye apply", "jobs ke liye apply",
        "job mein apply", "job apply karna",
        # Job-doing intent (wanting to work, not buy)
        "job karna hai", "job karna tha", "job karni hai",
        "job karna chahta", "job karna chahti",
        "mujhe job karna", "mujhe job chahiye",
        # Romanised / Hinglish
        "naukri chahiye", "naukri chaahiye",
        "naukri milegi", "naukri milega",
        "naukri hai",
        "naukri ke liye",
        "job chahiye", "job chaahiye",
        "job milega", "job milegi",
        "job ke liye call", "job ke liye phone",
        "job dhundh", "job ki talash",
        "job dila", "job lena hai",
        "rozgar chahiye", "rojgar chahiye",
        "rozgar milega", "rojgar milega",
        "employment chahiye",
        "hiring ho rahi hai", "hiring chal raha",
        "vacancy hai kya", "vacancy chahiye",
        # "related" / "se related" — STT commonly produces these for job-seeker callers
        "job se related", "job se releted", "job related", "job releted",
        "जॉब से रिलेटेड", "जॉब रिलेटेड", "job se riletad", "job riletad",
        "naukri se related", "naukri related",
        "employment se related", "employment related",
        "work se related", "work related dekh",
        # Active job-searching phrases
        "job search kar", "job search karna", "job search kar raha", "job search kar rahi",
        "job dhundh raha", "job dhundh rahi", "job dhundh rha", "job dhundh rhi",
        "naukri dhundh raha", "naukri dhundh rahi", "naukri dhundha", "nokri dhundh",
        "main khud job", "main khud naukri", "khud ke liye job", "apne liye job",
        "mujhe job chahiye tha", "mujhe naukri chahiye thi",
        "job ki talash", "naukri ki talash", "kaam ki talash",
        "job ढूंढ रहा", "job ढूंढ रही", "नौकरी ढूंढ रहा", "नौकरी ढूंढ रही",
        # Interview / application signals
        "interview ke liye", "interview chahiye", "interview dena",
        "interview dena chahta", "interview dena chahti", "interview dena tha",
        "interview ke liye call", "interview ke liye phone",
        # Resume / CV submission
        "resume bheja", "resume bheja tha", "resume diya", "resume send",
        "cv bheja", "cv diya", "cv send", "cv bheja tha",
        # Identity signals — caller is a freshers / job-applicant
        "fresher hoon", "fresher hu", "fresher hai main", "main fresher",
        "main job seeker", "job seeker hoon", "job seeker hu",
        # Part-time / full-time job requests
        "part time job", "part time kaam", "full time job", "full time kaam",
        "part time chahiye", "full time chahiye",
        # Work-from-home job requests
        "work from home job", "work from home chahiye", "ghar se kaam chahiye",
        "घर से काम चाहिए", "घर बैठकर काम",
        # काम ढूंढ variants (work-searching, not task-related)
        "kaam dhundh raha", "kaam dhundh rahi", "kaam dhundha", "kaam dhundhi",
        "काम ढूंढ रहा", "काम ढूंढ रही", "काम की तलाश", "कामकी तलाश",
        "mujhe kaam chahiye", "mujhe koi kaam chahiye",
        # Salary/package asking in job-seeker context (not B2B pricing)
        "salary kitni milegi", "salary kya milegi", "salary kitni milega",
        "kitni salary milegi", "stipend kitna", "stipend kya milega",
        # Additional STT variants (STT often drops/merges syllables)
        "nokri chahiye", "naukari chahiye", "naukari milegi", "naukari ke liye",
        "rozgaar chahiye", "rojgaar chahiye",
        # Describing personal employment status/history (mid-call Case B signals)
        "mujhe job chahiye thi", "job chahiye thi mujhe",
        "main job kar raha tha", "main job kar rahi thi",
        "pehle job thi", "pehle job tha", "job chali gayi", "job chhut gayi",
    ]
    # Scan live user turns AND muted-transcript (user may say job signal during bot's speaking window)
    _all_user_text_parts = [
        (t.get("text") or "").lower() for t in non_empty_user_turns
    ] + [
        (m or "").lower() for m in (muted_transcript or []) if (m or "").strip()
    ]
    _user_text_for_job = unicodedata.normalize("NFC", " ".join(_all_user_text_parts))
    _job_seeker_literal = any(
        unicodedata.normalize("NFC", pat.lower()) in _user_text_for_job
        for pat in _JOB_SEEKING_PATTERNS
    )
    # Token co-occurrence fallback: if ANY user turn contains a job-indicator word AND a
    # seeking-context word, it's a job-seeker signal even if the exact phrase isn't listed.
    # This catches novel STT outputs and regional phrasings without exhaustive enumeration.
    _JOB_CORE = frozenset(unicodedata.normalize("NFC", w) for w in {
        "job", "जॉब", "naukri", "naukari", "nokri", "नौकरी", "नोकरी",
        "rozgar", "rojgar", "rozgaar", "rojgaar", "employment",
        "vacancy", "interview", "fresher", "resume",
    })
    _SEEKING_CONTEXT = frozenset(unicodedata.normalize("NFC", w) for w in {
        "chahiye", "chaahiye", "chahiye", "dhundh", "ढूंढ", "ढूंढ़", "dhundha",
        "talash", "तलाश", "search", "milega", "milegi", "milni",
        "apply", "karna", "related", "riletad", "lena", "dila",
        "seeking", "seeker",
    })
    # Bigrams that look like job-seeking but are actually manufacturing/B2B terms.
    # If any of these appear in a user turn, don't count "job" as an employment indicator.
    _JOB_WORK_EXCLUSIONS = frozenset({
        "job work",       # contract machining / manufacturing (e.g. "CNC job work ke liye")
        "job ka kaam",    # same concept in Hindi
        "job order",      # factory job order
        "job sheet",      # manufacturing job sheet
    })
    _job_seeker_cooccur = False
    for _ut in _all_user_text_parts:
        _ut_nfc = unicodedata.normalize("NFC", _ut)
        # Skip turns where "job" appears as a manufacturing bigram, not an employment word
        if any(excl in _ut_nfc for excl in _JOB_WORK_EXCLUSIONS):
            continue
        _words = {unicodedata.normalize("NFC", w.strip(".,!?।॥ ").lower())
                  for w in _ut_nfc.split() if w.strip(".,!?।॥ ")}
        if _words & _JOB_CORE and _words & _SEEKING_CONTEXT:
            _job_seeker_cooccur = True
            break
    if _job_seeker_literal or _job_seeker_cooccur:
        return {
            "call_outcome": "Not Interested",
            "call_outcome_description": DISPOSITION_MAP["Not Interested"],
            "call_summary": "Caller is seeking employment/job opportunities — this is not a product inquiry.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # Buyer explicitly identifies this call as automated/computer/robot and dismisses it.
    # "कंप्यूटर कॉल" / "computer call" is the caller saying "I know this is a bot" —
    # they are rejecting the call, not engaging with the product. Deterministically
    # Short Hangup unless the approved closing already fired (all specs collected before
    # the dismissal), in which case the approved closing is the authoritative outcome.
    _ROBOT_CALL_SIGNALS = [
        "कंप्यूटर कॉल", "computer call", "computer ka call",
        "robot call", "machine call", "automated call", "bot call",
        "recorded call", "auto call",
    ]
    _user_text_combined = unicodedata.normalize("NFC", " ".join(
        (t.get("text") or "").lower() for t in non_empty_user_turns
    ))
    _buyer_dismissed_as_robot = any(
        unicodedata.normalize("NFC", sig.lower()) in _user_text_combined
        for sig in _ROBOT_CALL_SIGNALS
    )
    if _buyer_dismissed_as_robot and not _approved_closing_present:
        return {
            "call_outcome": "Short Hangup",
            "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
            "call_summary": (
                "Buyer explicitly identified and dismissed this as an automated/computer call "
                "— no product engagement obtained."
            ),
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # Pre-LLM: detect caller who dialled to contact a company/seller directly
    # rather than to purchase through Justdial sellers.
    # "बात करना था" / "contact karna tha" = "I wanted to talk TO [company]"
    # combined with an opening rejection ("ना"/"नहीं") signals the caller was
    # trying to reach the company directly — not a product purchase intent.
    _DIRECT_CONTACT_PATTERNS = [
        "बात करना था", "baat karna tha",
        "बात करनी थी", "baat karni thi",
        "से बात करना था", "se baat karna tha",
        "से बात करनी थी", "se baat karni thi",
        "contact karna tha", "contact karni thi",
        "संपर्क करना था", "sampark karna tha",
    ]
    _user_text_for_contact = unicodedata.normalize("NFC", " ".join(
        (t.get("text") or "").lower() for t in non_empty_user_turns
    ))
    _first_live_turn_text = (non_empty_user_turns[0].get("text") or "") if non_empty_user_turns else ""
    _first_user_text_nfc = unicodedata.normalize("NFC", _first_live_turn_text.lower().strip())
    _first_turn_has_na_rejection = (
        "नहीं" in _first_user_text_nfc
        or _first_user_text_nfc.startswith("ना ")
        or _first_user_text_nfc.startswith("ना,")
        or _first_user_text_nfc.startswith("ना।")
    )
    _direct_contact_intent = _first_turn_has_na_rejection and any(
        unicodedata.normalize("NFC", pat.lower()) in _user_text_for_contact
        for pat in _DIRECT_CONTACT_PATTERNS
    )
    if _direct_contact_intent:
        return {
            "call_outcome": "Could Not Confirm",
            "call_outcome_description": DISPOSITION_MAP["Could Not Confirm"],
            "call_summary": (
                "Caller's intent was to contact the company/seller directly — "
                "this was not a product purchase inquiry through Justdial."
            ),
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # Transfer-to-someone-else: receptionist/assistant answered and offered to connect
    # to the actual decision maker, OR user handed the phone to another person mid-call.
    # No product confirmation possible → Could Not Confirm.
    _TRANSFER_PATTERNS = [
        r"\bgive you to\b", r"\bconnect you to\b", r"\btransfer to\b",
        r"\bput you through\b", r"\bput you on to\b", r"\bpass you to\b",
        r"\bput you to\b", r"\bhand you to\b",
        # Hindi: "भाई/boss/sir से बात करा/करवा" patterns
        r"भाई\s+से\s+बात\s+करा", r"bhai\s+se\s+baat\s+kara",
        r"भाई\s+को\s+दे", r"bhai\s+ko\s+de",
        r"boss\s+से\s+बात", r"boss\s+ko\s+de",
        r"sir\s+से\s+बात\s+करा", r"sir\s+ko\s+de",
        r"sahab\s+se\s+baat", r"साहब\s+से\s+बात",
        r"brother\s+se\s+baat", r"bhai\s+se\s+baat",
    ]
    # A turn counts as a "handoff" if it contains a transfer pattern anywhere in it —
    # even if it also contains a number that looks like a spec (e.g. "भाई साहब, 1200").
    _has_handoff_turn = any(
        any(re.search(p, (t.get("text") or "").lower()) for p in _TRANSFER_PATTERNS)
        for t in non_empty_user_turns
    )
    if _has_handoff_turn:
        return {
            "call_outcome": "Could Not Confirm",
            "call_outcome_description": DISPOSITION_MAP["Could Not Confirm"],
            "call_summary": "Call answered by a gatekeeper who offered to transfer — decision maker not reached.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # All user turns contain only bare call-presence signals (hello, haan bolo, achha, etc.)
    # → Short Hangup regardless of agent progression. The bot can advance on bare signals
    # (greeting accepted as a response), so `_agent_progressed` is NOT a reliable guard
    # when every captured user turn is a bare signal.
    # NFC-normalise each token so Devanagari vowel marks compare correctly.
    # Split on hyphens/dashes first so "हाँ-हाँ" is treated as two tokens ["हाँ", "हाँ"]
    # rather than the single concatenated string "हाँहाँ" which would miss the set lookup.
    def _tokenize_bare(text: str) -> set[str]:
        tokens = set()
        for part in re.split(r'[-–—]', text):
            for w in part.split():
                t = _strip_punct(w)
                if t:
                    tokens.add(t)
        return tokens

    if non_empty_user_turns and all(
        not (_tokenize_bare(t.get("text") or "") - _BARE_CALL_SIGNAL_TOKENS - _INFO_REQUEST_TOKENS)
        for t in non_empty_user_turns
    ):
        # Distinguish: if all tokens are info-request words (and no bare call-presence signal
        # overlap), the buyer was asking "what is this call about?" — prefer a Short Hangup
        # summary that reflects the info-seeking intent.
        _all_tokens = set().union(
            *(_tokenize_bare(t.get("text") or "") for t in non_empty_user_turns)
        )
        _info_only = bool(_all_tokens - _BARE_CALL_SIGNAL_TOKENS)  # tokens beyond bare set
        _summary_bare = (
            "No product engagement — buyer asked what the call was about ('info'/'जानकारी') "
            "but did not confirm the product."
            if _info_only else
            "No product engagement — buyer responded only with bare call-presence signals."
        )
        return {
            "call_outcome": "Short Hangup",
            "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
            "call_summary": _summary_bare,
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # Pre-LLM: agent stuck on opening question — never progressed to spec questions.
    # Pattern: last agent turn is still a re-ask of the opening product confirmation
    # ("तो क्या आपको X चाहिए?") AND the user gave only bare call-presence signals
    # (or nothing at all).  Multiple re-asks of the opening look like agent "progression"
    # to the LLM inference rules, producing false Interested classifications.
    # "तो क्या आपको" is the bot's specific re-ask phrasing — spec questions never use it.
    _last_agent_is_opening_reask = (
        "तो क्या आपको" in _last_agent_text
        or "to kya aapko" in _last_agent_text
    )
    _all_user_bare_or_empty = not non_empty_user_turns or all(
        not (
            {_strip_punct(w) for w in (t.get("text") or "").split() if w.strip()}
            - _BARE_CALL_SIGNAL_TOKENS
        )
        for t in non_empty_user_turns
    )
    if _last_agent_is_opening_reask and _all_user_bare_or_empty:
        _muted_note = (
            f" Muted capture: {'; '.join(muted_transcript)}." if muted_transcript else ""
        )
        return {
            "call_outcome": "Short Hangup",
            "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
            "call_summary": (
                "Agent re-asked the opening product-confirmation question and received no "
                "substantive response — buyer gave only a bare call-presence signal or "
                f"nothing at all.{_muted_note}"
            ),
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

    # --- End pre-LLM guards ---

    questions = schema.get("question", []) if schema else []
    q_list = json.dumps(
        [{
            "id": q.get("id", ""),
            "text": q.get("text", ""),
            "type": q.get("type", ""),
            "quantity_unit": q.get("quantity_unit") or [],
            "options": [{"id": o.get("id", ""), "text": o.get("text", "")} for o in (q.get("option") or []) if o.get("text")],
        } for q in questions],
        ensure_ascii=False,
    )
    lines = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript)
    _muted_lines = ""
    if muted_transcript:
        _muted_block = "\n".join(f"  - {m}" for m in muted_transcript)
        _muted_lines = (
            f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"MUTED-WINDOW TRANSCRIPT (user speech captured while bot was speaking)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"These are things the buyer said DURING the bot's turns (mic was muted, so Gemini did not hear them).\n"
            f"Use for outcome classification and context only — do NOT use for QnA extraction unless there is no corresponding live transcript turn.\n"
            f"{_muted_block}"
        )
    cut_note = (
        "\nNote: The call ended before the bot's closing phrase. "
        "Determine the outcome based on what was actually collected."
        if base_status == "disconnected" else ""
    )

    # Detect whether the transcript ends with an agent turn that has NO user response
    # after it at all — meaning the agent's last question is completely unanswered.
    _non_empty_turns = [t for t in transcript if (t.get("text") or "").strip()]
    _last_agent_idx = max(
        (i for i, t in enumerate(_non_empty_turns) if t.get("role") == "assistant"),
        default=-1,
    )
    _has_user_after_last_agent = any(
        t.get("role") == "user"
        for t in _non_empty_turns[_last_agent_idx + 1:]
    ) if _last_agent_idx >= 0 else False
    _ends_on_agent_no_response = _last_agent_idx >= 0 and not _has_user_after_last_agent
    _trailing_agent_note = (
        "\n⚠ TRANSCRIPT ENDS ON AGENT QUESTION: The last turn in the transcript is from "
        "the agent — there is no user turn after this final agent question. Apply the "
        "following two-case rule:\n"
        "  CASE A — TRULY UNANSWERED: There is NO user turn anywhere between the agent's "
        "FIRST ask of this question and the end of the transcript (i.e. the user never "
        "responded to this question at all). → Do NOT include it in qna. Do NOT pick an "
        "answer from the option list. Any qna entry for this question is a hallucination.\n"
        "  CASE B — RE-ASK AFTER UNCLEAR RESPONSE: There IS a user turn between the "
        "agent's first ask and the agent's re-ask (e.g. user said 'बस', 'I don't know', "
        "a garbled word) — the agent re-asked because the answer was unclear, not because "
        "the user never responded. → That earlier user response IS the answer. Keep it in "
        "qna as 'Not Sure' with opt_id null. Do NOT remove this entry."
        if _ends_on_agent_no_response else ""
    )

    def _tokens(text: str) -> set[str]:
        import unicodedata as _ud
        t = _ud.normalize("NFC", text)
        return {_strip_punct(w) for w in t.split() if w.strip()}

    _first_user_text = (non_empty_user_turns[0].get("text") or "") if non_empty_user_turns else ""
    _first_user_tokens = _tokens(_first_user_text)
    # Fix 1 — GP-7 Case A: if the agent progressed but the first user turn is garbled STT
    # noise (every token is either <2 chars or an all-same-character pattern like "aaaa"),
    # don't inject the PRODUCT CONFIRMED note — the bot may have mis-advanced on noise.
    _first_user_has_real_word = any(
        len(tok) >= 2 and not re.match(r"^(.)\1+$", tok)
        for tok in _first_user_tokens
    )
    # Require ≥2 meaningful tokens for implicit confirmation (prevents a single ambiguous word
    # like "यह" from firing the PRODUCT CONFIRMED note when agent progressed past greeting).
    # Explicit confirmation tokens (हाँ, yes, bilkul, etc.) still work with just 1 token.
    _first_user_meaningful_count = sum(
        1 for t in _first_user_tokens if len(t) >= 2 and not re.match(r"^(.)\1+$", t)
    )
    # Only fire _product_confirmed_note when the buyer explicitly used a confirmation
    # token (हाँ / yes / ji / bilkul / etc.). Removing the _agent_progressed branch
    # prevents ambient background conversation ("बना कर दे दिया क्लाइंट को",
    # "पानी मत दो", etc.) from being treated as product confirmation just because
    # the bot happened to advance on it. GP-7 in the LLM prompt still handles
    # structural inference for sparse-but-genuine transcripts.
    _first_turn_is_confirmation = bool(_first_user_tokens & _CONFIRMATION_TOKENS)
    # If the bot's second turn is a re-ask of the opening question (contains the
    # re-ask pattern "तो क्या आपको"), the agent did NOT accept the first हाँ/जी as a
    # product confirmation — so we must not inject the PRODUCT CONFIRMED note either.
    _second_agent_text = (
        (_agent_turns_with_text[1].get("text") or "").lower()
        if len(_agent_turns_with_text) >= 2 else ""
    )
    _bot_reask_patterns = (
        "तो क्या आपको", "to kya aapko", "क्या आपको", "kya aapko",
        "do you need", "do you still need", "क्या आप",
        # Step 1b of new 2-step greeting — agent asking requirement after search confirmation
        "की requirement है ना", "requirement hai na",
        # Step 1a re-ask
        "देख रहे हैं", "dekh rahe hain",
    )
    _agent_reask_opening = any(p in _second_agent_text for p in _bot_reask_patterns)
    # Also block the note when the buyer's first turn contains an explicit "नहीं" —
    # even if the agent mistakenly proceeded to spec questions (bot error), the buyer
    # rejection is the authoritative signal.
    _first_turn_has_explicit_no = "नहीं" in unicodedata.normalize("NFC", _first_user_text)

    # Detect "connect me to [agent]" pattern across ALL user turns.
    # A buyer asking to be connected to the bot by name proves they don't realise they're
    # already talking to it — any earlier हाँ/जी was a phone-pickup reflex, not product
    # confirmation. Covers phrases like "Simran से बात करवाईए".
    _CONNECT_TO_AGENT_PATTERNS = [
        "से बात करवाईए", "से बात करा दो", "से बात करा दीजिए", "से बात करवा दो",
        "se baat karwaiye", "se baat kara do", "se baat kara dijiye", "se baat karwa do",
        "से connect करवाईए", "se connect karwaiye",
        "से बात करो", "se baat karo",
        "से बात करना है", "se baat karni hai",
    ]
    _user_asks_for_agent = any(
        any(
            unicodedata.normalize("NFC", p) in unicodedata.normalize("NFC", (t.get("text") or "").lower())
            for p in _CONNECT_TO_AGENT_PATTERNS
        )
        for t in non_empty_user_turns
    )

    # Detect identity / origin question in the buyer's FIRST turn.
    # When a caller responds to the product greeting with a question like
    # "आप कहां से बोल रहे हो?" or "कंप्यूटर कॉल?" alongside a bare "हां", the bot
    # may have advanced on the reflex "हां", not on a genuine product confirmation.
    # Suppress _product_confirmed_note so the LLM evaluates the call freely.
    _IDENTITY_Q_PATTERNS = [
        "कहां से", "कहाँ से", "kahan se", "kaha se",          # "where are you calling from?"
        "कौन बोल", "kaun bol",                                  # "who is speaking?"
        "आप कौन", "aap kaun",                                   # "who are you?"
        "कौन सी company", "kaun si company", "kaunsi company",  # "which company?"
        "कंप्यूटर कॉल", "computer call", "computer ka call",   # "is this a computer call?"
        "machine call", "robot call", "automated call",
        "कहाँ से आप", "aap kahan se",
        "कहाँ से call", "kahan se call",
    ]
    _first_turn_has_identity_q = any(
        unicodedata.normalize("NFC", p.lower()) in unicodedata.normalize("NFC", _first_user_text.lower())
        for p in _IDENTITY_Q_PATTERNS
    )

    _product_confirmed_note = (
        f"\n⚠ PRODUCT CONFIRMED: The agent asked specification questions (progressed past "
        f"the greeting), which means the buyer confirmed the product. Do NOT classify as "
        f"Could Not Confirm or Short Hangup. "
        f"Classify as Interested (zero valid specs), Enriched (1+ valid specs), or Approved."
        if _first_turn_is_confirmation
        and not _wrong_opener
        and not _truncated_greeting   # buyer never heard the product question
        and not _agent_reask_opening
        and not _first_turn_has_explicit_no
        and not _user_asks_for_agent
        and not _first_turn_has_identity_q   # "हां" bundled with who-are-you/where-from is a reflex
        else ""
    )
    # Phantom signal: user asked to be connected to the agent they are already talking to.
    # Any earlier confirmation token was a phone-pickup reflex, not product engagement.
    _phantom_connect_note = (
        "\n⚠ PHANTOM ENGAGEMENT — USER ASKED TO BE CONNECTED TO THE AGENT: "
        "A buyer turn contains a phrase asking to speak to or be connected to the agent "
        "(e.g. 'Simran से बात करवाईए'). This proves the user did not realise they were "
        "already talking to the bot — any earlier 'हाँ/जी/yes' was a phone-pickup reflex, "
        "NOT a product confirmation. GP-7 Case E applies. "
        "Evaluate as Could Not Confirm or Short Hangup."
        if _user_asks_for_agent else ""
    )
    # Identity / origin question in first turn — "हाँ, आप कहां से बोल रहे हो?" is a
    # phone-pickup reflex, not product confirmation. The bot may have mis-advanced on the
    # bare "हाँ" component. The LLM must not treat agent progression as structural proof.
    _identity_q_note = (
        "\n⚠ IDENTITY QUESTION IN FIRST BUYER TURN: The buyer's first response contained "
        "an identity or origin question ('आप कहां से?', 'कंप्यूटर कॉल?', 'कौन बोल रहा है?', "
        "etc.) alongside or instead of a product confirmation. The bot may have advanced on "
        "the reflexive 'हाँ/हेलो' component of that turn, NOT on genuine product interest. "
        "GP-7 Case A applies — evaluate product_confirmed from the buyer's actual words across "
        "all turns. If the buyer never gave a clear product-specific confirmation → Could Not "
        "Confirm or Short Hangup."
        if _first_turn_has_identity_q else ""
    )
    # When the agent re-asked the opening product question the bot itself rejected the
    # first response as insufficient. If the post-reask user turn is ALSO off-topic or
    # background noise, the bot may have advanced a second time on a misread — GP-7
    # Case A applies and structural progression alone does not prove product confirmation.
    _reask_opening_note = (
        "\n⚠ AGENT RE-ASKED THE OPENING QUESTION: The agent's second turn re-asked the "
        "product confirmation question, meaning the bot did NOT accept the first user "
        "response as a valid product confirmation. GP-7 Case A applies — the bot may have "
        "advanced incorrectly. Do NOT rely on agent progression alone as structural proof "
        "of product_confirmed = TRUE. Evaluate from the buyer's actual words.\n"
        "CRITICAL — if the buyer's only captured speech is a bare call-presence signal "
        "(हेलो / हाँ / जी / ok) or there is no live buyer turn at all: product_confirmed = FALSE. "
        "The agent's multiple turns are all re-asks of the same opening question — NOT "
        "progression to spec questions. → Short Hangup."
        if _agent_reask_opening else ""
    )
    _wrong_opener_note = (
        "\n🚨 GREETING FAILURE: The agent's first turn was a connection probe "
        "(\"क्या आप अभी line पर हैं?\") instead of the standard product greeting. "
        "This is a Gemini model failure — the bot may have advanced the conversation "
        "on background noise or ambient conversation, NOT on a real product response. "
        "GP-7 does NOT apply here. Evaluate product confirmation exclusively from the "
        "buyer's actual words. If the buyer's turns are background conversation with no "
        "product signal → Could Not Confirm."
        if _wrong_opener else ""
    )
    # Truncated greeting: TTS was cut before the product question was delivered.
    # The buyer never heard the product question, so any हाँ/जी is just a phone-answer
    # reflex, NOT product confirmation. Agent progression is meaningless here.
    # Outcome must be Short Hangup (no product topic was ever raised).
    _truncated_greeting_note = (
        "\n🚨 TRUNCATED GREETING: The agent's first turn started the greeting but was "
        "cut off before stating the product or asking the product question "
        "(it ended with 'आपको' or similar incomplete phrase). The buyer NEVER heard "
        "what product was being asked about. Any हाँ / जी / yes from the buyer is purely "
        "a phone-answering reflex, NOT product confirmation. GP-7 does NOT apply. "
        "All subsequent background chatter is ambient noise. → Short Hangup."
        if _truncated_greeting else ""
    )

    _user_sparse = not non_empty_user_turns or len(non_empty_user_turns) <= 1
    _user_sparse_note = ""
    if not non_empty_user_turns:
        _user_sparse_note = (
            "\n⚠ SPARSE TRANSCRIPT: Zero buyer turns were captured. STT likely failed — the buyer DID speak but audio was not transcribed. "
            "Do NOT default to Short Hangup. Use AGENT BEHAVIOUR INFERENCE (see below) to reconstruct what happened."
        )
    elif len(non_empty_user_turns) <= 1:
        _user_sparse_note = (
            "\nNote: Very few buyer turns were captured — STT may have missed responses. "
            "Combine available buyer turns with AGENT BEHAVIOUR INFERENCE to reach the correct outcome."
        )

    _agent_inference_section = ""
    if _user_sparse:
        _agent_inference_section = """
━━━━━━━━━━━━━━━━━━━━━━━━
AGENT BEHAVIOUR INFERENCE (apply when buyer transcript is missing or sparse)
━━━━━━━━━━━━━━━━━━━━━━━━

CORE PRINCIPLE: The bot is strictly programmed — it never advances to Question N without a valid answer to Question N-1. Agent progression is therefore a reliable proxy for buyer answers when STT did not capture speech.

READING THE AGENT'S QUESTION SEQUENCE:
• Agent said ONLY the opening greeting and stopped → buyer did not respond at all → "Short Hangup"
• Agent asked Q1 (first qualification question after the opening) → buyer confirmed the product → minimum "Interested"
• Agent asked Q2 or beyond (progressed past Q1) → buyer confirmed the product AND answered at least Q1 → minimum "Enriched"
• Agent said the closing line ("सारी details मिल गईं" / "relevant sellers आपसे contact करेंगे") → ALL questions were answered → "Approved"
• Agent asked business name or city → buyer confirmed product AND answered ALL spec questions

MUTED-WINDOW RESPONSES DURING THE OPENING GREETING (agent said only one turn):
When the muted transcript captures content during the agent's OPENING GREETING only (no Q1 asked):
• Bare acknowledgement (ठीक है, हाँ, ok, जी) → buyer answered the phone but gave no product signal → "Short Hangup"
• Ambiguous or off-topic (e.g., "नहीं अभी भी आ रही है", "क्या बात है", fragment sentences) → buyer spoke but product confirmation was NOT obtained → "Could Not Confirm"
• Only classify as Interested if the muted content EXPLICITLY states the product need (e.g., "हाँ, Nut Bolt चाहिए", "yes I need it")

SPECIAL AGENT PHRASES TO DETECT:
• "कोई response नहीं आया, इसलिए मैं call समाप्त कर रही हूँ" → inactivity timeout, buyer was truly silent → "Short Hangup"
• "मुझे सिर्फ 5 मिनट तक बात करने की permission है" / "I only have permission to talk for 5 minutes" → 5-minute hard timeout → use agent question count per the progression rules above
• Agent explicitly confirmed a value mid-turn ("ठीक है — [value] note kar liya", "[value] समझ गया", "okay [value]") → treat [value] as the buyer's answer for the preceding question

QnA EXTRACTION when buyer turns are absent:
• Extract any values the agent explicitly confirmed in their turns
• For questions the agent progressed past but where you found no confirmed value → set answ "Not Sure", opt_id null
• Never invent values — only use what the agent explicitly stated
"""

    disposition_options = "\n".join(f'  "{k}": {v}' for k, v in DISPOSITION_MAP.items())
    current_dt_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    _duration_note = ""
    if duration_secs is not None:
        _dur_label = f"{duration_secs:.0f}s"
        if duration_secs < 20:
            _duration_note = (
                f"\n⚠ SHORT CALL ({_dur_label}): This call lasted under 20 seconds. "
                f"A call this short rarely produces genuine product engagement. "
                f"If the buyer's signal is unclear, garbled, off-topic, or just a bare "
                f"acknowledgement (हाँ / ji / yes / ok) with no product-specific statement, "
                f"prefer Short Hangup over BOTH Interested AND Could Not Confirm. "
                f"Only classify as Interested if the buyer gave a clear, product-specific "
                f"confirmation beyond a single-word acknowledgement. "
                f"Only classify as Could Not Confirm if there is a clear reason the "
                f"confirmation couldn't happen (hold, handoff, IVR). Garbled or off-topic "
                f"audio alone is Short Hangup, not Could Not Confirm."
            )
        elif duration_secs < 25:
            _duration_note = (
                f"\n📞 CALL DURATION: {_dur_label}. Relatively short — "
                f"weigh buyer engagement carefully before classifying as Interested."
            )
        else:
            _duration_note = f"\n📞 CALL DURATION: {_dur_label}."

    prompt = f"""You are a strict call-analysis engine for JustDial's AI outbound qualification calls. Return accurate structured JSON — no guessing, no approximating. Every rule below is mandatory.{cut_note}{_wrong_opener_note}{_truncated_greeting_note}{_phantom_connect_note}{_identity_q_note}{_reask_opening_note}{_user_sparse_note}{_trailing_agent_note}{_product_confirmed_note}{_duration_note}

Current date/time (IST, GMT+5:30): {current_dt_str}

━━━━━━━━━━━━━━━━━━━━━━━━
TRANSCRIPT
━━━━━━━━━━━━━━━━━━━━━━━━
{lines}{_muted_lines}

━━━━━━━━━━━━━━━━━━━━━━━━
QUALIFICATION QUESTIONS
━━━━━━━━━━━━━━━━━━━━━━━━
{q_list}
{_agent_inference_section}
━━━━━━━━━━━━━━━━━━━━━━━━
GLOBAL PRINCIPLES (defined once — referenced by name throughout)
━━━━━━━━━━━━━━━━━━━━━━━━

GP-1  POSITIVE PROGRESSION: A reflex "नहीं" followed by spec details, a product question, or
      continued engagement means product IS confirmed. Classify on the final positive stance,
      not the opening negative.

GP-2  FINAL STATE WINS:
      • Explicit final rejection overrides earlier weak interest.
      • Explicit final confirmation overrides early reflex "नहीं".
      • Off-topic later turns (agent identity, caller location, unrelated topics) do NOT undo
        prior confirmed qualification. Only an explicit requirement withdrawal can downgrade
        a Tier 3 classification.

GP-3  NEGATIVE TONE ≠ REJECTION: Rudeness, impatience, or dismissive phrasing ("jaldi bolo",
      "kya hai", "nahi nahi") is NOT a rejection. Rejection requires explicit, final
      requirement withdrawal.

GP-4  GENERIC CONVERSATION ≠ INTEREST: The following are participation signals only —
      they do NOT imply product interest:
        "haan bolo" / "achha" / "theek hai" / "kaun hai" / "kis company se?" /
        "human ho ya bot?" / "can you help me?" / "why are you calling?" / "hello"
      Interest requires: product confirmation OR a product-specific question OR a buying signal.

GP-5  OPERATIONAL > CONVERSATIONAL: A buyer saying "haan / ji / ok / standard / kuch bhi /
      you decide" to a spec question is answering conversationally — NOT providing a
      valid_spec_value. Only concrete, operationally useful data counts.

GP-6  SELF-SOURCING OVERRIDES REJECTION: "Khud dekh lenge / apne aap le lenge" with a live
      requirement → Will do it Myself, not Not Interested. Requirement exists; only JD's
      help is declined.

GP-7  AGENT PROGRESSION GUARANTEES PRODUCT CONFIRMATION: The bot is strictly programmed and
      NEVER asks any qualification question (quantity, grade, spec, type, application, etc.)
      without first receiving product confirmation from the buyer. Therefore:
        • If the transcript shows the agent asking ANY question from the qualification schema
          listed above → product_confirmed is STRUCTURALLY TRUE.
        • The outcome CANNOT be Could Not Confirm or Short Hangup in this case.
        • This overrides any superficially vague or unclear buyer responses you observe.
      You do not need to re-derive product_confirmed from buyer turns alone when the agent's
      own behaviour already proves it.
      EXCEPTION — bot mis-step (applies to FIRST turn OR ALL turns):
        Case A — First turn only: if the buyer's first response to the opening product question
          was entirely off-topic (e.g., a person's name, a personal-call response, an unrelated
          utterance like "Aryan" or "Himank hello"), the bot may have advanced incorrectly on a
          misread. GP-7 does NOT apply — evaluate product_confirmed from the buyer's actual words.
        Case B — All turns are background noise (apply GP-8 before GP-7): if EVERY buyer turn
          throughout the entire call is off-topic personal chatter, name-calls, or background
          conversation unrelated to the product — domestic talk, side conversations, commenting on
          unrelated things (water, bathroom, payments, people nearby, "बना कर दे दिया क्लाइंट को",
          "पानी मत दो", "बाथरूम में से ना बात करो") — no single turn engages with any product
          topic, spec, or buying signal — then the bot ran a one-sided conversation with background
          noise. GP-7 does NOT apply. Evaluate as Could Not Confirm.
        Case C — Wrong opener (🚨 GREETING FAILURE note present above): the agent started with
          a connection probe ("क्या आप अभी line पर हैं?") instead of the product greeting, so
          the bot may have advanced on ambient noise. GP-7 does NOT apply regardless of agent
          turn count. Evaluate product confirmation from the buyer's actual words only.
        Case D — Explicit buyer rejection ignored by bot (bot error): if the buyer's FIRST
          live response to the opening product question begins with or prominently contains
          "नहीं" (no) — e.g. "नहीं मैम", "नहीं जी", "नहीं, हमें नहीं चाहिए" — AND the agent
          then proceeded to ask spec questions without resolving the rejection, this is a bot
          programming error. The agent advanced on a misread. GP-7 does NOT apply. Evaluate
          product_confirmed from the buyer's actual words. A clear consistent "नहीं" to the
          opening question = product_confirmed FALSE → classify as Not Interested.
        Case E — Bare phone-pickup signal injected as muted capture: if the only user signal
          is a reflexive greeting or acknowledgement ("हाँ जी", "हाँ", "जी", "हेलो") captured
          during the bot's greeting window (mic was muted), and the agent advanced on this
          signal alone with no subsequent live user confirmation — the bot advanced on a
          phone-pickup reflex, not a product confirmation. GP-7 does NOT apply. Evaluate as
          Could Not Confirm or Short Hangup based on actual engagement.

GP-8  PHANTOM ENGAGEMENT / BACKGROUND NOISE: When ALL of the following are true simultaneously:
        ✓ ZERO valid_spec_values were captured across all questions
        ✓ NO buyer turn contains any product-related word, quantity, spec, or buying signal
        ✓ Buyer turns read as background conversations, name-calls, or side-chatter
          (e.g. calling out a person's name, commenting on unrelated topics like payments/internet,
          domestic talk, conversations with people nearby, rambling with no product relevance)
      → The agent was capturing background noise, not a real engaged buyer.
      → product_confirmed = FALSE. GP-7 does NOT apply.
      → Outcome: "Could Not Confirm"
      This applies regardless of whether closing_line_spoken is TRUE or FALSE — a bot that
      ran its full flow (or any part of it) while the user was talking to someone else or
      in a noisy environment did not achieve genuine qualification.

━━━━━━━━━━━━━━━━━━━━━━━━
DEFINITIONS
━━━━━━━━━━━━━━━━━━━━━━━━

product_topic_reached — agent named the product AND buyer responded to the product itself
                        (not merely to the caller's identity or presence).

product_confirmed     — TRUE if ANY of:
                          • buyer said yes/ji/bilkul/theek hai/haan in response to
                            "आपको X की requirement है ना?" or equivalent opening question
                          • buyer provided a spec value, quantity, or product variant
                          • buyer asked a product-specific question (pricing, delivery,
                            availability, specs) — generic call questions do NOT count.
                        Apply GP-1: later confirmation overrides early "नहीं".

valid_spec_value      — a concrete, operationally useful answer: named option, number+unit,
                        material/grade, specific measurable choice.
                        NOT valid: "haan / yes / ji / ok", "standard", "kuch bhi",
                        "you decide", "don't know", "हम्म", or any vague filler.
                        ASR CORRUPTION: interpret phonetically/contextually garbled text by
                        intent and context; the agent's echo-confirmation in the next turn
                        is the strongest signal.

closing_line_spoken   — ALL of the following conditions are met:
                          1. The LAST assistant turn contains "relevant sellers"
                          2. The LAST assistant turn also contains EITHER
                               "सारी details मिल गईं"  (mixed Hindi+English form)
                             OR "सारी डिटेल्स मिल गई" (full Devanagari form)
                        Both conditions must be satisfied simultaneously. No other phrasing qualifies.

━━━━━━━━━━━━━━━━━━━━━━━━
EVALUATION ORDER
━━━━━━━━━━━━━━━━━━━━━━━━

Evaluate tiers in order: Tier 1 → Tier 2 → Tier 3 → Tier 4.
Within each tier, return the FIRST fully satisfied outcome.
Once an outcome is matched, do not evaluate lower tiers.

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 1 — SYSTEM / TERMINAL
━━━━━━━━━━━━━━━━━━━━━━━━
These override everything else when the signal is unambiguous.

SHORT HANGUP
  Condition: product_topic_reached == FALSE AND buyer gave only bare call-presence signals.
  Bare signals: "hello", "haan", "kaun hai", "ek second", "hold on", "ruko", calling out a
    name thinking it was a personal call, or asking "kaun bol raha hai?" — responses about the
    caller's identity or presence, not the product.
  Test: Did the buyer engage with the product topic in ANY way?
    YES → do NOT use Short Hangup.   NO → Short Hangup.
  NOT ALLOWED IF: buyer said "नहीं" in response to the product question (that IS product
    engagement). Short Hangup requires zero product engagement.
  → "Short Hangup"

VOICEMAIL
  Condition: call answered by automated voicemail/IVR, not a live human.
  Signals (any one is sufficient):
    English: "leave a message", "after the beep", "you have reached [name/voicemail]",
             "unable to take your call", "mailbox is full"
    Hindi:   "sandesh chhod", "beep ke baad", "uplabdh nahi / उपलब्ध नहीं",
             "aapka call abhi", "subscriber"
  Pattern: robotic/templated text with no human conversational structure.
  NOT ALLOWED IF: a real human conversational response exists anywhere in the transcript.
  → "Voicemail"

CALL ON HOLD
  Condition: a carrier/PBX hold-music announcement appears in a user turn — same phrase
    repeated in multiple languages, OR any of: "put your call on hold", "placed your call on
    hold", "hold par rakha hai", "होल्ड पर राख्यो छे".
  Note: buyer saying "hold on" themselves → Short Hangup, NOT this. This applies only when
    the carrier/PBX automated message appears as a transcript turn.
  → "Could Not Confirm"

WRONG NUMBER
  Condition: person who answered confirmed the number belongs to someone else.
  → "Wrong Number"

LANGUAGE ISSUE
  Condition: communication was entirely impossible throughout the call due to language mismatch.
  → "Language Issue"

ABUSIVE LEAD
  Condition: a live HUMAN was abusive or used profanity. NOT automated system messages —
    phrases like "hang up" or "call cannot be taken" from IVR/voicemail are NEVER abusive.
  → "Abusive Lead"

DNC
  Condition: buyer explicitly asked not to be contacted again.
  Examples: "dobara mat call karna", "remove my number", "number हटा दो".
  → "DNC Client : Don't Call Further"

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 2 — OPERATIONAL ROUTING
━━━━━━━━━━━━━━━━━━━━━━━━
Check these before qualification outcomes (Tier 3).

SELLER INTENT
  Condition: caller is on the supply/service side — NOT a buyer.
  Signals: offering own products/services, seeking manufacturing contracts, wanting to list
    on JD, or supplying the exact product being discussed.
  Strict: must be clear from what the caller SAYS. A manufacturer buying materials for their
    own use is still a buyer.
  → "Seller Intent"

ALREADY SPOKEN
  Condition: requirement already discussed with JD/seller, OR requirement already fulfilled/no longer active.
  Examples: "already purchased", "kaam ho gaya", "le liya", "sorted", "already spoken to seller",
    "ab nahi hai" / "nahi ab nahi hai" (not anymore — requirement existed but is now gone/fulfilled),
    "ho gaya" / "khatam ho gaya" / "pura ho gaya" (done / finished / completed),
    "already hua" / "already le liya" / "already connected" / "already kisi ne baat ki".
  KEY RULE: if the buyer uses "ab" (now/anymore) to negate the requirement — "ab nahi chahiye",
    "ab nahi hai", "pehle tha ab nahi", "ab zaroorat nahi" — treat as Already Spoken, NOT Not
    Interested. The "ab" signals the requirement existed before and has since been resolved.
  → "Already Spoken"

CALL RESCHEDULED
  Condition: buyer asked to be called at a SPECIFIC date and/or time.
  Strict: "baad mein / call later / abhi busy hoon" without a specific time → Could Not
    Confirm (Tier 4), NOT this outcome.
  → "Call Rescheduled"

ALTERNATE NUMBER
  Condition: buyer provided a different contact number for follow-up.
  → "Alternate Number"

WILL DO IT MYSELF
  Condition: buyer still has the requirement but will source it independently, explicitly
    declining JD's help. Apply GP-6.
  Examples: "khud dekh lenge", "don't send sellers", "apne aap le lenge".
  Strict: requirement must exist — only JD's assistance is rejected.
  → "Will do it Myself"

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 3 — QUALIFICATION
━━━━━━━━━━━━━━━━━━━━━━━━
Reached ONLY if product_confirmed == TRUE and no Tier 1/2 outcome matched.
If product_confirmed == FALSE → skip directly to Tier 4.

Evaluate in this exact order:

1. APPROVED — closing line present  ← HARD OVERRIDE
   IF closing_line_spoken == TRUE AND ≥1 valid_spec_value was captured:
   → "Approved"  ← MANDATORY. No exceptions.
   HARD OVERRIDE: The closing line ("सारी details मिल गईं" + "relevant sellers") is spoken
   ONLY when the bot's internal logic confirms the full qualification flow was completed
   successfully. When it appears, the outcome MUST be Approved. You are NOT permitted to
   downgrade to Enriched or Interested under any circumstances — not because a spec seemed
   vague, not because one question appeared unanswered, not because of ASR corruption. The
   bot's confirmed completion is authoritative and supersedes your individual spec evaluation.
   If you are about to emit Enriched or Interested and closing_line_spoken is TRUE, STOP and
   emit Approved instead.
   EXCEPTION — GP-8 overrides this rule: if GP-8 (phantom engagement / background noise)
   applies — zero valid spec values AND all buyer turns are incoherent background noise — then
   closing_line_spoken does NOT make this Approved. The closing line fired on a phantom
   conversation. Classify as Could Not Confirm.

2. APPROVED — all specs answered
   IF all {len(questions)} qualification question(s) received valid_spec_value answers:
   → "Approved"

3. ENRICHED
   IF ≥1 valid_spec_value was captured (but not all questions answered):
   → "Enriched"

4. INTERESTED
   IF product_confirmed == TRUE AND 0 valid_spec_value answers:
   → "Interested"

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 4 — UNCERTAIN / NEGATIVE
━━━━━━━━━━━━━━━━━━━━━━━━
Reach this tier only if no Tier 1–3 outcome matched.

INTERESTED (positive engagement, no product confirmation)
  Condition: product_confirmed == FALSE but buyer showed clear, product-specific positive
    interest — asked about pricing, delivery, specs, availability, or quantity — without a
    final clear rejection.
  Apply GP-4: generic call questions ("can you help me?", "which company?", identity
    questions) do NOT qualify.
  → "Interested"

COULD NOT CONFIRM
  Condition: ANY of:
    a) vague/non-committal about the product ("शायद", "पता नहीं", "I'll think about it")
    b) vague callback with no product signal — "baad mein", "call later", "busy" without
       a specific time
    c) call dropped before any product confirmation and no other rule matched
    d) buyer's responses were off-topic with no product engagement detected
  NOT ALLOWED IF: buyer said "हाँ/yes" or gave any spec detail → use Interested.
  NOT ALLOWED IF: buyer clearly rejected → use Not Interested.
  NOT ALLOWED IF: the agent asked ANY qualification question from the schema listed above
    (see GP-7 — agent progression structurally proves product_confirmed is TRUE; Could Not
    Confirm requires product_confirmed == FALSE or never reached). Use Interested minimum.
  → "Could Not Confirm"

Disambiguation:
  product_topic_reached | product engagement     | final stance     → outcome
  FALSE                 | none (bare signals)    | —                → Short Hangup (Tier 1)
  TRUE                  | vague / off-topic      | unclear          → Could Not Confirm
  TRUE                  | product-specific       | unclear          → Interested (Tier 4)
  TRUE                  | product-specific       | explicit reject  → Not Interested
  TRUE                  | any                    | self-source      → Will do it Myself (Tier 2)

NOT INTERESTED
  Condition: buyer CONSISTENTLY and CLEARLY stated they do not need the product. The
    requirement itself is entirely gone AND was never fulfilled elsewhere.
  ALL must be true:
    ✓ buyer explicitly rejected the product (not just an initial reflex "नहीं")
    ✓ NO positive engagement, NO spec answers, NO product questions anywhere in the call
    ✓ buyer's FINAL overall stance is negative
    ✓ cannot be explained by Seller Intent / Will do it Myself / Already Spoken / Wrong Number
  STRICT EXCLUSION: if buyer uses temporal language — "ab nahi chahiye", "ab nahi hai",
    "pehle tha ab nahi", "nahi ab nahi" — the requirement existed before and is now gone.
    This is Already Spoken (fulfilled), NOT Not Interested.
  Apply GP-1 (POSITIVE PROGRESSION) and GP-3 (NEGATIVE TONE ≠ REJECTION).
  → "Not Interested"

TECHNICAL ISSUE
  Condition: call connected but disrupted entirely by technical problems with no meaningful
    exchange achieved. Strict: if any positive exchange occurred before the issue, use the
    appropriate Tier 3 outcome instead.
  → "Technical Issue - Call Connected"

OTHER CASES
  Use ONLY if truly none of the above applies after careful evaluation of all tiers.
  → "Other Cases"

Valid outcome values (use EXACT strings only):
{disposition_options}

━━━━━━━━━━━━━━━━━━━━━━━━
CONSISTENCY CHECK (mandatory before emitting JSON)
━━━━━━━━━━━━━━━━━━━━━━━━
After completing Step 2 (qna extraction), self-verify:
• valid_spec_count ≥ 1 AND product_confirmed → outcome MUST be Enriched or Approved (never Interested).
• valid_spec_count == 0 AND product_confirmed → outcome MUST be Interested (never Enriched or Approved).
• closing_line_spoken AND product_confirmed AND valid_spec_count ≥ 1 → outcome MUST be Approved.

━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — EXTRACT QnA
━━━━━━━━━━━━━━━━━━━━━━━━

PRE-STEP (mandatory): Read the transcript sequentially. Each time the AGENT asks one of the
listed qualification questions (in any language/paraphrase), record the question_id and the
IMMEDIATELY FOLLOWING buyer turn as its raw answer.

EXTRACTION RULES (all mandatory):

1. POSITION RULE: Attribute each buyer response to the qualification question the AGENT asked
   immediately before that buyer turn. Nth question asked = Nth buyer answer. Never reassign
   based on answer format or data type.

2. AGENT-CONFIRMATION RULE: If the buyer's response is garbled/unclear (STT noise),
   verbose/embedded in a long sentence, OR missing entirely (no buyer turn between two agent
   turns), and the AGENT's next turn explicitly restates or confirms a value (e.g. "ठीक है —
   [value]", "okay, X", "aapne [value] bataya", "achha, [value]"), treat that agent-confirmed
   value as the buyer's answer for the preceding question.
   STT NUMBERS: agent may render Hindi numerals in romanized form — "das/dash"=10, "bees"=20,
   "teen"=3, "paanch"=5, "sau"=100. "dash units note kar liya" means agent confirmed 10 units.
   ANTI-HALLUCINATION: if buyer turn is missing AND agent gave no confirmed value, set
   answ "Not Sure", opt_id null. A vague filler ("हम्म", "umm", "achha") followed by an
   agent assumption is NOT a confirmed answer.
   UNANSWERED FINAL QUESTION: transcript ends immediately after the agent's question with no
   subsequent user OR agent turn → question is completely unanswered. Omit from qna entirely.

3. CORRECTION RULE: If a buyer turn clearly corrects or confirms a PREVIOUSLY answered
   question (does NOT match any option of the current question), update the prior answer —
   do NOT assign to the current question.

4. POST-WRAP-UP RULE: If the buyer speaks AFTER the agent's closing statement and clearly
   answers an unanswered question, include it in qna.

5. NO CROSS-TYPE REASSIGNMENT: a grade/spec answer stays with the spec question; a quantity
   answer stays with the quantity question.

6. OPT_ID MATCHING:
   a. Exact match (case-insensitive) → use that option's id.
   b. STT digit-drop: "40 GSM" vs option "140 GSM" (buyer value is a numeric suffix of the
      option text) → use that option's id.
   c. No match → set opt_id to null.

7. QUANTITY FORMAT: For type=="quantity" questions, answ MUST be "<number> <unit>" (e.g.
   "5 pieces"). Use buyer's unit if stated; else use first value from that question's
   quantity_unit list. If buyer could not give a number → set answ to "Not Sure".
   Apply this format ONLY to quantity questions — never to grade/spec answers.

Each qna entry: {{"id": <qid>, "quest": <question text>, "answ": <normalized English answer>, "opt_id": <matching option id or null>}}

━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2B — EXTRACT BUSINESS DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━

Extract these three fields from the full transcript. Do NOT infer or guess — only extract
values explicitly stated by the buyer.

  Field          | Value rules
  is_business    | "True" if buyer confirmed business/commercial/shop/company use;
                 | "False" if buyer said personal/home use;
                 | "" if not discussed or answer was unclear.
  business_name  | Exact name buyer stated for their business/shop/company; "" if not stated.
                 | MUST be in English — transliterate Devanagari/regional script to Roman letters
                 | (e.g. "एस एस पोर्टेबल कैबिन" → "SS Portable Cabin").
  business_city  | City buyer stated specifically for their business location; "" if not stated.
                 | MUST be in English — use the standard English spelling of the city/state
                 | (e.g. "भुवनेश्वर, उड़ीसा" → "Bhubaneswar, Odisha").
                 | Do NOT use the buyer's personal city as business_city unless explicitly
                 | stated in the context of their business during the call.

If is_business is "False" or "" → set both business_name and business_city to "".

━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — RETURN JSON
━━━━━━━━━━━━━━━━━━━━━━━━

Return a SINGLE JSON object with EXACTLY these keys — no extra keys, no markdown, no explanation:
{{
  "call_outcome": "<one exact string from the valid outcome list>",
  "call_outcome_description": "<the corresponding description from the list>",
  "call_summary": "<1-2 sentence English summary of what happened on the call>",
  "is_business": "<'True' | 'False' | '' — per Step 2B>",
  "business_name": "<business name in English (transliterated if needed), or ''>",
  "business_city": "<business city in English, or ''>",
  "qna": [ ...entries per Step 2... ],
  "product_change": {{"product_name": "<new product name>"}},  // or {{}} if no product switch
  "rescheduled_to": "<ISO datetime YYYY-MM-DDTHH:MM:SS in IST if rescheduled, else ''>"
}}

STRICT OUTPUT RULES:
- call_outcome MUST be one of the exact strings from the valid outcome list. Any deviation is an error.
- Do NOT guess, hallucinate, or invent values. If unsure, choose the most conservative option.
- Do NOT include null fields — use "" or {{}} as specified above.
- Return ONLY the JSON object. No markdown fences, no commentary before or after."""

    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        async with http_session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
            if "candidates" not in data or not data["candidates"]:
                raise ValueError(f"No candidates: {data.get('error') or data}")
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(raw)
            outcome = result.get("call_outcome", "")
            if outcome not in _VALID_OUTCOMES:
                outcome = status_to_outcome(base_status)
                result["call_outcome"] = outcome
            result["call_outcome_description"] = DISPOSITION_MAP.get(outcome, "")
            result.setdefault("qna", [])
            result.setdefault("product_change", {})
            result.setdefault("rescheduled_to", "")
            pc = result.get("product_change") or {}
            if isinstance(pc, dict) and "new_product" in pc and "product_name" not in pc:
                result["product_change"] = {"product_name": pc.get("new_product", "")}

            # ── DETERMINISTIC POST-PROCESSING ──────────────────────────────

            qna = result.get("qna") or []

            # 1. opt_id must be null whenever the answer is "Not Sure" or empty.
            for entry in qna:
                if (entry.get("answ") or "").strip().lower() in ("not sure", ""):
                    entry["opt_id"] = None

            # 2a. Approved → Could Not Confirm when the LLM applied the closing-line hard
            #     override but zero valid spec values were actually captured (GP-8 phantom
            #     engagement, or all-Not-Sure responses).  "Approved" requires ≥1 valid spec —
            #     if the LLM returns Approved with none, it has mis-applied the rule.
            if outcome == "Approved" and questions:
                _schema_ids_chk = {str(q.get("id", "")) for q in questions if q.get("id")}
                _valid_chk = {
                    str(e.get("id", ""))
                    for e in qna
                    if str(e.get("id", "")) in _schema_ids_chk
                    and (e.get("answ") or "").strip().lower() not in ("not sure", "")
                }
                if _schema_ids_chk and not _valid_chk:
                    logger.info(
                        "[POST-PROC] Approved → Could Not Confirm: closing line fired "
                        "but zero valid spec values captured (phantom engagement / all-Not-Sure)"
                    )
                    outcome = "Could Not Confirm"
                    result["call_outcome"] = outcome
                    result["call_outcome_description"] = DISPOSITION_MAP[outcome]
                    result["qna"] = []

            # 2b. Enriched → Approved when every schema question ID has a real answer.
            #    The LLM sometimes counts business-name/city questions (asked after specs)
            #    as unanswered qualification questions, leaving the outcome at Enriched even
            #    though every schema question has a valid answer in the qna array.
            #    Guard: we check by schema question ID, not raw count, so spurious extra
            #    qna entries from business-detail questions don't trigger the promotion.
            if outcome == "Enriched" and questions:
                schema_ids = {str(q.get("id", "")) for q in questions if q.get("id")}
                answered_ids = {
                    str(e.get("id", ""))
                    for e in qna
                    if str(e.get("id", "")) in schema_ids
                    and (e.get("answ") or "").strip().lower() not in ("not sure", "")
                }
                if schema_ids and answered_ids >= schema_ids:
                    logger.info(
                        f"[POST-PROC] Enriched → Approved: all schema question IDs "
                        f"{schema_ids} have valid answers"
                    )
                    outcome = "Approved"
                    result["call_outcome"] = outcome
                    result["call_outcome_description"] = DISPOSITION_MAP[outcome]

            # 3. Hard outcomes that must never be overridden by downstream logic.
            _HARD_OUTCOMES = {
                "Short Hangup", "Voicemail", "Wrong Number", "Seller Intent",
                "Abusive Lead", "DNC Client : Don't Call Further",
                "Language Issue", "Technical Issue - Call Connected",
            }

            if outcome not in _HARD_OUTCOMES:
                # 4. No user signal at all → cannot be Interested/Enriched/Approved.
                #    Gemini greeting TTS splits into multiple chunks, so 2 agent turns
                #    with zero user speech is still a Short Hangup, not engagement.
                if not _has_any_user_signal and outcome in ("Interested", "Enriched", "Approved"):
                    logger.info(
                        f"[POST-PROC] {outcome} with zero user signal (no live turns, no "
                        f"muted transcript) → Short Hangup"
                    )
                    outcome = "Short Hangup"
                    result["call_outcome"] = outcome
                    result["call_outcome_description"] = DISPOSITION_MAP[outcome]
                    result["qna"] = []

                # 5a. No live user turns + Interested → Short Hangup.
                #     Muted-capture content is recorded during the bot's greeting window —
                #     before the user has heard the product question. Any LLM "Interested"
                #     based solely on muted content (no live transcript user turn) is
                #     unreliable. Downgrade to Short Hangup for short calls.
                if (
                    outcome == "Interested"
                    and not non_empty_user_turns
                    and duration_secs is not None
                    and duration_secs < 20
                ):
                    logger.info(
                        f"[POST-PROC] Interested → Short Hangup: no live user turns, "
                        f"only muted-capture content, duration={duration_secs:.0f}s < 20s"
                    )
                    outcome = "Short Hangup"
                    result["call_outcome"] = outcome
                    result["call_outcome_description"] = DISPOSITION_MAP[outcome]
                    result["qna"] = []

                # 5a-2. Hard sub-20s Interested → Short Hangup regardless of live turns.
                #       The 2-step greeting alone takes 5+ seconds; in < 20s the bot
                #       cannot complete Step 1a + Step 1b + even start Q1. Any "Interested"
                #       returned for a sub-20s call is either a phone-answer reflex or
                #       Sarvam/post-call STT noise that fooled the LLM — not real engagement.
                if (
                    outcome == "Interested"
                    and duration_secs is not None
                    and duration_secs < 20
                ):
                    logger.info(
                        f"[POST-PROC] Interested → Short Hangup: hard sub-20s rule, "
                        f"duration={duration_secs:.0f}s (live turns present but call too short "
                        f"for genuine qualification)"
                    )
                    outcome = "Short Hangup"
                    result["call_outcome"] = outcome
                    result["call_outcome_description"] = DISPOSITION_MAP[outcome]
                    result["qna"] = []

                # 5b. Duration-aware Interested → Short Hangup for very short calls.
                #     Calls at or under 30 s with only bare acknowledgements (हाँ / ji / yes / ok)
                #     and no valid spec values are almost always Short Hangups — the buyer
                #     said a reflexive yes and disconnected, not a genuine product confirmation.
                #     ~10 % of these may be genuine quick yeses; that tradeoff is accepted.
                #     Boundary is inclusive (≤ 30) to catch exact-30s boundary cases.
                if (
                    outcome == "Interested"
                    and duration_secs is not None
                    and duration_secs <= 30
                ):
                    _BARE_ACK_SET = {
                        "haan", "ha", "han", "ji", "jee", "yes", "okay", "ok",
                        "हाँ", "हां", "हा", "जी", "ठीक", "theek", "bilkul",
                        "haan ji", "ji haan", "sahi", "acha", "achha", "accha",
                        # Gujarati/Marathi phone-answer greeting — never a product confirmation
                        "om", "hello", "hi", "हेलो", "हाय",
                        # exclamations / filler — not product confirmation
                        "वाह", "wah", "अरे", "arrey",
                        # function word appearing alone in rescue-injection noise
                        "है",
                        # Devanagari "yes" — STT sometimes renders English "yes" in Devanagari
                        "यस",
                        # Devanagari "all right" — common Hindi phone filler
                        "ऑल", "राइट",
                    }
                    _bare_ack_nfc = {unicodedata.normalize("NFC", w) for w in _BARE_ACK_SET}
                    _all_user_words: set[str] = set()
                    for _t in transcript:
                        if _t.get("role") == "user":
                            for _w in (_t.get("text") or "").split():
                                _clean = _strip_punct(_w)
                                if _clean:
                                    _all_user_words.add(_clean)
                    # Also include words from muted transcript
                    for _m in (muted_transcript or []):
                        for _w in (_m or "").split():
                            _clean = unicodedata.normalize("NFC", re.sub(r"[^\w]", "", _w.lower()))
                            if _clean:
                                _all_user_words.add(_clean)
                    if not _all_user_words or not (_all_user_words - _bare_ack_nfc):
                        logger.info(
                            f"[POST-PROC] Interested → Short Hangup: duration={duration_secs:.0f}s < 25s, "
                            f"user signal is bare acknowledgement only: {_all_user_words}"
                        )
                        outcome = "Short Hangup"
                        result["call_outcome"] = outcome
                        result["call_outcome_description"] = DISPOSITION_MAP[outcome]
                        result["qna"] = []

                # 6. Short-call Could Not Confirm → Short Hangup when user speech is
                #    off-topic / garbled / contains no product signal.
                #    "Could Not Confirm" requires a clear reason (hold, handoff, IVR).
                #    Background noise captured as STT text is NOT a reason — it's Short Hangup.
                if (
                    outcome == "Could Not Confirm"
                    and duration_secs is not None
                    and duration_secs < 20
                    and not _has_handoff_turn         # handoff is a valid CNC reason
                ):
                    # Collect all user-side words
                    _cnc_user_words: set[str] = set()
                    for _t in transcript:
                        if _t.get("role") == "user":
                            for _w in (_t.get("text") or "").split():
                                _c = _strip_punct(_w)
                                if _c:
                                    _cnc_user_words.add(_c)
                    # Words that indicate a legitimate CNC (hold, transfer, IVR prompts)
                    _CNC_SIGNALS = {
                        "hold", "wait", "ruko", "रुको", "minute", "मिनट", "second", "सेकंड",
                        "bhai", "भाई", "boss", "sir", "साहब", "transfer", "connect",
                    }
                    _nfc_cnc = {unicodedata.normalize("NFC", w) for w in _CNC_SIGNALS}
                    if not (_cnc_user_words & _nfc_cnc):
                        logger.info(
                            f"[POST-PROC] Could Not Confirm → Short Hangup: "
                            f"dur={duration_secs:.0f}s < 20s, no valid CNC signal in user words: "
                            f"{_cnc_user_words}"
                        )
                        outcome = "Short Hangup"
                        result["call_outcome"] = outcome
                        result["call_outcome_description"] = DISPOSITION_MAP[outcome]
                        result["qna"] = []

            # ── END POST-PROCESSING ────────────────────────────────────────

            return result
    except Exception as e:
        logger.error(f"[ANALYSIS] LLM analysis failed: {type(e).__name__}: {e}")
        return fallback_analysis(base_status)


_FALLBACK_B2B_SCORE: dict = {"deal_value": "", "lead_intent_score": "", "urgency_flag": "no"}


async def generate_b2b_score(
    transcript: list[dict],
    http_session: aiohttp.ClientSession,
    model: str = "gemini-3.1-flash-lite",
) -> dict:
    """Run B2B lead-scoring rubric on the transcript. Returns deal_value, lead_intent_score, urgency_flag."""
    if not transcript:
        return _FALLBACK_B2B_SCORE.copy()

    lines = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in transcript)

    prompt = f"""You are an expert B2B lead qualification analyst. Your task is to score a sales call transcript and return a structured JSON object. Score only what is explicitly stated — do not infer or assume missing information. If the user is talking about multiple products, consider only the main product in the conversation.

SCORING RUBRIC (max 10 points)

1. Requirement Intent (0–5 pts) — How certain is the prospect about purchasing?
   → Explicit, confident intent ("we need", "we want to order")          5
   → Positive but hedged ("probably", "thinking about it", "might")      3–4
   → Vague or exploratory only ("just checking", "not sure yet")         1–2
   → No intent, or explicitly not buying                                 0  → triggers final_score override (see rules)

2. Requirement Clarity (0–3.5 pts) — How actionable is the stated requirement?
   → Quantity + product type + specifications all clearly stated         3–3.5
   → Quantity or specs stated, but not both                              1.5–2.5
   → Neither quantity nor specs provided                                 0–1

3. Engagement & Completion (0–1.5 pts) — Did the prospect actively participate?
   → Answered all or most questions and stayed till the end              1.5
   → Partial engagement, some questions skipped or deflected             0.5–1
   → Dropped call or non-cooperative                                     0

DERIVED FIELDS
- urgency_flag: Set true if the prospect explicitly mentions urgency (e.g. "urgent", "ASAP", "by Friday", specific near deadline). Otherwise false.
- extracted_quantity: The numeric quantity stated. If a range is given, return the average.
- estimated_unit_price: Infer a reasonable B2B market price range per unit strictly in the Indian landscape, based on the product type and any constraints mentioned on the call. Return as an object with low and high values in INR.
- estimated_deal_value: STRICTLY computed as extracted_quantity * ((estimated_unit_price.low + estimated_unit_price.high) / 2). Without fail, use the average of the unit price range, DO NOT use a not a low–high range of the value at any instance.
- lead_category: Based on final_score — "High" (7–10), "Medium" (4–6.9), "Low" (0–3.9).

HARD RULES
1. If requirement_intent_score = 0, set final_score = 0 immediately and do not compute other scores.
2. final_score = requirement_intent_score + clarity_score + engagement_score. No other formula.
3. Score buying signals only — ignore tone, sentiment, and politeness.
4. The reason field must follow this structure: [what signals intent] · [what clarity gaps exist, if any] · [engagement observation].
5. A relevant short or single-word answer ("yes", "correct", "confirmed") given in direct response to a question counts as fully valid for that dimension. Do not penalize brevity — score the signal, not the elaboration.

OUTPUT — strict JSON, no additional keys or commentary:
{{
  "deal_value": "<estimated deal value as a single number string, e.g. '₹75,000', or '' if cannot be determined>",
  "lead_intent_score": "<final_score as a string, e.g. '7.5'>",
  "urgency_flag": "<'yes' if urgency detected, 'no' otherwise>"
}}

CONVERSATION TO ANALYZE:
{lines}"""

    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={GEMINI_API_KEY}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
        }
        async with http_session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
            if "candidates" not in data or not data["candidates"]:
                raise ValueError(f"No candidates: {data.get('error') or data}")
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(raw)
            result.setdefault("deal_value", "")
            result.setdefault("lead_intent_score", "")
            result.setdefault("urgency_flag", "no")
            return result
    except Exception as e:
        logger.error(f"[B2B SCORE] LLM scoring failed: {type(e).__name__}: {e}")
        return _FALLBACK_B2B_SCORE.copy()
