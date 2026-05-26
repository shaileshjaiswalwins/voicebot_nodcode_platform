import os
from pathlib import Path

from dotenv import load_dotenv

# Keep .env as the default config source, but allow one-off process launches to
# override values safely (for example a test worker with a different agent name).
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

_MONGO_URI_FROM_ENV = "MONGO_URI" in os.environ
MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = os.getenv("VOICEBOT_PLATFORM_DB", "ai_voice_bot_management")
MONGO_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))

BOT_COLLECTION = os.getenv("VOICEBOT_BOT_COLLECTION", "tbl_ai_vb_bot_definitions")
BOT_VERSION_COLLECTION = os.getenv("VOICEBOT_VERSION_COLLECTION", "tbl_ai_vb_bot_versions")
BOT_TEMPLATE_COLLECTION = os.getenv("VOICEBOT_TEMPLATE_COLLECTION", "tbl_ai_vb_bot_templates")
CAMPAIGN_COLLECTION = os.getenv("VOICEBOT_CAMPAIGN_COLLECTION", "tbl_ai_vb_campaigns")
TRANSCRIPT_COLLECTION = os.getenv("MONGO_COLLECTION", "tbl_ai_vb_call_transcripts")
CALL_EVENT_COLLECTION = os.getenv("VOICEBOT_CALL_EVENT_COLLECTION", "tbl_ai_vb_call_events")
LEGACY_TRANSCRIPT_DB = os.getenv("VOICEBOT_LEGACY_TRANSCRIPT_DB", "")
LEGACY_TRANSCRIPT_COLLECTION = os.getenv("VOICEBOT_LEGACY_TRANSCRIPT_COLLECTION", "")
PLATFORM_SETTINGS_COLLECTION = os.getenv(
    "VOICEBOT_PLATFORM_SETTINGS_COLLECTION",
    "tbl_ai_vb_platform_settings",
)
TEST_RECORDING_DIR = Path(
    os.getenv("VOICEBOT_TEST_RECORDING_DIR", str(Path.home() / "Documents" / "voicebot_test_recordings"))
).expanduser()

DEFAULT_USER = os.getenv("VOICEBOT_DEFAULT_USER", "system")
VOICEBOT_ENV = os.getenv("VOICEBOT_ENV", "local")

_DASHBOARD_ORIGINS_RAW = os.getenv(
    "DASHBOARD_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
DASHBOARD_ORIGINS = [o.strip() for o in _DASHBOARD_ORIGINS_RAW.split(",") if o.strip()]

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_URL = os.getenv("LIVEKIT_API_URL") or LIVEKIT_URL
LIVEKIT_BROWSER_URL = os.getenv("LIVEKIT_BROWSER_URL") or LIVEKIT_URL
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "voice-bot-justdial")

LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST", "")
LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in {"", "replace-me", "changeme", "change-me", "secret", "devkey"}


def config_warnings() -> list[str]:
    warnings: list[str] = []
    if not _MONGO_URI_FROM_ENV:
        warnings.append("MONGO_URI is using the built-in default; set it explicitly per environment.")
    if VOICEBOT_ENV not in {"local", "staging", "prod"}:
        warnings.append("VOICEBOT_ENV should be one of local, staging, prod.")
    if "*" in DASHBOARD_ORIGINS:
        warnings.append("DASHBOARD_ORIGINS must not use '*' when credentialed CORS is enabled.")
    for name, value in {
        "LIVEKIT_URL": LIVEKIT_URL,
        "LIVEKIT_API_KEY": LIVEKIT_API_KEY,
        "LIVEKIT_API_SECRET": LIVEKIT_API_SECRET,
    }.items():
        if _is_placeholder(value):
            warnings.append(f"{name} is missing or still has a placeholder value.")
    if LANGFUSE_ENABLED:
        for name, value in {
            "LANGFUSE_PUBLIC_KEY": LANGFUSE_PUBLIC_KEY,
            "LANGFUSE_SECRET_KEY": LANGFUSE_SECRET_KEY,
            "LANGFUSE_BASE_URL": LANGFUSE_BASE_URL,
        }.items():
            if _is_placeholder(value):
                warnings.append(f"{name} is required when LANGFUSE_ENABLED=true.")
    return warnings


def validate_startup_config(strict: bool = False) -> list[str]:
    warnings = config_warnings()
    if strict and warnings:
        raise RuntimeError("Invalid voicebot platform configuration: " + "; ".join(warnings))
    return warnings
