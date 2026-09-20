# LiveKit Native AcmeCorp Bot + No-Code Platform

Standalone LiveKit-Agents voicebot for AcmeCorp product qualification calls plus a V1 internal no-code platform for PMs and developers. The runtime uses Gemini Live (s2s) via `livekit-agents` natively, while the platform stores bot prompts/settings, versions, campaigns and transcripts in MongoDB.

## Requirements

- Python ≥ 3.10
- [`uv`](https://astral.sh/uv) package manager
- A running LiveKit server (SFU + SIP)

## Setup

```bash
cd livekit_native_bot

# Install dependencies into an isolated venv
uv sync
```

## Environment Variables

Edit `.env` before starting. Key variables:

| Variable | Description |
|---|---|
| `LIVEKIT_URL` | LiveKit server WebSocket URL (e.g. `ws://127.0.0.1:7880`) |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `GEMINI_LIVE_API_KEY` | Google Gemini API key (used as `GOOGLE_API_KEY` internally) |
| `BOT_LANGUAGE` | Default language: `hindi`, `malayalam`, `tamil`, etc. |

## Run

```bash
./start.sh
```

Or manually:

```bash
uv run python bot.py start
```

## Platform API and Dashboard

Seed the current hardcoded bot into MongoDB:

```bash
uv run python -m voicebot_platform.seed_default
```

Start the FastAPI platform service:

```bash
./start_api.sh
```

Start the React dashboard:

```bash
cd frontend
npm install
npm run dev
```

See `docs/platform_v1.md` for API routes, handoff notes, observability setup and the LiveKit/Langfuse monitoring plan.

## Architecture

- **Model**: `gemini-3.1-flash-live-preview` via `livekit.plugins.google.realtime.RealtimeModel`
- **Voice**: Aoede (Google TTS)
- **VAD**: Gemini built-in (START_SENSITIVITY_LOW, END_SENSITIVITY_HIGH, 800ms silence)
- **SIP**: Joins LiveKit rooms dispatched from the SIP trunk; reads `sip.phoneNumber` from participant attributes
- **Function tools**: `FetchLead` and `FetchCategorySchema` via MIS API
- **Call log**: Saved to backend MongoDB after call ends
- **Recording**: Written to `call_records/recording_<room>.wav`
- **No-code config**: Active published bot versions are fetched from MongoDB by `assistant_id`; active calls keep their startup config snapshot.
- **Post-call judging**: Gemini classifies the call against a 19-outcome disposition rubric. An optional second judge (TypeSafe Jev) returns a probability per outcome and a per-question answered/not-answered signal, off by default — see [JEV_SECOND_OPINION.md](JEV_SECOND_OPINION.md) for the design, tradeoffs and open questions.

## Notes

- `gemini-3.1-flash-live-preview` does not support `generate_reply()` or `update_instructions()` mid-session. Greeting, inactivity nudges, and timeout messages are pre-composed localized strings spoken via `session.say()`.
- This bot is designed to run standalone on a separate server from the Pipecat bot.
