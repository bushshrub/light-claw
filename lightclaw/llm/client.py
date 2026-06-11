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

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(
            model=self._model, input=text
        )
        return resp.data[0].embedding
