"""Tests for call_metrics.CallMetricsCollector (untested — no coverage exists)."""
import sys
import types

import pytest

sys.path.insert(0, "..")  # repo root import if run standalone; adjust per conftest
from call_metrics import CallMetricsCollector


def _ev(**kwargs):
    ns = types.SimpleNamespace(**kwargs)
    return types.SimpleNamespace(metrics=ns)


class TestOnMetricsCollected:
    def test_stt_metrics_stashed_and_folded_into_next_bucket(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="stt_metrics", duration=0.5, audio_duration=1.0, timestamp=100))
        assert c.as_list() == []  # no bucket yet, STT has no speech_id
        c.on_metrics_collected(_ev(type="eou_metrics", speech_id="s1", end_of_utterance_delay=0.2,
                                    transcription_delay=0.1, timestamp=101))
        bucket = c.as_list()[0]
        assert bucket["stt_duration_ms"] == 500
        assert bucket["eou_delay_ms"] == 200

    def test_metrics_missing_speech_id_dropped_not_raised(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="llm_metrics", speech_id=None, ttft=0.1))
        assert c.as_list() == []

    def test_llm_metrics_records_tokens_and_timing(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="llm_metrics", speech_id="s1", ttft=0.3, duration=1.2,
                                    prompt_tokens=50, completion_tokens=20, timestamp=200))
        bucket = c.as_list()[0]
        assert bucket["llm_ttft_ms"] == 300
        assert bucket["llm_prompt_tokens"] == 50

    def test_tts_metrics_records_ttfb_and_duration(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="tts_metrics", speech_id="s1", ttfb=0.05, duration=0.8, timestamp=300))
        bucket = c.as_list()[0]
        assert bucket["tts_ttfb_ms"] == 50

    def test_unknown_metric_type_creates_bucket_but_sets_no_fields(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="unknown_metrics", speech_id="s1"))
        assert c.as_list() == [{"speech_id": "s1"}]

    def test_multiple_turns_preserve_order(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="llm_metrics", speech_id="s1", ttft=0.1, duration=0.5,
                                    prompt_tokens=1, completion_tokens=1, timestamp=1))
        c.on_metrics_collected(_ev(type="llm_metrics", speech_id="s2", ttft=0.1, duration=0.5,
                                    prompt_tokens=1, completion_tokens=1, timestamp=2))
        ids = [b["speech_id"] for b in c.as_list()]
        assert ids == ["s1", "s2"]

    def test_malformed_event_never_raises(self):
        c = CallMetricsCollector()
        # ev with no .metrics and no .type at all
        c.on_metrics_collected(object())
        assert c.as_list() == []

    def test_total_ms_sums_expected_components(self):
        c = CallMetricsCollector()
        c.on_metrics_collected(_ev(type="stt_metrics", duration=0.1, audio_duration=0.5, timestamp=1))
        c.on_metrics_collected(_ev(type="llm_metrics", speech_id="s1", ttft=0.2, duration=0.3,
                                    prompt_tokens=1, completion_tokens=1, timestamp=2))
        c.on_metrics_collected(_ev(type="tts_metrics", speech_id="s1", ttfb=0.05, duration=0.4, timestamp=3))
        bucket = c.as_list()[0]
        assert bucket["total_ms"] == 100 + 200 + 300 + 50  # stt_duration + llm_ttft + llm_duration + tts_ttfb


class TestOnFunctionToolsExecuted:
    def _tool_ev(self, calls):
        return types.SimpleNamespace(zipped=lambda: calls)

    def test_records_parsed_json_arguments(self):
        c = CallMetricsCollector()
        call = types.SimpleNamespace(name="lookup", arguments='{"a": 1}')
        output = types.SimpleNamespace(output="result", is_error=False)
        c.on_function_tools_executed(self._tool_ev([(call, output)]))
        tc = c.tool_calls_as_list()[0]
        assert tc["arguments"] == {"a": 1}
        assert tc["output"] == "result"

    def test_malformed_json_arguments_kept_as_raw_string(self):
        c = CallMetricsCollector()
        call = types.SimpleNamespace(name="lookup", arguments="not json")
        output = types.SimpleNamespace(output="result", is_error=False)
        c.on_function_tools_executed(self._tool_ev([(call, output)]))
        tc = c.tool_calls_as_list()[0]
        assert tc["arguments"] == "not json"

    def test_none_output_handled(self):
        c = CallMetricsCollector()
        call = types.SimpleNamespace(name="lookup", arguments=None)
        c.on_function_tools_executed(self._tool_ev([(call, None)]))
        tc = c.tool_calls_as_list()[0]
        assert tc["output"] is None
        assert tc["is_error"] is None

    def test_zipped_raising_is_swallowed(self):
        c = CallMetricsCollector()
        bad_ev = types.SimpleNamespace(zipped=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        c.on_function_tools_executed(bad_ev)  # must not raise
        assert c.tool_calls_as_list() == []


class TestOnSessionError:
    def test_records_recoverable_flag_and_message(self):
        c = CallMetricsCollector()
        err = types.SimpleNamespace(type="stt_error", error="disconnected", recoverable=True)
        c.on_session_error(err)
        e = c.errors_as_list()[0]
        assert e["recoverable"] is True
        assert e["message"] == "disconnected"

    def test_malformed_error_object_never_raises(self):
        c = CallMetricsCollector()
        c.on_session_error(object())  # getattr fallbacks should hold
        assert len(c.errors_as_list()) == 1
