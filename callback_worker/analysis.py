"""Post-call Gemini analysis — ported verbatim from bot.py."""

import json
import re
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger

from .config import GEMINI_API_KEY

IST = timezone(timedelta(hours=5, minutes=30))

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
    # Check whether any user-side signal exists at all (live transcript OR muted capture).
    _has_any_user_signal = bool(non_empty_user_turns) or bool(
        muted_transcript and any((m or "").strip() for m in muted_transcript)
    )
    if not non_empty_user_turns:
        if not _has_any_user_signal:
            # Zero user speech from any source — true Short Hangup.
            # Do NOT rely on agent turn count here: the Gemini realtime model streams
            # greeting TTS in multiple chunks, so 2+ agent turns can all be parts of
            # the opening greeting, NOT evidence that the user spoke.
            return {
                "call_outcome": "Short Hangup",
                "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
                "call_summary": "No user response recorded — call ended after agent greeting only.",
                "is_business": "", "business_city": "", "business_name": "",
                "qna": [], "product_change": {}, "rescheduled_to": "",
            }
        # Muted transcript has content but no live user turns — STT failed on live mic
        # but user did speak during muted window. Fall through to LLM with context.

    _VOICEMAIL_SIGNALS_PRE = [
        "leave a message", "leave your message", "please leave a message",
        "after the beep", "after the tone", "at the beep",
        "you have reached", "you've reached",
        "unable to take your call", "cannot take your call",
        "not available to take your call",
        "record your message", "record a message",
        "mailbox is full", "mailbox full",
        "voice mail recording", "voicemail recording",
        "you may hang up", "may hang up now",
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
            words = {re.sub(r"[^\w-￿]", "", w.lower()) for w in (turn.get("text") or "").split() if w.strip()}
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

    # 6. All user turns contain only greeting tokens → no product engagement → Short Hangup.
    # Strip ASCII non-word chars only (re \w misses Devanagari combining vowel marks like े ो).
    _HELLO_ONLY = {"hello", "हेलो", "helo", "halo", "हैलो"}
    if non_empty_user_turns and all(
        not ({re.sub(r"[^\w-￿]", "", w.lower()) for w in (t.get("text") or "").split() if w.strip()} - _HELLO_ONLY)
        for t in non_empty_user_turns
    ):
        return {
            "call_outcome": "Short Hangup",
            "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
            "call_summary": "No product engagement — buyer responded only with a greeting.",
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

    # Agent-progression check: the bot is strictly programmed — it NEVER advances to
    # asking spec questions without first receiving product confirmation. So if the
    # agent has ≥2 turns with text AND there is some user-side signal (live transcript
    # or muted capture), the product was confirmed regardless of what STT captured.
    _agent_progressed = _has_any_user_signal and len(_agent_turns_with_text) >= 2

    def _tokens(text: str) -> set[str]:
        import unicodedata as _ud
        t = _ud.normalize("NFC", text)
        return {re.sub(r"[^\w]", "", w.lower()) for w in t.split() if w.strip()}

    _first_user_text = (non_empty_user_turns[0].get("text") or "") if non_empty_user_turns else ""
    _first_turn_is_confirmation = (
        _agent_progressed
        or bool(_tokens(_first_user_text) & _CONFIRMATION_TOKENS)
    )
    _product_confirmed_note = (
        f"\n⚠ PRODUCT CONFIRMED: The agent asked specification questions (progressed past "
        f"the greeting), which means the buyer confirmed the product. Do NOT classify as "
        f"Could Not Confirm or Short Hangup. "
        f"Classify as Interested (zero valid specs), Enriched (1+ valid specs), or Approved."
        if _first_turn_is_confirmation else ""
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

    prompt = f"""You are a strict call-analysis engine for JustDial's AI outbound qualification calls. Return accurate structured JSON — no guessing, no approximating. Every rule below is mandatory.{cut_note}{_user_sparse_note}{_trailing_agent_note}{_product_confirmed_note}

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
      EXCEPTION — bot mis-step: if the buyer's ONLY response to the opening product question
      was entirely off-topic (e.g., said a person's name, spoke as if answering a personal
      call, gave a completely unrelated utterance like "Aryan" or "Himank hello"), the bot
      may have advanced incorrectly. In this case GP-7 does NOT apply — evaluate
      product_confirmed from the buyer's actual words, and Could Not Confirm is permitted.

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

closing_line_spoken   — BOTH of the following substrings appear in the LAST assistant turn:
                          1. "सारी details मिल गईं"    2. "relevant sellers"
                        Both must be present simultaneously. No other phrasing qualifies.

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
  Condition: requirement already discussed with JD/seller, OR requirement already fulfilled.
  Examples: "already purchased", "kaam ho gaya", "le liya", "sorted", "already spoken to seller".
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
    requirement itself is entirely gone.
  ALL must be true:
    ✓ buyer explicitly rejected the product (not just an initial reflex "नहीं")
    ✓ NO positive engagement, NO spec answers, NO product questions anywhere in the call
    ✓ buyer's FINAL overall stance is negative
    ✓ cannot be explained by Seller Intent / Will do it Myself / Already Spoken / Wrong Number
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
  business_city  | City buyer stated specifically for their business location; "" if not stated.
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
  "business_name": "<exact business name from transcript, or ''>",
  "business_city": "<business city from transcript, or ''>",
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

            # 2. Enriched → Approved when every schema question ID has a real answer.
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
