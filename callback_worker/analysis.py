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
    "Product Confirmed":                "The customer confirmed they need the product but answered ZERO specification questions.",
    "Not Interested":                   "The customer clearly stated they are not interested or do not need the product.",
    "Could Not Confirm":                "The customer was uncertain and could not confirm whether they still need the product.",
    "Alternate Number":                 "The customer provided a different or alternate contact number.",
    "Already Spoken":                   "The customer has already discussed or interacted about the requirement with JD or the seller.",
    "Will do it Myself":                "The customer still has the requirement but will source/handle it themselves without JD's help — they explicitly declined seller connections (e.g. 'मैं खुद देख लूँगा', 'I'll manage it myself'). The need exists; only JD's assistance is rejected. Distinct from Not Interested.",
    "Call Rescheduled":                 "The customer asked to call at a specific date and time.",
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

    prompt = f"""Analyze this JustDial AI product qualification call between an AI agent and a buyer.{cut_note}
Current date and time (IST, GMT+5:30): {current_dt_str}

Transcript:
{lines}

Qualification questions:
{q_list}

OUTCOME SELECTION RULES — work through these in order and stop at the first match:

0. SHORT HANGUP CHECK (evaluate first, before anything else):
   If the transcript contains ONLY the agent's opening introduction line (e.g. "हेलो, मैं Tanya बोल रही हूँ Justdial से — आपको X की requirement है ना?") and the customer either said NOTHING at all, OR gave only a single bare acknowledgement (e.g. "हाँ", "जी", "yes", "no", "नहीं") and then the call ended — with NO further product discussion, NO specification questions asked, and NO meaningful exchange — select "Short Hangup" immediately and stop. Do NOT apply any other rule.

BEFORE YOU BEGIN: Determine if the customer confirmed the product.
"Product confirmed" = the customer clearly indicated they still need the product. This includes ANY of:
  - Saying "हाँ" / "जी हाँ" / "हां" / "yes" / "ji" or any affirmative response to "do you need X?" or "आपको X की requirement है ना?"
  - Naming a specific product variant (e.g. "gate वाला", "stainless चाहिए")
  - Providing any product specification or quantity
If ANY of the above happened, the product IS confirmed — proceed to rules 1–3. Do NOT select "Could Not Confirm".

1. Customer confirmed the product AND answered ALL specification questions → "Approved"
2. Customer confirmed the product AND answered at least one (but not all) specification questions → "Enriched"
3. Customer confirmed the product but answered ZERO specification questions → "Product Confirmed"
4. Customer said they will source/handle the requirement themselves without JD's help (e.g. "मैं खुद देख लूँगा", "I'll manage it myself", "don't need sellers") — the need still exists but they rejected JD's assistance → "Will do it Myself"
   IMPORTANT: distinguish from "Not Interested" — "Will do it Myself" means the need is real but they want no help; "Not Interested" means the need itself is gone.
5. Any other clear outcome (Not Interested, Wrong Number, Voicemail, Rescheduled, Already Spoken, Language Issue, etc.) → use the matching outcome from the list below.
6. "Could Not Confirm" — ONLY if the customer gave genuinely vague or non-committal responses specifically about whether they still need the product (e.g. "शायद", "पता नहीं", "I'll think about it", "not sure if I still need it") AND gave no spec answers and no affirmative confirmation. Do NOT use this when the customer said "हाँ/yes" or provided any spec details.
7. LAST RESORT — only if the call ended with no meaningful conclusion and none of rules 1–6 apply → "Abruptly disconnected and not Receiving"

Choose the BEST matching call_outcome from ONLY these exact values:
{disposition_options}

Return a single JSON object with exactly these keys:
- "call_outcome": one of the exact strings listed above
- "call_outcome_description": the corresponding description string
- "call_summary": 1-2 sentence English summary
- "is_business": "True" if purchasing for business, "False" if personal, "" if unknown
- "qna": For EVERY qualification question answered in the call, include one object.
  PRE-STEP (REQUIRED before filling qna): Read the transcript sequentially. Each time the AGENT asks one of the listed qualification questions (in any language/paraphrase), record the question id and the IMMEDIATELY FOLLOWING BUYER turn as its answer. Build an ordered list of (question_id → buyer_answer) pairs using ONLY conversation position. Never reassign an answer to a different question after building these pairs.
  EXTRACTION RULES (follow strictly):
  1. Go through the transcript in ORDER. For each BUYER turn, identify which qualification question the AGENT was asking immediately before that turn.
  2. Attribute the BUYER's response to THAT question — use CONVERSATION POSITION, NOT answer format or data type to decide attribution. The Nth qualification question asked by the agent gets the Nth buyer answer — full stop.
  3. Include a question if the buyer gave ANY relevant response: a number, an option value, a free-text answer, or "others/other". Do NOT skip answers just because the agent did not re-confirm them aloud.
  4. AGENT-CONFIRMATION RULE: If the buyer's response to a question is garbled, unclear, or ambiguous (STT noise), but the AGENT's very next turn explicitly states a confirmed value for that question (e.g., "Industrial नोट कर लिया", "okay, X", "समझ गई, X"), treat that agent-confirmed value as the buyer's answer. Include this question in qna even if the raw buyer turn looks like noise.
  5. Do NOT reassign an answer from one question to another because the answer "looks like" a different question's data type.
     — CRITICAL: An answer containing a grade/specification value (e.g., "140 GSM", "40 GSM", "120 GSM") is a GRADE answer, NOT a Quantity answer — even though it has a number and unit. Keep it with whichever grade/spec question the agent asked immediately before it.
     — CRITICAL: An answer like "50 units", "100 pieces" is a QUANTITY answer only if the agent was asking about quantity at that point. Position decides attribution; data format does not.
  6. REPEAT/CORRECTION RULE: If a buyer turn contains a value (especially a number+unit) that clearly belongs to a PREVIOUSLY asked question and does NOT match any option of the current question, treat it as the buyer correcting or confirming the prior question's answer — update that prior answer and do NOT assign it to the current question.
  7. POST-WRAP-UP RULE: If the buyer speaks AFTER the agent's closing/wrap-up statement, check whether the utterance clearly answers any unanswered qualification question from earlier in the call. If yes, include it in qna as the answer to that question. The call is not fully closed until both sides stop speaking.
  8. For "opt_id": if the normalized answer matches one of the question's options exactly (case-insensitive), set opt_id to that option's id.
     STT digit-drop correction — speech-to-text frequently drops a leading digit. If the buyer's answer is NOT an exact option match, check whether any option's text has the buyer's numeric value as a numeric suffix (e.g., buyer said "40 GSM" but an option is "140 GSM"; buyer said "20" but an option is "120 GSM"). If a suffix match exists, use that option and set opt_id to its id.
     If no match at all: set opt_id to null.
  9. For questions with type=="quantity": "answ" MUST be in the form "<number> <unit>" (e.g. "5 pieces", "100 boxes"). Apply this formatting rule ONLY to the answer of the quantity question itself — never apply it to grade, GSM, or specification answers that happen to contain a number. Use the unit from the buyer's answer if stated; otherwise use the first value from that question's "quantity_unit" list. If the buyer answered "Not Sure" / "पता नहीं" / could not give a number, set answ to "Not Sure" with no unit.
  Each entry: {{"id": <qid>, "quest": <question text>, "answ": <normalized English answer>, "opt_id": <matching option id or null>}}
- "product_change": {{"product_name": <new product name>}} if the buyer switched products mid-call, else {{}}
- "rescheduled_to": ISO datetime "YYYY-MM-DDTHH:MM:SS" in IST (GMT+5:30, no timezone suffix) if rescheduled, else ""

Return ONLY the JSON — no markdown, no explanation."""

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
