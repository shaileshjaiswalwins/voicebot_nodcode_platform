# JustDial Voice AI No-Code Platform V1

## What Was Added

- `voicebot_platform.api` exposes the internal platform API for bots, versions, campaigns, transcripts, templates, voices and languages.
- `voicebot_platform.config_store` stores bot definitions and immutable bot versions in MongoDB.
- `bot.py` now reads the active published bot version from MongoDB by `assistant_id`; if lookup fails, it falls back to the existing hardcoded config.
- Every new transcript document includes `bot_id`, `bot_version_id`, `bot_version`, `campaign_id` and `config_snapshot`.
- The React dashboard scaffold lives in `frontend/`.
- `ops/docker-compose.observability.yml` gives infra a starting point for `livekit-monitor` and self-hosted Langfuse.

## Local Startup

Install dependencies:

```bash
uv sync
```

Seed the current hardcoded JustDial bot into Mongo:

```bash
uv run python -m voicebot_platform.seed_default
```

Start the API:

```bash
./start_api.sh
```

Start the LiveKit worker:

```bash
./start.sh
```

Start the dashboard:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Mongo Collections

- `bot_definitions`: bot identity, owner, status, active version pointer.
- `bot_versions`: immutable draft/published configs.
- `bot_templates`: reusable starter configs.
- `campaigns`: outbound campaign to bot/lead API mapping.
- `call_transcripts`: existing transcript collection, now enriched with config snapshots.

## Runtime Safety Rule

The worker fetches config once when the LiveKit room starts. That config is copied into `config_snapshot` and used for the full call. Publishing a new version only changes future rooms, so live calls continue on the old prompt/settings.

## API Summary

- `GET /api/bots`
- `POST /api/bots`
- `GET /api/bots/{bot_id}`
- `POST /api/bots/{bot_id}/draft`
- `POST /api/bots/{bot_id}/publish`
- `POST /api/bots/{bot_id}/rollback`
- `POST /api/bots/{bot_id}/duplicate`
- `GET /api/campaigns`
- `POST /api/campaigns`
- `POST /api/bots/{bot_id}/test-session`
- `GET /api/transcripts`
- `GET /api/transcripts/{transcript_id}`
- `GET /api/options/voices`
- `GET /api/options/languages`
- `GET /api/templates`

## Team Handoff

Frontend developer:

- Replace the JSON editor in `frontend/src/main.tsx` with structured controls for prompt, model, voice, language, VAD, tools and publishing.
- Add transcript detail, filters and trace links.
- Wire JustDial SSO headers instead of the local `X-JD-User` placeholder.

Backend developer:

- Confirm the exact lead API request/response shape and add validation around campaign lead API mapping.
- Add role enforcement for PM, Developer, Admin and Viewer once SSO group names are known.
- Add LiveKit room creation for test calls if this platform should dispatch test calls directly.
- Extend Langfuse metadata once production project keys and retention rules are available.

Infra developer:

- Rotate the SIP credentials that were shared in chat.
- Deploy `livekit-monitor`, register its webhook with the on-prem LiveKit server, and confirm room/participant visibility.
- Deploy Langfuse, create project keys, and set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_HOST`.
- Confirm safe concurrent call limits on the single persistent LiveKit server.

## Observability

The runtime emits Langfuse events when credentials are present:

- `call_started`
- `first_audio_received`
- `first_model_response`
- `transcript_saved`
- `callback_sent`
- `callback_failed`
- `call_ended`

For local and staging Pipecat pipeline visualization, add `pipecat-ai-whisker` only to Pipecat-based workers. The current runtime is LiveKit-native, so Whisker is not forced into `bot.py`.
