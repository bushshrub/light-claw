"""Background job manager: submit prompts as async tasks, track status."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lightclaw.config import config_dir

if TYPE_CHECKING:
    from lightclaw.agent import AgentLoop

_HISTORY_LIMIT = 100


@dataclass
class Job:
    id: str
    prompt: str
    thread_id: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: str | None = None
    error: str | None = None
    source_routine_id: str | None = None  # set when spawned by a routine
    task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "thread_id": self.thread_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "source_routine_id": self.source_routine_id,
        }

    @property
    def elapsed(self) -> str:
        end = self.finished_at or time.time()
        secs = end - self.started_at
        if secs < 60:
            return f"{secs:.1f}s"
        return f"{secs / 60:.1f}m"


class JobManager:
    def __init__(self) -> None:
        self._agent: AgentLoop | None = None
        self._jobs: dict[str, Job] = {}
        self._history_path = os.path.join(config_dir(), "jobs.json")
        self._done_callbacks: list[Any] = []

    def set_agent(self, agent: AgentLoop) -> None:
        self._agent = agent

    def on_done(self, callback) -> None:
        """Register a callback(job) called when any job finishes."""
        self._done_callbacks.append(callback)

    # --- submission ---

    async def submit(
        self,
        prompt: str,
        job_id: str | None = None,
        thread_id: str = "jobs",
        source_routine_id: str | None = None,
    ) -> Job:
        if self._agent is None:
            raise RuntimeError("JobManager has no agent — call set_agent() first")
        if job_id is None:
            job_id = f"job_{int(time.time())}"
            suffix = 0
            while job_id in self._jobs:
                suffix += 1
                job_id = f"job_{int(time.time())}_{suffix}"

        job = Job(
            id=job_id,
            prompt=prompt,
            thread_id=thread_id,
            source_routine_id=source_routine_id,
        )
        self._jobs[job_id] = job
        job.task = asyncio.create_task(self._run(job), name=job_id)
        return job

    async def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        try:
            job.result = await self._agent.run(job.prompt, thread_id=job.thread_id)
            job.status = "completed"
        except asyncio.CancelledError:
            job.status = "cancelled"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
        finally:
            job.finished_at = time.time()
            self._persist(job)
            for cb in self._done_callbacks:
                try:
                    cb(job)
                except Exception:
                    pass

    # --- control ---

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or not job.task:
            return False
        if job.status in ("completed", "failed", "cancelled"):
            return False
        job.task.cancel()
        return True

    # --- queries ---

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status in ("pending", "running")]

    def list_all(self, limit: int = 20) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
        return jobs[:limit]

    # --- persistence ---

    def _persist(self, job: Job) -> None:
        os.makedirs(os.path.dirname(self._history_path), exist_ok=True)
        try:
            history = self._load_history()
            history = [h for h in history if h["id"] != job.id]
            history.insert(0, job.to_dict())
            history = history[:_HISTORY_LIMIT]
            with open(self._history_path, "w") as f:
                json.dump(history, f, indent=2)
        except Exception:
            pass

    def _load_history(self) -> list[dict]:
        if not os.path.exists(self._history_path):
            return []
        with open(self._history_path) as f:
            return json.load(f)

    def load_history(self) -> list[dict]:
        return self._load_history()
