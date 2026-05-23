import os
from pathlib import Path

from dotenv import load_dotenv

# Keep .env as the default config source, but allow one-off process launches to
# override values safely (for example a test worker with a different agent name).
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = os.getenv("VOICEBOT_PLATFORM_DB", "ai_voice_bot_management")
MONGO_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))

BOT_COLLECTION = os.getenv("VOICEBOT_BOT_COLLECTION", "tbl_ai_vb_bot_definitions")
BOT_VERSION_COLLECTION = os.getenv("VOICEBOT_VERSION_COLLECTION", "tbl_ai_vb_bot_versions")
BOT_TEMPLATE_COLLECTION = os.getenv("VOICEBOT_TEMPLATE_COLLECTION", "tbl_ai_vb_bot_templates")
CAMPAIGN_COLLECTION = os.getenv("VOICEBOT_CAMPAIGN_COLLECTION", "tbl_ai_vb_campaigns")
TRANSCRIPT_COLLECTION = os.getenv("MONGO_COLLECTION", "tbl_ai_vb_call_transcripts")
LEGACY_TRANSCRIPT_DB = os.getenv("VOICEBOT_LEGACY_TRANSCRIPT_DB", "")
LEGACY_TRANSCRIPT_COLLECTION = os.getenv("VOICEBOT_LEGACY_TRANSCRIPT_COLLECTION", "")
PLATFORM_SETTINGS_COLLECTION = os.getenv(
    "VOICEBOT_PLATFORM_SETTINGS_COLLECTION",
    "tbl_ai_vb_platform_settings",
)

DEFAULT_USER = os.getenv("VOICEBOT_DEFAULT_USER", "system")
VOICEBOT_ENV = os.getenv("VOICEBOT_ENV", "local")

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
