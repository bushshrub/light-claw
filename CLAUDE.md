# light-claw

Local agent OS — Python reimplementation of [ironclaw](https://github.com/nearai/ironclaw) core concepts.
Runs against any OpenAI-compatible endpoint (llama.cpp, OpenAI, etc.).

## Architecture

```
lightclaw/
├── config.py              Config dataclass, XDG path resolution, .env loading
├── log.py                 Structured logging
├── llm/client.py          Async OpenAI-compat wrapper (LLMClient) — chat() + chat_stream()
├── memory/
│   └── workspace.py       SQLite + FTS5: conversation history + searchable notes
├── tools/
│   ├── registry.py        Tool registry, @tool decorator, JSON schema auto-gen
│   ├── builtins.py        Built-in tools: memory_set/get/search, safe_shell, issue reporting
│   ├── subagent.py        Subagent tools: subagent_run, subagent_team (parallel agents)
│   └── shell_guard.py     Docker sandbox guard for safe_shell
├── agent/loop.py          Agentic loop: streaming LLM → parallel tool calls → loop
├── mcp/manager.py         MCP server manager: add/connect/teardown stdio servers
├── scheduler/engine.py    APScheduler cron + interval jobs
├── routines/manager.py    Named routines (persistent scheduled tasks)
├── jobs/manager.py        Job tracking
├── issue_tracker/
│   ├── base.py            IssueTracker ABC
│   └── github.py          GitHub issue tracker implementation
├── channels/
│   ├── discord_bot.py     Discord channel: DM + @mention → agent → reply
│   └── signal_bot.py      Signal channel integration
├── prompts/               System prompt files (system.md + channel-specific)
└── repl/cli.py            Typer CLI + Rich REPL (streaming Markdown, token counter)

lightclaw-tools/
└── opencode/              MCP server exposing opencode in a Docker sandbox

lightclaw-webui/           SvelteKit web UI (pnpm, Svelte 5 + TypeScript)
├── src/routes/+page.svelte   Main chat page
├── src/lib/api.ts            API client (SSE streaming, history, memory, tools)
├── src/lib/markdown.ts       marked + highlight.js renderer
├── src/lib/components/
│   ├── ChatMessage.svelte    Single message with markdown/image rendering
│   ├── StatusBar.svelte      Token usage bar (mirrors REPL toolbar)
│   └── Modal.svelte          Generic modal dialog
└── build/                    Production build (served by FastAPI)

lightclaw/web/
└── server.py          FastAPI server: SSE /api/chat, history, memory, tools, upload
```

## Running

```bash
# Interactive REPL
uv run lightclaw

# One-shot message
uv run lightclaw run "summarise my notes"

# Discord bot
DISCORD_BOT_TOKEN=... uv run lightclaw discord

# Web UI (build frontend first, then start server)
cd lightclaw-webui && pnpm build && cd ..
uv run lightclaw web                     # serves at http://127.0.0.1:8000
uv run lightclaw web --port 3000         # custom port

# Dev mode (frontend hot-reload + FastAPI backend)
uv run lightclaw web &                   # backend on :8000
cd lightclaw-webui && pnpm dev           # vite on :5173, proxies /api → :8000

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
| `LIGHTCLAW_GITHUB_TOKEN` | — | GitHub token for issue tracker |
| `XDG_CONFIG_HOME` | `~/.config` | Config/data root |

MCP server configs live at `~/.config/lightclaw/mcp.json`.

## Safety

### safe_shell (Docker sandbox)

`safe_shell` is the **only** code/command execution tool. There is no `shell` tool — it was removed after an agent escaped the host via `shell` for generated code execution.

`safe_shell` runs commands inside Docker (`python:3.12-slim` by default) with:
- **Zero bind mounts** — no host filesystem access
- **`--network none`** — no network access
- Custom image selectable per call

Requires Docker installed and running. Returns error string if Docker not found.

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
- **No host shell** — `shell` tool removed; `safe_shell` runs commands in Docker with zero bind mounts and no network
- **MCP for extensibility** — prefer MCP tools over built-in shell for external integrations
- **XDG config dir** — all state in `~/.config/lightclaw/`, no home dir clutter
- **Streaming LLM** — `chat_stream()` with `stream_options.include_usage`; REPL shows live Markdown + token counter (`↳ N tok`)
- **Parallel tool execution** — all tool calls in a single LLM round run via `asyncio.gather`, enabling true concurrency
- **Subagents** — `subagent_run(prompt, label)` / `subagent_team(tasks)` spawn parallel `AgentLoop` instances sharing the same tool registry; subagents can recurse
