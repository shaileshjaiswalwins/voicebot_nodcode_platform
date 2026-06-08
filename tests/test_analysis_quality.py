from __future__ import annotations

import pytest

from callback_worker.analysis import generate_call_analysis


class _NoopSession:
    async def post(self, *_, **__):
        raise AssertionError("pre-LLM guard should return before HTTP")


@pytest.mark.asyncio
async def test_short_hello_only_is_not_interested():
    result = await generate_call_analysis(
        [{"role": "recording", "text": "hello hello"}],
        "disconnected",
        {},
        _NoopSession(),
        duration_secs=8,
        transcript_source="recording_verified",
    )

    assert result["call_outcome"] == "Short Hangup"
    assert result["analysis_transcript_source"] == "recording_verified"
    assert result["confidence"] >= 0.8


@pytest.mark.asyncio
async def test_zero_user_signal_is_short_hangup_with_metadata():
    result = await generate_call_analysis(
        [{"role": "assistant", "text": "Hello, did you search?"}],
        "disconnected",
        {},
        _NoopSession(),
        duration_secs=6,
        transcript_source="gemini_live",
    )

    assert result["call_outcome"] == "Short Hangup"
    assert "zero_user_signal" in result["disqualifiers"]
    assert result["needs_review"] is False
