# Frontend Server Setup

This document explains why Node.js exists on the staging server and how to use it safely.

## What Node Is For

Node is only for the React dashboard in `frontend/`.

Use it to:

- Install frontend packages.
- Build the dashboard into static files.
- Run a staging/dev preview of the dashboard.

Node is not required for:

- The Python FastAPI backend.
- The LiveKit voicebot worker.
- Gemini, Sarvam, Langfuse, or MongoDB.
- Existing live voice calls.

## Installed Server Version

On the staging server, Node was installed under the SSH user's home directory:

```txt
~/.local/node-v20.19.5-linux-x64
```

Symlinks were added in:

```txt
~/bin/node
~/bin/npm
~/bin/npx
```

If `node` is not found after SSH login, run:

```bash
source ~/.bashrc
```

Expected:

```bash
node -v
npm -v
```

```txt
v20.19.5
10.8.2
```

## Safe Build Command

From the project root:

```bash
./build_frontend.sh
```

This runs:

```bash
cd frontend
npm install
npm run build
```

The output is created in:

```txt
frontend/dist
```

These are static browser files. They can later be served by Nginx, FastAPI static hosting, or another internal web server.

## Safe Staging Preview Command

From the project root:

```bash
FRONTEND_PORT=5173 ./start_frontend.sh
```

Open:

```txt
http://<server-ip>:5173
```

Use this only for staging/testing. For production, prefer serving the built `frontend/dist` directory through a proper web server.

## Recommended Ports

Use:

- `8010` for the safe FastAPI backend.
- `8091` for the safe LiveKit test worker.
- `5173` or `5174` for frontend staging preview.

Avoid:

- `8081`, `8082`, `8083` because existing bot workers have used these ports.
- `8000` because another dashboard/backend service has used it on the server.

## Common Checks

Check Node:

```bash
source ~/.bashrc
node -v
npm -v
```

Build frontend:

```bash
./build_frontend.sh
```

Run frontend preview:

```bash
FRONTEND_PORT=5173 ./start_frontend.sh
```

Stop preview:

```bash
Ctrl+C
```

## Product Note

Installing Node does not by itself change the dashboard UX. It makes frontend deployment and staging repeatable from the server, so the team is not dependent on a developer laptop to build or preview the React app.
