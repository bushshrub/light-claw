# Web server

- FastAPI server lives in `lightclaw/web/server.py`.
- Static build is served from `lightclaw-webui/build/` via `StaticFiles`.
- `create_app(cfg, workspace=..., registry=...)` accepts optional shared workspace/registry.
  When started as a REPL connector, always pass the REPL's workspace and registry so both
  interfaces share the same conversation history and memory — do NOT create a new WebSession
  with its own Workspace when the REPL is already running.
- `WebSession._owns_workspace` controls whether `start()`/`stop()` open/close the workspace.
- Web connector uses `uvicorn.Server` + `asyncio.create_task(server.serve())` so it runs on
  the same event loop as the REPL without blocking.
- Standalone `lightclaw web` command: `create_app(cfg)` with no workspace — it owns its own.
