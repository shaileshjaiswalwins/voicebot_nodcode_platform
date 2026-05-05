"""Post-call Gemini analysis — ported verbatim from bot.py."""

import json
import re
from datetime import datetime, timedelta, timezone

import aiohttp
from loguru import logger

from .config import GEMINI_API_KEY

IST = timezone(timedelta(hours=5, minutes=30))

DISPOSITION_MAP: dict[str, str] = {
    "Short Hangup":                      "The call ended after the agent's opening line only — the customer said nothing, or gave a single bare yes/no, and disconnected before any product discussion or qualification questions occurred.",
    "Voicemail":                        "The call went to the recipient's voicemail instead of connecting directly.",
    "Wrong Number":                     "The number dialed does not belong to the intended customer.",
    "Approved":                         "The customer confirmed the product and answered ALL specification questions.",
    "Enriched":                         "The customer confirmed the product and answered at least one (but not all) specification questions.",
    "Interested":                       "The customer showed clear positive interest in the product during the conversation (engaged meaningfully, asked follow-up questions, showed enthusiasm) but did NOT give an explicit confirmation of their requirement. The buyer's intent seems positive but no direct 'हाँ/yes' or product confirmation was obtained.",
    "Product Confirmed":                "The customer confirmed they need the product but answered ZERO specification questions.",
    "Not Interested":                   "The customer clearly stated they are not interested or do not need the product.",
    "Could Not Confirm":                "The customer was uncertain and could not confirm whether they still need the product.",
    "Alternate Number":                 "The customer provided a different or alternate contact number.",
    "Already Spoken":                   "The customer has already discussed or interacted about the requirement with JD or the seller.",
    "Will do it Myself":                "The customer still has the requirement but will source/handle it themselves without JD's help — they explicitly declined seller connections (e.g. 'मैं खुद देख लूँगा', 'I'll manage it myself'). The need exists; only JD's assistance is rejected. Distinct from Not Interested.",
    "Call Rescheduled":                 "The customer asked to call at a specific date and time.",
    "Seller Intent":                    "The caller is a seller or vendor trying to offer their own products/services — they are NOT a buyer with a requirement. They may want to list on JustDial or pitch their business. This is the opposite of a buyer lead.",
    "Abruptly disconnected and not Receiving": "The customer disconnected or stopped responding before confirming whether they need the product — zero product confirmation was obtained.",
    "Abusive Lead":                     "The recipient exhibited abusive or inappropriate behavior during the call.",
    "DNC Client : Don't Call Further":  "The customer explicitly requested not to be contacted again.",
    "Other Cases":                      "The call outcome does not fit into any predefined categories.",
    "Technical Issue - Call Connected": "The call connected but was disrupted by technical issues.",
    "Language Issue":                   "Communication was not possible due to a language mismatch.",
}

_VALID_OUTCOMES = set(DISPOSITION_MAP.keys())


def status_to_outcome(status: str) -> str:
    return {
        "completed": "Could Not Confirm",
        "disconnected": "Abruptly disconnected and not Receiving",
    }.get(status, "Abruptly disconnected and not Receiving")


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
Work through the numbered rules below in ORDER. Stop at the FIRST rule that matches. Do not skip ahead or apply a lower-numbered rule if a higher-numbered one already matched.
━━━━━━━━━━━━━━━━━━━━━━━━

RULE 0 — SHORT HANGUP (check this BEFORE everything else):
  Condition: The call ended with ZERO substantive buyer engagement. This means EITHER:
    (a) The buyer said absolutely nothing at all, OR
    (b) The buyer's ONLY utterance(s) across the ENTIRE call are bare, non-substantive words — including but not limited to: "हाँ", "हां", "जी", "yes", "no", "नहीं", "ok", "okay", "hello", "हेलो", "सर", "sir", "जब", or similar single-word non-answers — AND no product discussion or spec answers were obtained.
  IMPORTANT: The agent may have spoken multiple turns (including spec questions) before the buyer responded. This does NOT disqualify Short Hangup. What matters is whether the BUYER gave any substantive response. If the buyer only ever uttered bare words/greetings and the call ended, this is Short Hangup regardless of how many agent turns occurred.
  → Output: "Short Hangup". STOP. Do not evaluate any further rule.

RULE 1 — SELLER INTENT (check second):
  Condition: The caller is acting as a SELLER or VENDOR — they are offering their own products/services, trying to list on JustDial, or pitching their business. They are NOT a buyer with a requirement.
  Signals: phrases like "हम supply करते हैं", "हमारे पास stock है", "मैं manufacturer हूँ", "I want to list my business", "we provide X".
  → Output: "Seller Intent". STOP.

RULE 2 — VOICEMAIL:
  Condition: The call was answered by an automated voicemail/IVR system and no human spoke.
  → Output: "Voicemail". STOP.

RULE 3 — LANGUAGE ISSUE:
  Condition: Communication was entirely impossible because neither party could understand the other's language throughout the call.
  → Output: "Language Issue". STOP.

RULE 4 — ABUSIVE LEAD:
  Condition: The recipient was abusive, used profanity, or behaved inappropriately.
  → Output: "Abusive Lead". STOP.

RULE 5 — DNC:
  Condition: The customer explicitly said they do NOT want to be called again (e.g. "dobara mat call karna", "remove my number", "मुझे call मत करो").
  → Output: "DNC Client : Don't Call Further". STOP.

RULE 6 — WRONG NUMBER:
  Condition: The person who answered confirmed the number does not belong to the intended customer.
  → Output: "Wrong Number". STOP.

━━ PRODUCT CONFIRMATION GATE ━━
Before applying Rules 7–12, determine: Did the customer confirm the product?
"Product confirmed" = the customer clearly indicated they still need the product via ANY of:
  • Saying "हाँ" / "जी हाँ" / "हां" / "yes" / "ji" / "bilkul" in response to "do you need X?" or "आपको X की requirement है ना?"
  • Naming a specific product variant or material (e.g. "gate वाला", "stainless चाहिए")
  • Providing ANY specific product specification, grade, or quantity value
  • Asking the agent a question about the product (pricing, delivery, etc.) — implicit confirmation
If ANY of the above happened → product IS confirmed. Proceed to Rules 7–9.
If NONE of the above happened → skip Rules 7–9 and go to Rule 10.

━━ WHAT COUNTS AS A VALID SPEC ANSWER ━━
A specification question is "answered" ONLY if the buyer provided a SPECIFIC value:
  ✓ Valid: a named option ("Rubber", "Three Phase", "Double Door"), a number+unit ("500 pieces", "20 L"), a material name, a grade, any concrete choice from the question's options.
  ✗ NOT valid: "हाँ" / "हां" / "yes" / "जी" / "ok" said in response to a spec question — these are bare acknowledgements, NOT spec values. Saying "yes" to "capacity कितनी चाहिए — 20L, 25L, 30L?" does NOT count as answering the capacity question.
  ✗ NOT valid: vague answers like "standard", "whatever is normal", "you decide", "don't know" — these give no usable data.
Count a spec question as answered ONLY when the buyer supplied an actual value from the options or a concrete free-text equivalent.

RULE 7 — APPROVED:
  Condition: Product confirmed AND buyer answered ALL {len(questions)} specification questions with valid specific values.
  → Output: "Approved". STOP.

RULE 8 — ENRICHED:
  Condition: Product confirmed AND buyer answered at least ONE but NOT ALL specification questions with valid specific values.
  → Output: "Enriched". STOP.

RULE 9 — PRODUCT CONFIRMED:
  Condition: Product confirmed AND buyer answered ZERO specification questions with valid specific values.
  → Output: "Product Confirmed". STOP.

RULE 10 — INTERESTED (positive engagement without explicit confirmation):
  Condition: The customer did NOT give an explicit product confirmation but showed CLEAR positive interest — they engaged meaningfully with the product topic, asked follow-up questions about it, or showed enthusiasm — without ever rejecting or denying the need.
  STRICT: Do NOT use "Interested" if the customer was vague or non-committal. There must be a clearly positive, engaged response.
  → Output: "Interested". STOP.

RULE 11 — NOT INTERESTED:
  Condition: The customer clearly stated they do NOT need the product or are not interested. The requirement itself is gone.
  Signals: "नहीं चाहिए", "requirement नहीं है", "cancel कर दो", "I don't need it", "already purchased", "work is done".
  STRICT: Do NOT confuse with "Will do it Myself" (need exists but rejects JD's help) or "Could Not Confirm" (unsure).
  → Output: "Not Interested". STOP.

RULE 12 — WILL DO IT MYSELF:
  Condition: The customer STILL has the requirement but will source/handle it themselves without JD's help. They explicitly declined seller connections.
  Signals: "मैं खुद देख लूँगा", "I'll manage it myself", "don't send sellers", "khud khareed lenge".
  STRICT: The need must be real and present; only JD's assistance is rejected.
  → Output: "Will do it Myself". STOP.

RULE 13 — CALL RESCHEDULED:
  Condition: The customer asked to be called back at a SPECIFIC date and/or time.
  STRICT: A vague "call later" is NOT rescheduled — there must be a specific time commitment.
  → Output: "Call Rescheduled". STOP.

RULE 14 — ALREADY SPOKEN:
  Condition: The customer explicitly stated they have already spoken about this requirement with JD staff or the seller.
  → Output: "Already Spoken". STOP.

RULE 15 — ALTERNATE NUMBER:
  Condition: The customer provided a DIFFERENT contact number for follow-up.
  → Output: "Alternate Number". STOP.

RULE 16 — TECHNICAL ISSUE:
  Condition: The call connected but was cut or disrupted purely by technical problems (line drops, audio failure) with no meaningful exchange.
  → Output: "Technical Issue - Call Connected". STOP.

RULE 17 — COULD NOT CONFIRM:
  Condition: The customer gave genuinely vague or non-committal responses about whether they STILL need the product (e.g. "शायद", "पता नहीं", "I'll think about it", "not sure yet"). No spec answers, no affirmative confirmation, no clear rejection.
  STRICT: Do NOT use this if the customer said "हाँ/yes" or provided any spec detail — that is product confirmed. Do NOT use this for customers who were clearly interested (use "Interested" instead).
  → Output: "Could Not Confirm". STOP.

RULE 18 — ABRUPTLY DISCONNECTED (LAST RESORT ONLY):
  Condition: The call ended abruptly (line dropped, no goodbye) with zero product confirmation and none of Rules 0–17 applied.
  STRICT: Only use this when the call was clearly cut mid-conversation. Do NOT use this as a default when another rule fits better.
  → Output: "Abruptly disconnected and not Receiving". STOP.

RULE 19 — OTHER CASES (absolute last resort):
  Condition: Truly none of the above rules apply.
  → Output: "Other Cases".

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
            f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
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
