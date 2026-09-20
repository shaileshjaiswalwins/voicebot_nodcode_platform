# Server Safety Rules

This project has live voicebot processes on the same server as the dashboard staging copy. These rules prevent accidental impact on live calls.

## Golden Rule

Do not edit, restart, or deploy to the live folder unless the program head explicitly confirms a live deploy window.

## Known Folders

Safe staging folder:

```txt
/home/yogeshv_10011835/voicebot_nodcode_platform_ai_mgmt
```

Live/current runtime folder:

```txt
/home/yogeshv_10011835/voicebot_nodcode_platform
```

Any work for the dashboard branch should happen in the safe staging folder only.

## Known Branches

Dashboard branch:

```txt
ai_voice_bot_management
```

Live branch reported by engineering:

```txt
main-temp
```

Treat `main-temp` as the live production branch until engineering confirms otherwise.

## Safe Ports

Use these for dashboard staging:

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

## Safe Worker Name

Use this worker for test calls from the dashboard:

```txt
voice-bot-voicedesk-test
```

Do not point dashboard tests at the live worker unless engineering explicitly approves.

## Deploying To Safe Staging

From local Mac repo:

```bash
cd /Users/voicedesk/voicebot_nodcode_platform
git checkout ai_voice_bot_management
git status --short
./ops/deploy_safe_server.sh
```

The script refuses to deploy unless:

- The current branch is `ai_voice_bot_management`.
- The local worktree is clean.
- The target path ends with `voicebot_nodcode_platform_ai_mgmt`.

The script does not restart live services.

## Live Deploy Policy

Before any live deploy:

1. Confirm the target branch with engineering.
2. Confirm the target folder with engineering.
3. Confirm the call schedule and deploy window.
4. Confirm rollback steps.
5. Get explicit approval from the program head.

The safe staging deploy script intentionally refuses live deploys. A separate release script should be created only after the team agrees on production deployment ownership.

## Quick Server Checks

Check listening ports:

```bash
ss -lntp | grep -E '8000|8010|8081|8082|8083|8091|5173|5174'
```

Check safe API:

```bash
curl -s http://127.0.0.1:8010/health
curl -s http://127.0.0.1:8010/health/ready | python3 -m json.tool
```

Check safe test worker:

```bash
grep 'registered worker' /home/yogeshv_10011835/voicebot_nodcode_platform_ai_mgmt/logs/worker-test-8091.log | tail -1
```
