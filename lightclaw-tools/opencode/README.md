# lightclaw-opencode-mcp

MCP server that exposes [opencode](https://opencode.ai) as tools inside a Docker sandbox. Attach it to any lightclaw instance to give the agent a powerful coding sub-agent.

## Prerequisites

- Docker
- [opencode](https://opencode.ai/docs) CLI (only needed for `opencode_run_local`)

## Sandbox image

The sandbox image (`lightclaw-opencode-sandbox`) is built automatically by CI and published to GHCR. Pull it:

```bash
docker pull ghcr.io/<your-org>/lightclaw-opencode-sandbox:latest
```

Set `SANDBOX_IMAGE` in `.env` to point to it:

```
SANDBOX_IMAGE=ghcr.io/<your-org>/lightclaw-opencode-sandbox:latest
```

To build locally instead:

```bash
docker build -f Dockerfile.sandbox -t lightclaw-opencode-sandbox:latest .
```

## Running the server

```bash
cd lightclaw-tools/opencode
cp .env.example .env   # set SANDBOX_IMAGE
uv run python server.py
```

## Wiring into lightclaw

```bash
lightclaw mcp add opencode \
  --command uv \
  --arg run \
  --arg --project \
  --arg /path/to/lightclaw-tools/opencode \
  --arg python \
  --arg server.py
```

Then `lightclaw mcp list` should show `opencode` and `/tools` will include `opencode_run` and `opencode_run_local`.

## Tools

| Tool | Description |
|---|---|
| `opencode_run(task, workspace)` | Run a coding task in the Docker sandbox. `workspace` is an absolute host path mounted into the container. |
| `opencode_run_local(task, cwd?, model?)` | Run opencode directly on the host — no Docker isolation. Only use for trusted tasks. |

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SANDBOX_IMAGE` | `lightclaw-opencode-sandbox:latest` | Docker image to use for sandboxed runs |
