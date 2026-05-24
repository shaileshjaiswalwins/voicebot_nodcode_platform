"""Pydantic request/response schemas for the platform API.

Validation here serves three purposes:

1. Reject obviously-bad payloads at the edge (negative temperature, multi-megabyte
   prompts) instead of letting them reach Mongo or the runtime.
2. Convert ObjectId strings to ObjectId at a single chokepoint, with a clear
   400 instead of an opaque 500.
3. Give the dashboard a contract it can derive types from.

Designed to be permissive enough that the existing dashboard JSON payloads keep
working — we add validation without breaking what already works.
"""

from __future__ import annotations

import re
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ----- Helpers --------------------------------------------------------------

_OBJECT_ID_RE = re.compile(r"^[a-fA-F0-9]{24}$")


def assert_object_id(value: str, field: str = "id") -> ObjectId:
    """Convert a 24-char hex string into ObjectId, raising ValueError for the
    API layer to translate into HTTP 400. Used by route handlers, not by models.
    """
    if not isinstance(value, str) or not _OBJECT_ID_RE.match(value):
        raise ValueError(f"{field} must be a 24-character hex ObjectId")
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise ValueError(f"{field} is not a valid ObjectId") from exc


# ----- Runtime config field constraints -------------------------------------

ALLOWED_MODELS = {
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-exp",
    # Extend here as new models are approved. Keeping a set is intentional —
    # if a PM types a typo into the JSON panel it should fail validation
    # rather than reach the LiveKit worker.
}

MAX_SYSTEM_PROMPT_BYTES = 32 * 1024
MAX_TEXT_FIELD_BYTES = 4 * 1024
MAX_FUNCTIONS = 20
MIN_CALL_DURATION = 30
MAX_CALL_DURATION = 1800  # 30 minutes — hard ceiling


class RuntimeConfigModel(BaseModel):
    """Loose schema for the bot runtime config — every field is optional so
    drafts can be saved incrementally, but everything that IS supplied is
    bounds-checked. Unknown keys are preserved (forward-compat).
    """

    model_config = ConfigDict(extra="allow")

    assistant_id: str | None = None
    organization_id: str | None = None
    model: str | None = None
    voice: str | None = None
    language: str | None = None
    livekit_language: str | None = None
    sarvam_language: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_call_duration: int | None = Field(default=None, ge=MIN_CALL_DURATION, le=MAX_CALL_DURATION)
    system_prompt: str | None = None
    initial_message: str | None = None
    call_end_text: str | None = None
    function_calling: bool | None = None
    gemini_silence_duration_ms: int | None = Field(default=None, ge=100, le=10000)
    gemini_prefix_padding_ms: int | None = Field(default=None, ge=0, le=5000)
    post_speech_hold_ms: int | None = Field(default=None, ge=0, le=10000)
    sarvam_min_rms: int | None = Field(default=None, ge=0, le=20000)
    functions: list[dict[str, Any]] | None = None

    @field_validator("model")
    @classmethod
    def _model_allowed(cls, v):
        if v is None or v == "":
            return v
        if v not in ALLOWED_MODELS:
            raise ValueError(
                f"model {v!r} is not in the allowlist. "
                f"Allowed: {sorted(ALLOWED_MODELS)}. Update voicebot_platform/schemas.py to extend."
            )
        return v

    @field_validator("system_prompt")
    @classmethod
    def _prompt_size(cls, v):
        if v is None:
            return v
        if len(v.encode("utf-8")) > MAX_SYSTEM_PROMPT_BYTES:
            raise ValueError(f"system_prompt exceeds {MAX_SYSTEM_PROMPT_BYTES} bytes")
        return v

    @field_validator("initial_message", "call_end_text")
    @classmethod
    def _text_size(cls, v):
        if v is None:
            return v
        if len(v.encode("utf-8")) > MAX_TEXT_FIELD_BYTES:
            raise ValueError(f"field exceeds {MAX_TEXT_FIELD_BYTES} bytes")
        return v

    @field_validator("functions")
    @classmethod
    def _functions_size(cls, v):
        if v is None:
            return v
        if len(v) > MAX_FUNCTIONS:
            raise ValueError(f"too many functions (max {MAX_FUNCTIONS})")
        return v


# ----- Endpoint payloads ----------------------------------------------------


class CreateBotPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    owner: str | None = Field(default=None, max_length=200)
    assistant_id: str | None = Field(default=None, max_length=200)
    orchestration: dict[str, Any] | None = None
    config: RuntimeConfigModel | None = None
    notes: str | None = Field(default=None, max_length=2000)


class SaveDraftPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    config: RuntimeConfigModel
    notes: str | None = Field(default=None, max_length=2000)


class PublishPayload(BaseModel):
    version_id: str | None = None

    @field_validator("version_id")
    @classmethod
    def _vid(cls, v):
        if v is None or v == "":
            return v
        if not _OBJECT_ID_RE.match(v):
            raise ValueError("version_id must be a 24-character hex ObjectId")
        return v


class RollbackPayload(BaseModel):
    version_id: str

    @field_validator("version_id")
    @classmethod
    def _vid(cls, v):
        if not _OBJECT_ID_RE.match(v):
            raise ValueError("version_id must be a 24-character hex ObjectId")
        return v


class CampaignPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=200)
    campaign_key: str | None = Field(default=None, max_length=200)
    bot_id: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=50)
    lead_api: dict[str, Any] | None = None


class TestSessionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    campaign_id: str | None = Field(default=None, max_length=200)
    lead_id: str | None = Field(default=None, max_length=200)
    call_id: str | None = Field(default=None, max_length=200)
    mobile: str | None = Field(default=None, max_length=20)
    srchterm: str | None = Field(default=None, max_length=500)
    buyer_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    test_worker_agent_name: str | None = Field(default=None, max_length=80)

    @field_validator("test_worker_agent_name")
    @classmethod
    def _test_worker_agent_name(cls, v):
        if v is None or v == "":
            return v
        if not _AGENT_NAME_RE.match(v):
            raise ValueError("test_worker_agent_name must be alphanumeric (plus . _ -) and 1-80 chars")
        return v


class LangfuseUpdatePayload(BaseModel):
    enabled: bool | None = None
    environment: str | None = None
    send_transcripts: bool | None = None
    send_prompts: bool | None = None

    @field_validator("environment")
    @classmethod
    def _env(cls, v):
        if v is None:
            return v
        if v not in {"local", "staging", "prod"}:
            raise ValueError("environment must be one of local, staging, prod")
        return v


_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class RuntimeSettingsUpdatePayload(BaseModel):
    livekit_api_url: str | None = Field(default=None, max_length=500)
    livekit_browser_url: str | None = Field(default=None, max_length=500)
    livekit_agent_name: str | None = Field(default=None, max_length=80)

    @field_validator("livekit_agent_name")
    @classmethod
    def _agent_name(cls, v):
        if v is None or v == "":
            return v
        if not _AGENT_NAME_RE.match(v):
            raise ValueError("livekit_agent_name must be alphanumeric (plus . _ -) and 1-80 chars")
        return v


class PhrasePayload(BaseModel):
    category: str | None = None
    text: str | None = Field(default=None, max_length=500)
    language: str | None = Field(default=None, max_length=10)
    notes: str | None = Field(default=None, max_length=500)
