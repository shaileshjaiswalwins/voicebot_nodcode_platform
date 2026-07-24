#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
exec uv run python -m campaign_dialer_worker.worker
