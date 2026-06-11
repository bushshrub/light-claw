"""Built-in tools registered into the default registry at import time."""

from __future__ import annotations

import contextvars
import shlex
import subprocess
import sys
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path

from lightclaw.tools.registry import get_default_registry
from lightclaw.tools.shell_guard import check_async

_reg = get_default_registry()

# ---------------------------------------------------------------------------
# Issue confirmation handler (pluggable, ContextVar-based)
# ---------------------------------------------------------------------------

ConfirmHandler = Callable[[str, str, str], Awaitable[bool]]

_issue_confirm_handler: contextvars.ContextVar[ConfirmHandler | None] = (
    contextvars.ContextVar("issue_confirm_handler", default=None)
)


def set_issue_confirm_handler(handler: ConfirmHandler | None) -> contextvars.Token:
    """Set a custom issue confirmation handler for the current async context."""
    return _issue_confirm_handler.set(handler)


def reset_issue_confirm_handler(token: contextvars.Token) -> None:
    _issue_confirm_handler.reset(token)


async def _default_confirm(title: str, body_preview: str, tracker_name: str) -> bool:
    """Stdin-based confirmation for interactive contexts."""
    if not sys.stdin.isatty():
        return False
    print(f"\n[issue] Ready to file on {tracker_name}:")
    print(f"  Title: {title}")
    print(f"  Body preview: {body_preview[:300]}...")
    ans = input("  File this issue? [y/N] ").strip().lower()
    return ans == "y"


@_reg.tool(description="Remember a key-value note in persistent memory.")
async def memory_set(key: str, value: str) -> str:
    from lightclaw.memory import Workspace
    async with Workspace() as ws:
        await ws.remember(key, value)
    return f"Stored: {key}"


@_reg.tool(description="Recall a stored note by key.")
async def memory_get(key: str) -> str:
    from lightclaw.memory import Workspace
    async with Workspace() as ws:
        val = await ws.recall(key)
    return val or f"(no entry for {key!r})"


@_reg.tool(description="Search persistent memory with a full-text query.")
async def memory_search(query: str) -> list[dict]:
    from lightclaw.memory import Workspace
    async with Workspace() as ws:
        return await ws.search(query)


@_reg.tool(
    description=(
        "Run a shell command and return stdout+stderr. "
        "Dangerous commands (rm, sudo, dd, etc.) blocked unconditionally. "
        "All other commands require user approval or a prior whitelist entry."
    )
)
async def shell(command: str) -> str:
    verdict, reason = await check_async(command)
    if verdict == "deny":
        return reason or "[DENIED]"

    tokens = shlex.split(command)
    try:
        result = subprocess.run(
            tokens,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": str(Path.home()),
            },
        )
    except FileNotFoundError:
        return f"Command not found: {tokens[0]!r}"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30s"

    output = result.stdout + result.stderr
    return output[:4096]


# ---------------------------------------------------------------------------
# Routine tools — agent can manage its own routines
# ---------------------------------------------------------------------------

@_reg.tool(
    description=(
        "List all configured routines. "
        "Returns id, type (cron/event), trigger, enabled status, and prompt."
    )
)
def routine_list() -> list[dict]:
    from lightclaw.routines import RoutineEngine
    return [r.to_dict() for r in RoutineEngine().load()]


@_reg.tool(
    description=(
        "Add or update a persistent routine. "
        "type='cron' requires a 5-part cron trigger (minute hour day month dow). "
        "type='event' requires trigger='startup'. "
        "If a routine with the same id exists it is overwritten."
    )
)
def routine_add(
    id: str,
    type: str,
    trigger: str,
    prompt: str,
    thread_id: str = "routines",
) -> str:
    from lightclaw.routines import Routine, RoutineEngine
    from lightclaw.routines.engine import get_running

    if type not in ("cron", "event"):
        return "Error: type must be 'cron' or 'event'"
    if type == "cron" and len(trigger.split()) != 5:
        return "Error: cron trigger must be 5 parts (minute hour day month dow)"
    if type == "event" and trigger not in ("startup",):
        return f"Error: unknown event trigger {trigger!r}. Supported: startup"

    routine = Routine(id=id, type=type, trigger=trigger, prompt=prompt, thread_id=thread_id)

    # Use running engine so APScheduler is updated live; fall back to file-only
    engine = get_running() or RoutineEngine()
    engine.add(routine)
    return f"Routine '{id}' saved ({type}: {trigger})"


@_reg.tool(description="Remove a routine by id.")
def routine_remove(id: str) -> str:
    from lightclaw.routines import RoutineEngine
    from lightclaw.routines.engine import get_running

    engine = get_running() or RoutineEngine()
    return f"Removed '{id}'" if engine.remove(id) else f"Routine '{id}' not found"


@_reg.tool(description="Enable a disabled routine.")
def routine_enable(id: str) -> str:
    from lightclaw.routines import RoutineEngine
    from lightclaw.routines.engine import get_running

    engine = get_running() or RoutineEngine()
    return f"Enabled '{id}'" if engine.set_enabled(id, True) else f"Routine '{id}' not found"


@_reg.tool(description="Disable a routine without removing it.")
def routine_disable(id: str) -> str:
    from lightclaw.routines import RoutineEngine
    from lightclaw.routines.engine import get_running

    engine = get_running() or RoutineEngine()
    return f"Disabled '{id}'" if engine.set_enabled(id, False) else f"Routine '{id}' not found"


@_reg.tool(description="Trigger a routine to run immediately, regardless of its schedule.")
async def routine_run_now(id: str) -> str:
    from lightclaw.routines.engine import get_running

    engine = get_running()
    if engine is None:
        return "RoutineEngine is not running (start the REPL first)"
    ok = await engine.run_now(id)
    return f"Fired routine '{id}'" if ok else f"Routine '{id}' not found"


# ---------------------------------------------------------------------------
# Issue reporting tool
# ---------------------------------------------------------------------------

@_reg.tool(
    description=(
        "File a bug report or issue against the lightclaw project tracker. "
        "Collects title, description, and optionally the current exception stack trace, "
        "then shows a preview and REQUIRES explicit user confirmation before filing. "
        "Returns the URL of the created issue on success. "
        "Will not file without user approval — safe to call from error handlers."
    )
)
async def lightclaw_system_report_issue(
    title: str,
    description: str,
    include_stack_trace: bool = True,
) -> str:
    import platform
    from importlib.metadata import version, PackageNotFoundError

    from lightclaw.issue_tracker import get_default_tracker

    tracker = get_default_tracker()
    if tracker is None:
        return (
            "No issue tracker configured. "
            "Set LIGHTCLAW_GITHUB_TOKEN and LIGHTCLAW_ISSUE_REPO in your .env to enable issue filing."
        )

    try:
        lc_version = version("light-claw")
    except PackageNotFoundError:
        lc_version = "unknown"

    stack = traceback.format_exc()
    has_exception = stack.strip() not in ("", "NoneType: None")

    body_parts = [description]

    if include_stack_trace and has_exception:
        body_parts.append("\n## Stack Trace\n\n```\n" + stack.strip() + "\n```")

    body_parts.append(
        f"\n## System Info\n\n"
        f"- Python: {sys.version}\n"
        f"- Platform: {platform.platform()}\n"
        f"- light-claw: {lc_version}\n"
    )

    body = "\n".join(body_parts)

    handler = _issue_confirm_handler.get()
    if handler is not None:
        confirmed = await handler(title, body, tracker.tracker_name)
    else:
        confirmed = await _default_confirm(title, body, tracker.tracker_name)

    if not confirmed:
        return "[CANCELLED] Issue filing cancelled by user."

    try:
        url = await tracker.file_issue(title=title, body=body)
        return f"Issue filed: {url}"
    except Exception as exc:
        return f"Failed to file issue: {exc}"
