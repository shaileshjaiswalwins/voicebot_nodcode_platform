"""Unit tests for backend/evals.py's run_scenario() — direct, not through the router (which
mocks run_evals wholesale in test_evals.py). Mocks the Gemini client to control transcript
outcomes deterministically."""

from unittest.mock import MagicMock

from backend.evals import run_scenario
from backend.models import EvalScenario


def _mock_client(replies: list[str]) -> MagicMock:
    """Returns a fake genai.Client whose generate_content() yields `replies` in order,
    across both the caller-simulator and bot-simulator calls."""
    client = MagicMock()
    responses = iter(replies)

    def _generate_content(*, model, contents):
        resp = MagicMock()
        resp.text = next(responses)
        return resp

    client.models.generate_content.side_effect = _generate_content
    return client


def test_scenario_with_real_dialogue_and_no_forbidden_phrases_passes():
    client = _mock_client([
        "I'm not interested in this at all, please remove my number.",
        "Understood, I'll remove your number. Have a good day!",
        "[END]",
    ])
    scenario = EvalScenario(
        name="Not interested",
        caller_persona="You are not interested.",
        max_turns=3,
        must_contain=[],
        must_not_contain=["error", "undefined"],
    )
    result = run_scenario(client, "You are a helpful bot.", scenario)
    assert result["transcript"], "expected at least one turn to have been simulated"
    assert result["passed"] is True


def test_scenario_with_empty_transcript_does_not_pass():
    """Regression: an empty transcript (caller-simulator emits [END] on its very first turn,
    e.g. because its persona instructions primed it to 'end the conversation' immediately)
    previously counted as passed=True, since there were no missing/forbidden phrases to find
    against zero bot text — silently reporting a scenario that never actually ran as a clean
    pass."""
    client = _mock_client(["[END]"])
    scenario = EvalScenario(
        name="Not interested — should close politely",
        caller_persona="You are not interested. Say so clearly in your first reply and end the conversation.",
        max_turns=3,
        must_contain=[],
        must_not_contain=["error", "undefined"],
    )
    result = run_scenario(client, "You are a helpful bot.", scenario)
    assert result["transcript"] == []
    assert result["passed"] is False


def test_scenario_with_forbidden_phrase_fails():
    client = _mock_client([
        "Hi, what is this about?",
        "This is an error in our system, undefined behavior occurred.",
        "[END]",
    ])
    scenario = EvalScenario(
        name="Should not error",
        caller_persona="You ask a simple question.",
        max_turns=3,
        must_contain=[],
        must_not_contain=["error", "undefined"],
    )
    result = run_scenario(client, "You are a helpful bot.", scenario)
    assert result["passed"] is False
    assert "error" in result["forbidden_phrases_found"]
