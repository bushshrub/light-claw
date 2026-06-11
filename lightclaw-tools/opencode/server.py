"""MCP server: exposes opencode as tools, running it in a Docker sandbox."""

from __future__ import annotations

import asyncio
import os
import re
import sys

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

from sandbox import Sandbox, list_models  # noqa: E402

_sandbox = Sandbox()

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


mcp = FastMCP(
    "opencode",
    instructions=(
        "Tools for running opencode, a coding agent.\n\n"
        "Use opencode_run for isolated Docker sandbox execution (recommended for untrusted tasks). "
        "Use opencode_run_local for direct host execution when Docker is unavailable.\n\n"
        "To add new tools to the running lightclaw agent, use opencode_add_lightclaw_extension. "
        "It always prompts the user for approval before writing any code."
    ),
)


@mcp.tool()
async def opencode_list_models() -> str:
    """List available models from the opencode provider config.

    Returns provider/model strings that can be passed to the model parameter
    of opencode_run or opencode_run_local. Also shows the current default.
    Use openrouter/* models when OPENROUTER_API_KEY is set.
    """
    models = list_models(_sandbox.opencode_config_dir)
    default = _sandbox.default_model or "(opencode default)"
    lines = [f"Default model: {default}", "", "Configured models:"]
    lines += [f"  {m}" for m in models] if models else ["  (none found in config)"]
    lines += [
        "",
        "OpenRouter models (if OPENROUTER_API_KEY is set): openrouter/<model>",
        "  e.g. openrouter/anthropic/claude-sonnet-4-5",
        "       openrouter/google/gemini-2.5-pro",
        "       openrouter/openai/gpt-4o",
    ]
    return "\n".join(lines)


@mcp.tool()
async def opencode_run(
    task: str,
    workspace: str,
    model: str | None = None,
) -> str:
    """Run a coding task using opencode inside a Docker sandbox.

    The workspace directory is mounted read-write into the container.
    opencode will read and modify files within it.
    Use opencode_list_models to see available models.

    Args:
        task: The coding task or instruction for opencode.
        workspace: Absolute path to the project directory on the host.
        model: Model in provider/model format. Defaults to the model lightclaw
            is configured to use. Examples: 'llama-local/gemma4-12b',
            'openrouter/anthropic/claude-sonnet-4-5'.
    """
    try:
        return await _sandbox.run_task(task, workspace, model=model)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
async def opencode_run_local(task: str, cwd: str | None = None, model: str | None = None) -> str:
    """Run a coding task using opencode directly on the host (no Docker).

    WARNING: opencode has full filesystem access when run locally.
    Only use this when Docker is not available or for explicitly trusted tasks.

    Args:
        task: The coding task or instruction for opencode.
        cwd: Working directory (defaults to current directory).
        model: Model override in provider/model format (e.g. 'openai/gpt-4o').
    """
    effective_model = model or _sandbox.default_model
    cmd = ["opencode", "run", task]
    if cwd:
        cmd += ["--dir", cwd]
    if effective_model:
        cmd += ["--model", effective_model]
    cmd += ["--dangerously-skip-permissions", "--print-logs"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        return "Error: task timed out after 300s"

    output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
    return _strip_ansi(output).strip()


def _extensions_dir() -> str:
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = os.path.join(xdg, "lightclaw", "extensions")
    os.makedirs(path, exist_ok=True)
    return path


def _mirror_agents_md(ext_dir: str) -> None:
    """Copy AGENTS.md from the project root into the extensions dir if needed."""
    # AGENTS.md lives in the same directory as this server.
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "AGENTS.md"))
    if not os.path.isfile(src):
        return
    dest = os.path.join(ext_dir, "AGENTS.md")
    if os.path.isfile(dest) and os.path.getmtime(src) <= os.path.getmtime(dest):
        return
    try:
        import shutil
        shutil.copy2(src, dest)
    except OSError:
        pass


@mcp.tool()
async def opencode_add_lightclaw_extension(
    name: str,
    description: str,
    model: str | None = None,
) -> str:
    """Write a new lightclaw tool extension using opencode.

    ALWAYS requires explicit user approval via terminal prompt before running.
    opencode writes a Python file to ~/.config/lightclaw/extensions/ guided by
    AGENTS.md in that directory. After this returns, call
    lightclaw_extension_load('<name>.py') to load the new tools into the agent.

    Args:
        name: Snake_case filename stem without .py (e.g. 'weather', 'github_tools').
        description: Detailed spec of what tools to write and how they should behave.
        model: Optional opencode model override (e.g. 'openrouter/anthropic/claude-sonnet-4-5').
    """
    ext_dir = _extensions_dir()
    _mirror_agents_md(ext_dir)

    ext_path = os.path.join(ext_dir, f"{name}.py")
    existing = sorted(f for f in os.listdir(ext_dir) if f.endswith(".py") and not f.startswith("_"))

    # --- Require explicit user approval via /dev/tty (bypasses MCP stdio transport) ---
    sys.stderr.write("\n")
    sys.stderr.write("┌─ opencode_add_lightclaw_extension ───────────────────────────────\n")
    sys.stderr.write(f"│  File   : {ext_path}\n")
    sys.stderr.write(f"│  Task   : {description[:200]}{'…' if len(description) > 200 else ''}\n")
    sys.stderr.write(f"│  Model  : {model or _sandbox.default_model or 'opencode default'}\n")
    sys.stderr.write("│\n")
    sys.stderr.write("│  opencode will write code to your filesystem. Review the output\n")
    sys.stderr.write("│  carefully before calling lightclaw_extension_load to activate it.\n")
    sys.stderr.write("└──────────────────────────────────────────────────────────────────\n")
    sys.stderr.write("Approve? [y/N]: ")
    sys.stderr.flush()

    try:
        with open("/dev/tty") as tty:
            answer = tty.readline().strip().lower()
    except OSError:
        return (
            "Cannot prompt for approval: /dev/tty not available. "
            "Extension creation cancelled."
        )

    if answer != "y":
        return "Extension creation cancelled by user."

    # --- Build task with AGENTS.md prepended ---
    agents_md_path = os.path.join(ext_dir, "AGENTS.md")
    agents_context = ""
    if os.path.isfile(agents_md_path):
        with open(agents_md_path) as f:
            agents_context = f.read()

    task = (
        f"{agents_context}\n\n"
        f"---\n\n"
        f"# Task\n\n"
        f"Write a lightclaw extension named `{name}.py` in the current directory.\n\n"
        f"## What it should do\n\n"
        f"{description}\n\n"
        f"## Existing extensions in this directory\n\n"
        f"{', '.join(existing) or 'none'}\n\n"
        f"Write ONLY `{name}.py`. Do not create or modify any other files."
    )

    effective_model = model or _sandbox.default_model
    cmd = ["opencode", "run", task, "--dangerously-skip-permissions", "--print-logs"]
    if effective_model:
        cmd += ["--model", effective_model]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ext_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return (
                f"opencode timed out after 300s. "
                f"Check for a partial file at {ext_path}. "
                f"If it looks correct, call lightclaw_extension_load('{name}.py')."
            )
    except FileNotFoundError:
        return (
            "opencode CLI not found on PATH.\n"
            "Install: curl -fsSL https://opencode.ai/install | sh"
        )

    raw = _strip_ansi((stdout + stderr).decode(errors="replace")).strip()

    if not os.path.isfile(ext_path):
        return (
            f"opencode did not create `{name}.py`.\n\n"
            f"opencode output:\n{raw[-3000:]}"
        )

    return (
        f"Extension written: {ext_path}\n\n"
        f"Next step: call lightclaw_extension_load('{name}.py') to load it into the agent.\n\n"
        f"opencode output:\n{raw[-2000:]}"
    )


def main() -> None:
    print(f"[opencode-mcp] sandbox image: {_sandbox.image}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
