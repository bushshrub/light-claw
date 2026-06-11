"""Subagent tools: spawn parallel agent instances for concurrent workstreams."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from lightclaw.tools.registry import get_default_registry

_reg = get_default_registry()


@_reg.tool(
    description=(
        "Run a subagent with the given prompt and return its final response. "
        "When the LLM calls subagent_run multiple times in a single response, "
        "all calls execute in parallel. Use this to delegate independent subtasks "
        "concurrently. Subagents share the same tool registry (can use all tools "
        "including spawning further subagents)."
    )
)
async def subagent_run(
    prompt: str,
    label: str = "",
) -> str:
    """Spawn a subagent and return its response."""
    from lightclaw.agent.loop import AgentLoop
    from lightclaw.config import get_config

    cfg = get_config()
    agent = AgentLoop(cfg, get_default_registry())
    thread_id = f"subagent_{label or uuid.uuid4().hex[:8]}"
    return await agent.run(prompt, thread_id=thread_id)


@_reg.tool(
    description=(
        "Run multiple subagents in parallel and return all results. "
        "tasks is a list of objects, each with: "
        "'label' (short identifier, e.g. 'search', 'summarise') and "
        "'prompt' (the full task instruction for that subagent). "
        "All subagents run concurrently. "
        "Returns list of {label, result} objects. "
        "Use instead of multiple subagent_run calls when the task list is known upfront."
    )
)
async def subagent_team(
    tasks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Spawn multiple subagents in parallel, return [{label, result}, ...]."""
    from lightclaw.agent.loop import AgentLoop
    from lightclaw.config import get_config

    cfg = get_config()

    async def _run(label: str, prompt: str) -> dict[str, str]:
        agent = AgentLoop(cfg, get_default_registry())
        thread_id = f"subagent_{label or uuid.uuid4().hex[:8]}"
        try:
            result = await agent.run(prompt, thread_id=thread_id)
        except Exception as exc:
            result = f"Error: {exc}"
        return {"label": label, "result": result}

    return list(
        await asyncio.gather(*[
            _run(t.get("label", f"task_{i}"), t.get("prompt", ""))
            for i, t in enumerate(tasks)
        ])
    )
