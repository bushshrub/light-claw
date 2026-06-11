"""Core agent loop: LLM reasoning + tool execution until final response."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from lightclaw.config import Config, get_config
from lightclaw.llm import LLMClient
from lightclaw.memory import Workspace
from lightclaw.tools.registry import Registry, get_default_registry


class AgentLoop:
    def __init__(
        self,
        config: Config | None = None,
        registry: Registry | None = None,
        workspace: Workspace | None = None,
    ) -> None:
        self._cfg = config or get_config()
        self._llm = LLMClient(self._cfg)
        self._registry = registry or get_default_registry()
        self._workspace = workspace

    # Each attachment dict has keys: type ("image"|"audio"|"video"|"other"),
    # data (bytes), mime_type (str), filename (str).
    _SUPPORTED_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def _build_user_content(
        self,
        user_message: str,
        attachments: list[dict[str, Any]] | None,
    ) -> str | list[Any]:
        """Return a plain string or a content-array for vision/multimodal requests."""
        if not attachments or not self._cfg.multimodal:
            return user_message

        parts: list[Any] = []
        if user_message:
            parts.append({"type": "text", "text": user_message})

        for att in attachments:
            att_type = att.get("type", "other")
            mime = att.get("mime_type", "application/octet-stream")
            filename = att.get("filename", "attachment")
            data: bytes = att.get("data", b"")

            if att_type == "image" and att.get("_url"):
                # Embed image: pass the public URL directly (no download needed).
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": att["_url"]},
                })
            elif att_type == "image" and data and mime in self._SUPPORTED_IMAGE_MIME:
                b64 = base64.b64encode(data).decode("ascii")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            elif att_type in ("audio", "video"):
                parts.append({
                    "type": "text",
                    "text": f"[User attached {att_type}: {filename}]",
                })
            else:
                parts.append({
                    "type": "text",
                    "text": f"[User attached file: {filename}]",
                })

        # If only text ended up in parts (e.g. all non-image attachments), flatten back
        if all(p.get("type") == "text" for p in parts):
            return "\n".join(p["text"] for p in parts)

        return parts

    async def run(
        self,
        user_message: str,
        thread_id: str = "default",
        extra_system: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Run agent loop; return final text response."""
        chunks = []
        async for chunk in self.stream(
            user_message, thread_id, extra_system, attachments
        ):
            chunks.append(chunk)
        return "".join(chunks)

    async def stream(
        self,
        user_message: str,
        thread_id: str = "default",
        extra_system: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks as agent reasons and executes tools."""
        # Build context
        history = []
        if self._workspace:
            history = await self._workspace.get_history(thread_id)
            await self._workspace.add_message("user", user_message, thread_id)

        system = self._cfg.system_prompt
        if extra_system:
            system = f"{system}\n\n{extra_system}"

        user_content = self._build_user_content(user_message, attachments)

        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_content})

        tools = self._registry.schemas()
        rounds = 0

        while rounds < self._cfg.max_tool_rounds:
            rounds += 1
            resp = await self._llm.chat(messages, tools=tools or None)
            msg = resp.choices[0].message

            # No tool calls → final answer
            if not msg.tool_calls:
                content = msg.content or ""
                if self._workspace:
                    await self._workspace.add_message("assistant", content, thread_id)
                yield content
                return

            # Append assistant message with tool calls
            messages.append(msg.model_dump(exclude_unset=False))

            # Execute each tool call
            tool_results: list[str] = []
            for tc in msg.tool_calls:
                name = tc.function.name
                args = tc.function.arguments
                try:
                    result = await self._registry.call(name, args)
                    result_str = (
                        json.dumps(result) if not isinstance(result, str) else result
                    )
                except Exception as exc:
                    result_str = f"Error: {exc}"
                tool_results.append(result_str)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # Fallback if max rounds hit
        fallback = "[max tool rounds reached]"
        if self._workspace:
            await self._workspace.add_message("assistant", fallback, thread_id)
        yield fallback
