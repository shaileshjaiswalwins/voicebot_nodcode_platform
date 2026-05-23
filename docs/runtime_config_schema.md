# Runtime Config Schema

This document is the contract between the dashboard (where PMs edit a bot) and `bot.py` (where the LiveKit worker reads the published config at call start).

The authoritative validation lives in `voicebot_platform/schemas.py` (`RuntimeConfigModel`). Every field below mirrors that model. The dashboard form should never write a value that would fail this schema.

## Lifecycle

```
PM edits config in dashboard → POST /api/bots/{id}/draft  →  validated by RuntimeConfigModel
                            → POST /api/bots/{id}/publish  →  becomes the active version
                            → next call's entrypoint fetches and snapshots it into tbl_ai_vb_call_transcripts.config_snapshot
```

The snapshot is immutable per-call. Live calls always finish on the snapshot they started with; only new calls see a freshly published config.

## Fields

| Field | Type | Constraints | Notes |
|---|---|---|---|
| `assistant_id` | string | optional | Backend overrides this on save; do not rely on what the form sends. |
| `organization_id` | string | optional | Free text. |
| `model` | string | must be in `ALLOWED_MODELS` | Allowlist in `schemas.py`. Add new Gemini models there. |
| `voice` | string | optional | Voice id from `tbl_ai_vb_voice_catalog`. |
| `language` | string | optional | Language id from `tbl_ai_vb_language_catalog`. |
| `livekit_language` | string | optional | LiveKit locale code, e.g. `hi-IN`. Defaults from language entry. |
| `sarvam_language` | string | optional | Sarvam STT locale code. Defaults to `livekit_language`. |
| `temperature` | number | 0.0–2.0 | Gemini sampling temperature. |
| `max_call_duration` | int | 30–1800 sec | Hard call timeout. |
| `system_prompt` | string | ≤ 32 KB UTF-8 | The full role prompt sent to Gemini. |
| `initial_message` | string | ≤ 4 KB | First spoken line. Supports `{product}` template variable. |
| `call_end_text` | string | ≤ 4 KB | Closing sentence the bot says when the qualification finishes. |
| `function_calling` | bool | optional | Whether the bot may call the `functions` list below. |
| `gemini_silence_duration_ms` | int | 100–10000 | VAD: silence required before commit. |
| `gemini_prefix_padding_ms` | int | 0–5000 | VAD: pre-roll padding. |
| `post_speech_hold_ms` | int | 0–10000 | Pause after bot finishes speaking before opening the mic. |
| `sarvam_min_rms` | int | 0–20000 | Sarvam: minimum RMS for VAD. |
| `functions` | array | ≤ 20 entries | Function-tool definitions for Gemini. |

Additional keys (any not listed above) are preserved in Mongo but not validated. Treat them as developer-only knobs and document them here when they graduate to PM-editable.

## Read paths

| Where | Reads what | Refresh |
|---|---|---|
| `bot.py` entrypoint | active published config via `fetch_active_bot_config(assistant_id)` | per call |
| `callback_worker.analysis` | disposition map + voicemail/hold phrases | 60s in-process cache |
| Dashboard Builder | active + draft via `GET /api/bots/{id}` | per refresh / per save |

## What's editable from the dashboard

| Catalog | Mongo collection | Editable in UI |
|---|---|---|
| Bot prompt, voice, language, VAD | `tbl_ai_vb_bot_versions.config` | Builder + JSON panel |
| Phrase library (voicemail, hold-music, DNC) | `tbl_ai_vb_phrase_library` | Library → Phrases |
| Call outcome descriptions | `tbl_ai_vb_outcome_catalog` | Library → Outcomes |
| Voices catalog | `tbl_ai_vb_voice_catalog` | API only (UI follow-up) |
| Languages catalog | `tbl_ai_vb_language_catalog` | API only (UI follow-up) |
| Per-language settings | `tbl_ai_vb_language_settings` | Library → Languages |
| Langfuse settings | `tbl_ai_vb_platform_settings:langfuse` | Observability |
| LiveKit runtime URLs/agent name | `tbl_ai_vb_platform_settings:runtime` | Settings |

## What is NOT editable from the dashboard

These need a code deploy:

- `ALLOWED_MODELS` in `schemas.py` — adding a new Gemini model.
- The 18 outcome `key` values themselves — downstream callback logic in `callback.py:48` uses key strings.
- LiveKit / Mongo / Langfuse credentials (kept in `.env`).
- VOICEBOT_ENV and DASHBOARD_ORIGINS.
- Allowlist of users (until SSO ships).
