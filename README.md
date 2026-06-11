# light-claw

> [!WARNING]
> This ENTIRE project is vibe coded (except for this sentence).
> Do not use it.
>
> Also, this is distributed as SOURCE ONLY. There are no binaries.
> You need to tune it for yourself. If unsure, just ask an LLM.

Local agent OS — Python reimplementation of [ironclaw](https://github.com/nearai/ironclaw) core concepts. Runs an agentic loop against any OpenAI-compatible endpoint: llama.cpp, OpenAI, Anthropic (via proxy), or any compatible server.

> **Data retention notice:** light-claw is designed for use with models that have **zero data retention** (local models via llama.cpp, self-hosted endpoints, or API tiers with no-retention agreements). Using this project with models that do retain data is **not recommended** — conversation history, notes, and tool outputs may contain sensitive information that should not leave your machine.

## Features

- **Agentic loop** — LLM drives tool calls until task complete
- **Persistent memory** — SQLite + FTS5 workspace: conversation history and searchable notes
- **Shell tool** — gated behind a two-tier approval system (denylist + interactive whitelist)
- **MCP support** — attach any stdio MCP server; tools appear automatically
- **Scheduler** — cron and interval jobs via APScheduler
- **Discord channel** — DM or @mention the bot; responses streamed back
- **Extensible** — `@tool` decorator auto-registers functions into the tool registry

## Install

Requires Python 3.12+. Uses [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/youruser/light-claw
cd light-claw
cp .env.example .env   # edit with your endpoint/key/model
uv run lightclaw
```

## Usage

```bash
# Interactive REPL
uv run lightclaw

# One-shot message
uv run lightclaw run "summarise my notes"

# Discord bot
DISCORD_BOT_TOKEN=... uv run lightclaw discord

# MCP management
uv run lightclaw mcp add filesystem --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --arg /Users/you
uv run lightclaw mcp list
uv run lightclaw mcp tools filesystem
uv run lightclaw mcp remove filesystem
```

## Configuration

All config via `.env` (auto-loaded) or environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `LIGHTCLAW_BASE_URL` | `http://localhost:8080/v1` | OpenAI-compat endpoint |
| `LIGHTCLAW_API_KEY` | `local` | API key (`local` for llama.cpp) |
| `LIGHTCLAW_MODEL` | `local-model` | Model name |
| `LIGHTCLAW_DB` | `~/.config/lightclaw/workspace.db` | SQLite path |
| `DISCORD_BOT_TOKEN` | — | Discord bot token |
| `XDG_CONFIG_HOME` | `~/.config` | Config/data root |

MCP server configs live at `~/.config/lightclaw/mcp.json`. All state stays in `~/.config/lightclaw/`.

## Architecture

```
lightclaw/
├── config.py               Config dataclass, XDG path resolution, .env loading
├── llm/client.py           Async OpenAI-compat wrapper (LLMClient)
├── memory/workspace.py     SQLite + FTS5: conversation history + searchable notes
├── tools/
│   ├── registry.py         Tool registry, @tool decorator, JSON schema auto-gen
│   ├── builtins.py         Built-in tools: memory_set/get/search, shell (gated)
│   └── shell_guard.py      Two-tier shell gate: denylist + interactive whitelist
├── agent/loop.py           Agentic loop: LLM → tool calls → loop until done
├── mcp/manager.py          MCP server manager: add/connect/teardown stdio servers
├── scheduler/engine.py     APScheduler cron + interval jobs
├── channels/discord_bot.py Discord channel: DM + @mention → agent → reply
└── repl/cli.py             Typer CLI + Rich REPL
```

## Safety

### Shell tool

The shell tool is **off by default**. When enabled, a two-tier gate controls execution:

1. **Unconditional denylist** — `rm`, `sudo`, `dd`, `mkfs`, `kill`, `nc`, `chmod`, `crontab`, shell operators (`;`, `|`, `&`, `` ` ``), `$()`, `eval`, `exec`, and redirects to system paths are hardcoded-blocked and cannot be whitelisted.

2. **Interactive approval** — all other commands prompt before first run:
   ```
   [shell] Agent wants to run:
     git status
     [y] run once  [n] deny  [a] always allow  [b] always block
   ```

Whitelist/blocklist persist to `~/.config/lightclaw/shell_whitelist.json`. In non-interactive contexts (Discord, scheduler, piped input) the tool auto-denies.

Additional guards: `shell=False` (no injection via unquoted args), protected path rejection (`/etc`, `/sys`, `/dev`, `/proc`), stripped subprocess env, 30s timeout, 4 KiB output cap.

## Extending

### Custom tools

```python
from lightclaw.tools import tool

@tool(description="Fetch current weather for a city.")
def get_weather(city: str) -> str:
    ...
```

Import your module before starting the REPL — the tool auto-registers.

### MCP servers

```bash
# Filesystem access
lightclaw mcp add fs --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --arg /Users/you

# SQLite read access
lightclaw mcp add db --command npx --arg -y --arg @modelcontextprotocol/server-sqlite --arg /path/to/db.sqlite

# Git tools
lightclaw mcp add git --command uvx --arg mcp-server-git
```

MCP tools appear prefixed `mcp__<server>__<tool>` in `/tools`. MCP servers run as child processes under your user — review server source before adding.

## Key design decisions

- **OpenAI-compat only** — single LLM interface works with llama.cpp, OpenAI, Anthropic proxy, or any compatible server
- **SQLite not Postgres** — zero-config, file-based, portable; FTS5 for full-text search
- **Shell tool off by default** — explicit opt-in required; safety guards always active when on
- **MCP for extensibility** — prefer MCP tools over built-in shell for external integrations
- **XDG config dir** — all state in `~/.config/lightclaw/`, no home dir clutter

## License

[GPL v3](LICENSE)
