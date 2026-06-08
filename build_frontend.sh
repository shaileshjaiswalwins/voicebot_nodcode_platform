#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/frontend"

if ! command -v node >/dev/null 2>&1; then
    echo "[build_frontend.sh] Node.js is not available on PATH." >&2
    echo "[build_frontend.sh] On the staging server, run: source ~/.bashrc" >&2
    exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
    echo "[build_frontend.sh] npm is not available on PATH." >&2
    exit 1
fi

echo "[build_frontend.sh] Node: $(node -v)"
echo "[build_frontend.sh] npm: $(npm -v)"
echo "[build_frontend.sh] Installing frontend dependencies..."
npm install

echo "[build_frontend.sh] Building React dashboard..."
npm run build

echo "[build_frontend.sh] Frontend build ready in frontend/dist"
