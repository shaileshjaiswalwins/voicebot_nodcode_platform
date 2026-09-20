# Local Dashboard To Safe Backend

Use this when you want the React dashboard on your Mac to talk to the safe staging API on the LiveKit server without touching the live checkout or live ports.

## One-Time Local Frontend Setting

Create `frontend/.env.local`:

```bash
cd /Users/acmecorp/voicebot_nodcode_platform
printf 'VITE_API_BASE=http://localhost:8010\n' > frontend/.env.local
```

This makes the browser call `localhost:8010`. The SSH tunnel below forwards that local port to the safe API running on the server.

## Start The SSH Tunnel

Run this from your Mac after VPN is connected:

```bash
ssh -L 8010:127.0.0.1:8010 yogeshv_10011835@192.168.41.116 -N
```

Expected result:

- The terminal appears to hang. That is correct; it means the tunnel is open.
- Keep this terminal open while using the dashboard.
- If it asks for a password, enter your server password.

In a second terminal, verify the tunnel:

```bash
curl http://localhost:8010/health
```

Expected output:

```json
{"status":"ok"}
```

## Start The Local Dashboard

Run this from your Mac in another terminal:

```bash
cd /Users/acmecorp/voicebot_nodcode_platform/frontend
node node_modules/vite/bin/vite.js --host 0.0.0.0 --port 5173
```

Open:

```txt
http://localhost:5173
```

## Why This Matters

The safe backend is on `192.168.41.116:8010`, but laptops may not be able to reach that port directly. The tunnel lets the browser use `localhost:8010` while the actual API request reaches the safe server.

This avoids:

- accidentally using an old local backend on `localhost:8000`
- accidentally touching the live checkout
- accidentally dispatching test calls to the wrong worker

## Quick Health Checks

```bash
curl http://localhost:8010/api/settings/runtime
curl 'http://localhost:8010/api/transcripts?limit=5'
curl 'http://localhost:8010/api/call-events?limit=5'
```

Expected runtime values:

- `livekit_agent_name` should be `voice-bot-acmecorp-test`
- `livekit_api_url` should be `http://192.168.41.116:7880`
- `livekit_browser_url` should be `ws://192.168.41.116:7880`
