"""Tests for the Jev second opinion in callback_worker/jev_judge.py.

The behaviour that matters is not the API call — it is what the verdict is
allowed to change. Post-processing rules 2a-2d in analysis.py each recount
answered questions by hand; these tests pin the single count that replaces them,
and pin that enforce mode cannot touch anything outside the qualification family.
"""

from dataclasses import dataclass, field

import pytest

from callback_worker import jev_judge

DISPOSITION_MAP = {
    "Approved": "Confirmed and answered ALL specification questions.",
    "Enriched": "Confirmed and answered at least one but not all.",
    "Interested": "Confirmed but answered zero.",
    "Could Not Confirm": "Did not confirm.",
    "Not Interested": "Not interested.",
    "Abusive Lead": "Abusive behaviour.",
    "DNC Client : Don't Call Further": "Asked not to be contacted.",
}

QUESTIONS = [
    {"id": "q1", "text": "What quantity do you need?"},
    {"id": "q2", "text": "What material?"},
    {"id": "q3", "text": "Which city?"},
]


@dataclass
class _Noul:
    noul: float


@dataclass
class _Choice:
    choice: str
    confidence: float = 0.9
    probabilities: dict = field(default_factory=dict)


@dataclass
class _Response:
    choices: dict
    nouls: dict


def _response(outcome="Approved", confidence=0.9, answered=(0.9, 0.9, 0.9), b2b=0.8):
    return _Response(
        choices={"outcome": _Choice(outcome, confidence, {outcome: confidence})},
        nouls={
            **{f"answered_q{i + 1}": _Noul(p) for i, p in enumerate(answered)},
            "b2b": _Noul(b2b),
        },
    )


def _result(outcome="Approved"):
    return {"call_outcome": outcome, "call_outcome_description": DISPOSITION_MAP.get(outcome, "")}


# ── mode gate ──────────────────────────────────────────────────────────────

def test_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("JEV_JUDGE_MODE", raising=False)
    assert jev_judge.mode() == "off"


def test_mode_without_a_key_is_off(monkeypatch):
    monkeypatch.setenv("JEV_JUDGE_MODE", "enforce")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert jev_judge.mode() == "off"


def test_unknown_mode_is_off(monkeypatch):
    monkeypatch.setenv("JEV_JUDGE_MODE", "yolo")
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert jev_judge.mode() == "off"


def test_valid_modes_pass_through(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    for m in ("shadow", "enforce"):
        monkeypatch.setenv("JEV_JUDGE_MODE", m)
        assert jev_judge.mode() == m


# ── question construction ──────────────────────────────────────────────────

def test_questions_skip_rows_with_no_id_or_text():
    pytest.importorskip("typesafe_sdk")
    qs = jev_judge.build_questions(
        "lines",
        QUESTIONS + [{"id": "", "text": "no id"}, {"id": "q9", "text": ""}],
        DISPOSITION_MAP,
    )
    assert set(qs) == {"outcome", "b2b", "answered_q1", "answered_q2", "answered_q3"}


# ── the count that replaces rules 2a-2d ────────────────────────────────────

def test_all_answered_implies_approved():
    assert jev_judge.expected_tier(3, 3, "Enriched") == "Approved"


def test_some_answered_implies_enriched():
    assert jev_judge.expected_tier(1, 3, "Approved") == "Enriched"


def test_zero_answered_has_no_opinion():
    # Interested vs Not Interested vs Could Not Confirm turns on intent, not a count.
    assert jev_judge.expected_tier(0, 3, "Interested") is None


def test_no_opinion_outside_the_qualification_family():
    assert jev_judge.expected_tier(3, 3, "Abusive Lead") is None
    assert jev_judge.expected_tier(3, 3, "DNC Client : Don't Call Further") is None


def test_no_opinion_when_the_schema_has_no_questions():
    assert jev_judge.expected_tier(0, 0, "Approved") is None


def test_answered_gate_is_a_threshold_not_a_vote():
    v = jev_judge.verdict_from(_response(answered=(0.95, 0.61, 0.59)), QUESTIONS)
    assert v.answered_ids(0.6) == {"q1", "q2"}
    assert v.answered_ids(0.9) == {"q1"}


# ── shadow mode ────────────────────────────────────────────────────────────

def test_shadow_records_but_never_corrects():
    result = _result("Approved")
    v = jev_judge.verdict_from(_response("Enriched", 0.9, (0.9, 0.1, 0.1)), QUESTIONS)
    out = jev_judge.reconcile(
        result, v, QUESTIONS, judge_mode="shadow", disposition_map=DISPOSITION_MAP
    )
    assert out["call_outcome"] == "Approved"          # untouched
    assert out["jev"]["expected_tier"] == "Enriched"  # but recorded
    assert out["jev"]["needs_review"] is True
    assert out["jev"]["agrees_with_gemini"] is False
    assert "corrected_from" not in out["jev"]


def test_off_mode_attaches_nothing():
    result = _result("Approved")
    v = jev_judge.verdict_from(_response(), QUESTIONS)
    out = jev_judge.reconcile(
        result, v, QUESTIONS, judge_mode="off", disposition_map=DISPOSITION_MAP
    )
    assert "jev" not in out


def test_a_failed_judge_call_changes_nothing():
    result = _result("Approved")
    out = jev_judge.reconcile(
        result, None, QUESTIONS, judge_mode="enforce", disposition_map=DISPOSITION_MAP
    )
    assert out == {"call_outcome": "Approved", "call_outcome_description": DISPOSITION_MAP["Approved"]}


# ── enforce mode ───────────────────────────────────────────────────────────

def test_enforce_demotes_approved_with_one_answer():
    """Rule 2a's case: closing line fired, but only one spec value is real."""
    result = _result("Approved")
    v = jev_judge.verdict_from(_response("Approved", 0.9, (0.95, 0.05, 0.05)), QUESTIONS)
    out = jev_judge.reconcile(
        result, v, QUESTIONS, judge_mode="enforce", disposition_map=DISPOSITION_MAP
    )
    assert out["call_outcome"] == "Enriched"
    assert out["call_outcome_description"] == DISPOSITION_MAP["Enriched"]
    assert out["jev"]["corrected_from"] == "Approved"


def test_enforce_promotes_enriched_when_every_question_is_answered():
    """Rule 2b's case, from a count instead of a set comparison."""
    result = _result("Enriched")
    v = jev_judge.verdict_from(_response("Enriched", 0.9, (0.9, 0.9, 0.9)), QUESTIONS)
    out = jev_judge.reconcile(
        result, v, QUESTIONS, judge_mode="enforce", disposition_map=DISPOSITION_MAP
    )
    assert out["call_outcome"] == "Approved"


def test_enforce_leaves_terminal_outcomes_alone():
    """An abusive or DNC call is decided by a deterministic guard, not a probability."""
    for terminal in ("Abusive Lead", "DNC Client : Don't Call Further"):
        result = _result(terminal)
        v = jev_judge.verdict_from(_response("Approved", 0.99, (0.9, 0.9, 0.9)), QUESTIONS)
        out = jev_judge.reconcile(
            result, v, QUESTIONS, judge_mode="enforce", disposition_map=DISPOSITION_MAP
        )
        assert out["call_outcome"] == terminal
        assert "corrected_from" not in out["jev"]


def test_low_confidence_asks_for_review_without_blocking():
    result = _result("Approved")
    v = jev_judge.verdict_from(_response("Approved", 0.21, (0.9, 0.9, 0.9)), QUESTIONS)
    out = jev_judge.reconcile(
        result, v, QUESTIONS, judge_mode="enforce", disposition_map=DISPOSITION_MAP
    )
    assert out["call_outcome"] == "Approved"
    assert out["jev"]["needs_review"] is True
    assert any("low outcome confidence" in r for r in out["jev"]["review_reasons"])


def test_agreement_and_full_answers_need_no_review():
    result = _result("Approved")
    v = jev_judge.verdict_from(_response("Approved", 0.93, (0.9, 0.9, 0.9)), QUESTIONS)
    out = jev_judge.reconcile(
        result, v, QUESTIONS, judge_mode="enforce", disposition_map=DISPOSITION_MAP
    )
    assert out["jev"]["needs_review"] is False
    assert out["jev"]["review_reasons"] == []


def test_stored_block_stays_small():
    v = jev_judge.verdict_from(_response("Approved", 0.9, (0.9, 0.5, 0.2)), QUESTIONS)
    doc = v.as_document(0.6)
    assert doc["answered_count"] == 1
    assert len(doc["top_outcomes"]) <= 3
    assert set(doc) == {"outcome", "confidence", "top_outcomes", "answered", "answered_count", "b2b"}
