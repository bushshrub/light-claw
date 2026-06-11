"""Core agent loop: LLM reasoning + tool execution until final response."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

from lightclaw.config import Config, get_config
from lightclaw.llm import LLMClient
from lightclaw import log
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
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        self._context_length: int | None = self._cfg.context_length

    @property
    def token_stats(self) -> dict[str, int]:
        return dict(self._tokens)

    @property
    def context_length(self) -> int | None:
        return self._context_length

    # Each attachment dict has keys: type ("image"|"audio"|"video"|"other"),
    # data (bytes), mime_type (str), filename (str), _url (str, for remote files).
    _SUPPORTED_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    _SUPPORTED_AUDIO_MIME = {"audio/wav", "audio/webm", "audio/mp3", "audio/mpeg", "audio/x-m4a", "audio/flac"}

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
            elif att_type == "audio" and data and mime in self._SUPPORTED_AUDIO_MIME:
                # For audio, we need to handle it differently based on native audio mode
                if self._cfg.native_audio_mode:
                    # In native audio mode, we save the audio to a temporary file
                    # and use the process_audio tool
                    import tempfile
                    import os
                    
                    # Create a temporary file for the audio
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{mime.split('/')[-1]}") as tmp:
                        tmp.write(data)
                        tmp_path = tmp.name
                    
                    # Store the temporary path for later processing
                    # We'll need to clean this up later
                    att["_temp_path"] = tmp_path
                    
                    parts.append({
                        "type": "text",
                        "text": f"[User attached audio: {filename}]",
                    })
                else:
                    # In non-native mode, just note the audio attachment
                    parts.append({
                        "type": "text",
                        "text": f"[User attached audio: {filename}]",
                    })
            elif att_type in ("video", "other"):
                parts.append({
                    "type": "text",
                    "text": f"[User attached {att_type}: {filename}]",
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

    async def compact_history(
        self,
        thread_id: str = "default",
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        """Summarise conversation history into one message. Returns messages removed."""
        if not self._workspace:
            return 0
        history = await self._workspace.get_history(thread_id)
        if len(history) < 4:
            return 0
        n = len(history)
        if on_progress:
            on_progress(f"summarising {n} messages...")
        msgs: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Summarise the following conversation into a single concise paragraph, "
                    "preserving all important context, decisions, and facts."
                ),
            }
        ]
        msgs.extend(history)
        try:
            resp = await self._llm.chat(msgs)
            summary = resp.choices[0].message.content or ""
        except Exception as exc:
            if on_progress:
                on_progress(f"error: {exc}")
            return 0
        if on_progress:
            on_progress("replacing history with summary...")
        await self._workspace.clear_history(thread_id)
        await self._workspace.add_message(
            "assistant",
            f"[Conversation summary — {n} previous messages]\n\n{summary}",
            thread_id,
        )
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        return n

    async def stream(
        self,
        user_message: str,
        thread_id: str = "default",
        extra_system: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks as agent reasons and executes tools."""
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

        # Check if we have audio attachments and native audio mode is enabled
        audio_attachments = []
        if attachments and self._cfg.native_audio_mode:
            for att in attachments:
                if att.get("type") == "audio":
                    audio_attachments.append(att)

        tools = self._registry.schemas()
        rounds = 0

        if self._context_length is None:
            self._context_length = await self._llm.fetch_context_length()

        _agent_token = _current_agent.set(self)
        try:
            while rounds < self._cfg.max_tool_rounds:
                rounds += 1

                if log._enabled(log.DEBUG):
                    for m in messages:
                        role = m.get("role", "?")
                        content = m.get("content") or ""
                        preview = (content[:120] + "…") if len(content) > 120 else content
                        log.debug(f"[{role}] {preview!r}")

                text_parts: list[str] = []
                tool_call_acc: dict[int, dict[str, Any]] = {}

                resp = await self._llm.chat_stream(messages, tools=tools or None)
                async for chunk in resp:
                    if chunk.usage:
                        self._tokens["prompt"] += chunk.usage.prompt_tokens or 0
                        self._tokens["completion"] += chunk.usage.completion_tokens or 0
                        self._tokens["total"] += chunk.usage.total_tokens or 0

                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

                    if delta.content:
                        text_parts.append(delta.content)
                        yield delta.content

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_call_acc:
                                fn = tc_delta.function
                                tool_call_acc[idx] = {
                                    "id": tc_delta.id or "",
                                    "type": "function",
                                    "function": {
                                        "name": fn.name or "" if fn else "",
                                        "arguments": fn.arguments or "" if fn else "",
                                    },
                                }
                            else:
                                if tc_delta.id:
                                    tool_call_acc[idx]["id"] = tc_delta.id
                                fn = tc_delta.function
                                if fn:
                                    if fn.name:
                                        tool_call_acc[idx]["function"]["name"] += fn.name
                                    if fn.arguments:
                                        tool_call_acc[idx]["function"]["arguments"] += fn.arguments

                full_text = "".join(text_parts)

                if log._enabled(log.DEBUG):
                    if tool_call_acc:
                        for tc in sorted(tool_call_acc.values(), key=lambda t: t["id"]):
                            log.debug(f"← tool_call {tc['function']['name']}({tc['function']['arguments']})")
                    else:
                        log.debug(f"← assistant {full_text[:120]!r}")

                if not tool_call_acc:
                    if self._workspace:
                        await self._workspace.add_message("assistant", full_text, thread_id)
                    return

                tool_calls_list = [tool_call_acc[i] for i in sorted(tool_call_acc)]
                messages.append({
                    "role": "assistant",
                    "content": full_text or None,
                    "tool_calls": tool_calls_list,
                })

                async def _call_tool(tc: dict[str, Any]) -> tuple[str, str]:
                    name = tc["function"]["name"]
                    args = tc["function"]["arguments"]
                    log.info(f"tool {name}  args={args}")
                    try:
                        result = await self._registry.call(name, args)
                        result_str = (
                            json.dumps(result) if not isinstance(result, str) else result
                        )
                    except Exception as exc:
                        result_str = f"Error: {exc}"
                    return tc["id"], result_str

                tool_results = await asyncio.gather(*[_call_tool(tc) for tc in tool_calls_list])
                for tool_id, result_str in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result_str,
                    })

                # Process audio attachments if native audio mode is enabled
                if audio_attachments and self._cfg.native_audio_mode:
                    for att in audio_attachments:
                        temp_path = att.get("_temp_path")
                        if temp_path and os.path.exists(temp_path):
                            try:
                                # Call the process_audio tool
                                tool_result = await self._registry.call(
                                    "process_audio",
                                    {
                                        "audio_file_path": temp_path,
                                        "prompt": "Please analyze this audio file.",
                                    }
                                )
                                
                                # Add the audio processing result to the conversation
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": f"audio_{len(messages)}",
                                    "content": f"Audio processing result: {tool_result}",
                                })
                                
                                # Clean up temporary file
                                os.unlink(temp_path)
                                
                            except Exception as exc:
                                # Clean up temporary file on error
                                if temp_path and os.path.exists(temp_path):
                                    os.unlink(temp_path)
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": f"audio_error_{len(messages)}",
                                    "content": f"Error processing audio: {exc}",
                                })

            fallback = "[max tool rounds reached]"
            if self._workspace:
                await self._workspace.add_message("assistant", fallback, thread_id)
            yield fallback
        finally:
            _current_agent.reset(_agent_token)


_current_agent: contextvars.ContextVar["AgentLoop | None"] = contextvars.ContextVar(
    "_current_agent", default=None
)
