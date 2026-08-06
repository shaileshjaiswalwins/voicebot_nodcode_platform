#!/usr/bin/env bash
set -euo pipefail

uv run uvicorn backend.main:app --host 0.0.0.0 --port "${VOICEBOT_API_PORT:-8000}"
