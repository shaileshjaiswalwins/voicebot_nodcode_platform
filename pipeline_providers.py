"""STT/TTS/LLM provider factory for bot_pipeline.py.

Reads a bot's config dict (the same `_bot_config` dict bot_pipeline.py already builds from
the admin API / room metadata) and returns the right livekit-agents plugin instance for
STT, TTS, and LLM.

CRITICAL: a bot config with no stt_provider/tts_provider/llm_provider set MUST reproduce
today's hardcoded stack exactly (Sarvam STT saaras:v3 hi-IN, Sarvam TTS bulbul:v3/simran/
hi-IN, Gemini gemini-3.1-flash-lite) — every existing bot has no provider fields set, so
this is a strict backward-compatibility requirement, not just a sensible default.
"""
import os

from loguru import logger

from livekit.plugins import deepgram, elevenlabs, google, openai, sarvam

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
        return sarvam.STT(
            language=bot_config.get("stt_language") or "hi-IN",
            model=bot_config.get("stt_model") or "saaras:v3",
            mode="transcribe",
            api_key=SARVAM_API_KEY or None,
            flush_signal=True,
        )
    if provider == "deepgram":
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
        return sarvam.TTS(
            target_language_code=bot_config.get("tts_language") or "hi-IN",
            model=bot_config.get("tts_model") or "bulbul:v3",
            speaker=bot_config.get("tts_voice") or "simran",
            api_key=SARVAM_API_KEY or None,
            # linear16 at 24000Hz: matches bot_pipeline.py's original tuning (avoids the
            # accumulating timing jitter that 22050Hz produced there).
            speech_sample_rate=24000,
            output_audio_codec="linear16",
            temperature=0.75,
            pace=1.0,
            send_completion_event=True,
        )
    if provider == "elevenlabs":
        return elevenlabs.TTS(
            voice_id=bot_config.get("tts_voice") or "l7kNoIfnJKPg7779LI2t",
            api_key=ELEVENLABS_API_KEY or None,
        )


def build_llm(bot_config: dict):
    provider = (bot_config.get("llm_provider") or DEFAULT_LLM_PROVIDER).lower()
    temperature = float(bot_config.get("temperature") or 0.4)
    if provider not in ("gemini", "openai"):
        logger.warning(f"[PIPELINE-PROVIDERS] Unknown llm_provider {provider!r}, falling back to {DEFAULT_LLM_PROVIDER!r}")
        provider = DEFAULT_LLM_PROVIDER
    if provider == "gemini":
        return google.LLM(
            model=bot_config.get("llm_model") or "gemini-3.1-flash-lite",
            api_key=GEMINI_API_KEY or None,
            temperature=temperature,
        )
    if provider == "openai":
        return openai.LLM(
            model=bot_config.get("llm_model") or "gpt-4.1",
            api_key=OPENAI_API_KEY or None,
            temperature=temperature,
        )
