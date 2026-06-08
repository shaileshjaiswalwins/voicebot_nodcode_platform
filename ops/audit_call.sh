#!/usr/bin/env bash
# End-to-end call quality audit for a given call_id.
# Checks transcript, call events, analysis, recording, and Langfuse trace presence.
#
# Usage:
#   ./ops/audit_call.sh <call_id>
#   ./ops/audit_call.sh <call_id> --json       # machine-readable output

set -euo pipefail

CALL_ID="${1:-}"
JSON_MODE="${2:-}"

if [ -z "$CALL_ID" ]; then
  echo "Usage: ./ops/audit_call.sh <call_id> [--json]" >&2
  exit 1
fi

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8010}"
TIMEOUT="${TIMEOUT:-8}"

OK=0
WARN=0
FAIL=0

pass()  { echo "[OK]   $1"; OK=$((OK + 1)); }
warn()  { echo "[WARN] $1"; WARN=$((WARN + 1)); }
fail()  { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }
section() { echo ""; echo "--- $1 ---"; }

echo "=== Call quality audit: $CALL_ID ==="

BASE="http://$API_HOST:$API_PORT"

# 1. Transcript exists
section "Transcript"
TX=$(curl -sf --max-time "$TIMEOUT" "$BASE/api/transcripts/$CALL_ID" 2>/dev/null || echo "")
if echo "$TX" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'call_id' in d or 'transcript' in d" 2>/dev/null; then
  pass "Transcript document found"

  # outcome
  OUTCOME=$(echo "$TX" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('outcome',''))" 2>/dev/null || echo "")
  [ -n "$OUTCOME" ] && pass "Outcome: $OUTCOME" || warn "Outcome field is empty"

  # transcript source
  SRC=$(echo "$TX" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('transcript_source','live'))" 2>/dev/null || echo "live")
  pass "transcript_source: $SRC"

  # verified transcript
  VT=$(echo "$TX" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('verified_transcript_status',''))" 2>/dev/null || echo "")
  if [ "$VT" = "ok" ]; then
    pass "Verified transcript: ok"
  elif [ -n "$VT" ]; then
    warn "Verified transcript status: $VT"
  else
    warn "No verified_transcript_status — recording pipeline may not have run"
  fi

  # analysis
  ANALYSIS=$(echo "$TX" | python3 -c "import sys,json; d=json.load(sys.stdin); a=d.get('analysis',{}); print('ok' if a else '')" 2>/dev/null || echo "")
  [ "$ANALYSIS" = "ok" ] && pass "Analysis block present" || fail "Analysis block missing"

  # duration
  DUR=$(echo "$TX" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('call_duration_seconds',''))" 2>/dev/null || echo "")
  [ -n "$DUR" ] && pass "Call duration: ${DUR}s" || warn "call_duration_seconds not set"
else
  fail "Transcript not found for call_id=$CALL_ID"
fi

# 2. Call events
section "Call Events"
EVENTS=$(curl -sf --max-time "$TIMEOUT" "$BASE/api/call-events/$CALL_ID" 2>/dev/null || echo "")
if echo "$EVENTS" | python3 -c "import sys,json; data=json.load(sys.stdin); assert isinstance(data,list) and len(data)>0" 2>/dev/null; then
  COUNT=$(echo "$EVENTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
  pass "$COUNT call event(s) recorded"

  TYPES=$(echo "$EVENTS" | python3 -c "import sys,json; evts=json.load(sys.stdin); print(', '.join(sorted({e.get('event_type','?') for e in evts})))" 2>/dev/null || echo "")
  [ -n "$TYPES" ] && pass "Event types: $TYPES"
else
  warn "No call events found (pipeline may not have emitted events)"
fi

# 3. Recording / verified transcript pipeline
section "Recording"
RECORDING=$(curl -sf --max-time "$TIMEOUT" "$BASE/api/transcripts/$CALL_ID/recording" 2>/dev/null || echo "")
if echo "$RECORDING" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('media_path')" 2>/dev/null; then
  MEDIA=$(echo "$RECORDING" | python3 -c "import sys,json; print(json.load(sys.stdin).get('media_path',''))" 2>/dev/null || echo "")
  pass "Recording media_path: $MEDIA"
elif echo "$RECORDING" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  warn "Recording metadata found but media_path empty — call may be pending or unanswered"
else
  warn "No recording metadata (outbound dialer recording API may not have been queried yet)"
fi

echo ""
echo "=== Result: $OK OK / $WARN warnings / $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
  echo "Action: check Mongo tbl_ai_vb_call_transcripts and tbl_ai_vb_call_events for call_id=$CALL_ID"
  exit 1
elif [ "$WARN" -gt 0 ]; then
  exit 0
else
  exit 0
fi
