# LiveKit And Langfuse Ops Checklist

## LiveKit Server Reachability From Docker

Run these commands after SSH-ing into the server where you plan to run `livekit-monitor`.

```bash
# 1. Confirm Docker works
docker --version
docker ps

# 2. Confirm DNS or IP route to LiveKit
ping -c 3 <LIVEKIT_HOST_OR_IP>

# 3. Confirm the LiveKit API/WebSocket port is reachable
nc -vz <LIVEKIT_HOST_OR_IP> <LIVEKIT_PORT>

# 4. If the LiveKit URL is HTTPS/WSS, confirm TLS and HTTP response
curl -vk https://<LIVEKIT_HOST_OR_IP>:<LIVEKIT_PORT>

# 5. Confirm a container can also reach the same host and port
docker run --rm curlimages/curl:8.8.0 -vk https://<LIVEKIT_HOST_OR_IP>:<LIVEKIT_PORT>
```

Replace:

- `<LIVEKIT_HOST_OR_IP>` with the LiveKit server IP or hostname.
- `<LIVEKIT_PORT>` with the public LiveKit HTTPS/WSS port. Common choices are `443`, `7880`, or your internal reverse-proxy port.

If LiveKit is exposed as `ws://` instead of `wss://`, use:

```bash
curl -v http://<LIVEKIT_HOST_OR_IP>:<LIVEKIT_PORT>
docker run --rm curlimages/curl:8.8.0 -v http://<LIVEKIT_HOST_OR_IP>:<LIVEKIT_PORT>
```

## Langfuse Environment Variables

Keep real keys in `.env`, never in Git.

```bash
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=<public-key>
LANGFUSE_SECRET_KEY=<secret-key>
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
VOICEBOT_ENV=local
```

Use `VOICEBOT_ENV=local`, `VOICEBOT_ENV=staging`, or `VOICEBOT_ENV=prod`.

The dashboard Observability page can enable or disable Langfuse without editing code. The backend still needs the keys in `.env`.
