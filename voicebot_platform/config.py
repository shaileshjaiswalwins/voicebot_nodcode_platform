import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://192.168.13.65:27017")
MONGO_DB = os.getenv("VOICEBOT_PLATFORM_DB", "ai_voice_bot_management")

BOT_COLLECTION = os.getenv("VOICEBOT_BOT_COLLECTION", "tbl_ai_vb_bot_definitions")
BOT_VERSION_COLLECTION = os.getenv("VOICEBOT_VERSION_COLLECTION", "tbl_ai_vb_bot_versions")
BOT_TEMPLATE_COLLECTION = os.getenv("VOICEBOT_TEMPLATE_COLLECTION", "tbl_ai_vb_bot_templates")
CAMPAIGN_COLLECTION = os.getenv("VOICEBOT_CAMPAIGN_COLLECTION", "tbl_ai_vb_campaigns")
TRANSCRIPT_COLLECTION = os.getenv("MONGO_COLLECTION", "tbl_ai_vb_call_transcripts")

DEFAULT_USER = os.getenv("VOICEBOT_DEFAULT_USER", "system")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "voice-bot-justdial")
