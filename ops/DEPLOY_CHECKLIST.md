# Deploy Checklist

Run through this top-to-bottom every time you deploy to a new server or after major changes.

---

## 1. On the server — gather facts first

Run these commands and note the outputs. You'll need them to fill in the config files.

```bash
whoami                          # → your Linux username (replaces "voicedesk")
pwd                             # → confirm you're in the right directory
which uv                        # → full path to uv (replaces /Users/voicedesk/.local/bin/uv)
python3 --version               # → should be 3.10+
cat /etc/os-release | head -3   # → Ubuntu/Debian/RHEL — affects log dir creation
```

---

## 2. Clone and set up the repo

```bash
git clone <repo-url> /home/<user>/voicebot_nodcode_platform
cd /home/<user>/voicebot_nodcode_platform
uv sync                         # installs all Python deps from uv.lock
```

---

## 3. Update ops/supervisord.conf for this server

Three values to change — nothing else:

| What | Find | Replace with |
|------|------|--------------|
| Working directory | `/Users/voicedesk/voicebot_nodcode_platform` | `/home/<user>/voicebot_nodcode_platform` |
| uv path (×3 commands) | `/Users/voicedesk/.local/bin/uv` | output of `which uv` |
| HOME env var (×3) | `HOME="/Users/voicedesk"` | `HOME="/home/<user>"` |

```bash
# Quick sed replacement — substitute your actual values:
USER=deploy
UV_PATH=$(which uv)
sed -i "s|/Users/voicedesk/voicebot_nodcode_platform|/home/$USER/voicebot_nodcode_platform|g" ops/supervisord.conf
sed -i "s|/Users/voicedesk/.local/bin/uv|$UV_PATH|g" ops/supervisord.conf
sed -i "s|HOME=\"/Users/voicedesk\"|HOME=\"/home/$USER\"|g" ops/supervisord.conf
```

---

## 4. Update ops/voicebot-worker.service for this server

Same substitutions as above, plus one more:

```bash
USER=deploy
UV_PATH=$(which uv)
sed -i "s|User=voicedesk|User=$USER|g" ops/voicebot-worker.service
sed -i "s|/Users/voicedesk/voicebot_nodcode_platform|/home/$USER/voicebot_nodcode_platform|g" ops/voicebot-worker.service
sed -i "s|/Users/voicedesk/.local/bin/uv|$UV_PATH|g" ops/voicebot-worker.service
```

---

## 5. Create the .env file

```bash
cp .env.example .env            # if you have one, otherwise create from scratch
nano .env
```

Required variables — confirm every one is set:

```
VOICEBOT_ENV=production
VOICEBOT_PLATFORM_DB=ai_voice_bot_management
MONGO_URI=mongodb://192.168.13.65:27017   # or your server's MongoDB
LIVEKIT_URL=ws://192.168.41.116:7880
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_AGENT_NAME=voice-bot-voicedesk
GEMINI_API_KEY=...
SARVAM_API_KEY=...
MIS_API_BASE=http://192.168.8.67:8000
DASHBOARD_ORIGINS=http://<server-ip>
```

```bash
chmod 600 .env                  # prevent other users reading it
```

---

## 6. Create log directory

supervisord will fail silently if this doesn't exist:

```bash
sudo mkdir -p /var/log/voicebot
sudo chown $(whoami):$(whoami) /var/log/voicebot
```

---

## 7. Build the frontend

```bash
cd frontend
npm ci                          # clean install from package-lock.json
npm run build                   # outputs to frontend/dist/
cd ..
```

---

## 8. Smoke-test before starting supervisord

Run each process manually for 10 seconds to confirm no startup crashes:

```bash
# Test API starts cleanly
uv run uvicorn voicebot_platform.api:app --host 0.0.0.0 --port 8000
# Ctrl+C after "Application startup complete." appears

# Test bot worker starts cleanly
uv run python bot.py start
# Ctrl+C after "Agent registered" or similar appears
```

---

## 9. Install and start supervisord

```bash
# Install supervisor if not present
pip install supervisor           # or: apt install supervisor

# Start everything
supervisord -c ops/supervisord.conf

# Confirm all three processes are RUNNING (not STARTING or FATAL)
supervisorctl -c ops/supervisord.conf status
```

Expected output:
```
voicebot:voicebot-api       RUNNING   pid 1234, uptime 0:00:05
voicebot:voicebot-worker    RUNNING   pid 1235, uptime 0:00:05
voicebot:voicebot-callback  STOPPED   Not started   ← OK, autostart=false
```

---

## 10. Verify the API is live

```bash
curl http://localhost:8000/api/bots
# Should return [] or a list of bots — not a connection error
```

---

## 11. Verify the dashboard loads

Open `http://<server-ip>:8000` in a browser.
- Agents tab loads
- Campaigns tab loads
- No red diagnostic banners

---

## Common failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `FATAL: can't chdir to directory` | Wrong `directory=` path in supervisord.conf | Re-run step 3 |
| `FATAL: command not found: uv` | Wrong `command=` path | Re-run step 3 with correct `which uv` output |
| API starts then immediately exits | Missing .env variable | Check `stderr_logfile` in `/var/log/voicebot/api-err.log` |
| Bot worker keeps restarting | MongoDB unreachable or bad LIVEKIT creds | Check `/var/log/voicebot/bot-worker-err.log` |
| Dashboard loads but shows no data | API running on wrong port or CORS blocked | Check `DASHBOARD_ORIGINS` in .env matches the URL you're using |
| `/var/log/voicebot` permission denied | Log dir owned by root | Re-run step 6 with correct `chown` |

---

## Useful day-to-day commands

```bash
# Check status
supervisorctl -c ops/supervisord.conf status

# Restart one process after a code change
supervisorctl -c ops/supervisord.conf restart voicebot:voicebot-api

# Restart everything
supervisorctl -c ops/supervisord.conf restart all

# Tail live logs
tail -f /var/log/voicebot/bot-worker.log
tail -f /var/log/voicebot/api-err.log

# Stop everything cleanly
supervisorctl -c ops/supervisord.conf shutdown
```
