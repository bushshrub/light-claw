"""Skills: named, reusable prompt templates the agent can define and invoke.

Local skills live in ~/.config/lightclaw/skills.json.
Remote skills are fetched from a registry (JSON array of Skill objects).
Registry URL is configurable via LIGHTCLAW_SKILLS_REGISTRY env var.
"""

from __future__ import annotations

import json
import os
import string
from dataclasses import asdict, dataclass, field
from typing import Any

from lightclaw.config import config_dir
from lightclaw.tools.registry import get_default_registry

_reg = get_default_registry()
_SKILLS_PATH = os.path.join(config_dir(), "skills.json")
_DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/bushshrub/light-claw/master/skills-registry.json"
)


def get_registry_url() -> str:
    return os.environ.get("LIGHTCLAW_SKILLS_REGISTRY", _DEFAULT_REGISTRY_URL)


@dataclass
class Skill:
    id: str
    description: str
    prompt: str
    params: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load() -> list[Skill]:
    if not os.path.exists(_SKILLS_PATH):
        return []
    with open(_SKILLS_PATH) as f:
        return [Skill(**s) for s in json.load(f)]


def _save(skills: list[Skill]) -> None:
    os.makedirs(os.path.dirname(_SKILLS_PATH), exist_ok=True)
    with open(_SKILLS_PATH, "w") as f:
        json.dump([s.to_dict() for s in skills], f, indent=2)


def _extract_params(prompt: str) -> list[str]:
    return list(dict.fromkeys(
        fname for _, fname, _, _ in string.Formatter().parse(prompt) if fname
    ))


async def _fetch_registry() -> list[dict] | str:
    """Fetch the remote skills registry. Returns list on success, error string on failure."""
    try:
        import httpx
    except ImportError:
        return "httpx not installed — run: uv add httpx"
    url = get_registry_url()
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={"User-Agent": "lightclaw/0.1"})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        return f"Failed to fetch registry ({url}): {exc}"


# ---------------------------------------------------------------------------
# Local CRUD tools
# ---------------------------------------------------------------------------

@_reg.tool(description=(
    "Define a new skill (or overwrite an existing one). "
    "id is a short kebab-case name (e.g. 'summarize', 'code-review'). "
    "prompt is a template — use {variable} placeholders for runtime substitution. "
    "description explains what the skill does."
))
def skill_add(id: str, description: str, prompt: str) -> str:
    skills = _load()
    skills = [s for s in skills if s.id != id]
    params = _extract_params(prompt)
    skills.append(Skill(id=id, description=description, prompt=prompt, params=params))
    _save(skills)
    param_str = f" (params: {params})" if params else ""
    return f"Skill '{id}' saved{param_str}"


@_reg.tool(description="List all locally installed skills with id, description, and declared params.")
def skill_list() -> list[dict]:
    return [
        {"id": s.id, "description": s.description, "params": s.params, "prompt": s.prompt}
        for s in _load()
    ]


@_reg.tool(description=(
    "Update an existing skill. Only non-empty fields are changed. "
    "Changing prompt automatically re-derives the params list."
))
def skill_update(id: str, description: str = "", prompt: str = "") -> str:
    skills = _load()
    for s in skills:
        if s.id == id:
            if description:
                s.description = description
            if prompt:
                s.prompt = prompt
                s.params = _extract_params(prompt)
            _save(skills)
            return f"Skill '{id}' updated"
    return f"Skill '{id}' not found"


@_reg.tool(description="Delete a locally installed skill by id.")
def skill_delete(id: str) -> str:
    skills = _load()
    before = len(skills)
    skills = [s for s in skills if s.id != id]
    if len(skills) == before:
        return f"Skill '{id}' not found"
    _save(skills)
    return f"Skill '{id}' deleted"


@_reg.tool(description=(
    "Run a locally installed skill by id. "
    "params is a dict mapping {variable} placeholder names to their values "
    "(e.g. {\"topic\": \"Python async\"}). "
    "The expanded prompt runs as a subagent and its result is returned."
))
async def skill_run(id: str, params: dict[str, str] | None = None) -> str:
    skill = next((s for s in _load() if s.id == id), None)
    if skill is None:
        return f"Skill '{id}' not found. Use skill_list() to see available skills."
    try:
        prompt = skill.prompt.format(**(params or {}))
    except KeyError as exc:
        return (
            f"Missing required param {exc.args[0]!r} for skill '{id}'. "
            f"Required params: {skill.params}"
        )
    except Exception as exc:
        return f"Failed to expand skill '{id}' template: {exc}"

    from lightclaw.agent.loop import AgentLoop
    from lightclaw.config import get_config
    agent = AgentLoop(get_config(), get_default_registry())
    return await agent.run(prompt, thread_id=f"skill_{id}")


# ---------------------------------------------------------------------------
# Registry tools
# ---------------------------------------------------------------------------

@_reg.tool(description=(
    "Search the remote skills registry by keyword. "
    "Matches against skill id and description. "
    "Returns matching skills with id, description, and params."
))
async def skill_search(query: str) -> list[dict] | str:
    data = await _fetch_registry()
    if isinstance(data, str):
        return data  # error message
    q = query.lower()
    matches = [
        {"id": s["id"], "description": s["description"], "params": s.get("params", [])}
        for s in data
        if q in s["id"].lower() or q in s.get("description", "").lower()
    ]
    return matches if matches else f"No registry skills match {query!r}"


@_reg.tool(description=(
    "Install a skill from the remote registry by id. "
    "Downloads the skill definition and saves it locally. "
    "Overwrites any existing local skill with the same id."
))
async def skill_install(id: str) -> str:
    data = await _fetch_registry()
    if isinstance(data, str):
        return data  # error message
    entry = next((s for s in data if s["id"] == id), None)
    if entry is None:
        available = [s["id"] for s in data]
        return f"Skill '{id}' not found in registry. Available: {available}"
    params = _extract_params(entry["prompt"])
    skills = _load()
    skills = [s for s in skills if s.id != id]
    skills.append(Skill(
        id=entry["id"],
        description=entry["description"],
        prompt=entry["prompt"],
        params=params,
    ))
    _save(skills)
    param_str = f" (params: {params})" if params else ""
    return f"Installed skill '{id}'{param_str}"
