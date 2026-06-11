"""Tool registry: register Python functions as LLM-callable tools."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable

from pydantic import TypeAdapter

_PYTHON_TO_JSON: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "NoneType": "null",
}


def _type_to_json_schema(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    try:
        return TypeAdapter(annotation).json_schema()
    except Exception:
        name = getattr(annotation, "__name__", str(annotation))
        return {"type": _PYTHON_TO_JSON.get(name, "string")}


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[Callable, dict[str, Any]]] = {}

    def register(
        self,
        fn: Callable,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        tool_name = name or fn.__name__
        doc = description or (inspect.getdoc(fn) or f"Call {tool_name}")
        sig = inspect.signature(fn)
        props: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            schema = _type_to_json_schema(param.annotation)
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            props[param_name] = schema

        spec: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": doc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }
        self._tools[tool_name] = (fn, spec)

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self.register(fn, name=name, description=description)
            return fn
        return decorator

    def schemas(self) -> list[dict[str, Any]]:
        return [spec for _, spec in self._tools.values()]

    async def call(self, name: str, arguments: str | dict[str, Any]) -> Any:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name!r}")
        fn, _ = self._tools[name]
        args = arguments if isinstance(arguments, dict) else json.loads(arguments)
        result = fn(**args)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def register_raw(
        self,
        name: str,
        spec: dict[str, Any],
        fn: Callable,
    ) -> None:
        """Register a pre-built OpenAI tool spec with an arbitrary callable."""
        self._tools[name] = (fn, spec)

    def names(self) -> list[str]:
        return list(self._tools)


# Module-level default registry + decorator
_default_registry = Registry()


def tool(
    name: str | None = None,
    description: str | None = None,
) -> Callable:
    return _default_registry.tool(name=name, description=description)


def get_default_registry() -> Registry:
    return _default_registry
