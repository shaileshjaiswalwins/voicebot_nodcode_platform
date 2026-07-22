---
name: voicebot-trace
description: |
  Diagnose missing-data issues in the voicebot data flow (test-call transcripts
  not appearing, callbacks not sent, wrong Mongo collection, env divergence
  between processes). Walks the 5-layer path
  browser → platform API → LiveKit → bot.py worker → Mongo
  and prints a verdict against the most common failure modes.
allowed-tools:
  - Bash
  - Read
  - Grep
  - AskUserQuestion
triggers:
  - transcript not appearing
  - test call didn't save
  - callback not sent
  - my call didn't land in the dashboard
  - why is my transcript missing
  - voicebot dataflow debug
---

# Trace a missing transcript / callback in the voicebot flow

## When to use

User reports that **something they expected to land in Mongo/dashboard did not**: a transcript, a callback, a saved test call. The cause is almost always one of six things — this skill enumerates them in priority order.

## The flow you are debugging

```
[Dashboard]
   POST /api/bots/{id}/webrtc-test-session
        ↓
[Platform API]
   creates LiveKit room with metadata{test_session:true, assistant_id, ...}
   dispatches agent with agent_name = runtime.livekit_agent_name
        ↓
[LiveKit server]
   waits for a worker registered as that agent_name
        ↓
[bot.py worker]   ← must be on same LIVEKIT_URL, same agent name
   entrypoint() joins room
   save_call_data() on disconnect → insert into Mongo at:
        db   = $VOICEBOT_PLATFORM_DB (or $MONGO_DB), default ai_voice_bot_management
        coll = $MONGO_COLLECTION,                   default tbl_ai_vb_call_transcripts
        ↓
[Dashboard]
   GET /api/transcripts → reads same (db, collection)
```

A break in **any** layer can produce "missing data". The skill's job is to identify which.

## Phase 1 — scan every transcript-shaped collection in the cluster

This is the highest-signal step. Run from the project root, on a machine that can reach the Mongo cluster:

```bash
cd /Users/justdial/voicebot_nodcode_platform
uv run python - <<'PY'
import os
from voicebot_platform.mongo import get_client
print("ENV the bot/platform would inherit if launched from this shell:")
for k in ("MONGO_URI","VOICEBOT_PLATFORM_DB","MONGO_DB","MONGO_COLLECTION",
         "LIVEKIT_URL","LIVEKIT_AGENT_NAME"):
    print(f"  {k:24s} = {os.environ.get(k, '(unset, default applies)')!r}")
print()
client = get_client()
for db_name in client.list_database_names():
    if db_name in {"admin","config","local"}: continue
    for coll in client[db_name].list_collection_names():
        sample = client[db_name][coll].find_one()
        if not sample or not any(k in sample for k in ("call_id","lead_id","transcript","room_name")):
            continue
        latest = client[db_name][coll].find_one(
            sort=[("created_at",-1)],
            projection={"call_id":1,"room_name":1,"status":1,"created_at":1},
        )
        n = client[db_name][coll].estimated_document_count()
        print(f"{db_name}.{coll}  ({n} docs)  latest={latest}")
PY
```

### Interpreting the output

Look at the **room_name** of the latest doc in each collection:

- `room_name` starts with `test-` (e.g. `test-e8c0fd31-a1b2c3d4e5`) → that's a real dashboard test call.
- `room_name` is `Campaign_<id>_...` → production SIP call.
- `room_name` = `room-api` or hand-crafted → synthetic test from curl, not your dashboard flow.

If your missing call shows up in the **wrong** db/collection (e.g. `ai_lead_qualify.call_transcripts` instead of `ai_voice_bot_management.tbl_ai_vb_call_transcripts`), the bot worker has the wrong env. Jump to Phase 2.

If your call doesn't show up **anywhere**, the bot worker either isn't running, isn't dispatching, or is crashing in `save_call_data`. Jump to Phase 3.

If your call shows up in the **right** collection but the dashboard doesn't display it, jump to Phase 4 (frontend / API connectivity).

## Phase 2 — env divergence between bot worker and platform

The bot worker and the platform API can read different Mongo databases. They must match.

```bash
# What the platform API process sees right now:
curl -s http://localhost:8000/health/ready | jq

# What the bot worker process inherits (run on the box where it runs):
WORKER_PID=$(pgrep -f "python bot.py" | head -1)
[ -n "$WORKER_PID" ] && cat /proc/$WORKER_PID/environ 2>/dev/null | tr '\0' '\n' \
  | grep -iE '^(MONGO|VOICEBOT|LIVEKIT)' \
  || echo "no worker, or /proc/environ not readable (macOS: pid not introspectable this way)"
```

Specifically check:

| Bot env | Platform env | Must equal? |
|---|---|---|
| `MONGO_URI` | `MONGO_URI` (api) | **YES** — different clusters → data lost |
| `(VOICEBOT_PLATFORM_DB or MONGO_DB)` | `VOICEBOT_PLATFORM_DB` (api) | **YES** |
| `MONGO_COLLECTION` | `MONGO_COLLECTION` (api) | **YES** |
| `LIVEKIT_URL` | `LIVEKIT_URL` (api) | **YES** — different LK servers → no agent joins |
| `LIVEKIT_AGENT_NAME` | dashboard Settings → "Test call worker agent name" | **YES** — mismatch → dispatch queues forever |

A frequent failure pattern: user changed `.env` for the platform but the bot worker has been running since before and still holds the old env. Restart the bot worker after any env change.

## Phase 3 — bot worker didn't pick up the call, or crashed mid-save

```bash
# Locate worker logs (default path matches bot.py:65)
LOGDIR=${BOT_LOG_DIR:-/home/yogeshv_10011835/voicebot_nodcode_platform/logs}/${BOT_PORT:-8081}
LATEST=$(ls -t $LOGDIR 2>/dev/null | head -1)
[ -n "$LATEST" ] && tail -300 $LOGDIR/$LATEST \
  | grep -E "CALL START|test_session|SAVE_CALL|MONGO|insert failed|callback" \
  || echo "no log directory at $LOGDIR — worker may not be running on this host"
```

| What you see | Verdict |
|---|---|
| No `[CALL START]` line near the test time | Worker not running OR LiveKit dispatch didn't reach it (agent-name mismatch / different LIVEKIT_URL). |
| `[CALL START]` then `[MONGO] insert failed: <error>` | Save path crashed. Read the error (auth, network, BSON). |
| `[CALL START]` then `[MONGO] Transcript saved` | It really did save. Re-run Phase 1 to find where; if it's there but dashboard doesn't show, Phase 4. |
| `[CALL START]` but no SAVE_CALL line at all | Save trigger never fired. Check that the dashboard's Stop button called the close-room endpoint — `livekit_sessions.close_webrtc_test_room` is what gives the worker its disconnect signal. |

## Phase 4 — dashboard can't display what's in Mongo

If the doc exists in the right collection but the dashboard shows empty:

1. **Browser DevTools → Network** → click Refresh → look at `GET /api/transcripts`.
   - 200 with empty array → query filter mismatch; check the doc's `bot_id` matches the active bot.
   - 5xx / timeout → backend can't reach Mongo. Confirm with `curl http://localhost:8000/health/ready`.
   - 200 with the doc → frontend cache. Hard-reload (Cmd+Shift+R) to clear `jd-vb:v2:*` keys.

2. **If platform API can't reach Mongo** — common when running the dashboard on a laptop but Mongo is on the corporate `192.168.x.x` network. Either:
   - SSH tunnel: `ssh -L 27017:192.168.13.65:27017 <jumpbox> -N`, then `MONGO_URI=mongodb://localhost:27017 ./start_api.sh`
   - Run `start_api.sh` on the same server as the bot worker.

## Output format

End with a one-paragraph verdict that names the failed layer and the fix:

> Your test call's transcript landed in `ai_voice_bot_management.tbl_ai_vb_call_transcripts` (Phase 1) but your dashboard backend can't reach Mongo from this laptop (Phase 4 → `health/ready` returned 503). Fix: open an SSH tunnel to the cluster or run `start_api.sh` on the server. ETA: 5 minutes.

## Anti-patterns

- Don't speculate before Phase 1. The cluster scan tells you which layer to investigate; don't read code first.
- Don't ask the user to restart things until you've shown them which env is currently in effect.
- Don't blame the frontend cache unless Phase 1 confirms the doc exists in the right collection.
