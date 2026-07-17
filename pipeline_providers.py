"""STT/TTS/LLM provider factory for bot_pipeline.py.

Reads a bot's config dict (the same `_bot_config` dict bot_pipeline.py already builds from
the admin API / room metadata) and returns the right livekit-agents plugin instance for
STT, TTS, and LLM.

The full per-provider parameter surface is assembled by provider_params.py (a pure module),
so a PM can tune every relevant Sarvam STT / Sarvam TTS / Gemini knob via the bot config's
`stt_options` / `tts_options` / `llm_options` dicts. This module only attaches the API key
and instantiates the plugin.

CRITICAL: a bot config with no stt_provider/tts_provider/llm_provider (and no *_options) set
MUST reproduce today's hardcoded stack exactly (Sarvam STT saaras:v3 hi-IN, Sarvam TTS
bulbul:v3/simran/hi-IN 24000Hz linear16, Gemini gemini-3.1-flash-lite) — every existing bot
has no provider fields set, so this is a strict backward-compatibility requirement.

Plugin packages are imported lazily inside each branch so this module loads even in
environments where only the Sarvam + Google plugins are installed.
"""
import os

from loguru import logger

from provider_params import (
    build_gemini_llm_kwargs,
    build_sarvam_stt_kwargs,
    build_sarvam_tts_kwargs,
)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DEFAULT_STT_PROVIDER = "sarvam"
DEFAULT_TTS_PROVIDER = "sarvam"
DEFAULT_LLM_PROVIDER = "gemini"


def build_stt(bot_config: dict):
    provider = (bot_config.get("stt_provider") or DEFAULT_STT_PROVIDER).lower()
    if provider not in ("sarvam", "deepgram"):
        logger.warning(f"[PIPELINE-PROVIDERS] Unknown stt_provider {provider!r}, falling back to {DEFAULT_STT_PROVIDER!r}")
        provider = DEFAULT_STT_PROVIDER
    if provider == "sarvam":
        from livekit.plugins import sarvam

        kwargs = build_sarvam_stt_kwargs(bot_config)
        logger.info(f"[PIPELINE-PROVIDERS] Sarvam STT kwargs: {kwargs}")
        return sarvam.STT(api_key=SARVAM_API_KEY or None, **kwargs)
    if provider == "deepgram":
        from livekit.plugins import deepgram

        return deepgram.STT(
            model=bot_config.get("stt_model") or "nova-3",
            language=bot_config.get("stt_language") or "en-US",
            api_key=DEEPGRAM_API_KEY or None,
        )


def build_tts(bot_config: dict):
    provider = (bot_config.get("tts_provider") or DEFAULT_TTS_PROVIDER).lower()
    if provider not in ("sarvam", "elevenlabs"):
        logger.warning(f"[PIPELINE-PROVIDERS] Unknown tts_provider {provider!r}, falling back to {DEFAULT_TTS_PROVIDER!r}")
        provider = DEFAULT_TTS_PROVIDER
    if provider == "sarvam":
        from livekit.plugins import sarvam

        kwargs = build_sarvam_tts_kwargs(bot_config)
        logger.info(f"[PIPELINE-PROVIDERS] Sarvam TTS kwargs: {kwargs}")
        return sarvam.TTS(api_key=SARVAM_API_KEY or None, **kwargs)
    if provider == "elevenlabs":
        from livekit.plugins import elevenlabs

        return elevenlabs.TTS(
            voice_id=bot_config.get("tts_voice") or "l7kNoIfnJKPg7779LI2t",
            api_key=ELEVENLABS_API_KEY or None,
        )


def build_llm(bot_config: dict):
    provider = (bot_config.get("llm_provider") or DEFAULT_LLM_PROVIDER).lower()
    if provider not in ("gemini", "openai"):
        logger.warning(f"[PIPELINE-PROVIDERS] Unknown llm_provider {provider!r}, falling back to {DEFAULT_LLM_PROVIDER!r}")
        provider = DEFAULT_LLM_PROVIDER
    if provider == "gemini":
        from livekit.plugins import google

        kwargs = build_gemini_llm_kwargs(bot_config)
        logger.info(f"[PIPELINE-PROVIDERS] Gemini LLM kwargs: {kwargs}")
        return google.LLM(api_key=GEMINI_API_KEY or None, **kwargs)
    if provider == "openai":
        from livekit.plugins import openai

        return openai.LLM(
            model=bot_config.get("llm_model") or "gpt-4.1",
            api_key=OPENAI_API_KEY or None,
            temperature=float(bot_config.get("temperature") or 0.4),
        )
