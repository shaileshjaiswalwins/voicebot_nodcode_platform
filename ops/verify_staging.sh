#!/usr/bin/env bash
# Post-deploy health verification for the safe staging environment.
# Run this after deploy_safe_server.sh to confirm the API and test worker are live.
#
# Usage:
#   ./ops/verify_staging.sh                    # default host/ports
#   API_PORT=8010 WORKER_PORT=8091 ./ops/verify_staging.sh

set -euo pipefail

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8010}"
WORKER_PORT="${WORKER_PORT:-8091}"
EXPECTED_AGENT_NAME="${EXPECTED_AGENT_NAME:-voice-bot-voicedesk-test}"
TIMEOUT="${TIMEOUT:-5}"

OK=0
FAIL=0

pass() { echo "[OK]  $1"; OK=$((OK + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "=== Staging verification: $API_HOST API=$API_PORT WORKER=$API_PORT ==="

# 1. Port presence
for PORT in "$API_PORT" "$WORKER_PORT"; do
  if ss -lntp 2>/dev/null | grep -q ":$PORT "; then
    pass "Port $PORT is listening"
  else
    fail "Port $PORT is NOT listening"
  fi
done

# 2. API /health
HEALTH=$(curl -sf --max-time "$TIMEOUT" "http://$API_HOST:$API_PORT/health" 2>/dev/null || echo "")
if echo "$HEALTH" | grep -q "ok\|healthy\|alive"; then
  pass "/health responded: $HEALTH"
else
  fail "/health did not return a healthy response (got: ${HEALTH:-no response})"
fi

# 3. API /health/ready (Mongo connectivity)
READY=$(curl -sf --max-time "$TIMEOUT" "http://$API_HOST:$API_PORT/health/ready" 2>/dev/null || echo "")
if echo "$READY" | grep -q '"status".*"ok"\|"ready"'; then
  pass "/health/ready is ready"
elif [ -n "$READY" ]; then
  fail "/health/ready returned: $READY"
else
  fail "/health/ready did not respond"
fi

# 4. Runtime settings endpoint — proves Mongo round-trip
SETTINGS=$(curl -sf --max-time "$TIMEOUT" "http://$API_HOST:$API_PORT/api/settings/runtime" 2>/dev/null || echo "")
if echo "$SETTINGS" | grep -q "livekit_agent_name\|agent_name"; then
  AGENT_NAME=$(echo "$SETTINGS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('livekit_agent_name',''))" 2>/dev/null || echo "")
  if [ "$AGENT_NAME" = "$EXPECTED_AGENT_NAME" ]; then
    pass "Runtime settings OK — agent_name=$AGENT_NAME"
  else
    fail "Runtime settings agent_name='$AGENT_NAME' (expected '$EXPECTED_AGENT_NAME')"
  fi
else
  fail "/api/settings/runtime did not return expected shape (got: ${SETTINGS:0:120})"
fi

# 5. Campaigns list — proves tbl_ai_vb_campaigns readable
CAMPAIGNS=$(curl -sf --max-time "$TIMEOUT" "http://$API_HOST:$API_PORT/api/campaigns" 2>/dev/null || echo "")
if echo "$CAMPAIGNS" | python3 -c "import sys,json; data=json.load(sys.stdin); assert isinstance(data,list)" 2>/dev/null; then
  COUNT=$(echo "$CAMPAIGNS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
  pass "Campaigns endpoint returned $COUNT campaigns"
else
  fail "Campaigns endpoint did not return a list (got: ${CAMPAIGNS:0:120})"
fi

echo ""
echo "=== Result: $OK passed / $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
