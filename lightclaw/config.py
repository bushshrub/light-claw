"""Runtime configuration loaded from .env then env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


from pathlib import Path

def _load_system_prompt() -> str:
    prompt_file = Path(__file__).parent / "prompts" / "system.md"
    try:
        return prompt_file.read_text().strip()
    except Exception:
        return "You are light-claw, a local AI assistant. Be concise and helpful."


def config_dir() -> str:
    """XDG_CONFIG_HOME/lightclaw, or ~/.config/lightclaw on macOS/Linux."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "lightclaw")


TUI_LOCK_FILE = os.path.join(config_dir(), ".tui-active")


@dataclass
class Config:
    base_url: str = field(
        default_factory=lambda: os.environ.get(
            "LIGHTCLAW_BASE_URL", "http://localhost:8080/v1"
        )
    )
    api_key: str = field(
        default_factory=lambda: os.environ.get("LIGHTCLAW_API_KEY", "local")
    )
    model: str = field(
        default_factory=lambda: os.environ.get("LIGHTCLAW_MODEL", "local-model")
    )
    db_path: str = field(
        default_factory=lambda: os.environ.get(
            "LIGHTCLAW_DB",
            os.path.join(config_dir(), "workspace.db"),
        )
    )
    github_token: str = field(
        default_factory=lambda: os.environ.get("LIGHTCLAW_GITHUB_TOKEN", "")
    )
    issue_repo: str = field(
        default_factory=lambda: os.environ.get("LIGHTCLAW_ISSUE_REPO", "bushshrub/light-claw")
    )
    system_prompt: str = field(default_factory=_load_system_prompt)
    max_tool_rounds: int = 10
    multimodal: bool = True
    context_length: int | None = field(
        default_factory=lambda: int(v) if (v := os.environ.get("LIGHTCLAW_CONTEXT_LENGTH")) else None
    )
    discord_guild_id: int | None = field(
        default_factory=lambda: int(v) if (v := os.environ.get("DISCORD_GUILD_ID")) else None
    )
    discord_prefix: str = field(
        default_factory=lambda: os.environ.get("DISCORD_PREFIX", "lc")
    )


_default: Config | None = None


def get_config() -> Config:
    global _default
    if _default is None:
        _default = Config()
    return _default


def set_config(cfg: Config) -> None:
    global _default
    _default = cfg
