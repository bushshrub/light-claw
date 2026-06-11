# Learned Rules for light-claw

## Safety

- Extension writing ALWAYS requires explicit user approval via `/dev/tty`.
  Tool must be `opencode_add_lightclaw_extension` in the opencode MCP server — never a builtin.
- No `shell` tool. `safe_shell` only, runs in Docker with zero bind mounts and no network.
- Never commit `.env`, `secrets`, or `settings.local.json`. Only `.claude/settings.local.json` is gitignored, not the whole `.claude/` directory.

## File edit reliability

- `cli.py` is modified by a background linter/formatter between reads.
  If Edit fails with "file modified since read", use Write to rewrite the whole file in one shot.

## Project structure rules

- `AGENTS.md` belongs in `lightclaw-tools/opencode/` — it's opencode's guide, not lightclaw's.
  Never place it in the project root.
- Extensions live in `~/.config/lightclaw/extensions/`. `_AGENTS_MD_SRC` in both
  `lightclaw/tools/extensions.py` and `lightclaw-tools/opencode/server.py` must point to
  `lightclaw-tools/opencode/AGENTS.md`.
- `CLAUDE.md` must be updated with every architectural change. Overflow to `.claude/`.

## Architecture

- MCP transport owns stdio. Use `/dev/tty` directly for terminal prompts inside MCP servers.
- Extensions: two-step flow — `opencode_add_lightclaw_extension` (writes file, user approves)
  → `lightclaw_extension_load('<name>.py')` (hot-loads into registry).
- `lightclaw_read_source` path must be blocked from escaping `_LIGHTCLAW_SRC_ROOT`
  (normpath + startswith check).
- Image paste in REPL: `Ctrl+Y` key binding reads clipboard via `osascript` (macOS) or
  `xclip` (Linux). Pending attachments accumulate in `_pending_image_attachments`, merged on submit.
- `load_all_extensions()` runs at module import time (`_auto_loaded = load_all_extensions()`
  at bottom of `extensions.py`), so extensions are live at startup.

## Textual TUI (`lightclaw/repl/tui.py`)

- **No prompt_toolkit REPL.** It was deleted. `lightclaw repl` → Textual TUI only.
  No `--classic` flag. Do not re-add the old `_repl()` function.

- **dock:bottom compose order matters.** Textual places the *first* yielded `dock: bottom`
  widget closest to the screen edge (very bottom). Second goes just above it. Correct order:
  ```python
  yield Static(id="statusbar")  # very bottom
  yield Input(id="input")       # just above statusbar
  ```
  Reversing this puts the Input at the very bottom — wrong.

- **Console redirect.** `lightclaw/console.py` exports `_Console(_RichConsole)`. When
  `set_tui_writer(fn)` is called, `console.print()` passes Rich renderables *directly*
  to `fn` (which is `RichLog.write()`). Rich Tables, Panels, Markdown all work — do not
  pre-render to a string buffer. `console.status()` returns `nullcontext()` in TUI mode.

- **Live context bar during streaming.** `AgentLoop._tokens` only updates on the final
  usage chunk (end of stream). To make the bar grow live, track `_streaming_chars` (raw
  char count per response) on the App. `_update_status()` adds `_streaming_chars // 4`
  as a rough token estimate. Reset to 0 in the streaming `finally` block.

- **Rate-limit active widget, not statusbar.**
  - `active.update(Markdown(response))` throttled to ≥50ms between calls (prevents flicker).
  - `_update_status()` called on every chunk (no rate-limit; widget is cheap).

- **Separate job agent.** `ReplSession._job_agent` is a distinct `AgentLoop` passed to
  `JobManager.set_agent()`. Background job tokens do not pollute the interactive session
  counter shown in the statusbar.

## Agent loop (`lightclaw/agent/loop.py`)

- **`_current_agent` ContextVar** is set at the top of `AgentLoop.stream()` and reset
  in `finally`. Tools can call `from lightclaw.agent.loop import _current_agent` to
  read their own agent. Used by `get_token_usage` and `compact_conversation` in builtins.

- **`compact_history()`** summarises all thread messages into one assistant message,
  clears history, resets `_tokens` to zero. Auto-triggered at 75% context usage by the TUI.
