"""Shared Rich console; supports optional TUI redirect."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable

from rich.console import Console as _RichConsole

_tui_writer: Callable[[Any], None] | None = None


def set_tui_writer(fn: Callable[[Any], None] | None) -> None:
    global _tui_writer
    _tui_writer = fn


class _Console(_RichConsole):
    def print(self, *args, **kwargs) -> None:
        if _tui_writer is not None:
            kwargs.pop("end", None)
            kwargs.pop("highlight", None)
            if len(args) == 1:
                _tui_writer(args[0])
            elif args:
                from rich.text import Text
                _tui_writer(Text(" ".join(str(a) for a in args)))
        else:
            super().print(*args, **kwargs)

    def status(self, *args, **kwargs):
        if _tui_writer is not None:
            return nullcontext()
        return super().status(*args, **kwargs)


console = _Console()
