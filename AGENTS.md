# AGENTS.md

This file is the onboarding brief for coding agents working on the JustDial
Voicebot platform. Read it before changing code, running server commands, or
debugging runtime behavior.

## Project Overview

This repository contains a standalone LiveKit-native voicebot runtime plus an
internal PM/developer dashboard for managing JustDial voicebots.

The current core product is the JustDial lead qualification voice agent
("Tanya"/"Simran" in prompts). The platform goal is to let PMs configure,
draft, publish, test, monitor, and review voicebots without repeatedly touching
server files.

Important product boundaries:

- V1 is not a general drag-and-drop bot builder. It is a prompt, settings,
  catalog, test-call, transcript, and observability platform.
- The runtime is LiveKit Agents native with Gemini Live. Do not assume this is a
  Pipecat runtime unless a specific file proves it.
- MIS, dialer, CRM, and recording APIs are external JustDial systems. Keep this
  platform standalone and integrate through explicit APIs/config.

## Repository Map

- `bot.py` - LiveKit worker/runtime. Reads a published bot config at call start,
  runs Gemini Live, saves transcripts, emits call events, and sends Langfuse
  observability.
- `voicebot_platform/api.py` - FastAPI dashboard/backend API.
- `voicebot_platform/config_store.py` - Mongo-backed bot definitions, draft and
  published versions, templates, catalogs, runtime settings.
- `voicebot_platform/livekit_sessions.py` - WebRTC test room creation/close
  logic for dashboard test calls.
- `voicebot_platform/observability.py` - Langfuse and Mongo call-event helpers.
- `callback_worker/` - post-call analysis, callback delivery, recording lookup,
  verified transcript pipeline.
- `frontend/` - React/Vite dashboard.
- `docs/` - architecture, runtime schema, frontend/backend handoff, and server
  safety documentation.
- `ops/` - safe staging deploy and observability infra helpers.

## Source Of Truth

Bot configuration lifecycle:

1. PM edits config in the dashboard.
2. API saves a draft in `tbl_ai_vb_bot_versions`.
3. Publishing creates the next immutable active version.
4. `bot.py` fetches the active version once when a LiveKit room starts.
5. The fetched config is copied into each transcript as `config_snapshot`.

Never break immutable per-call snapshots. Live calls must finish on the config
they started with; new published versions should affect only future calls.

Transcript and observability responsibilities:

- `tbl_ai_vb_call_transcripts` is the conversation and analysis record.
- `tbl_ai_vb_call_events` is the operational call timeline.
- Langfuse is for model, prompt, latency, and trace observability.
- Server log files are fallback diagnostics, not the product UI source.
- Verified transcripts come from recording-derived post-call STT when available.
  Gemini Live transcript is useful for real-time behavior but is not always the
  final source of truth.

## Common Commands

Install Python dependencies:

```bash
uv sync
```

Seed default bot/catalog data:

```bash
uv run python -m voicebot_platform.seed_default
```

Start API locally or on safe staging:

```bash
./start_api.sh
```

Start LiveKit worker:

```bash
./start.sh
```

Start frontend:

```bash
cd frontend
npm install
npm run dev
```

Build frontend:

```bash
cd frontend
npm run build
```

Run backend tests:

```bash
uv run pytest tests/ -q
```

Quick syntax check for backend runtime files:

```bash
uv run python -m py_compile bot.py callback_worker/recording.py callback_worker/worker.py callback_worker/analysis.py voicebot_platform/observability.py voicebot_platform/config_store.py
```

Safe server deploy to the staging checkout:

```bash
./ops/deploy_safe_server.sh
```

## Server Safety Rules

There are live voicebot processes on the same server as the safe dashboard
staging copy. Do not disturb live services while working on the dashboard branch.

Known safe staging checkout:

```txt
/home/yogeshv_10011835/voicebot_nodcode_platform_ai_mgmt
```

Known live/current runtime checkout:

```txt
/home/yogeshv_10011835/voicebot_nodcode_platform
```

Dashboard/safe branch:

```txt
ai_voice_bot_management
```

Live branch reported by engineering:

```txt
main-temp
```

Safe staging ports:

```txt
8010  FastAPI dashboard backend
8091  LiveKit test worker
5173  React/Vite frontend preview
5174  Alternate React/Vite frontend preview
```

Avoid these unless engineering explicitly schedules it:

```txt
8000  Existing backend/dashboard service
8081  Existing bot worker port
8082  Existing bot worker port
8083  Existing bot worker port
```

Safe test worker name:

```txt
voice-bot-justdial-test
```

Never edit, restart, or deploy the live folder/ports without explicit approval
from the program owner and engineering. Prefer read-only checks first:

```bash
ss -lntp | grep -E '8000|8010|8081|8082|8083|8091|5173|5174'
curl -s http://127.0.0.1:8010/health
curl -s http://127.0.0.1:8010/health/ready | python3 -m json.tool
```

## Environment And Secrets

Keep secrets out of git. Use `.env` on the server/local machine and only
placeholder values in `.env.example`.

Important env vars:

- `MONGO_URI`
- `VOICEBOT_PLATFORM_DB`
- `MONGO_SERVER_SELECTION_TIMEOUT_MS`
- `VOICEBOT_ENV`
- `LIVEKIT_URL`
- `LIVEKIT_API_URL`
- `LIVEKIT_BROWSER_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `LIVEKIT_AGENT_NAME`
- `GEMINI_LIVE_API_KEY` or `GOOGLE_API_KEY`
- `LANGFUSE_ENABLED`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_BASE_URL`
- `DASHBOARD_ORIGINS`

Langfuse US Cloud base URL:

```txt
https://us.cloud.langfuse.com
```

Langfuse env vars configure capability. The Mongo-backed dashboard setting
controls whether tracing is enabled at runtime.

Do not add TTL/delete behavior to audit logs, call transcripts, or call events
unless the user explicitly approves a retention policy.

## Mongo Collections

The platform uses the `ai_voice_bot_management` database in staging. Important
collections use the `tbl_ai_vb_` prefix:

- `tbl_ai_vb_bot_definitions`
- `tbl_ai_vb_bot_versions`
- `tbl_ai_vb_campaigns`
- `tbl_ai_vb_call_transcripts`
- `tbl_ai_vb_call_events`
- `tbl_ai_vb_callback_deliveries`
- `tbl_ai_vb_phrase_library`
- `tbl_ai_vb_outcome_catalog`
- `tbl_ai_vb_language_catalog`
- `tbl_ai_vb_language_settings`
- `tbl_ai_vb_voice_catalog`
- `tbl_ai_vb_platform_settings`
- `tbl_ai_vb_audit_logs`

If transcripts are missing, first verify whether the worker wrote to the wrong
database/collection, whether the worker joined the LiveKit room, and whether the
dashboard API can reach Mongo from its process.

## Recording And Verified Transcript Flow

For production/outbound calls, the callback worker can fetch call recording
metadata from the JustDial recording API. The API may return multiple rows for a
mobile number; prefer an answered row with a non-empty `media_path` closest to
the call time.

Recording config is bot-specific where possible:

- `recording.service_id`
- `recording.dialer_city`

Store both live and verified transcript metadata when available:

- `live_transcript`
- `verified_transcript`
- `transcript_source`
- `verified_transcript_status`
- `transcript_quality_flags`

Outcome analysis should prefer verified transcript when available and fall back
to live transcript only when verification is unavailable or failed.

## Coding Guidelines

- Prefer the existing patterns and helper modules before adding new frameworks.
- Keep blocking Mongo/network work out of the LiveKit event loop. Use async APIs,
  executors, or background tasks where the surrounding code already does so.
- Runtime errors must not crash active calls if a non-critical write fails.
  Swallow observability/write failures after logging a clear warning/event.
- Add Pydantic validation for API payloads.
- Return helpful 4xx/5xx errors from APIs; avoid opaque "Failed to fetch" UX.
- Preserve frontend graceful degradation: cached fallback data, retry buttons,
  panel-level errors, and no dead-end buttons.
- Keep UI controls PM-friendly. If a setting affects test calls, expose it in
  Settings instead of requiring SSH edits.
- Do not create broad refactors while fixing production blockers.
- Do not revert user changes in a dirty worktree.

## Testing Expectations

For backend changes:

- Run focused tests first, then `uv run pytest tests/ -q` when practical.
- Add tests for new API filters, schema validation, Mongo fallback behavior, and
  transcript/call-event writes.
- For call runtime changes, also run a safe WebRTC test call on the staging
  worker and check `tbl_ai_vb_call_transcripts`, `tbl_ai_vb_call_events`, and
  Langfuse traces.

For frontend changes:

- Run `cd frontend && npm run build`.
- Open the dashboard locally and verify the affected flow.
- Test error states, empty states, loading states, and refresh/retry behavior.

For server validation:

- Use port `8010` for API and `8091` for test worker.
- Confirm the test session response returns `agent_name:
  voice-bot-justdial-test`.
- Confirm worker logs show a matching `registered worker` and `[CALL START]`.

## Known Runtime Failure Modes

These are high-value issues to keep in mind when reviewing call quality work:

- If the user speaks during the greeting while Gemini audio is muted, buffered
  Sarvam text must be forwarded to Gemini once the greeting window closes. Guard
  against duplicate injection and emit call events for this path.
- If Gemini transcribes a final user turn but does not produce a response, a
  bot-response watchdog should retry once and emit latency/failure events. Avoid
  infinite reinjection loops.
- Reducing greeting early-unmute timing can improve capture of mid-greeting user
  speech, but test for echo, interruption artifacts, and duplicate turns.
- Clear greeting-related speaking timestamps before measuring first user-turn
  latency. Negative latency values should be treated as instrumentation bugs.

## Git And Review Guidelines

- Check `git status --short` before editing. User changes may already exist.
- Make atomic commits that include only related files.
- Do not commit `.env`, logs, recordings, large generated artifacts, or secret
  values.
- Do not push or deploy to live without explicit approval.
- If pulling from GitLab and the user says "accept incoming", confirm the target
  branch and limit conflict resolution to the intended branch.
- For code review, lead with bugs, regressions, risks, and missing tests.

## Useful Docs

- `docs/platform_v1.md` - platform architecture and API overview.
- `docs/runtime_config_schema.md` - dashboard/runtime config contract.
- `docs/server_safety_rules.md` - safe staging vs live server rules.
- `docs/local_dashboard_to_safe_backend.md` - local frontend to safe backend.
- `docs/frontend_backend_fastapi_handoff.md` - integration handoff.
- `docs/livekit_langfuse_ops_checklist.md` - observability operations.
