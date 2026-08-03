#!/usr/bin/env bash
# Restarts the dashboard backend (backend.main:app — the FastAPI app all bot/testcall/
# transcripts/etc. routers live under, distinct from voicebot_platform/api.py which is a
# separate, unrelated app that start_api.sh happens to launch). Rotates backend_dev.log
# once it crosses ROTATE_MB instead of letting it grow unbounded across restarts.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG_FILE="logs/backend_dev.log"
ROTATE_MB="${ROTATE_MB:-20}"
PORT="${VOICEBOT_API_PORT:-8000}"

mkdir -p logs

if [ -f "$LOG_FILE" ]; then
  size_mb=$(( $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE") / 1048576 ))
  if [ "$size_mb" -ge "$ROTATE_MB" ]; then
    mv -f "$LOG_FILE" "${LOG_FILE}.1"
    echo "[restart_backend_dev] Rotated ${LOG_FILE} (was ${size_mb}MB) -> ${LOG_FILE}.1"
  fi
fi

# Kill any process already holding the port before starting a fresh one — avoids the
# two-processes-on-one-port hang this script was written to prevent from recurring.
existing_pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$existing_pid" ]; then
  echo "[restart_backend_dev] Stopping existing process(es) on port ${PORT}: ${existing_pid}"
  kill $existing_pid 2>/dev/null || true
  sleep 1
fi

nohup .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG_FILE" 2>&1 &
disown
echo "[restart_backend_dev] Started backend.main:app on port ${PORT} (PID $!)"
