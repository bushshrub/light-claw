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
        "Tools for running opencode, a coding agent. "
        "Use opencode_run for isolated Docker sandbox execution (recommended). "
        "Use opencode_run_local for direct host execution when Docker is unavailable."
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


def main() -> None:
    print(f"[opencode-mcp] sandbox image: {_sandbox.image}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
