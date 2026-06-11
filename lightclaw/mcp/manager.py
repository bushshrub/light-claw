"""MCP server manager: add/remove servers, connect sessions, expose tools."""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any

from lightclaw.config import config_dir
from lightclaw.console import console
from lightclaw.tools.registry import Registry


class MCPManager:
    def __init__(self) -> None:
        self._config_path = os.path.join(config_dir(), "mcp.json")
        self._exit_stack = contextlib.AsyncExitStack()
        self._sessions: dict[str, Any] = {}

    # --- config persistence ---

    def load_config(self) -> dict[str, Any]:
        if not os.path.exists(self._config_path):
            return {}
        with open(self._config_path) as f:
            return json.load(f).get("servers", {})

    def _save_config(self, servers: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w") as f:
            json.dump({"servers": servers}, f, indent=2)

    def add_server(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        servers = self.load_config()
        servers[name] = {"command": command, "args": args, "env": env or {}}
        self._save_config(servers)

    def remove_server(self, name: str) -> bool:
        servers = self.load_config()
        if name not in servers:
            return False
        del servers[name]
        self._save_config(servers)
        return True

    # --- lifecycle ---

    async def start(self, registry: Registry) -> None:
        await self._exit_stack.__aenter__()
        for name, cfg in self.load_config().items():
            try:
                await self._connect(name, cfg, registry)
            except Exception as exc:
                console.print(f"[red][MCP] {name}: failed to connect —[/red] {exc}")

    async def stop(self) -> None:
        await self._exit_stack.aclose()

    async def _connect(
        self, name: str, cfg: dict[str, Any], registry: Registry
    ) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env") or None,
        )
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(params)
        )
        session: ClientSession = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()
        self._sessions[name] = session

        result = await session.list_tools()
        for mcp_tool in result.tools:
            tool_name = f"mcp__{name}__{mcp_tool.name}"
            _session = session
            _orig = mcp_tool.name

            async def _call(_s=_session, _n=_orig, **kwargs: Any) -> str:
                r = await _s.call_tool(_n, kwargs)
                parts = [c.text for c in r.content if hasattr(c, "text")]
                return "\n".join(parts) if parts else "(no output)"

            spec: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": mcp_tool.description or f"{name}/{mcp_tool.name}",
                    "parameters": mcp_tool.inputSchema
                    or {"type": "object", "properties": {}},
                },
            }
            registry.register_raw(tool_name, spec, _call)

        console.print(
            f"[green][MCP] {name}:[/green] connected, "
            f"[cyan]{len(result.tools)}[/cyan] tool(s) registered"
        )
