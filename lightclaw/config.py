"""Runtime configuration loaded from .env then env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def config_dir() -> str:
    """XDG_CONFIG_HOME/lightclaw, or ~/.config/lightclaw on macOS/Linux."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "lightclaw")


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
    system_prompt: str = (
        "You are light-claw, a local AI assistant. "
        "You have access to tools and persistent memory. "
        "Be concise and helpful."
    )
    max_tool_rounds: int = 10
    multimodal: bool = True


_default: Config | None = None


def get_config() -> Config:
    global _default
    if _default is None:
        _default = Config()
    return _default


def set_config(cfg: Config) -> None:
    global _default
    _default = cfg
