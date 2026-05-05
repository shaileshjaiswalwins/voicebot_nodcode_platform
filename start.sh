#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Kill any stale worker on the LiveKit agents port
fuser -k 8081/tcp 2>/dev/null || true

UV=$(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")

if ! command -v "$UV" &>/dev/null; then
    echo "uv not found. Install it: curl -Ls https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "[start.sh] Syncing dependencies..."
"$UV" sync

echo "[start.sh] Starting LiveKit native bot..."
"$UV" run python bot.py start
