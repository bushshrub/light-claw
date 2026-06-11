"""Signal channel: responds to incoming messages via signal-cli.

Requires signal-cli installed and a phone number registered:
  https://github.com/AsamK/signal-cli
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import sys
from typing import Any

from lightclaw.agent import AgentLoop
from lightclaw.config import Config, get_config
from lightclaw.console import console
from lightclaw.memory import Workspace
from lightclaw.tools.registry import Registry, get_default_registry
from lightclaw.tools.shell_guard import reset_approval_handler, set_approval_handler

_SUPPORTED_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _split(text: str, limit: int = 1500) -> list[str]:
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class SignalBot:
    def __init__(
        self,
        phone: str,
        config_dir: str | None = None,
        config: Config | None = None,
        workspace: Workspace | None = None,
        registry: Registry | None = None,
    ) -> None:
        self._phone = phone
        self._config_dir = config_dir or os.path.expanduser("~/.local/share/signal-cli")
        cfg = config or get_config()
        self._agent = AgentLoop(cfg, registry or get_default_registry(), workspace)
        self._running = False

    def _cli(self, *args: str) -> list[str]:
        return ["signal-cli", "--config", self._config_dir, "-a", self._phone, *args]

    async def _receive(self) -> list[dict[str, Any]]:
        proc = await asyncio.create_subprocess_exec(
            *self._cli("receive", "--output=json", "--timeout", "5"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return []

        envelopes = []
        for line in stdout.decode().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                envelopes.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return envelopes

    async def _send(self, recipient: str, message: str) -> None:
        for chunk in _split(message):
            proc = await asyncio.create_subprocess_exec(
                *self._cli("send", "-m", chunk, recipient),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()

    def _extract_attachments(self, data_message: dict[str, Any]) -> list[dict[str, Any]]:
        attachments = []
        for att in data_message.get("attachments", []):
            saved_path = att.get("filename")
            content_type = att.get("contentType", "")
            if saved_path and os.path.isfile(saved_path):
                ext = os.path.splitext(saved_path)[1].lower()
                mime = _EXT_MIME.get(ext) or content_type or "application/octet-stream"
                try:
                    with open(saved_path, "rb") as f:
                        data = f.read()
                    attachments.append({
                        "type": "image" if mime in _SUPPORTED_IMAGE_MIME else "other",
                        "data": data,
                        "mime_type": mime,
                        "filename": os.path.basename(saved_path),
                    })
                except OSError:
                    pass
        return attachments

    async def _handle_envelope(self, envelope: dict[str, Any]) -> None:
        env = envelope.get("envelope", {})
        sender = env.get("source") or env.get("sourceNumber")
        if not sender or sender == self._phone:
            return

        data_msg = env.get("dataMessage")
        if not data_msg:
            return

        text = data_msg.get("message") or ""
        attachments = self._extract_attachments(data_msg)

        if not text and not attachments:
            return

        thread_id = f"signal_{sender}"
        is_interactive = sys.stdin.isatty()

        async def _signal_approver(command: str) -> str:
            if not is_interactive:
                return "deny"
            console.print(f"\n[yellow]\\[shell] Agent wants to run:[/yellow]\n  [bold]{command}[/bold]\n  [y] run once  [n] deny")
            try:
                ans = await asyncio.get_event_loop().run_in_executor(None, input, "> ")
                return "run" if ans.strip().lower() == "y" else "deny"
            except Exception:
                return "deny"

        token = set_approval_handler(_signal_approver)
        try:
            response = await self._agent.run(
                text,
                thread_id=thread_id,
                attachments=attachments or None,
            )
            await self._send(sender, response)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            console.print(f"[red][Signal] error handling message from {sender}:[/red] {exc}\n{tb}")
            try:
                await self._send(sender, f"Error: {exc}")
            except Exception:
                pass
        finally:
            reset_approval_handler(token)

    async def start(self) -> None:
        self._running = True
        console.print(f"[green][Signal][/green] listening on [cyan]{self._phone}[/cyan]  (config: {self._config_dir})")
        console.print("[dim][Signal] requires signal-cli — https://github.com/AsamK/signal-cli[/dim]")
        while self._running:
            try:
                envelopes = await self._receive()
                for envelope in envelopes:
                    asyncio.create_task(self._handle_envelope(envelope))
            except Exception as exc:
                console.print(f"[red][Signal] receive error:[/red] {exc}")
                await asyncio.sleep(5)

    async def close(self) -> None:
        self._running = False
