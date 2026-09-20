#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-voice-host.internal}"
TARGET_USER="${TARGET_USER:-yogeshv_10011835}"
TARGET_DIR="${TARGET_DIR:-/home/yogeshv_10011835/voicebot_nodcode_platform_ai_mgmt}"
BRANCH="${BRANCH:-ai_voice_bot_management}"
ARCHIVE_NAME="${ARCHIVE_NAME:-voicebot_nodcode_platform_ai_mgmt.tar.gz}"

case "$TARGET_DIR" in
  *voicebot_nodcode_platform_ai_mgmt) ;;
  *)
    echo "[deploy_safe_server.sh] Refusing to deploy outside the safe staging folder." >&2
    echo "[deploy_safe_server.sh] TARGET_DIR must end with voicebot_nodcode_platform_ai_mgmt." >&2
    echo "[deploy_safe_server.sh] Current TARGET_DIR: $TARGET_DIR" >&2
    exit 1
    ;;
esac

if [[ "${ALLOW_LIVE_DEPLOY:-}" == "true" ]]; then
  echo "[deploy_safe_server.sh] ALLOW_LIVE_DEPLOY=true is not supported by this script." >&2
  echo "[deploy_safe_server.sh] Live deploys need explicit human review and a separate release script." >&2
  exit 1
fi

if [[ -n "$(git status --short)" ]]; then
  echo "[deploy_safe_server.sh] Refusing to deploy with uncommitted local changes." >&2
  git status --short >&2
  exit 1
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "[deploy_safe_server.sh] Refusing to deploy from branch '$CURRENT_BRANCH'." >&2
  echo "[deploy_safe_server.sh] Expected branch: $BRANCH" >&2
  exit 1
fi

echo "[deploy_safe_server.sh] Creating archive from $BRANCH..."
git archive --format=tar.gz -o "/tmp/$ARCHIVE_NAME" "$BRANCH"

echo "[deploy_safe_server.sh] Copying archive to $TARGET_USER@$TARGET_HOST..."
scp "/tmp/$ARCHIVE_NAME" "$TARGET_USER@$TARGET_HOST:/home/$TARGET_USER/$ARCHIVE_NAME"

echo "[deploy_safe_server.sh] Extracting into safe staging folder: $TARGET_DIR"
ssh "$TARGET_USER@$TARGET_HOST" "set -euo pipefail
  mkdir -p '$TARGET_DIR'
  if [ -f '$TARGET_DIR/.env' ]; then
    cp '$TARGET_DIR/.env' /tmp/voicebot_ai_mgmt.env.backup
  fi
  rm -rf '$TARGET_DIR.tmp'
  mkdir -p '$TARGET_DIR.tmp'
  tar -xzf '/home/$TARGET_USER/$ARCHIVE_NAME' -C '$TARGET_DIR.tmp'
  if [ -f /tmp/voicebot_ai_mgmt.env.backup ]; then
    cp /tmp/voicebot_ai_mgmt.env.backup '$TARGET_DIR.tmp/.env'
  fi
  rm -rf '$TARGET_DIR.previous'
  if [ -d '$TARGET_DIR' ]; then
    mv '$TARGET_DIR' '$TARGET_DIR.previous'
  fi
  mv '$TARGET_DIR.tmp' '$TARGET_DIR'
  chmod +x '$TARGET_DIR/start.sh' '$TARGET_DIR/start_api.sh' '$TARGET_DIR/build_frontend.sh' '$TARGET_DIR/start_frontend.sh' 2>/dev/null || true
  echo '[deploy_safe_server.sh] Safe staging code updated.'
"

echo "[deploy_safe_server.sh] Done. This script does not restart live services."
