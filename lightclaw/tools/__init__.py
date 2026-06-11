from .registry import Registry, tool
from . import builtins as _builtins  # noqa: F401 — registers built-in tools
from . import subagent as _subagent  # noqa: F401 — registers subagent tools

__all__ = ["Registry", "tool"]
