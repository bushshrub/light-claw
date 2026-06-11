"""Dynamic extension loader for lightclaw.

Extensions are Python modules in ~/.config/lightclaw/extensions/.
Each .py file registers tools via @tool at import time.
They are auto-loaded when this module is imported (i.e. at startup).

Writing extensions requires explicit user approval — use
mcp__opencode__opencode_add_lightclaw_extension (in the opencode MCP server).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

from lightclaw.config import config_dir
from lightclaw.tools.registry import get_default_registry

_reg = get_default_registry()

# AGENTS.md lives next to the opencode MCP server, not in the project root.
_AGENTS_MD_SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "lightclaw-tools", "opencode", "AGENTS.md")
)


def extensions_dir() -> str:
    """Return (and create if needed) ~/.config/lightclaw/extensions/."""
    path = os.path.join(config_dir(), "extensions")
    os.makedirs(path, exist_ok=True)
    _mirror_agents_md(path)
    return path


def _mirror_agents_md(ext_dir: str) -> None:
    """Keep a copy of AGENTS.md in the extensions dir so opencode finds it."""
    if not os.path.isfile(_AGENTS_MD_SRC):
        return
    dest = os.path.join(ext_dir, "AGENTS.md")
    if os.path.isfile(dest) and os.path.getmtime(_AGENTS_MD_SRC) <= os.path.getmtime(dest):
        return
    try:
        with open(_AGENTS_MD_SRC) as f:
            content = f.read()
        with open(dest, "w") as f:
            f.write(content)
    except OSError:
        pass


def load_extension(file_path: str) -> list[str]:
    """Exec a .py extension file, return list of newly registered tool names."""
    before = set(get_default_registry().names())
    stem = os.path.splitext(os.path.basename(file_path))[0]
    module_name = f"lightclaw_ext_{stem}"

    # Evict cached version so reload picks up edits
    sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot create module spec for: {file_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    after = set(get_default_registry().names())
    return sorted(after - before)


def load_all_extensions() -> dict[str, list[str]]:
    """Load all .py files from the extensions dir. Returns {filename: [tool_names]}."""
    ext_dir = os.path.join(config_dir(), "extensions")
    if not os.path.isdir(ext_dir):
        return {}
    results: dict[str, list[str]] = {}
    for fname in sorted(os.listdir(ext_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(ext_dir, fname)
        try:
            results[fname] = load_extension(path)
        except Exception as exc:
            results[fname] = [f"ERROR: {exc}"]
    return results


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@_reg.tool(description=(
    "List all installed lightclaw extensions from ~/.config/lightclaw/extensions/. "
    "Shows filename and whether it is currently loaded in this session."
))
def lightclaw_extensions_list() -> list[dict[str, Any]]:
    ext_dir = os.path.join(config_dir(), "extensions")
    if not os.path.isdir(ext_dir):
        return []
    results = []
    for fname in sorted(os.listdir(ext_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        stem = os.path.splitext(fname)[0]
        module_name = f"lightclaw_ext_{stem}"
        results.append({
            "file": fname,
            "path": os.path.join(ext_dir, fname),
            "loaded": module_name in sys.modules,
        })
    return results


@_reg.tool(description=(
    "Hot-load (or reload) a lightclaw extension by filename. "
    "filename is the .py file in ~/.config/lightclaw/extensions/ (e.g. 'weather.py'). "
    "Use this after opencode_add_lightclaw_extension writes a new extension file. "
    "Returns the names of any new tools registered."
))
def lightclaw_extension_load(filename: str) -> str:
    ext_dir = os.path.join(config_dir(), "extensions")
    path = os.path.join(ext_dir, filename)
    if not os.path.isfile(path):
        return f"Not found: {path}"
    try:
        tools = load_extension(path)
        if tools:
            return f"Loaded '{filename}' — new tools: {', '.join(tools)}"
        return f"Loaded '{filename}' — no new tools (may already be registered)"
    except Exception as exc:
        return f"Failed to load '{filename}': {exc}"


# ---------------------------------------------------------------------------
# Auto-load at import time
# ---------------------------------------------------------------------------

_auto_loaded: dict[str, list[str]] = load_all_extensions()
