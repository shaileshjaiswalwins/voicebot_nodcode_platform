"""Pure assembly of the keyword arguments passed to the Sarvam STT, Sarvam TTS and Gemini
LLM plugin constructors (Plan 02, Phase 8 — dynamic provider params).

No livekit imports: this module only turns a bot's config dict into a plain kwargs dict, so
it is unit-testable without the plugin packages installed and can be shared by
pipeline_providers.py / bot.py / bot_dev.py.

STRICT BACK-COMPAT: an empty config must reproduce today's exact hardcoded stack
(Sarvam STT saaras:v3 hi-IN; Sarvam TTS bulbul:v3 / simran / hi-IN, 24000Hz linear16,
temperature 0.75, pace 1.0; Gemini gemini-3.1-flash-lite temperature 0.4). Every existing
bot has no provider options set, so the defaults below are a compatibility contract.

Each provider's params are described by a spec of `kwarg -> ParamSpec(kind, default,
legacy_key)`:
  - `kind`: how to coerce a value from the UI/config ("str", "int", "float", "bool", "raw").
  - `default`: value always sent when nothing is configured. `_UNSET` means "omit unless
    the config provides it" (so the plugin applies its own default).
  - `legacy_key`: an older flat config key (e.g. tts_voice) that maps to this kwarg.

Precedence, highest last: spec default < legacy flat field < `<provider>_options` dict.
Unknown keys in the options dict are dropped so a typo can never crash a constructor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

_UNSET = object()


@dataclass(frozen=True)
class ParamSpec:
    kind: str = "str"          # str | int | float | bool | raw
    default: Any = _UNSET
    legacy_key: str | None = None


def _coerce(value: Any, kind: str) -> Any:
    if kind == "raw":
        return value
    if kind == "int":
        return int(float(value)) if not isinstance(value, bool) else int(value)
    if kind == "float":
        return float(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    # str
    return str(value)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _assemble(config: dict, options_key: str, spec: dict[str, ParamSpec]) -> dict[str, Any]:
    options = config.get(options_key) or {}
    if not isinstance(options, dict):
        options = {}
    out: dict[str, Any] = {}
    for kwarg, ps in spec.items():
        raw = _UNSET
        if ps.default is not _UNSET:
            raw = ps.default
        if ps.legacy_key is not None:
            legacy = config.get(ps.legacy_key)
            if not _is_blank(legacy):
                raw = legacy
        if kwarg in options and not _is_blank(options[kwarg]):
            raw = options[kwarg]
        if raw is _UNSET:
            continue
        try:
            out[kwarg] = _coerce(raw, ps.kind)
        except (TypeError, ValueError):
            # A bad value from the UI must never crash the pipeline — skip it. If the spec
            # had a hard default, fall back to that so behavior stays defined.
            logger.warning(f"[PROVIDER-PARAMS] {options_key}.{kwarg}={raw!r} invalid for kind={ps.kind}; skipping")
            if ps.default is not _UNSET:
                try:
                    out[kwarg] = _coerce(ps.default, ps.kind)
                except (TypeError, ValueError):
                    pass
    return out


# ── Sarvam STT ───────────────────────────────────────────────────────────────
SARVAM_STT_SPEC: dict[str, ParamSpec] = {
    "language": ParamSpec("str", "hi-IN", legacy_key="stt_language"),
    "model": ParamSpec("str", "saaras:v3", legacy_key="stt_model"),
    "mode": ParamSpec("str", "transcribe"),
    "flush_signal": ParamSpec("bool", True),
    "sample_rate": ParamSpec("int"),
    "high_vad_sensitivity": ParamSpec("bool"),
    "input_audio_codec": ParamSpec("str"),
    # Fine-grained VAD (saaras:v3 only) — optional, unset by default.
    "positive_speech_threshold": ParamSpec("float"),
    "negative_speech_threshold": ParamSpec("float"),
    "min_speech_frames": ParamSpec("int"),
    "first_turn_min_speech_frames": ParamSpec("int"),
    "negative_frames_count": ParamSpec("int"),
    "negative_frames_window": ParamSpec("int"),
    "start_speech_volume_threshold": ParamSpec("float"),
    "interrupt_min_speech_frames": ParamSpec("int"),
    "pre_speech_pad_frames": ParamSpec("int"),
    "num_initial_ignored_frames": ParamSpec("int"),
}

# ── Sarvam TTS ───────────────────────────────────────────────────────────────
SARVAM_TTS_SPEC: dict[str, ParamSpec] = {
    "target_language_code": ParamSpec("str", "hi-IN", legacy_key="tts_language"),
    "model": ParamSpec("str", "bulbul:v3", legacy_key="tts_model"),
    "speaker": ParamSpec("str", "simran", legacy_key="tts_voice"),
    # Behavior-preserving audio defaults from the original bot_pipeline.py tuning.
    "speech_sample_rate": ParamSpec("int", 24000),
    "output_audio_codec": ParamSpec("str", "linear16"),
    "temperature": ParamSpec("float", 0.75),
    "pace": ParamSpec("float", 1.0),
    "send_completion_event": ParamSpec("bool", True),
    # Optional style / format params — unset by default.
    "pitch": ParamSpec("float"),
    "loudness": ParamSpec("float"),
    "num_channels": ParamSpec("int"),
    "min_buffer_size": ParamSpec("int"),
    "max_chunk_length": ParamSpec("int"),
    "output_audio_bitrate": ParamSpec("str"),
    "enable_preprocessing": ParamSpec("bool"),
    "enable_cached_responses": ParamSpec("bool"),
    "dict_id": ParamSpec("str"),
}

# ── Gemini LLM ───────────────────────────────────────────────────────────────
GEMINI_LLM_SPEC: dict[str, ParamSpec] = {
    "model": ParamSpec("str", "gemini-3.1-flash-lite", legacy_key="llm_model"),
    "temperature": ParamSpec("float", 0.4, legacy_key="temperature"),
    "top_p": ParamSpec("float"),
    "top_k": ParamSpec("int"),
    "presence_penalty": ParamSpec("float"),
    "frequency_penalty": ParamSpec("float"),
    "max_output_tokens": ParamSpec("int"),
    "seed": ParamSpec("int"),
    "thinking_config": ParamSpec("raw"),
}

# ── IndicF5 (our own in-house TTS, livekit_indic5_tts.py) ───────────────────
INDIC5_TTS_SPEC: dict[str, ParamSpec] = {
    "speaker": ParamSpec("str", "simran", legacy_key="tts_voice"),
    "sample_rate": ParamSpec("int", 24000),
    "nfe_step": ParamSpec("int", 16),
    "style": ParamSpec("str", "auto"),
    "transliterate": ParamSpec("bool", True),
    "speed": ParamSpec("float", 1.0),
    "fallback_speaker": ParamSpec("str", "simran"),
}


def build_sarvam_stt_kwargs(config: dict) -> dict[str, Any]:
    return _assemble(config, "stt_options", SARVAM_STT_SPEC)


def build_sarvam_tts_kwargs(config: dict) -> dict[str, Any]:
    return _assemble(config, "tts_options", SARVAM_TTS_SPEC)


def build_gemini_llm_kwargs(config: dict) -> dict[str, Any]:
    return _assemble(config, "llm_options", GEMINI_LLM_SPEC)


def build_indic5_tts_kwargs(config: dict) -> dict[str, Any]:
    return _assemble(config, "tts_options", INDIC5_TTS_SPEC)
