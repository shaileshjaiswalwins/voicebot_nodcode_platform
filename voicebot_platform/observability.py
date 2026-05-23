from __future__ import annotations

from typing import Any

from loguru import logger

from .config import LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
from .platform_settings import get_langfuse_settings


class LangfuseRecorder:
    def __init__(self) -> None:
        self._client = None
        self._import_error: str | None = None

    def _credentials_configured(self) -> bool:
        return bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._credentials_configured():
            return None
        try:
            from langfuse import get_client

            self._client = get_client()
            return self._client
        except Exception as exc:
            self._import_error = str(exc)
            logger.warning(f"[LANGFUSE] disabled: {exc}")
            return None

    def status(self) -> dict[str, Any]:
        settings = self._safe_settings()
        return {
            "enabled": bool(settings.get("enabled")),
            "credentials_configured": self._credentials_configured(),
            "base_url": LANGFUSE_BASE_URL,
            "environment": settings.get("environment", "local"),
            "send_transcripts": bool(settings.get("send_transcripts", True)),
            "send_prompts": bool(settings.get("send_prompts", True)),
            "last_error": self._import_error,
        }

    def event(self, name: str, metadata: dict[str, Any], *, input: Any = None, output: Any = None) -> None:
        settings = self._safe_settings()
        if not settings.get("enabled"):
            return
        client = self._get_client()
        if client is None:
            return

        environment = settings.get("environment", "local")
        trace_key = (
            metadata.get("call_id")
            or metadata.get("room_name")
            or metadata.get("lead_id")
            or metadata.get("assistant_id")
            or "voicebot-call"
        )
        try:
            trace_id = client.create_trace_id(seed=f"{environment}:{trace_key}")
            event_metadata = {
                **metadata,
                "environment": environment,
                "service": "justdial-voicebot",
                "trace_url": client.get_trace_url(trace_id=trace_id),
            }
            if not settings.get("send_transcripts", True):
                event_metadata.pop("transcript", None)
            if not settings.get("send_prompts", True):
                event_metadata.pop("prompt", None)
                event_metadata.pop("system_prompt", None)
                event_metadata.pop("config_snapshot", None)

            client.create_event(
                trace_context={"trace_id": trace_id},
                name=name,
                input=input,
                output=output,
                metadata=event_metadata,
                version=str(metadata.get("bot_version") or metadata.get("bot_version_id") or ""),
                level="ERROR" if str(metadata.get("status", "")).lower() in {"failed", "error"} else "DEFAULT",
            )
            if name in {"transcript_saved", "callback_sent", "call_ended"}:
                client.flush()
        except Exception as exc:
            self._import_error = str(exc)
            logger.warning(f"[LANGFUSE] event {name!r} failed: {exc}")

    def flush(self) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.flush()
        except Exception as exc:
            logger.warning(f"[LANGFUSE] flush failed: {exc}")

    def _safe_settings(self) -> dict[str, Any]:
        try:
            return get_langfuse_settings()
        except Exception as exc:
            logger.warning(f"[LANGFUSE] settings lookup failed: {exc}")
            return {"enabled": False, "environment": "local"}


recorder = LangfuseRecorder()
