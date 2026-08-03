"""LangSmith tracing for voice calls.

Standalone integration — this codebase does not use LangChain, so traces are
built manually with `langsmith.RunTree` instead of the `@traceable` decorator.
One root run per call ("voice_call"), one child run per conversation turn.

Wired in from a single choke point: `_save_transcript_to_dashboard_db` in
bot.py, which every entrypoint (bot_dev_param.py, bot_pipeline.py,
workflow_engine.py) already calls at end-of-call — so every call type gets
traced without touching each entrypoint's event-handling code.

Never raises into the call path: tracing failures are logged and swallowed,
never allowed to affect call handling or the Mongo transcript save.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from loguru import logger

# Read lazily (on first trace_completed_call), not at import time: bot.py imports
# this module before it calls load_dotenv(), so module-level os.getenv() here would
# always see an empty environment in the actual worker process.
_client = None
_client_init_attempted = False


def _get_client():
    global _client, _client_init_attempted
    if _client_init_attempted:
        return _client
    _client_init_attempted = True
    if os.getenv("LANGSMITH_TRACING", "").strip().lower() not in ("1", "true", "yes"):
        return None
    try:
        from langsmith import Client as _LangSmithClient
        _client = _LangSmithClient()
    except Exception as _e:  # pragma: no cover - defensive, tracing is best-effort
        logger.warning(f"[LANGSMITH] Client init failed — tracing disabled: {_e}")
        _client = None
    return _client


# Standard-bot entrypoints (bot_dev_param.py, bot_pipeline.py) log live transcripts
# with role "user"/"assistant"; workflow-bot calls go through build_transcript_from_session
# (bot.py:1578-79), whose ROLE_MAP relabels those as "buyer"/"agent" instead. Both must
# be recognized here or workflow-bot transcripts silently produce zero turns.
_USER_ROLES = {"user", "buyer"}
_ASSISTANT_ROLES = {"assistant", "agent"}


def _merge_consecutive_same_role(transcript: list[dict]) -> list[dict]:
    """Real transcripts aren't strictly alternating user/assistant — re-asks, retries,
    and split STT commits produce back-to-back entries from the same role (a real call
    can log 4 consecutive user turns before the agent replies once). Pairing those
    directly produced turns with the text on only one side (e.g. {user: '...', assistant:
    None}) — every other turn in the trace was missing half the exchange. Collapsing
    consecutive same-role entries into one block first makes the alternating pairer
    below actually alternate."""
    blocks: list[dict] = []
    for item in transcript or []:
        role = item.get("role")
        text = item.get("text", "")
        if not text:
            continue
        if blocks and blocks[-1]["_bucket"] == ("user" if role in _USER_ROLES else "assistant" if role in _ASSISTANT_ROLES else role):
            blocks[-1]["text"] += " " + text
        else:
            bucket = "user" if role in _USER_ROLES else "assistant" if role in _ASSISTANT_ROLES else role
            blocks.append({"_bucket": bucket, "role": role, "text": text})
    return blocks


def _turns_from_transcript(transcript: list[dict]) -> list[dict]:
    """Pair up user/assistant transcript entries into turns (each side pre-merged
    from any consecutive same-role runs — see _merge_consecutive_same_role)."""
    turns: list[dict] = []
    pending_user = None
    for item in _merge_consecutive_same_role(transcript):
        role, text = item["_bucket"], item["text"]
        if role == "user":
            if pending_user is not None:
                turns.append({"user": pending_user, "assistant": None})
            pending_user = text
        elif role == "assistant":
            turns.append({"user": pending_user, "assistant": text})
            pending_user = None
    if pending_user is not None:
        turns.append({"user": pending_user, "assistant": None})
    return turns


def trace_completed_call(mongo_doc: dict, bot_id: str) -> str | None:
    """Log a full call (already-finished) to LangSmith as one trace tree.

    Safe to call unconditionally — no-ops if LANGSMITH_TRACING is not set to
    a truthy value, or if the SDK/client failed to initialize.

    Returns the root run's id (str) on success, or None if tracing is disabled or
    posting failed. Expects `mongo_doc["analysis"]` (call_outcome/qna/etc) to already
    be populated — bot.py's _save_transcript_to_dashboard_db computes it inline,
    synchronously, before calling this, since dashboard-driven calls have no other
    post-call analysis pipeline to patch this in later.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        from langsmith import RunTree

        project = os.getenv("LANGSMITH_PROJECT", "default")
        room_name = mongo_doc.get("room_name", "")
        transcript = mongo_doc.get("transcript") or []
        start = mongo_doc.get("call_start_time")
        end = mongo_doc.get("call_end_time")
        start_dt = datetime.fromtimestamp(start, tz=timezone.utc) if start else None
        end_dt = datetime.fromtimestamp(end, tz=timezone.utc) if end else None
        source = mongo_doc.get("source") or (
            "web_test" if str(room_name).startswith("test-") else "batch"
        )

        root = RunTree(
            name="voice_call",
            run_type="chain",
            project_name=project,
            client=client,
            inputs={
                "bot_id": bot_id,
                "room_name": room_name,
                "source": source,
                "lead_id": mongo_doc.get("lead_id"),
                # Which STT/TTS/LLM provider+model this specific call actually used —
                # resolved at call time (pipeline_providers.resolve_provider_summary or,
                # for workflow bots, built from what's actually instantiated), not just
                # the bot's raw config (which can be blank fields meaning "use default").
                "provider_config": mongo_doc.get("provider_config") or {},
            },
            start_time=start_dt,
        )
        root.post()

        def _component_window(end_ts, duration_ms):
            """(start_dt, end_dt) for one STT/LLM/TTS component, or None if this
            call's turn_metrics has no data for it (e.g. streaming STT reports
            duration_ms=0 — a real value, not "missing", so check end_ts alone)."""
            if not end_ts:
                return None
            end_t = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            start_t = datetime.fromtimestamp(end_ts - (duration_ms or 0) / 1000, tz=timezone.utc)
            return start_t, end_t

        turn_metrics = mongo_doc.get("turn_metrics") or []
        turns = _turns_from_transcript(transcript)

        # First pass: compute each turn's real [start, end] window from its own
        # STT/LLM/TTS component timestamps, so tool calls (which only have a single
        # capture-time timestamp, no turn correlator — see call_metrics.py) can be
        # assigned to whichever turn's window contains them.
        turn_windows = []
        for i, turn in enumerate(turns):
            tm = turn_metrics[i] if i < len(turn_metrics) else {}
            windows = {
                "stt": _component_window(tm.get("stt_timestamp"), tm.get("stt_duration_ms")),
                "llm": _component_window(tm.get("llm_timestamp"), tm.get("llm_duration_ms")),
                "tts": _component_window(tm.get("tts_timestamp"), tm.get("tts_duration_ms")),
            }
            present = [w for w in windows.values() if w is not None]
            turn_start = min((w[0] for w in present), default=start_dt)
            turn_end = max((w[1] for w in present), default=end_dt)
            turn_windows.append((tm, windows, turn_start, turn_end))

        tool_calls_by_turn: dict[int, list[dict]] = {}
        for tc in (mongo_doc.get("tool_calls") or []):
            ts = tc.get("timestamp")
            if ts is None or not turn_windows:
                continue
            tc_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            # Prefer a turn whose window actually contains it; otherwise nearest by
            # distance to the turn's end (tool calls fire mid-turn, before the turn's
            # own end is known from LLM/TTS timestamps).
            contained = [i for i, (_, _, s, e) in enumerate(turn_windows) if s <= tc_dt <= e]
            if contained:
                idx = contained[0]
            else:
                idx = min(range(len(turn_windows)), key=lambda i: abs((turn_windows[i][3] - tc_dt).total_seconds()))
            tool_calls_by_turn.setdefault(idx, []).append(tc)

        for i, turn in enumerate(turns):
            tm, windows, turn_start, turn_end = turn_windows[i]
            for tc in tool_calls_by_turn.get(i, []):
                tc_t = datetime.fromtimestamp(tc["timestamp"], tz=timezone.utc)
                turn_start, turn_end = min(turn_start, tc_t), max(turn_end, tc_t)
            # A turn's own start/end MUST span its real component timestamps (all from
            # during the call) — leaving these unset defaults to "now", i.e. whenever
            # this function runs (well after the call ended), which made every
            # component render as if it happened tens of seconds "before" its own
            # parent in the waterfall. Fall back to the root's overall call window
            # only for a turn with no metrics at all (e.g. workflow bots' greeting turn).

            child = root.create_child(
                name=f"turn_{i + 1}",
                run_type="chain",
                inputs={"user": turn["user"]},
                start_time=turn_start,
            )
            child.post()

            def _span(parent, name, run_type, window, outputs):
                if window is None:
                    return
                start_t, end_t = window
                span = parent.create_child(name=name, run_type=run_type, start_time=start_t)
                span.post()
                span.end(outputs=outputs, end_time=end_t)
                span.patch()

            _span(
                child, "stt", "tool", windows["stt"],
                {"duration_ms": tm.get("stt_duration_ms"), "audio_duration_ms": tm.get("stt_audio_duration_ms")},
            )
            _span(
                child, "llm", "llm", windows["llm"],
                {
                    "ttft_ms": tm.get("llm_ttft_ms"), "duration_ms": tm.get("llm_duration_ms"),
                    "prompt_tokens": tm.get("llm_prompt_tokens"), "completion_tokens": tm.get("llm_completion_tokens"),
                },
            )
            _span(
                child, "tts", "tool", windows["tts"],
                {"ttfb_ms": tm.get("tts_ttfb_ms"), "duration_ms": tm.get("tts_duration_ms")},
            )

            for tc in tool_calls_by_turn.get(i, []):
                # function_tools_executed gives only a single capture-time timestamp, not a
                # start/duration — rendered as a zero-width event marker rather than a bar.
                # Application-level failures (bot.py's execute_fn always catches and returns
                # {"error": ...} rather than raising) live inside `output`, not `is_error` —
                # `is_error` only reflects the SDK's own no-output case (e.g. StopResponse).
                tc_t = datetime.fromtimestamp(tc["timestamp"], tz=timezone.utc)
                tool_span = child.create_child(
                    name=tc.get("name") or "tool_call", run_type="tool", start_time=tc_t,
                    inputs={"arguments": tc.get("arguments")},
                )
                tool_span.post()
                tool_span.end(outputs={"output": tc.get("output"), "is_error": tc.get("is_error")}, end_time=tc_t)
                tool_span.patch()

            child.end(
                outputs={
                    "assistant": turn["assistant"],
                    "eou_delay_ms": tm.get("eou_delay_ms"),
                    "total_latency_ms": tm.get("total_ms"),
                },
                end_time=turn_end,
            )
            child.patch()

        session_errors = mongo_doc.get("session_errors") or []
        # A recoverable error (the pipeline retried and succeeded) shouldn't flag the
        # whole trace red — only a genuinely exhausted (non-recoverable) one should.
        fatal_errors = [e for e in session_errors if not e.get("recoverable")]
        root_error = (
            "; ".join(f"{e.get('type', 'error')}: {e.get('message', '')}" for e in fatal_errors)
            if fatal_errors else None
        )

        # Dashboard-driven calls have no other post-call analysis pipeline (see
        # bot.py's _save_transcript_to_dashboard_db) — analysis is computed inline,
        # synchronously, before this doc is even saved, so it's already available here.
        analysis = mongo_doc.get("analysis") or {}

        root.end(
            outputs={
                "status": mongo_doc.get("status"),
                "turn_count": mongo_doc.get("turn_count"),
                "call_duration_sec": mongo_doc.get("call_duration_sec"),
                "avg_response_latency_ms": mongo_doc.get("avg_response_latency_ms"),
                # Full verbatim conversation, always available here regardless of any
                # edge case in the per-turn pairing above — the guaranteed place to
                # read "what was actually said" for this call.
                "full_transcript": transcript,
                "session_errors": session_errors,
                "call_outcome": analysis.get("call_outcome"),
                "call_outcome_description": analysis.get("call_outcome_description"),
                "call_summary": analysis.get("call_summary"),
                "qna": analysis.get("qna"),
                "is_business": analysis.get("is_business"),
                "deal_value": analysis.get("deal_value"),
                "lead_intent_score": analysis.get("lead_intent_score"),
            },
            error=root_error,
            end_time=end_dt,
        )
        root.patch()
        # Each LiveKit job runs in its own short-lived subprocess, and langsmith's
        # Client spawns a non-daemon background thread for batched delivery — left
        # alone, that thread can hang the subprocess past job end (same class of
        # zombie-process issue this platform already had with orphaned prewarm
        # processes on port 8085). Flush synchronously so it's fully drained before
        # the job process is allowed to exit.
        client.flush()
        logger.info(f"[LANGSMITH] Trace posted | room={room_name!r} | turns={len(transcript)}")
        return str(root.id)
    except Exception as e:
        logger.warning(f"[LANGSMITH] Failed to post trace (call unaffected): {e}")
        return None
