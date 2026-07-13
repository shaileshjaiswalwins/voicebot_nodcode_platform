import os

from fastapi import APIRouter, Depends

from ..audit import log_audit
from ..auth import require_user
from ..db import db
from ..models import LangfuseSettingsPayload, RuntimeSettingsPayload

router = APIRouter(prefix="/api/settings", tags=["settings"])

_runtime = db["tbl_ai_vb_runtime_settings"]
_langfuse = db["tbl_ai_vb_langfuse_settings"]
_DOC_ID = "singleton"


@router.get("/runtime")
def get_runtime_settings(_: dict = Depends(require_user)) -> dict:
    doc = _runtime.find_one({"_id": _DOC_ID}) or {}
    return {
        "_id": _DOC_ID,
        "livekit_api_url": doc.get("livekit_api_url", os.getenv("LIVEKIT_API_URL", "")),
        "livekit_browser_url": doc.get("livekit_browser_url", os.getenv("LIVEKIT_BROWSER_URL", "")),
        "livekit_agent_name": doc.get("livekit_agent_name", os.getenv("LIVEKIT_AGENT_NAME", "")),
        "livekit_credentials_configured": bool(os.getenv("LIVEKIT_API_KEY") and os.getenv("LIVEKIT_API_SECRET")),
    }


@router.put("/runtime")
def update_runtime_settings(payload: RuntimeSettingsPayload, user: dict = Depends(require_user)) -> dict:
    _runtime.update_one({"_id": _DOC_ID}, {"$set": payload.model_dump()}, upsert=True)
    log_audit(user, "update", "runtime_settings", _DOC_ID)
    return get_runtime_settings(user)


@router.get("/langfuse")
def get_langfuse_settings(_: dict = Depends(require_user)) -> dict:
    doc = _langfuse.find_one({"_id": _DOC_ID}) or {}
    return {
        "enabled": doc.get("enabled", os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"),
        "credentials_configured": bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")),
        "environment": doc.get("environment", "local"),
        "base_url": doc.get("base_url", os.getenv("LANGFUSE_BASE_URL", "")),
        "send_transcripts": doc.get("send_transcripts", True),
        "send_prompts": doc.get("send_prompts", True),
        "runtime_status": {"last_error": doc.get("last_error", "")},
    }


@router.put("/langfuse")
def update_langfuse_settings(payload: dict, user: dict = Depends(require_user)) -> dict:
    allowed = {"enabled", "environment", "base_url", "send_transcripts", "send_prompts"}
    _langfuse.update_one({"_id": _DOC_ID}, {"$set": {k: v for k, v in payload.items() if k in allowed}}, upsert=True)
    log_audit(user, "update", "langfuse_settings", _DOC_ID)
    return get_langfuse_settings(user)
