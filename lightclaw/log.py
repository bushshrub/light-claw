"""Level-aware logging via Rich console.

Reads LOG_LEVEL env var (debug / info / warning; default: warning).
Configures stdlib logging to route through RichHandler so output
from third-party libraries (mcp, httpx, etc.) is consistent.
"""

from __future__ import annotations

import logging
import os

from rich.logging import RichHandler

from lightclaw.console import console

_RAW = os.environ.get("LOG_LEVEL", "warning").lower()
_LEVELS = {"debug": 10, "info": 20, "warning": 30}
_LEVEL = _LEVELS.get(_RAW, 30)

DEBUG = 10
INFO = 20
WARNING = 30

logging.basicConfig(
    level=_LEVEL,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)


def _enabled(level: int) -> bool:
    return _LEVEL <= level


def debug(msg: str) -> None:
    if _enabled(DEBUG):
        console.print(f"[dim]DEBUG {msg}[/dim]")


def info(msg: str) -> None:
    if _enabled(INFO):
        console.print(f"[dim]INFO  {msg}[/dim]")
