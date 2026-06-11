"""Thin async wrapper around any OpenAI-compatible endpoint (llama.cpp, OpenAI, etc.)."""

from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from lightclaw.config import Config, get_config


class LLMClient:
    def __init__(self, config: Config | None = None) -> None:
        cfg = config or get_config()
        self._model = cfg.model
        self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    @property
    def model(self) -> str:
        return self._model

    async def fetch_context_length(self) -> int | None:
        try:
            m = await self._client.models.retrieve(self._model)
            return getattr(m, "context_window", None)
        except Exception:
            return None

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            **kwargs,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return await self._client.chat.completions.create(**params)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Streaming chat completion. Final chunk includes usage via stream_options."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **kwargs,
        }
        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"
        return await self._client.chat.completions.create(**params)

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(
            model=self._model, input=text
        )
        return resp.data[0].embedding
