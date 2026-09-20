from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from .config import LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
from .platform_settings import get_langfuse_settings


_SETTINGS_TTL_SEC = 30


class LangfuseRecorder:
    def __init__(self) -> None:
        self._client = None
        self._import_error: str | None = None
        self._last_success_at: float | None = None
        self._settings_cache: tuple[float, dict[str, Any]] | None = None
        self._settings_lock = threading.Lock()
        # Bounded background pool so emitting events never blocks an async caller.
        # max_workers=2 is enough: events are infrequent, and we'd rather drop
        # than queue if Langfuse is slow.
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="langfuse")

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
            "last_success_at": self._last_success_at,
        }

    def event(self, name: str, metadata: dict[str, Any], *, input: Any = None, output: Any = None) -> None:
        """Non-blocking event emission.

        Settings lookup is cached, the actual Langfuse HTTP call runs in a
        background thread so async callers (bot.py entrypoint) never block on
        network I/O.
        """
        settings = self._safe_settings()
        if not settings.get("enabled"):
            return
        client = self._get_client()
        if client is None:
            return
        # Hand off the network work — fire and forget. We log on failure inside the worker.
        try:
            self._executor.submit(self._emit, name, metadata, input, output, settings, client)
        except RuntimeError:
            # Executor shut down — fall through to inline emit so we still record.
            self._emit(name, metadata, input, output, settings, client)

    def _emit(self, name: str, metadata: dict[str, Any], input: Any, output: Any, settings: dict[str, Any], client) -> None:
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
                "service": "voicedesk-voicebot",
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
            self._last_success_at = time.time()
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

    def invalidate_settings_cache(self) -> None:
        with self._settings_lock:
            self._settings_cache = None

    def _safe_settings(self) -> dict[str, Any]:
        with self._settings_lock:
            entry = self._settings_cache
            if entry and (time.monotonic() - entry[0]) < _SETTINGS_TTL_SEC:
                return entry[1]
        try:
            settings = get_langfuse_settings()
        except Exception as exc:
            logger.warning(f"[LANGFUSE] settings lookup failed: {exc}")
            settings = {"enabled": False, "environment": "local"}
        with self._settings_lock:
            self._settings_cache = (time.monotonic(), settings)
        return settings


recorder = LangfuseRecorder()
