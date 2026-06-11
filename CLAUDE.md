# light-claw

Local agent OS — Python reimplementation of [ironclaw](https://github.com/nearai/ironclaw) core concepts.
Runs against any OpenAI-compatible endpoint (llama.cpp, OpenAI, etc.).

## Architecture

```
lightclaw/
├── config.py          Config dataclass, XDG path resolution, .env loading
├── llm/client.py      Async OpenAI-compat wrapper (LLMClient)
├── memory/
│   └── workspace.py   SQLite + FTS5: conversation history + searchable notes
├── tools/
│   ├── registry.py    Tool registry, @tool decorator, JSON schema auto-gen
│   └── builtins.py    Built-in tools: memory_set/get/search, shell (gated)
├── agent/loop.py      Agentic loop: LLM → tool calls → loop until done
├── mcp/manager.py     MCP server manager: add/connect/teardown stdio servers
├── scheduler/engine.py APScheduler cron + interval jobs
├── channels/
│   └── discord_bot.py Discord channel: DM + @mention → agent → reply
└── repl/cli.py        Typer CLI + Rich REPL
```

## Running

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

All config via `.env` (loaded automatically) or environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `LIGHTCLAW_BASE_URL` | `http://localhost:8080/v1` | OpenAI-compat endpoint |
| `LIGHTCLAW_API_KEY` | `local` | API key (`local` for llama.cpp) |
| `LIGHTCLAW_MODEL` | `local-model` | Model name |
| `LIGHTCLAW_DB` | `~/.config/lightclaw/workspace.db` | SQLite path |
| `DISCORD_BOT_TOKEN` | — | Discord bot token |
| `XDG_CONFIG_HOME` | `~/.config` | Config/data root |

MCP server configs live at `~/.config/lightclaw/mcp.json`.

## Safety

### Shell tool

Works like Claude Code's permission system: approval-based with a persistent whitelist.

**Two-tier gate** (implemented in `lightclaw/tools/shell_guard.py`):

1. **Unconditional denylist** — `rm`, `sudo`, `dd`, `mkfs`, `kill`, `nc`, `chmod`, `crontab`, and others are hardcoded-blocked and cannot be whitelisted. Shell operators (`;`, `|`, `&`, `` ` ``), `$()`, `eval`, `exec`, and redirects to system paths are also unconditionally blocked.

2. **User approval prompt** — all other commands require interactive approval before first run:
   ```
   [shell] Agent wants to run:
     git status
     [y] run once  [n] deny  [a] always allow  [b] always block
   ```
   - `y` — run once, ask again next time
   - `a` — add to whitelist, run without prompting in future
   - `b` — add to blocklist, permanently deny this command
   - `n` / default — deny this call

Whitelist and blocklist persist to `~/.config/lightclaw/shell_whitelist.json`. In non-interactive contexts (Discord, scheduler, piped input) the tool auto-denies — no silent shell access without a user present.

Additional guards always active:
- **`shell=False`** — `shlex.split()` + list args, prevents injection via unquoted arguments
- **Protected path args** — arguments under `/etc`, `/sys`, `/dev`, `/proc` rejected
- **Minimal subprocess env** — only `PATH` and `HOME` passed; `LD_PRELOAD` etc. stripped
- **30s timeout**, **4 KiB output cap**

### Agent loop

- Tool calls are executed locally; no remote code execution
- MCP servers run as child processes under your user — review server source before adding
- Conversation history stored in local SQLite only

## Extending with tools

```python
from lightclaw.tools import tool

@tool(description="Fetch current weather for a city.")
def get_weather(city: str) -> str:
    ...
```

Import your module before starting the REPL and the tool auto-registers.

## MCP servers

Any MCP-compatible server (stdio transport) can be added:

```bash
# Filesystem access
lightclaw mcp add fs --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --arg /Users/you

# SQLite read access
lightclaw mcp add db --command npx --arg -y --arg @modelcontextprotocol/server-sqlite --arg /path/to/db.sqlite

# Git tools
lightclaw mcp add git --command uvx --arg mcp-server-git
```

MCP tools appear prefixed `mcp__<server>__<tool>` in `/tools`.

## Key design decisions

- **OpenAI-compat only** — single LLM interface works with llama.cpp, OpenAI, Anthropic (via proxy), or any compatible server
- **SQLite not Postgres** — zero-config, file-based, portable; FTS5 for full-text search
- **Shell tool off by default** — explicit opt-in required; safety guards active when on
- **MCP for extensibility** — prefer MCP tools over built-in shell for external integrations
- **XDG config dir** — all state in `~/.config/lightclaw/`, no home dir clutter
