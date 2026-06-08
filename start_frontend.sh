#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/frontend"

FRONTEND_PORT="${FRONTEND_PORT:-5173}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"

if ! command -v node >/dev/null 2>&1; then
    echo "[start_frontend.sh] Node.js is not available on PATH." >&2
    echo "[start_frontend.sh] On the staging server, run: source ~/.bashrc" >&2
    exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
    echo "[start_frontend.sh] npm is not available on PATH." >&2
    exit 1
fi

echo "[start_frontend.sh] Node: $(node -v)"
echo "[start_frontend.sh] npm: $(npm -v)"
echo "[start_frontend.sh] Installing frontend dependencies..."
npm install

echo "[start_frontend.sh] Starting Vite dev server on ${FRONTEND_HOST}:${FRONTEND_PORT}..."
npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
