"""Jev second opinion on the call outcome.

Gemini still does the work only a generative model can do: the summary, the
business fields, and pulling each answer out of the transcript. Jev answers two
things Gemini is bad at and has no confidence for:

  1. Which of the 19 dispositions this call was, with a probability per outcome.
  2. Per schema question, whether the buyer actually answered it.

Point 2 is the reason this exists. Post-processing rules 2a-2d in analysis.py all
recount answered questions by hand because the model miscounts them, and each one
re-derives the Approved/Enriched/Interested line from a slightly different angle.
A Noul per question plus a count in Python is that line, once.

Modes (JEV_JUDGE_MODE):
    off      — never called.
    shadow   — called, recorded under `jev` in the analysis, changes nothing.
    enforce  — may correct the outcome within the qualification family only.

Shadow is the default whenever a key is present. Run it there until the recorded
agreement rate says the thresholds are right.

Design notes, tradeoffs and open questions: JEV_SECOND_OPINION.md
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Only these outcomes may be corrected in enforce mode. Everything else — DNC,
# Abusive Lead, Voicemail, Wrong Number, Technical Issue — is either set by a
# deterministic guard before the LLM runs or carries a consequence too heavy to
# hand to a probability.
QUALIFICATION_FAMILY = frozenset({
    "Approved", "Enriched", "Interested", "Could Not Confirm", "Not Interested",
})

ANSWERED_NOUL = (
    "Did the buyer give a genuine, on-topic reply to this question: {text}\n"
    "An honest 'I am not sure' or 'I do not know' COUNTS as answered. "
    "Silence, a changed subject, a question back to the agent, or no reply at all "
    "does NOT count as answered."
)

B2B_NOUL = (
    "Is this caller buying for a business or trade purpose, rather than for personal "
    "or household use?"
)


def mode() -> str:
    """off | shadow | enforce. Unknown values fall back to off, loudly."""
    raw = os.getenv("JEV_JUDGE_MODE", "off").strip().lower()
    if raw not in ("off", "shadow", "enforce"):
        logger.warning("[JEV] unknown JEV_JUDGE_MODE %r — treating as off", raw)
        return "off"
    if raw != "off" and not os.getenv("TYPESAFE_API_KEY"):
        logger.warning("[JEV] JEV_JUDGE_MODE=%s but TYPESAFE_API_KEY is unset — treating as off", raw)
        return "off"
    return raw


def answered_gate() -> float:
    return float(os.getenv("JEV_ANSWERED_GATE", "0.6"))


def outcome_gate() -> float:
    return float(os.getenv("JEV_OUTCOME_CONFIDENCE_GATE", "0.55"))


@dataclass
class JevVerdict:
    outcome: str
    outcome_confidence: float
    probabilities: dict[str, float]
    answered: dict[str, float] = field(default_factory=dict)   # question id -> probability
    b2b: float | None = None

    def answered_ids(self, gate: float) -> set[str]:
        return {qid for qid, p in self.answered.items() if p >= gate}

    def as_document(self, gate: float) -> dict[str, Any]:
        """The block stored on the transcript. Keep it small and inspectable."""
        top = sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return {
            "outcome": self.outcome,
            "confidence": round(self.outcome_confidence, 4),
            "top_outcomes": [{"outcome": o, "p": round(p, 4)} for o, p in top],
            "answered": {qid: round(p, 4) for qid, p in self.answered.items()},
            "answered_count": len(self.answered_ids(gate)),
            "b2b": None if self.b2b is None else round(self.b2b, 4),
        }


# ── The call ───────────────────────────────────────────────────────────────

def build_questions(lines: str, schema_questions: list[dict], disposition_map: dict[str, str]) -> dict:
    """One question set: the disposition Choice, one Noul per schema question, one B2B Noul."""
    from typesafe_sdk import Choice, Noul

    questions: dict[str, Any] = {
        "outcome": Choice(
            instructions=(
                "Read the call transcript in state and decide what this call was. "
                "Judge only what the transcript shows."
            ),
            criteria=dict(disposition_map),
        ),
        "b2b": Noul(instructions=B2B_NOUL),
    }
    for q in schema_questions:
        qid = str(q.get("id") or "")
        text = (q.get("text") or "").strip()
        if not qid or not text:
            continue
        questions[f"answered_{qid}"] = Noul(instructions=ANSWERED_NOUL.format(text=text))
    return questions


async def judge(
    *,
    lines: str,
    schema_questions: list[dict],
    disposition_map: dict[str, str],
    muted_lines: str = "",
) -> JevVerdict | None:
    """One system_one call. Returns None on any failure — never raises into the worker."""
    from typesafe_sdk import AsyncTypeSafeClient

    questions = build_questions(lines, schema_questions, disposition_map)
    state = {"transcript": lines}
    if muted_lines:
        state["muted_transcript"] = muted_lines

    try:
        async with AsyncTypeSafeClient() as client:
            response = await client.system_one(state=state, questions=questions)
    except Exception as exc:  # the worker must survive a judge outage
        logger.warning("[JEV] judge call failed, continuing without it: %s", exc)
        return None

    return verdict_from(response, schema_questions)


def verdict_from(response: Any, schema_questions: list[dict]) -> JevVerdict:
    choice = response.choices["outcome"]
    answered = {}
    for q in schema_questions:
        qid = str(q.get("id") or "")
        key = f"answered_{qid}"
        if qid and key in response.nouls:
            answered[qid] = response.nouls[key].noul
    b2b = response.nouls["b2b"].noul if "b2b" in response.nouls else None
    return JevVerdict(
        outcome=choice.choice,
        outcome_confidence=choice.confidence,
        probabilities=dict(choice.probabilities),
        answered=answered,
        b2b=b2b,
    )


# ── Reconciliation: what the verdict is allowed to change ──────────────────

def expected_tier(answered_count: int, total_questions: int, gemini_outcome: str) -> str | None:
    """The Approved / Enriched / Interested line, derived from a count.

    Returns None when the call is not in the qualification family at all, which
    is where this rule has no opinion.
    """
    if gemini_outcome not in QUALIFICATION_FAMILY or total_questions == 0:
        return None
    if answered_count >= total_questions:
        return "Approved"
    if answered_count > 0:
        return "Enriched"
    return None  # zero answered — Interested vs Not Interested vs Could Not Confirm
                 # turns on intent, not on a count, so leave it to the model.


def reconcile(
    result: dict,
    verdict: JevVerdict | None,
    schema_questions: list[dict],
    *,
    judge_mode: str,
    disposition_map: dict[str, str],
    gate: float | None = None,
    conf_gate: float | None = None,
) -> dict:
    """Attach the Jev block, and in enforce mode correct the outcome.

    Mutates and returns `result`, matching how analysis.py post-processing works.
    """
    if verdict is None or judge_mode == "off":
        return result

    gate = answered_gate() if gate is None else gate
    conf_gate = outcome_gate() if conf_gate is None else conf_gate

    gemini_outcome = result.get("call_outcome", "")
    total = len([q for q in schema_questions if q.get("id") and q.get("text")])
    answered_count = len(verdict.answered_ids(gate))
    tier = expected_tier(answered_count, total, gemini_outcome)

    block = verdict.as_document(gate)
    block["agrees_with_gemini"] = verdict.outcome == gemini_outcome
    block["expected_tier"] = tier
    block["mode"] = judge_mode

    # Why a human should look. Empty list means nothing stood out.
    review: list[str] = []
    if verdict.outcome_confidence < conf_gate:
        review.append(f"low outcome confidence {verdict.outcome_confidence:.2f} < {conf_gate:.2f}")
    if not block["agrees_with_gemini"]:
        review.append(f"jev says {verdict.outcome!r}, gemini says {gemini_outcome!r}")
    if tier and tier != gemini_outcome:
        review.append(f"{answered_count}/{total} questions answered implies {tier!r}")
    block["review_reasons"] = review
    block["needs_review"] = bool(review)

    if judge_mode == "enforce" and tier and tier != gemini_outcome:
        # Only the count-derived tier is enforced, and only inside the family.
        # The disposition Choice itself stays advisory until calibration says otherwise.
        logger.info(
            "[JEV] %s -> %s: %d of %d schema questions answered",
            gemini_outcome, tier, answered_count, total,
        )
        block["corrected_from"] = gemini_outcome
        result["call_outcome"] = tier
        result["call_outcome_description"] = disposition_map.get(tier, "")

    result["jev"] = block
    return result
