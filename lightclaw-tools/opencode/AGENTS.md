# AGENTS.md — lightclaw extension framework

This directory (`~/.config/lightclaw/extensions/`) contains **user extensions**:
Python modules that add new tools to the running lightclaw agent. Each `.py` file
here is loaded automatically at startup.

You are `opencode`. You have been asked to write a lightclaw extension.
See `example_extension.py` in this directory for a working reference.

---

## File structure

One extension = one Python file placed in the **current directory**.
Name it `<purpose>.py` in snake_case — e.g. `weather.py`, `github_tools.py`.

---

## How to write a tool

```python
from lightclaw.tools import tool

@tool(description="One clear sentence: what this tool does and when to use it.")
async def my_tool_name(required_param: str, optional_param: int = 10) -> str:
    # implementation
    return "result"
```

### Rules

| Rule | Detail |
|---|---|
| `@tool(description=...)` | The description is what the agent reads. Be specific and actionable. |
| Function name | = tool name. `snake_case`. Prefix with domain: `weather_get`, `gh_issue_list`. |
| Type hints | Required on every parameter. `str \| None = None` for optional. |
| Return type | Must be JSON-serialisable: `str`, `int`, `float`, `bool`, `list`, `dict`. |
| Async | Use `async def` for I/O (HTTP, subprocess, SQLite). Sync is fine for pure compute. |
| Multiple tools | One file can register as many tools as needed. |
| No new deps | Only packages already in lightclaw's venv (see list below). |
| No side effects | Do not modify files outside this directory. |

---

## Available packages

```python
# Standard library (always available)
import asyncio, os, json, re, subprocess, tempfile, datetime, pathlib, shutil

# Third-party (already installed in lightclaw)
import httpx           # async HTTP — httpx.AsyncClient(timeout=10)
import aiosqlite       # async SQLite
from pydantic import BaseModel

# lightclaw internals
from lightclaw.tools import tool         # ← the decorator you MUST use
from lightclaw.memory import Workspace   # persistent SQLite memory store
from lightclaw.config import get_config  # LLM config: base_url, model, api_key
```

---

## Example 1 — HTTP tool

```python
from lightclaw.tools import tool
import httpx

@tool(description="Fetch the current weather for a city. Returns a one-line summary.")
async def weather_get(city: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"https://wttr.in/{city}?format=3")
        resp.raise_for_status()
        return resp.text.strip()
```

## Example 2 — memory-backed tool

```python
from lightclaw.tools import tool
from lightclaw.memory import Workspace

@tool(description="Store a named note in persistent memory.")
async def notes_set(key: str, value: str) -> str:
    async with Workspace() as ws:
        await ws.remember(key, value)
    return f"Stored: {key}"
```

## Example 3 — subprocess tool

```python
from lightclaw.tools import tool
import asyncio

@tool(description="Run ripgrep over a directory and return matching lines (max 50).")
async def rg_search(pattern: str, directory: str = ".") -> str:
    proc = await asyncio.create_subprocess_exec(
        "rg", "--max-count", "50", pattern, directory,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (stdout + stderr).decode(errors="replace")[:4000]
```

---

## What NOT to do

- Do **not** `pip install` or `uv add` — extensions cannot add dependencies.
- Do **not** create `__init__.py` or subdirectories — flat files only.
- Do **not** modify files outside the current directory.
- Do **not** use `print()` — return values instead.
- Do **not** write tests, READMEs, or setup files — just the tool `.py` file.
- Do **not** import from `lightclaw.tools.builtins` — use `from lightclaw.tools import tool`.
