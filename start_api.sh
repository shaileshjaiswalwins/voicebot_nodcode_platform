#!/usr/bin/env bash
set -euo pipefail

uv run uvicorn voicebot_platform.api:app --host 0.0.0.0 --port "${VOICEBOT_API_PORT:-8000}"
