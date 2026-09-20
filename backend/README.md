## Admin API (Phase 1a/1b)

Run locally:

```
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

First login seeds a hardcoded admin user (`admin@acmecorp.com` / `password`) in the
`tbl_ai_vb_users` Mongo collection on first startup — see `backend/auth.py`.

Env vars (all optional, fall back to `.env` at repo root):
- `MONGO_URI`, `VOICEBOT_PLATFORM_DB` — where bots/versions/settings live.
- `DASHBOARD_JWT_SECRET` — set a real secret in any non-local environment.
- `DASHBOARD_ORIGINS` — comma-separated CORS allowlist for the frontend dev server.

`callback_worker/worker.py` now calls `GET /api/settings/platform/active-endpoints`
(unauthenticated, read-only) once per poll tick to resolve which environment's callback
URL is active, instead of importing a hardcoded constant — see
`callback_worker/config.py:resolve_active_endpoints`. If this API is unreachable, the
worker falls back to the existing `.env`-derived defaults, so a down admin API never
blocks call processing.
