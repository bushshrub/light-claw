# REPL connectors

- Connectors (discord, signal, web) are background asyncio tasks managed by `_ConnectorManager`.
- `enable(name, cfg, workspace, registry)` receives the REPL's live workspace and registry —
  pass them through to any service that needs shared state (e.g. web server).
- Connectors with a uvicorn `Server` are stopped via `server.should_exit = True`, not `bot.close()`.
  The entry dict uses key `"server"` vs `"bot"` to distinguish — check this in `disable()` and `stop_all()`.
- `_CONNECTOR_NAMES` tuple controls what appears in `/connectors list` and tab completion.
- `LIGHTCLAW_WEB_HOST` / `LIGHTCLAW_WEB_PORT` env vars override web connector bind address.
