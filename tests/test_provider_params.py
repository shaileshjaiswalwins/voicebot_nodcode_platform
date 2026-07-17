"""Tests for provider_params.py — pure assembly of the kwargs passed to the Sarvam STT,
Sarvam TTS and Gemini LLM plugin constructors (no livekit imports).

Strict back-compat: an empty config MUST reproduce today's hardcoded stack exactly, since
every existing bot has no provider options set."""

from provider_params import (
    build_sarvam_stt_kwargs,
    build_sarvam_tts_kwargs,
    build_gemini_llm_kwargs,
)


# ── Sarvam STT ───────────────────────────────────────────────────────────────

def test_stt_defaults_match_current_hardcoded_stack():
    kw = build_sarvam_stt_kwargs({})
    assert kw["language"] == "hi-IN"
    assert kw["model"] == "saaras:v3"
    assert kw["mode"] == "transcribe"
    assert kw["flush_signal"] is True
    # optional VAD params stay unset so the plugin uses its own defaults
    assert "sample_rate" not in kw
    assert "positive_speech_threshold" not in kw


def test_stt_legacy_flat_fields_still_honored():
    kw = build_sarvam_stt_kwargs({"stt_model": "saarika:v2.5", "stt_language": "en-IN"})
    assert kw["model"] == "saarika:v2.5"
    assert kw["language"] == "en-IN"


def test_stt_options_pass_through_and_coerce_types():
    kw = build_sarvam_stt_kwargs({"stt_options": {
        "mode": "codemix",
        "high_vad_sensitivity": True,
        "sample_rate": "8000",                 # string from UI -> int
        "positive_speech_threshold": "0.6",    # string -> float
        "min_speech_frames": "3",
    }})
    assert kw["mode"] == "codemix"
    assert kw["high_vad_sensitivity"] is True
    assert kw["sample_rate"] == 8000 and isinstance(kw["sample_rate"], int)
    assert kw["positive_speech_threshold"] == 0.6 and isinstance(kw["positive_speech_threshold"], float)
    assert kw["min_speech_frames"] == 3


def test_stt_options_override_legacy_and_unknown_keys_dropped():
    kw = build_sarvam_stt_kwargs({
        "stt_language": "en-IN",
        "stt_options": {"language": "ta-IN", "bogus_param": "x"},
    })
    assert kw["language"] == "ta-IN"      # options win over legacy flat
    assert "bogus_param" not in kw        # unknown keys never reach the constructor


def test_stt_blank_values_are_ignored():
    kw = build_sarvam_stt_kwargs({"stt_model": "", "stt_options": {"mode": ""}})
    assert kw["model"] == "saaras:v3"     # blank -> default
    assert kw["mode"] == "transcribe"


# ── Sarvam TTS ───────────────────────────────────────────────────────────────

def test_tts_defaults_match_current_hardcoded_stack():
    kw = build_sarvam_tts_kwargs({})
    assert kw["target_language_code"] == "hi-IN"
    assert kw["model"] == "bulbul:v3"
    assert kw["speaker"] == "simran"
    assert kw["speech_sample_rate"] == 24000
    assert kw["output_audio_codec"] == "linear16"
    assert kw["temperature"] == 0.75
    assert kw["pace"] == 1.0
    assert kw["send_completion_event"] is True
    # optional style params unset by default
    assert "pitch" not in kw
    assert "loudness" not in kw


def test_tts_legacy_voice_and_language_map_to_speaker_and_target_language():
    kw = build_sarvam_tts_kwargs({"tts_voice": "tarun", "tts_language": "en-IN", "tts_model": "bulbul:v2"})
    assert kw["speaker"] == "tarun"
    assert kw["target_language_code"] == "en-IN"
    assert kw["model"] == "bulbul:v2"


def test_tts_options_pass_through_with_ranges_and_coercion():
    kw = build_sarvam_tts_kwargs({"tts_options": {
        "pace": "1.2", "pitch": "-0.25", "loudness": "1.5",
        "min_buffer_size": "60", "max_chunk_length": "200",
        "output_audio_bitrate": "192k", "enable_preprocessing": True,
        "speaker": "priya",
    }})
    assert kw["pace"] == 1.2
    assert kw["pitch"] == -0.25
    assert kw["loudness"] == 1.5
    assert kw["min_buffer_size"] == 60
    assert kw["max_chunk_length"] == 200
    assert kw["output_audio_bitrate"] == "192k"
    assert kw["enable_preprocessing"] is True
    assert kw["speaker"] == "priya"


def test_tts_options_can_override_default_sample_rate_and_codec():
    kw = build_sarvam_tts_kwargs({"tts_options": {"speech_sample_rate": "8000", "output_audio_codec": "mulaw"}})
    assert kw["speech_sample_rate"] == 8000
    assert kw["output_audio_codec"] == "mulaw"


# ── Gemini LLM ───────────────────────────────────────────────────────────────

def test_llm_defaults_match_current_hardcoded_stack():
    kw = build_gemini_llm_kwargs({})
    assert kw["model"] == "gemini-3.1-flash-lite"
    assert kw["temperature"] == 0.4
    assert "top_p" not in kw


def test_llm_legacy_flat_temperature_and_model():
    kw = build_gemini_llm_kwargs({"llm_model": "gemini-3-flash-preview", "temperature": 0.85})
    assert kw["model"] == "gemini-3-flash-preview"
    assert kw["temperature"] == 0.85


def test_llm_options_pass_through_including_thinking_config_dict():
    kw = build_gemini_llm_kwargs({"llm_options": {
        "top_p": "0.9", "top_k": "40", "presence_penalty": "0.5",
        "frequency_penalty": "-0.2", "max_output_tokens": "1000", "seed": "7",
        "thinking_config": {"thinking_level": "medium"},
    }})
    assert kw["top_p"] == 0.9
    assert kw["top_k"] == 40
    assert kw["presence_penalty"] == 0.5
    assert kw["frequency_penalty"] == -0.2
    assert kw["max_output_tokens"] == 1000
    assert kw["seed"] == 7
    assert kw["thinking_config"] == {"thinking_level": "medium"}


def test_llm_options_override_legacy_temperature():
    kw = build_gemini_llm_kwargs({"temperature": 0.4, "llm_options": {"temperature": "1.1"}})
    assert kw["temperature"] == 1.1


def test_invalid_numeric_option_is_skipped_not_crashing():
    kw = build_sarvam_tts_kwargs({"tts_options": {"pace": "not-a-number"}})
    assert kw["pace"] == 1.0  # falls back to default, no exception
