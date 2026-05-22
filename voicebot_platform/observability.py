from __future__ import annotations

import os
from typing import Any

from loguru import logger


class LangfuseRecorder:
    def __init__(self) -> None:
        self.enabled = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
        self._client = None
        if not self.enabled:
            return
        try:
            from langfuse import Langfuse

            self._client = Langfuse()
        except Exception as exc:
            self.enabled = False
            logger.warning(f"[LANGFUSE] disabled: {exc}")

    def event(self, name: str, metadata: dict[str, Any]) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            trace = self._client.trace(
                name=f"voicebot:{metadata.get('call_id') or metadata.get('room_name') or 'call'}",
                metadata=metadata,
            )
            trace.event(name=name, metadata=metadata)
        except Exception as exc:
            logger.warning(f"[LANGFUSE] event {name!r} failed: {exc}")


recorder = LangfuseRecorder()
