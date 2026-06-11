from .registry import Registry, tool
from . import builtins as _builtins  # noqa: F401 — registers built-in tools
from . import subagent as _subagent  # noqa: F401 — registers subagent tools
from . import productivity as _productivity  # noqa: F401 — registers calendar/todo/weather tools
from . import skills as _skills  # noqa: F401 — registers skill tools
from . import extensions as _extensions  # noqa: F401 — registers extension tools + auto-loads user extensions

__all__ = ["Registry", "tool"]
