"""Shell approval gate: unconditional denylist + user-approval whitelist.

Approval is pluggable via a ContextVar so Discord/scheduler/REPL each
provide their own handler without any global state races.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import shlex
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from lightclaw.config import config_dir

# ---------------------------------------------------------------------------
# Unconditional denylist — cannot be whitelisted, ever.
# ---------------------------------------------------------------------------

_BANNED_EXECUTABLES = frozenset({
    "rm", "rmdir", "shred",
    "sudo", "su", "doas", "pkexec",
    "dd", "mkfs", "fdisk", "parted",
    "chmod", "chown", "chgrp",
    "passwd", "useradd", "userdel",
    "shutdown", "reboot", "halt",
    "kill", "killall", "pkill",
    "crontab", "at", "batch",
    "nc", "ncat", "netcat",
    "nmap",
    "tcpdump", "wireshark", "tshark",
    "iptables", "ip6tables", "pfctl",
    "mount", "umount",
    "insmod", "rmmod", "modprobe",
})

_BANNED_PATTERNS = [
    re.compile(r"[;&|`]"),
    re.compile(r"\$\("),
    re.compile(r">\s*/"),
    re.compile(r">>\s*/"),
    re.compile(r"<\s*\("),
    re.compile(r"\beval\b"),
    re.compile(r"\bexec\b"),
    re.compile(r"\bsource\b"),
    re.compile(r"\.\s+/"),
]

_PROTECTED_PATHS = ("/etc", "/sys", "/dev", "/proc", "/boot", "/sbin", "/usr/sbin")


def _hard_block(command: str) -> str | None:
    """Return reason string if command hits the unconditional denylist."""
    for pat in _BANNED_PATTERNS:
        if pat.search(command):
            return f"shell operator blocked: {pat.pattern!r}"
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return f"parse error: {exc}"
    if not tokens:
        return "empty command"
    if Path(tokens[0]).name in _BANNED_EXECUTABLES:
        return f"executable blocked: {Path(tokens[0]).name!r}"
    for tok in tokens[1:]:
        for p in _PROTECTED_PATHS:
            if tok.startswith(p):
                return f"protected path in args: {tok!r}"
    return None


# ---------------------------------------------------------------------------
# Whitelist / blocklist store
# ---------------------------------------------------------------------------

def _whitelist_path() -> str:
    return os.path.join(config_dir(), "shell_whitelist.json")


def _load() -> dict[str, list[str]]:
    path = _whitelist_path()
    if not os.path.exists(path):
        return {"allowed": [], "blocked": []}
    with open(path) as f:
        return json.load(f)


def _save(data: dict[str, list[str]]) -> None:
    path = _whitelist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _normalize(command: str) -> str:
    try:
        return shlex.join(shlex.split(command))
    except ValueError:
        return command.strip()


def is_allowed(command: str) -> bool:
    return _normalize(command) in _load()["allowed"]


def is_blocked(command: str) -> bool:
    return _normalize(command) in _load()["blocked"]


def add_allowed(command: str) -> None:
    data = _load()
    norm = _normalize(command)
    if norm not in data["allowed"]:
        data["allowed"].append(norm)
    _save(data)


def add_blocked(command: str) -> None:
    data = _load()
    norm = _normalize(command)
    if norm not in data["blocked"]:
        data["blocked"].append(norm)
    _save(data)


# ---------------------------------------------------------------------------
# Pluggable approval handler (ContextVar — safe under concurrent coroutines)
# ---------------------------------------------------------------------------

# Handler signature: async (command: str) -> "run" | "deny" | "allow_always" | "block_always"
ApprovalHandler = Callable[[str], Awaitable[str]]

_approval_handler: contextvars.ContextVar[ApprovalHandler | None] = (
    contextvars.ContextVar("approval_handler", default=None)
)


def set_approval_handler(handler: ApprovalHandler | None) -> contextvars.Token:
    """Set async approval handler for the current async context. Returns token to reset."""
    return _approval_handler.set(handler)


def reset_approval_handler(token: contextvars.Token) -> None:
    _approval_handler.reset(token)


# ---------------------------------------------------------------------------
# Approval prompt implementations
# ---------------------------------------------------------------------------

def _sync_prompt(command: str) -> str:
    """Blocking stdin prompt for REPL context."""
    print(f"\n\033[33m[shell] Agent wants to run:\033[0m")
    print(f"  \033[1m{command}\033[0m")
    print("  [y] run once  [n] deny  [a] always allow  [b] always block  (default: n)")
    try:
        choice = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "deny"
    if choice == "y":
        return "run"
    if choice == "a":
        return "allow_always"
    if choice == "b":
        return "block_always"
    return "deny"


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

async def check_async(command: str) -> tuple[str, str | None]:
    """
    Full async gate check.

    Returns (verdict, reason):
      verdict: "run" | "deny"
      reason:  human-readable string if denied, None if approved.
    """
    # 1. Unconditional denylist
    reason = _hard_block(command)
    if reason:
        return "deny", f"[BLOCKED] {reason}"

    # 2. User blocklist
    if is_blocked(command):
        return "deny", "[BLOCKED] command in your blocklist"

    # 3. Whitelist — run silently
    if is_allowed(command):
        return "run", None

    # 4. Approval — use injected async handler or fall back to sync stdin
    handler = _approval_handler.get()
    if handler is not None:
        decision = await handler(command)
    elif sys.stdin.isatty():
        decision = _sync_prompt(command)
    else:
        return "deny", "[DENIED] no approval handler in non-interactive context"

    if decision == "run":
        return "run", None
    if decision == "allow_always":
        add_allowed(command)
        return "run", None
    if decision == "block_always":
        add_blocked(command)
        return "deny", "[BLOCKED] added to your blocklist"
    return "deny", "[DENIED] by user"
