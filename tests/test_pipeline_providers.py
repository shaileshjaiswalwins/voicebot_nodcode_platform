"""Tests for pipeline_providers.py — the STT/TTS/LLM factory used by bot_pipeline.py.

Critical requirement: bots with no provider fields set in their config MUST get exactly
today's hardcoded stack (Sarvam STT saaras:v3, Sarvam TTS bulbul:v3/simran/hi-IN,
Gemini gemini-3.1-flash-lite) with zero behavior change — this is a strict
backward-compatibility requirement since every existing bot has no provider fields set.

Uses mocked plugin constructors rather than introspecting each plugin's private internal
attributes (which differ in shape across sarvam/deepgram/elevenlabs/google/openai) — this
proves the factory calls the right class with the right kwargs, which is what matters.
"""
import contextlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import pipeline_providers as pp


@contextlib.contextmanager
def patch_plugin(provider: str, cls_name: str):
    """Patch livekit.plugins.<provider>.<cls_name> with a MagicMock.

    pipeline_providers imports plugin packages lazily inside each branch. For plugins not
    installed in this environment (deepgram/elevenlabs/openai), inject a stub module so the
    lazy `from livekit.plugins import <provider>` resolves to our mock."""
    import livekit.plugins as lkp

    full = f"livekit.plugins.{provider}"
    created = False
    mod = sys.modules.get(full)
    if mod is None and not hasattr(lkp, provider):
        mod = types.ModuleType(full)
        sys.modules[full] = mod
        setattr(lkp, provider, mod)
        created = True
    elif mod is None:
        mod = getattr(lkp, provider)
    mock = MagicMock()
    had_attr = hasattr(mod, cls_name)
    prev = getattr(mod, cls_name, None)
    setattr(mod, cls_name, mock)
    try:
        yield mock
    finally:
        if had_attr:
            setattr(mod, cls_name, prev)
        else:
            with contextlib.suppress(AttributeError):
                delattr(mod, cls_name)
        if created:
            sys.modules.pop(full, None)
            with contextlib.suppress(AttributeError):
                delattr(lkp, provider)


@pytest.fixture(autouse=True)
def _dummy_provider_api_keys(monkeypatch):
    monkeypatch.setattr(pp, "SARVAM_API_KEY", "sarvam-key")
    monkeypatch.setattr(pp, "DEEPGRAM_API_KEY", "deepgram-key")
    monkeypatch.setattr(pp, "ELEVENLABS_API_KEY", "elevenlabs-key")
    monkeypatch.setattr(pp, "OPENAI_API_KEY", "openai-key")
    monkeypatch.setattr(pp, "GEMINI_API_KEY", "gemini-key")


class TestBackwardCompatibilityDefaults:
    """A bot config with no provider fields must reproduce today's exact hardcoded stack."""

    def test_default_stt_is_sarvam_saaras_v3_hindi_transcribe(self):
        with patch_plugin("sarvam", "STT") as mock_stt:
            pp.build_stt({})
        mock_stt.assert_called_once_with(
            language="hi-IN", model="saaras:v3", mode="transcribe",
            api_key="sarvam-key", flush_signal=True,
        )

    def test_default_tts_is_sarvam_bulbul_v3_simran_hindi(self):
        with patch_plugin("sarvam", "TTS") as mock_tts:
            pp.build_tts({})
        mock_tts.assert_called_once_with(
            target_language_code="hi-IN", model="bulbul:v3", speaker="simran",
            api_key="sarvam-key", speech_sample_rate=24000,
            output_audio_codec="linear16", temperature=0.75, pace=1.0,
            send_completion_event=True,
        )

    def test_default_llm_is_gemini_flash_lite(self):
        with patch_plugin("google", "LLM") as mock_llm:
            pp.build_llm({})
        mock_llm.assert_called_once_with(model="gemini-3.1-flash-lite", api_key="gemini-key", temperature=0.4)

    def test_llm_temperature_read_from_config(self):
        with patch_plugin("google", "LLM") as mock_llm:
            pp.build_llm({"temperature": 0.9})
        mock_llm.assert_called_once_with(model="gemini-3.1-flash-lite", api_key="gemini-key", temperature=0.9)


class TestProviderSelection:
    def test_stt_provider_deepgram_selected(self):
        with patch_plugin("deepgram", "STT") as mock_stt:
            result = pp.build_stt({"stt_provider": "deepgram"})
        mock_stt.assert_called_once()
        assert result is mock_stt.return_value

    def test_stt_provider_explicit_sarvam_still_works(self):
        with patch_plugin("sarvam", "STT") as mock_stt:
            pp.build_stt({"stt_provider": "sarvam"})
        mock_stt.assert_called_once()

    def test_stt_provider_unknown_falls_back_to_default(self):
        with patch_plugin("sarvam", "STT") as mock_stt:
            pp.build_stt({"stt_provider": "not-a-real-provider"})
        mock_stt.assert_called_once()

    def test_tts_provider_elevenlabs_selected(self):
        with patch_plugin("elevenlabs", "TTS") as mock_tts:
            result = pp.build_tts({"tts_provider": "elevenlabs"})
        mock_tts.assert_called_once()
        assert result is mock_tts.return_value

    def test_tts_provider_unknown_falls_back_to_default(self):
        with patch_plugin("sarvam", "TTS") as mock_tts:
            pp.build_tts({"tts_provider": "not-a-real-provider"})
        mock_tts.assert_called_once()

    def test_llm_provider_openai_selected(self):
        with patch_plugin("openai", "LLM") as mock_llm:
            result = pp.build_llm({"llm_provider": "openai"})
        mock_llm.assert_called_once()
        assert result is mock_llm.return_value

    def test_llm_provider_unknown_falls_back_to_default(self):
        with patch_plugin("google", "LLM") as mock_llm:
            pp.build_llm({"llm_provider": "not-a-real-provider"})
        mock_llm.assert_called_once()


class TestProviderSpecificSettings:
    def test_deepgram_reads_model_and_language_from_config(self):
        with patch_plugin("deepgram", "STT") as mock_stt:
            pp.build_stt({"stt_provider": "deepgram", "stt_model": "nova-2", "stt_language": "hi"})
        _, kwargs = mock_stt.call_args
        assert kwargs["model"] == "nova-2"
        assert kwargs["language"] == "hi"
        assert kwargs["api_key"] == "deepgram-key"

    def test_elevenlabs_reads_voice_from_config(self):
        with patch_plugin("elevenlabs", "TTS") as mock_tts:
            pp.build_tts({"tts_provider": "elevenlabs", "tts_voice": "some-voice-id"})
        _, kwargs = mock_tts.call_args
        assert kwargs["voice_id"] == "some-voice-id"
        assert kwargs["api_key"] == "elevenlabs-key"

    def test_sarvam_reads_voice_and_language_from_config(self):
        with patch_plugin("sarvam", "TTS") as mock_tts:
            pp.build_tts({"tts_provider": "sarvam", "tts_voice": "shubh", "tts_language": "ta-IN"})
        _, kwargs = mock_tts.call_args
        assert kwargs["speaker"] == "shubh"
        assert kwargs["target_language_code"] == "ta-IN"

    def test_openai_reads_model_from_config(self):
        with patch_plugin("openai", "LLM") as mock_llm:
            pp.build_llm({"llm_provider": "openai", "llm_model": "gpt-4o-mini"})
        _, kwargs = mock_llm.call_args
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["api_key"] == "openai-key"


class TestDynamicProviderOptions:
    """The full Sarvam/Gemini parameter surface is configurable via *_options dicts."""

    def test_sarvam_stt_vad_options_reach_constructor(self):
        with patch_plugin("sarvam", "STT") as mock_stt:
            pp.build_stt({"stt_options": {"mode": "codemix", "high_vad_sensitivity": True, "sample_rate": 8000}})
        _, kwargs = mock_stt.call_args
        assert kwargs["mode"] == "codemix"
        assert kwargs["high_vad_sensitivity"] is True
        assert kwargs["sample_rate"] == 8000

    def test_sarvam_tts_style_options_reach_constructor(self):
        with patch_plugin("sarvam", "TTS") as mock_tts:
            pp.build_tts({"tts_options": {"pace": 1.3, "pitch": -0.2, "loudness": 1.4, "speaker": "priya"}})
        _, kwargs = mock_tts.call_args
        assert kwargs["pace"] == 1.3
        assert kwargs["pitch"] == -0.2
        assert kwargs["loudness"] == 1.4
        assert kwargs["speaker"] == "priya"

    def test_gemini_options_reach_constructor(self):
        with patch_plugin("google", "LLM") as mock_llm:
            pp.build_llm({"llm_options": {"top_p": 0.9, "max_output_tokens": 512, "thinking_config": {"thinking_level": "low"}}})
        _, kwargs = mock_llm.call_args
        assert kwargs["top_p"] == 0.9
        assert kwargs["max_output_tokens"] == 512
        assert kwargs["thinking_config"] == {"thinking_level": "low"}
