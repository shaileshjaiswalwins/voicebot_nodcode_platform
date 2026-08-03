#!/usr/bin/env python3
"""Crash-restart supervisor for a LiveKit worker entrypoint script.

A worker process (bot_dev_param.py / bot_dev.py / bot_pipeline.py) that crashes or gets
killed just stops — nothing restarts it, and until start_worker_heartbeat's staleness
check (surfaced at GET /api/diagnostics/worker-health) is noticed, calls silently hang on
"waiting for bot to join". This wraps a worker script, restarts it on exit with capped
exponential backoff, and appends every restart (with exit code and how long it ran) to a
local JSONL log — so "why did it go down last night" has an answer beyond re-reading the
worker's own stdout by hand.

Usage:
    python scripts/supervise_worker.py bot_dev_param.py dev
    python scripts/supervise_worker.py bot_dev.py start
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "worker_supervisor.jsonl"
MIN_BACKOFF_S = 2
MAX_BACKOFF_S = 60
# A run shorter than this is treated as a crash-loop tick for backoff purposes (still
# logged either way) rather than a healthy long-lived process resetting the delay.
HEALTHY_RUN_S = 30


def _log_event(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(event) + "\n")
    print(f"[SUPERVISOR] {event}", file=sys.stderr)


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <worker_script.py> [args...]", file=sys.stderr)
        sys.exit(1)

    worker_args = [sys.executable, *sys.argv[1:]]
    backoff = MIN_BACKOFF_S
    _log_event({"event": "supervisor_started", "command": worker_args})

    while True:
        started = time.monotonic()
        _log_event({"event": "worker_starting", "command": worker_args})
        try:
            proc = subprocess.Popen(worker_args)
            exit_code = proc.wait()
        except KeyboardInterrupt:
            _log_event({"event": "supervisor_stopped", "reason": "keyboard_interrupt"})
            sys.exit(0)
        ran_for_s = round(time.monotonic() - started, 1)
        _log_event({"event": "worker_exited", "exit_code": exit_code, "ran_for_seconds": ran_for_s})

        backoff = MIN_BACKOFF_S if ran_for_s >= HEALTHY_RUN_S else min(backoff * 2, MAX_BACKOFF_S)
        _log_event({"event": "restarting_after_backoff", "backoff_seconds": backoff})
        time.sleep(backoff)


if __name__ == "__main__":
    main()
