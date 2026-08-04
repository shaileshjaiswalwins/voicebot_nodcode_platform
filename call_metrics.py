"""Per-turn STT/LLM/TTS latency breakdown for a single call.

`AgentSession.on("metrics_collected", ...)` fires once per pipeline component
per turn (livekit.agents.metrics: STTMetrics, LLMMetrics, TTSMetrics,
EOUMetrics), each tagged with a shared `speech_id` for that turn. This
collector buckets those events by speech_id so the per-component timing can
be attached to the call's saved transcript (and from there, to the LangSmith
trace) — answering "which part of the turn took how long" instead of just a
single end-to-end number.

One instance per call. Never raises: a malformed/unexpected metrics event is
logged and dropped, never allowed to break the call.
"""
from __future__ import annotations

import json
import time

from loguru import logger


class CallMetricsCollector:
    def __init__(self) -> None:
        self._by_speech_id: dict[str, dict] = {}
        self._order: list[str] = []
        # STT metrics never carry a speech_id (STT runs continuously, not per-turn —
        # only EOU/LLM/TTS, which belong to one generated response, share one). Stash
        # STT fields here and fold them into the next real (speech_id-keyed) bucket,
        # instead of giving STT its own bogus extra "turn".
        self._pending_stt: dict | None = None
        # function_tools_executed (livekit.agents.voice.events.FunctionToolsExecutedEvent)
        # carries no speech_id and no timestamp of its own — recorded at capture time
        # (call completion), which is the best correlator available for bucketing a
        # tool call into a turn downstream (langsmith_tracing.py matches it to whichever
        # turn's [start, end] window contains this timestamp).
        self._tool_calls: list[dict] = []
        # session-level pipeline errors (session.on("error", ...) — bot_dev_param.py/
        # bot_pipeline.py's _on_session_error already logs these; recorded here too so
        # they land in the LangSmith trace instead of only the log file.
        self._errors: list[dict] = []

    def _bucket(self, speech_id: str) -> dict:
        if speech_id not in self._by_speech_id:
            self._by_speech_id[speech_id] = {"speech_id": speech_id}
            self._order.append(speech_id)
        return self._by_speech_id[speech_id]

    def on_metrics_collected(self, ev) -> None:
        try:
            m = getattr(ev, "metrics", ev)
            mtype = getattr(m, "type", "")
            ts = getattr(m, "timestamp", None)

            if mtype == "stt_metrics":
                self._pending_stt = {
                    "stt_duration_ms": round(getattr(m, "duration", 0) * 1000),
                    "stt_audio_duration_ms": round(getattr(m, "audio_duration", 0) * 1000),
                    "stt_timestamp": ts,
                }
                return

            speech_id = getattr(m, "speech_id", None)
            if not speech_id:
                logger.warning(f"[CALL-METRICS] {mtype} event had no speech_id — dropped")
                return
            bucket = self._bucket(speech_id)
            if self._pending_stt is not None:
                bucket.update(self._pending_stt)
                self._pending_stt = None

            if mtype == "eou_metrics":
                bucket["eou_delay_ms"] = round(getattr(m, "end_of_utterance_delay", 0) * 1000)
                bucket["transcription_delay_ms"] = round(getattr(m, "transcription_delay", 0) * 1000)
                bucket["eou_timestamp"] = ts
            elif mtype == "stt_metrics":
                bucket["stt_duration_ms"] = round(getattr(m, "duration", 0) * 1000)
                bucket["stt_audio_duration_ms"] = round(getattr(m, "audio_duration", 0) * 1000)
                bucket["stt_timestamp"] = ts
            elif mtype == "llm_metrics":
                bucket["llm_ttft_ms"] = round(getattr(m, "ttft", 0) * 1000)
                bucket["llm_duration_ms"] = round(getattr(m, "duration", 0) * 1000)
                bucket["llm_prompt_tokens"] = getattr(m, "prompt_tokens", None)
                bucket["llm_completion_tokens"] = getattr(m, "completion_tokens", None)
                bucket["llm_timestamp"] = ts
            elif mtype == "tts_metrics":
                bucket["tts_ttfb_ms"] = round(getattr(m, "ttfb", 0) * 1000)
                bucket["tts_duration_ms"] = round(getattr(m, "duration", 0) * 1000)
                bucket["tts_timestamp"] = ts
            else:
                return

            bucket["total_ms"] = sum(
                bucket.get(k, 0) or 0
                for k in ("stt_duration_ms", "llm_ttft_ms", "llm_duration_ms", "tts_ttfb_ms")
            )
        except Exception as e:
            logger.warning(f"[CALL-METRICS] Failed to record metrics_collected event: {e}")

    def as_list(self) -> list[dict]:
        return [self._by_speech_id[k] for k in self._order]

    def on_function_tools_executed(self, ev) -> None:
        """Tool calls never raise in this codebase's execute_fn (bot.py:_execute_function_call
        catches everything and returns {"error": ...} as a normal successful output — see
        langsmith_tracing.py's docstring note) — so `is_error` here reflects the SDK's own
        notion of failure (e.g. a StopResponse with no output), not an HTTP/exception failure.
        Check the "output" text itself for an application-level error."""
        try:
            ts = time.time()
            for call, output in ev.zipped():
                try:
                    arguments = json.loads(call.arguments) if call.arguments else {}
                except (TypeError, ValueError):
                    arguments = call.arguments
                self._tool_calls.append({
                    "name": call.name,
                    "arguments": arguments,
                    "output": getattr(output, "output", None) if output else None,
                    "is_error": bool(getattr(output, "is_error", False)) if output else None,
                    "timestamp": ts,
                })
        except Exception as e:
            logger.warning(f"[CALL-METRICS] Failed to record function_tools_executed event: {e}")

    def tool_calls_as_list(self) -> list[dict]:
        return self._tool_calls

    def on_session_error(self, err) -> None:
        try:
            self._errors.append({
                "type": getattr(err, "type", ""),
                "message": str(getattr(err, "error", err)),
                "recoverable": bool(getattr(err, "recoverable", False)),
                "timestamp": time.time(),
            })
        except Exception as e:
            logger.warning(f"[CALL-METRICS] Failed to record session error: {e}")

    def errors_as_list(self) -> list[dict]:
        return self._errors
