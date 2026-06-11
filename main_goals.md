# light-claw: Project Objectives

## Core Goals

### 1. Full Local LLM Support
- Primary targets: llama.cpp, vLLM, Ollama — any OpenAI-compatible endpoint
- No dependency on cloud APIs; local inference is first-class, not an afterthought
- Model switching via config only; no code changes needed to swap backends

### 2. Minimal System Prompt Bloat
- System prompt stays lean — only what the agent needs to function
- No injected fluff, disclaimers, or assistant-persona scaffolding
- User controls system prompt content explicitly; defaults are minimal

### 3. Routines (Scheduled Agentic Tasks)
- Cron and interval-based scheduling via APScheduler
- Routines run the full agent loop: LLM + tools + memory
- Defined in config or via CLI; persist across restarts

### 4. Connectors
- **Discord** — DM and @mention trigger agent; replies in-channel
- **Signal** — message-based trigger (Signal CLI or signal-cli bridge)
- Connectors are read/write: agent can send outbound messages, not just receive

### 5. Agentic Coding via OpenCode (Docker-sandboxed)
- `opencode` invoked as a tool, always inside a Docker sandbox — never on bare host
- Sandbox is ephemeral: spun up per-task, torn down after
- File I/O between agent and sandbox via mounted workspace volume only
- No network access inside sandbox unless explicitly granted
