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
    "Enriched":                         "The customer confirmed the product and answered at least one (but not all) specification questions.",
    "Interested":                       "The customer confirmed they need the product but answered ZERO specification questions, OR showed clear positive interest (engaged meaningfully, asked follow-up questions, showed enthusiasm) without answering any spec questions. Covers both explicit product confirmation with zero specs and positive-but-unconfirmed engagement.",
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
) -> dict:
    if not transcript:
        return fallback_analysis(base_status)

    # --- Deterministic pre-LLM guards (saves cost + prevents model misclassification) ---

    user_turns = [t for t in transcript if t.get("role") == "user"]
    non_empty_user_turns = [t for t in user_turns if (t.get("text") or "").strip()]

    # No real user speech at all → Short Hangup (covers silent calls, inactivity timeouts,
    # and cases where STT saved empty strings for every user turn)
    if not non_empty_user_turns:
        return {
            "call_outcome": "Short Hangup",
            "call_outcome_description": DISPOSITION_MAP["Short Hangup"],
            "call_summary": "No user response recorded — call ended with agent turns only.",
            "is_business": "", "business_city": "", "business_name": "",
            "qna": [], "product_change": {}, "rescheduled_to": "",
        }

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
        "पुट योर कॉल ऑन होल्ड",           # transliterated English in Hindi script
        "होल्ड पर राख्यो छे",              # Gujarati hold-music phrase
        "hold par rakho chhe",
    ]

    for turn in transcript:
        text_lower = (turn.get("text") or "").lower()
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
    cut_note = (
        "\nNote: The call ended before the bot's closing phrase. "
        "Determine the outcome based on what was actually collected."
        if base_status == "disconnected" else ""
    )
    disposition_options = "\n".join(f'  "{k}": {v}' for k, v in DISPOSITION_MAP.items())
    current_dt_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

    prompt = f"""You are a strict call-analysis engine for JustDial's AI outbound qualification calls. Your ONLY job is to read the transcript and return accurate, structured JSON. Every rule below is mandatory — do not skip or approximate.{cut_note}

Current date and time (IST, GMT+5:30): {current_dt_str}

━━━━━━━━━━━━━━━━━━━━━━━━
TRANSCRIPT
━━━━━━━━━━━━━━━━━━━━━━━━
{lines}

━━━━━━━━━━━━━━━━━━━━━━━━
QUALIFICATION QUESTIONS
━━━━━━━━━━━━━━━━━━━━━━━━
{q_list}

━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — CLASSIFY THE CALL OUTCOME
━━━━━━━━━━━━━━━━━━━━━━━━

⚠ READ THE ENTIRE TRANSCRIPT BEFORE CLASSIFYING ⚠
Do NOT stop at the first negative word. Buyers often say "नहीं" reflexively at the start and then engage positively. The classification must reflect the OVERALL and FINAL state of the conversation, not a single early statement.

TWO CRITICAL META-RULES (apply throughout):

  PROGRESSION RULE: If the buyer starts negatively ("नहीं", "requirement नहीं है") but then CONTINUES talking, asks questions, provides specs, or engages with the product — that is POSITIVE PROGRESSION. Classify based on the positive engagement, not the initial "नहीं". Initial reflex negatives that are followed by substantive conversation are NOT "Not Interested."

  RETRACTION RULE: If the buyer initially seems to confirm the product but then CLEARLY and EXPLICITLY retracts (e.g. "मशीन नहीं लेना है", "actually I don't need it") — the retraction takes precedence over the earlier engagement. The buyer's final clear stance wins.

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 1 — DEFINITIVE CALL-ENDERS
Apply these first. Each is a complete, unambiguous signal that overrides everything else. STOP at the first match.
━━━━━━━━━━━━━━━━━━━━━━━━

RULE 0 — SHORT HANGUP:
  Condition A (zero engagement): The buyer said NOTHING at all — there are zero buyer turns in the transcript, or the call ended immediately after the agent's greeting with no buyer response whatsoever.
  Condition B (bare acknowledgment only): The buyer's ONLY responses were bare call-presence acknowledgments with no engagement with the product topic — e.g. "hello", "haan", "haan boliye", "ek second", "hold on", "ji", "kaun hai", "kya hai", "haan bolo", "ek minute", "ruko", "bhai", calling out a person's name ("आलोक भाई", "[name] bhai/ji") thinking it was a personal call, or asking about the agent's identity/gender ("लड़की है ना?", "kaun bol raha hai?", "kya aap insaan ho?", "which company?") — and the call ended before the agent even raised the product topic, OR the agent raised the product topic but the buyer's only response(s) remained in this bare-acknowledgment category with no reaction to the product itself.
  GENERAL PRINCIPLE: Any response that is solely about the presence/identity of the caller or agent, and does NOT in any way engage with, react to, or even acknowledge having heard the product topic, qualifies as a bare acknowledgment.
  COMBINED TEST: Did the buyer engage with the product topic in any way — hear about it, respond to it, ask about it, or react to it? If YES → do NOT use Short Hangup. Move to the next rule.
  STRICT: A buyer saying "नहीं" in response to the product question is a product-topic response → do NOT use Short Hangup. Only bare call-presence acknowledgments before the product topic count.
  → "Short Hangup". STOP.

RULE 1 — SELLER INTENT:
  Condition: The caller is NOT a buyer — they are on the supply/service side. This includes ANY of:
    • A vendor/supplier offering their own products or services ("हम supply करते हैं", "हमारे पास stock है", "we provide X", "हमारी कंपनी यही करती है")
    • A manufacturer or sub-contractor seeking job work / manufacturing contracts ("स्पेयर पार्ट बना रहा हूं", "जॉब वर्क करना है", "उससे मेरे को काम लेना है")
    • Someone trying to list their business on JustDial
    • Someone whose company already manufactures/supplies the exact product being discussed and wants to be a seller
  STRICT: Must be clear from what the caller SAYS, not inferred from their industry. A manufacturer genuinely buying materials for their own use is still a buyer.
  CRITICAL SIGNAL: Caller wants to GET work/contracts FROM other companies, not buy FROM JD's sellers → Seller Intent.
  → "Seller Intent". STOP.

RULE 2 — VOICEMAIL:
  Condition: The call was answered by an automated voicemail or IVR system rather than a live human.
  WHY THIS IS TRICKY: The voicemail system's recorded greeting gets transcribed into the transcript (usually as a "buyer" turn). The model must detect this automated text and classify as Voicemail — even if the words sound unusual or include dismissive phrases that might otherwise look like human rudeness.

  VOICEMAIL SIGNAL PHRASES — if ANY of the following appear anywhere in any transcript turn, classify as Voicemail immediately:
    English signals:
      • "leave a message", "leave your message", "please leave a message"
      • "after the beep", "after the tone", "at the beep"
      • "when you are finished recording", "when you have finished recording", "finished recording hang up"
      • "not available", "unable to take your call", "cannot take your call", "not available to take your call"
      • "you have reached", "you've reached", "you have reached the voicemail"
      • "record your message", "record a message"
      • "hang up or press", "press pound", "press hash"
      • "mailbox is full", "mailbox full"
      • "voice mail recording", "voicemail recording"
      • "you may hang up", "may hang up now"
      • "cannot come to the phone", "is not available right now"
      • "please try again later", "try your call again later"
    Hindi/Hinglish signals:
      • "sandesh chhod", "sandesh chhodein", "message chhod", "message chhodein"
      • "beep ke baad", "tone ke baad"
      • "uplabdh nahi", "उपलब्ध नहीं", "abhi available nahi"
      • "recording ke baad hang up", "recording khatam hone ke baad"
      • "aap ka call", "aapka call abhi"
      • "subscriber", "is number par", "yeh number"

  ADDITIONAL VOICEMAIL PATTERNS (any one is sufficient):
    • The agent's opening line is cut off mid-sentence in the very first agent turn (voicemail picks up during the greeting before it finishes)
    • A buyer/response turn contains a robotic or templated phrase with no conversational structure
    • The response sounds like a system announcement rather than a human reply
    • No back-and-forth human dialogue occurs — only the agent's turns and a system-style message

  CRITICAL: Do NOT let voicemail message content trigger Abusive Lead or any other rule. The voicemail system may say things like "hang up", "your call cannot be taken", "please try later" — these are automated system phrases, NOT human responses. Always classify as Voicemail if signals are present.
  → "Voicemail". STOP.

RULE 2A — CALL ON HOLD (caller placed bot on hold):
  Condition: A user/buyer turn contains a carrier or PBX hold-music IVR announcement — the clearest signal is the SAME message repeated in multiple languages within a single turn (e.g. Gujarati + Hindi + English in one block), or any of these phrases: "put your call on hold", "placed your call on hold", "please stay on the line", "stay on the line", "hold par rakha hai", "hold par raho", "लाइन पर रहो", "लाइन पर बने रहें", "होल्ड पर राख्यो छे".
  DISTINGUISH FROM VOICEMAIL: Voicemail = no live person ever answered. Hold = a live person answered but physically placed the call on hold. Do not confuse these.
  NOTE: A buyer saying "hold on" or "ek second" themselves is Rule 0 (Short Hangup), NOT this rule. Rule 2A applies only when the carrier/PBX system's automated hold-music message appears as a transcript turn.
  → "Could Not Confirm". STOP.

RULE 3 — WRONG NUMBER:
  Condition: Person who answered confirmed the number does not belong to the intended customer.
  → "Wrong Number". STOP.

RULE 4 — LANGUAGE ISSUE:
  Condition: Communication was entirely impossible due to a language mismatch throughout the entire call.
  → "Language Issue". STOP.

RULE 5 — ABUSIVE LEAD:
  Condition: A live HUMAN recipient was abusive, used profanity, or behaved inappropriately toward the agent.
  STRICT: This requires a real human response — not an automated system message. If there is ANY possibility the turn is from a voicemail or IVR system (even without explicit voicemail phrases), check Rule 2 first. Automated phrases like "hang up", "call cannot be taken", "please try again" are NEVER abusive — they are system messages.
  → "Abusive Lead". STOP.

RULE 6 — DNC:
  Condition: The customer explicitly said they do NOT want to be called again ("dobara mat call karna", "remove my number", "मुझे call मत करो", "number हटा दो").
  → "DNC Client : Don't Call Further". STOP.

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 2 — POSITIVE & SPECIFIC OUTCOMES
Check ALL of these BEFORE considering any negative outcome.
A call with even one positive signal belongs in this tier.
━━━━━━━━━━━━━━━━━━━━━━━━

━━ PRODUCT CONFIRMATION GATE ━━
Before applying Rules 7–9, determine: Did the customer confirm the product?
"Product confirmed" = the customer clearly indicated they still need the product via ANY of:
  • Saying "हाँ" / "जी हाँ" / "हां" / "yes" / "ji" / "bilkul" in response to "do you need X?" or "आपको X की requirement है ना?"
  • Naming a specific product variant or material (e.g. "gate वाला", "stainless चाहिए")
  • Providing ANY specific product specification, grade, or quantity value
  • Asking the agent a question SPECIFICALLY about the product (pricing, delivery, timeline, specs, availability) — implicit confirmation. Generic questions like "can you help me?", "which company?" or questions about the agent's identity do NOT count.
  PROGRESSION: If buyer said "नहीं" initially but then provided a spec or asked about the product → product IS confirmed. The later positive action overrides the initial "नहीं."
If ANY of the above happened anywhere in the call → product IS confirmed. Go to Rules 7–9.
If NONE of the above happened → skip Rules 7–9, go to Rule 10.

━━ WHAT COUNTS AS A VALID SPEC ANSWER ━━
  ✓ Valid: a named option ("Rubber", "Three Phase", "Double Door"), a number+unit ("500 pieces", "20 L"), a material/grade name, any concrete choice.
  ✗ NOT valid: "हाँ" / "हां" / "yes" / "जी" / "ok" in response to a spec question — bare acknowledgements, NOT spec values.
  ✗ NOT valid: vague filler sounds ("हम्म", "umm") — even if the agent assumed a value afterwards, the assumption does NOT count as a buyer answer.
  ✗ NOT valid: "standard", "whatever is normal", "you decide", "don't know" — no usable data.

RULE 7 — APPROVED:
  Condition: Product confirmed AND buyer answered ALL {len(questions)} specification questions with valid specific values.
  → "Approved". STOP.

RULE 8 — ENRICHED:
  Condition: Product confirmed AND buyer answered at least ONE but NOT ALL specification questions with valid specific values.
  → "Enriched". STOP.

RULE 9 — INTERESTED (product confirmed, zero specs):
  Condition: Product confirmed AND buyer answered ZERO specification questions with valid specific values.
  → "Interested". STOP.

RULE 10 — INTERESTED (positive engagement, no confirmation):
  Condition: Customer did NOT give an explicit product confirmation but showed CLEAR positive interest that is SPECIFICALLY ABOUT THE PRODUCT BEING QUALIFIED — e.g. asked about pricing, delivery timeline, product variants/specs, availability, quantity, or made a product-related comparison — without a final clear rejection.
  STRICT: There must be a clearly positive, product-focused response. The following do NOT qualify:
    ✗ Generic questions about the call or caller: "can you help me?", "what is this?", "which company are you from?", "kaun bol raha hai?" — about the call, not the product.
    ✗ Questions about the agent's identity or humanity: "lड़की है ना?", "are you a robot?", "who are you?"
    ✗ The buyer's expressed need is the OPPOSITE of buying the product (e.g. they want to sell/dispose of the item, not purchase it) — use Not Interested.
    ✗ Vague callbacks: "baad mein call karo", "call me later", "abhi busy hoon" without any product engagement — use Could Not Confirm.
    ✗ Vague or non-committal responses without product content — use Could Not Confirm (Tier 3).
  Reflex "नहीं" followed by genuine product-specific questions → use Interested.
  → "Interested". STOP.

RULE 11 — CALL RESCHEDULED:
  Condition: Customer asked to be called back at a SPECIFIC date and/or time.
  STRICT: A vague "call later" / "baad mein call karo" without a specific time is NOT rescheduled.
  → "Call Rescheduled". STOP.

RULE 12 — ALREADY SPOKEN:
  Condition: Customer explicitly stated they have already spoken about this requirement with JD staff or the seller, OR the customer's requirement has already been fulfilled (e.g. "already purchased", "kaam ho gaya", "le liya", "mil gaya", "sorted", "done already").
  → "Already Spoken". STOP.

RULE 13 — ALTERNATE NUMBER:
  Condition: Customer provided a DIFFERENT contact number for follow-up.
  → "Alternate Number". STOP.

RULE 14 — WILL DO IT MYSELF:
  Condition: Customer STILL has the requirement but will source/handle it themselves without JD's help. They explicitly declined seller connections.
  Signals: "मैं खुद देख लूँगा", "I'll manage it myself", "don't send sellers", "khud khareed lenge".
  STRICT: The need must be real and present; only JD's assistance is rejected. If the requirement itself is gone, use Not Interested (Tier 3).
  → "Will do it Myself". STOP.

━━━━━━━━━━━━━━━━━━━━━━━━
TIER 3 — NEGATIVE & UNCERTAIN OUTCOMES
Reach this tier ONLY if NONE of Rules 0–14 matched.
If ANY Tier 2 rule was even partially applicable, re-examine before falling here.
━━━━━━━━━━━━━━━━━━━━━━━━

RULE 15 — COULD NOT CONFIRM:
  Condition: Any of the following — (a) customer gave genuinely vague or non-committal responses about whether they STILL need the product (e.g. "शायद", "पता नहीं", "I'll think about it", "not sure yet"); (b) vague callback requests with no product engagement ("baad mein call karo", "call me later", "abhi busy hoon", "thodi der baad call karna") — the buyer gave no product signal, just asked to be called later without a specific time; (c) the call disconnected mid-conversation before any product confirmation was obtained and no other rule matched; (d) the call dropped after the product topic was raised but before the customer gave any usable response; (e) the buyer's response was off-topic (about something completely unrelated to the product) with no product engagement detected.
  No spec answers, no clear confirmation, no clear rejection required to use this outcome.
  STRICT: Do NOT use this if the customer said "हाँ/yes" or provided any spec detail → that is Interested (Tier 2). Do NOT use this if the customer was clearly positively interested → use Interested (Rule 10). Do NOT use this if the customer clearly rejected the product → use Not Interested (Rule 16). Do NOT use this if the customer never heard the product topic → use Short Hangup (Rule 0).
  → "Could Not Confirm". STOP.

RULE 16 — NOT INTERESTED:
  Condition: Customer CONSISTENTLY and CLEARLY stated they do NOT need the product. The requirement itself is entirely gone.
  MANDATORY CHECKS before selecting this — ALL must be true:
    ✓ Buyer explicitly said they don't need the product (not just an initial reflex "नहीं")
    ✓ There is NO positive engagement, NO spec answers, NO product questions anywhere in the call
    ✓ The buyer's FINAL and OVERALL stance is negative — not just an early statement that was later reversed
    ✓ Cannot be explained by Seller Intent, Will do it Myself, Already Spoken, or Wrong Number
  Signals: "नहीं चाहिए", "requirement नहीं है", "cancel कर दो".
  NOTE: "already purchased", "kaam ho gaya", "le liya", "sorted" etc. → use Already Spoken (Rule 12), NOT this rule.
  ⚠ DO NOT USE if: the buyer said "नहीं" once but then asked any question or provided any information → use Interested or Could Not Confirm.
  → "Not Interested". STOP.

RULE 17 — TECHNICAL ISSUE:
  Condition: The call connected but was disrupted entirely by technical problems (severe audio drops, line cuts) with no meaningful exchange achieved.
  STRICT: If any positive exchange occurred before the technical issue, use the appropriate Tier 2 outcome instead.
  → "Technical Issue - Call Connected". STOP.

RULE 18 — OTHER CASES (absolute last resort):
  Condition: Truly none of the above rules apply after careful evaluation of all tiers.
  → "Other Cases".

Valid outcome values (use EXACT strings only):
{disposition_options}

━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — EXTRACT QnA (for every qualification question answered in the call)
━━━━━━━━━━━━━━━━━━━━━━━━

PRE-STEP (mandatory): Read the transcript sequentially. Each time the AGENT asks one of the listed qualification questions (in any language/paraphrase), record the question_id and the IMMEDIATELY FOLLOWING BUYER turn as its raw answer. Build an ordered (question_id → buyer_answer) list using ONLY conversation position.

EXTRACTION RULES (all mandatory):
1. POSITION RULE: Attribute each buyer response to the qualification question the AGENT asked immediately before that buyer turn. Nth question asked = Nth buyer answer. Never reassign based on answer format or data type.
2. INCLUDE: any relevant buyer response — number, option, free-text, "others/other". Do NOT skip answers because the agent did not re-confirm them.
3. AGENT-CONFIRMATION RULE: If the buyer's response is garbled/unclear (STT noise) but the AGENT's very next turn explicitly restates a confirmed value (e.g. "Industrial नोट कर लिया", "okay, X"), treat that agent-confirmed value as the buyer's answer. Include the question.
   ANTI-HALLUCINATION EXCEPTION: This rule ONLY applies when the buyer gave a real (even if garbled) response. If the buyer said a vague filler sound ("हम्म", "umm", "uh", "achha") and the agent then ASSUMED a value and moved on (without the buyer actually confirming), do NOT credit the agent's assumption as the buyer's answer. A vague filler followed by an agent assumption is NOT a confirmed spec answer. Require that the buyer spoke a real value (however garbled) or explicitly echoed/confirmed the agent's restatement.
4. NO CROSS-TYPE REASSIGNMENT:
   — A grade/specification answer (e.g. "140 GSM", "40 GSM") stays with the spec/grade question — NOT reassigned to a quantity question even though it has a number.
   — A quantity answer (e.g. "50 pieces") is a quantity answer ONLY if the agent was asking about quantity at that moment.
5. CORRECTION RULE: If a buyer turn contains a value that clearly corrects or confirms a PREVIOUSLY answered question (and does NOT match any option of the current question), update the prior answer — do NOT assign to the current question.
6. POST-WRAP-UP RULE: If the buyer speaks AFTER the agent's closing/wrap-up statement and clearly answers an unanswered qualification question, include it in qna.
7. OPT_ID MATCHING:
   a. Exact match (case-insensitive) → use that option's id.
   b. STT digit-drop: if buyer said "40 GSM" but option is "140 GSM" (buyer value is a numeric suffix of the option text) → use that option's id.
   c. No match → set opt_id to null.
8. QUANTITY FORMAT: For type=="quantity" questions, "answ" MUST be "<number> <unit>" (e.g. "5 pieces"). Use buyer's unit if stated; else use first value from that question's quantity_unit list. If buyer could not give a number, set answ to "Not Sure".
   Apply this formatting ONLY to the quantity question — never to grade/spec answers.

Each qna entry: {{"id": <qid>, "quest": <question text>, "answ": <normalized English answer>, "opt_id": <matching option id or null>}}

━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — RETURN JSON
━━━━━━━━━━━━━━━━━━━━━━━━

Return a SINGLE JSON object with EXACTLY these keys — no extra keys, no markdown, no explanation:
{{
  "call_outcome": "<one exact string from the valid outcome list>",
  "call_outcome_description": "<the corresponding description from the list>",
  "call_summary": "<1-2 sentence English summary of what happened on the call>",
  "is_business": "<'True' if purchasing for business | 'False' if personal | '' if unknown>",
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
            f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
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
            return result
    except Exception as e:
        logger.error(f"[ANALYSIS] LLM analysis failed: {type(e).__name__}: {e}")
        return fallback_analysis(base_status)


_FALLBACK_B2B_SCORE: dict = {"deal_value": "", "lead_intent_score": "", "urgency_flag": "no"}


async def generate_b2b_score(
    transcript: list[dict],
    http_session: aiohttp.ClientSession,
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
- estimated_deal_value: Computed as {{ "low": extracted_quantity * estimated_unit_price.low, "high": extracted_quantity * estimated_unit_price.high }}.
- lead_category: Based on final_score — "High" (7–10), "Medium" (4–6.9), "Low" (0–3.9).

HARD RULES
1. If requirement_intent_score = 0, set final_score = 0 immediately and do not compute other scores.
2. final_score = requirement_intent_score + clarity_score + engagement_score. No other formula.
3. Score buying signals only — ignore tone, sentiment, and politeness.
4. The reason field must follow this structure: [what signals intent] · [what clarity gaps exist, if any] · [engagement observation].
5. A relevant short or single-word answer ("yes", "correct", "confirmed") given in direct response to a question counts as fully valid for that dimension. Do not penalize brevity — score the signal, not the elaboration.

OUTPUT — strict JSON, no additional keys or commentary:
{{
  "deal_value": "<estimated deal value range as a string, e.g. '₹50,000 - ₹1,00,000', or '' if cannot be determined>",
  "lead_intent_score": "<final_score as a string, e.g. '7.5'>",
  "urgency_flag": "<'yes' if urgency detected, 'no' otherwise>"
}}

CONVERSATION TO ANALYZE:
{lines}"""

    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
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
