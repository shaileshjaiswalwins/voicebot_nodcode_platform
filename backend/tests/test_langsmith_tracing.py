"""Tests for langsmith_tracing.py (untested — no coverage exists)."""
import os
from unittest.mock import patch

import langsmith_tracing as lt


class TestMergeConsecutiveSameRole:
    def test_collapses_consecutive_user_turns(self):
        transcript = [
            {"role": "user", "text": "hi"},
            {"role": "user", "text": "are you there"},
            {"role": "assistant", "text": "yes"},
        ]
        blocks = lt._merge_consecutive_same_role(transcript)
        assert len(blocks) == 2
        assert blocks[0]["text"] == "hi are you there"

    def test_buyer_agent_roles_mapped_to_user_assistant(self):
        transcript = [{"role": "buyer", "text": "hello"}, {"role": "agent", "text": "hi"}]
        blocks = lt._merge_consecutive_same_role(transcript)
        assert [b["_bucket"] for b in blocks] == ["user", "assistant"]

    def test_empty_text_entries_skipped(self):
        transcript = [{"role": "user", "text": ""}, {"role": "user", "text": "hi"}]
        blocks = lt._merge_consecutive_same_role(transcript)
        assert len(blocks) == 1

    def test_empty_transcript_returns_empty(self):
        assert lt._merge_consecutive_same_role([]) == []

    def test_none_transcript_returns_empty(self):
        assert lt._merge_consecutive_same_role(None) == []


class TestTurnsFromTranscript:
    def test_pairs_user_then_assistant(self):
        transcript = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]
        turns = lt._turns_from_transcript(transcript)
        assert turns == [{"user": "hi", "assistant": "hello"}]

    def test_trailing_unanswered_user_turn_kept(self):
        transcript = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"},
                      {"role": "user", "text": "you there?"}]
        turns = lt._turns_from_transcript(transcript)
        assert turns[-1] == {"user": "you there?", "assistant": None}

    def test_assistant_before_any_user_produces_none_user(self):
        transcript = [{"role": "assistant", "text": "welcome"}]
        turns = lt._turns_from_transcript(transcript)
        assert turns == [{"user": None, "assistant": "welcome"}]

    def test_double_user_turn_before_assistant_reply(self):
        transcript = [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "hello"},
            {"role": "user", "text": "q1"},
            {"role": "user", "text": "q2"},
        ]
        # q1/q2 collapse into one block before pairing, so only one trailing turn
        turns = lt._turns_from_transcript(transcript)
        assert turns[-1]["user"] == "q1 q2"


class TestGetClient:
    def setup_method(self):
        lt._client = None
        lt._client_init_attempted = False

    def test_disabled_when_env_flag_not_set(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
        assert lt._get_client() is None

    def test_cached_after_first_call(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        with patch("langsmith.Client") as MockClient:
            MockClient.return_value = "client-instance"
            first = lt._get_client()
            second = lt._get_client()
            assert first == second
            MockClient.assert_called_once()

    def test_client_init_exception_disables_tracing_not_raises(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "1")
        with patch("langsmith.Client", side_effect=RuntimeError("no api key")):
            assert lt._get_client() is None


class TestTraceCompletedCall:
    def setup_method(self):
        lt._client = None
        lt._client_init_attempted = False

    def test_noop_returns_none_when_tracing_disabled(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
        result = lt.trace_completed_call({"transcript": []}, "bot1")
        assert result is None

    def test_posting_failure_swallowed_returns_none(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        with patch("langsmith.Client"), patch("langsmith.RunTree", side_effect=RuntimeError("network down")):
            result = lt.trace_completed_call({"transcript": [], "call_start_time": 1, "call_end_time": 2}, "bot1")
            assert result is None

    # TODO: integration-style test with a fake RunTree that records create_child/post/end
    # calls, asserting tool_calls_by_turn windowing logic (lines 173-193) assigns tool
    # calls to the correct turn both when contained in a window and when nearest-fallback
    # is used. Needs a small in-memory RunTree fake since langsmith.Client isn't mockable
    # at this granularity without one.
