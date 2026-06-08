#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

export BOT_PORT="${BOT_PORT:-8081}"
PID_FILE="${BOT_PID_FILE:-/tmp/voicebot-${BOT_PORT}.pid}"

# Only stop a previous worker we ourselves started — never a random process
# that happens to be bound to BOT_PORT. If the PID file points at something
# unrelated, refuse to act rather than killing it silently.
if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
        CMD="$(ps -p "$OLD_PID" -o command= 2>/dev/null || true)"
        if [[ "$CMD" == *"bot.py"* ]]; then
            echo "[start.sh] Stopping previous bot.py worker pid=$OLD_PID..."
            kill "$OLD_PID" 2>/dev/null || true
            for _ in 1 2 3 4 5; do
                kill -0 "$OLD_PID" 2>/dev/null || break
                sleep 1
            done
            if kill -0 "$OLD_PID" 2>/dev/null; then
                echo "[start.sh] Force-killing stuck worker pid=$OLD_PID..."
                kill -9 "$OLD_PID" 2>/dev/null || true
            fi
        else
            echo "[start.sh] PID file pid=$OLD_PID does not look like a bot.py process (cmd: $CMD)." >&2
            echo "[start.sh] Refusing to kill it. Investigate and clear $PID_FILE manually." >&2
            exit 1
        fi
    fi
    rm -f "$PID_FILE"
fi

UV=$(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")

if ! command -v "$UV" &>/dev/null; then
    echo "uv not found. Install it: curl -Ls https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "[start.sh] Syncing dependencies..."
"$UV" sync

echo "[start.sh] Starting LiveKit native bot on port ${BOT_PORT}..."
"$UV" run python bot.py start &
WORKER_PID=$!
echo "$WORKER_PID" > "$PID_FILE"
echo "[start.sh] Worker pid=$WORKER_PID written to $PID_FILE"
wait "$WORKER_PID"
