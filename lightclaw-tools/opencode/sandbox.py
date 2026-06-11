"""Docker sandbox: runs opencode tasks in an isolated container."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "lightclaw-opencode-sandbox:latest")
_DEFAULT_OPENCODE_CONFIG = os.environ.get(
    "OPENCODE_CONFIG_DIR",
    os.path.expanduser("~/.config/opencode"),
)

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _strip_jsonc_comments(text: str) -> str:
    """Strip // line comments from JSONC without touching strings."""
    out, i, n = [], 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\":
                i += 1
                if i < n:
                    out.append(text[i])
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _read_opencode_providers(config_dir: str) -> dict[str, dict]:
    """Return {provider_id: provider_cfg} from the opencode config, or {}."""
    for filename in ("opencode.jsonc", "opencode.json", "config.json"):
        path = os.path.join(os.path.expanduser(config_dir), filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                text = _strip_jsonc_comments(f.read())
            return json.loads(text).get("provider", {})
        except Exception:
            pass
    return {}


def derive_default_model(config_dir: str = _DEFAULT_OPENCODE_CONFIG) -> str | None:
    """Derive opencode's default model from lightclaw's LLM env vars.

    Matches LIGHTCLAW_BASE_URL against configured opencode provider baseURLs,
    then returns '{provider_id}/{LIGHTCLAW_MODEL}'. Falls back to
    OPENCODE_DEFAULT_MODEL if set, or None.
    """
    if explicit := os.environ.get("OPENCODE_DEFAULT_MODEL"):
        return explicit

    base_url = os.environ.get("LIGHTCLAW_BASE_URL", "").rstrip("/")
    model = os.environ.get("LIGHTCLAW_MODEL", "")
    if not base_url or not model:
        return None

    for provider_id, cfg in _read_opencode_providers(config_dir).items():
        provider_url = cfg.get("options", {}).get("baseURL", "").rstrip("/")
        if provider_url and provider_url == base_url:
            return f"{provider_id}/{model}"

    return None


def list_models(config_dir: str = _DEFAULT_OPENCODE_CONFIG) -> list[str]:
    """Return all provider/model strings from the opencode config."""
    models = []
    for provider_id, cfg in _read_opencode_providers(config_dir).items():
        for model_id in cfg.get("models", {}):
            models.append(f"{provider_id}/{model_id}")
    return models


class Sandbox:
    def __init__(
        self,
        image: str = SANDBOX_IMAGE,
        opencode_config_dir: str = _DEFAULT_OPENCODE_CONFIG,
    ) -> None:
        self.image = image
        self.opencode_config_dir = opencode_config_dir
        self.default_model = derive_default_model(opencode_config_dir)

    async def build_image(self, dockerfile_dir: str | None = None) -> None:
        """Build the sandbox Docker image from Dockerfile.sandbox."""
        context = dockerfile_dir or os.path.dirname(__file__)
        proc = await asyncio.create_subprocess_exec(
            "docker", "build",
            "-f", os.path.join(context, "Dockerfile.sandbox"),
            "-t", self.image,
            context,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"docker build failed (exit {rc})")

    async def run_task(
        self,
        task: str,
        workspace: str,
        model: str | None = None,
        timeout: int = 300,
    ) -> str:
        """Run opencode inside Docker with the workspace mounted."""
        if not os.path.isabs(workspace):
            raise ValueError(f"workspace must be an absolute path: {workspace!r}")
        if not os.path.isdir(workspace):
            raise ValueError(f"workspace directory does not exist: {workspace!r}")

        effective_model = model or self.default_model

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{workspace}:/workspace",
            "--workdir", "/workspace",
            "-e", "HOME=/home/sandbox",
            "--memory", "2g",
            "--cpus", "1.5",
            "--security-opt", "no-new-privileges",
        ]

        # Mount host opencode config read-only at a staging path.
        # The entrypoint copies it to the writable config dir on each run,
        # so API key changes are always picked up without rebuilding the image.
        config_dir = os.path.expanduser(self.opencode_config_dir)
        if os.path.isdir(config_dir):
            cmd += ["-v", f"{config_dir}:/run/opencode-config-host:ro"]
        else:
            print(
                f"[sandbox] WARNING: opencode config dir not found: {config_dir!r} — "
                "container will run without provider configuration",
                file=sys.stderr,
            )

        # Forward provider API keys from host environment
        for env_key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            if val := os.environ.get(env_key):
                cmd += ["-e", f"{env_key}={val}"]

        cmd += [self.image, "run", task, "--dangerously-skip-permissions", "--print-logs"]
        if effective_model:
            cmd += ["--model", effective_model]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: task timed out after {timeout}s"

        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        return _strip_ansi(output).strip()
