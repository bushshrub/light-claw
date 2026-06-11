"""Routine engine: persistent cron + event-triggered agent tasks.

Routines survive restarts — stored in ~/.config/lightclaw/routines.json.
Supported trigger types:
  cron   — standard 5-part cron expression
  event  — named event; "startup" fires immediately when engine starts

Self-healing: when a routine job fails, the engine automatically submits
a heal job that asks the agent to diagnose and fix the routine. Capped at
3 heal attempts per routine per hour; disables the routine if exceeded.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lightclaw.config import config_dir
from lightclaw.console import console

if TYPE_CHECKING:
    from lightclaw.jobs import Job, JobManager

SUPPORTED_EVENTS = frozenset({"startup"})
_HEAL_MAX_PER_HOUR = 3

# Module-level singleton so tools can reach the running engine.
_running: RoutineEngine | None = None


def get_running() -> RoutineEngine | None:
    return _running


@dataclass
class Routine:
    id: str
    type: str        # "cron" | "event"
    trigger: str     # cron expr OR event name
    prompt: str
    enabled: bool = True
    thread_id: str = "routines"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RoutineEngine:
    def __init__(self) -> None:
        self._path = os.path.join(config_dir(), "routines.json")
        self._scheduler = AsyncIOScheduler()
        self._jobs: JobManager | None = None
        # routine_id -> list of heal attempt timestamps
        self._heal_attempts: dict[str, list[float]] = {}

    # --- persistence ---

    def load(self) -> list[Routine]:
        if not os.path.exists(self._path):
            return []
        with open(self._path) as f:
            return [Routine(**r) for r in json.load(f)]

    def _save(self, routines: list[Routine]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w") as f:
            json.dump([r.to_dict() for r in routines], f, indent=2)

    # --- CRUD ---

    def add(self, routine: Routine) -> None:
        routines = self.load()
        routines = [r for r in routines if r.id != routine.id]
        routines.append(routine)
        self._save(routines)
        if self._scheduler.running and routine.enabled and routine.type == "cron":
            self._register_cron(routine)

    def remove(self, routine_id: str) -> bool:
        routines = self.load()
        before = len(routines)
        routines = [r for r in routines if r.id != routine_id]
        if len(routines) == before:
            return False
        self._save(routines)
        self._unregister_cron(routine_id)
        return True

    def set_enabled(self, routine_id: str, enabled: bool) -> bool:
        routines = self.load()
        for r in routines:
            if r.id == routine_id:
                r.enabled = enabled
                self._save(routines)
                if enabled and r.type == "cron" and self._scheduler.running:
                    self._register_cron(r)
                elif not enabled:
                    self._unregister_cron(routine_id)
                return True
        return False

    def get(self, routine_id: str) -> Routine | None:
        return next((r for r in self.load() if r.id == routine_id), None)

    # --- lifecycle ---

    async def start(self, jobs: JobManager) -> None:
        global _running
        self._jobs = jobs
        self._scheduler.start()
        _running = self

        jobs.on_done(self._on_job_done)

        startup_routines = []
        for routine in self.load():
            if not routine.enabled:
                continue
            if routine.type == "cron":
                self._register_cron(routine)
            elif routine.type == "event" and routine.trigger == "startup":
                startup_routines.append(routine)

        for routine in startup_routines:
            await self._fire(routine)

    def stop(self) -> None:
        global _running
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        _running = None

    # --- scheduling ---

    def _register_cron(self, routine: Routine) -> None:
        parts = routine.trigger.split()
        if len(parts) != 5:
            return
        minute, hour, day, month, dow = parts
        trigger = CronTrigger(
            minute=minute, hour=hour, day=day, month=month, day_of_week=dow
        )

        async def _fire_cron() -> None:
            await self._fire(routine)

        self._scheduler.add_job(
            _fire_cron,
            trigger,
            id=f"routine_{routine.id}",
            replace_existing=True,
        )

    def _unregister_cron(self, routine_id: str) -> None:
        job_id = f"routine_{routine_id}"
        if self._scheduler.running:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass

    async def _fire(self, routine: Routine) -> None:
        if self._jobs is None:
            return
        job_id = f"routine_{routine.id}_{int(time.time())}"
        await self._jobs.submit(
            routine.prompt,
            job_id=job_id,
            thread_id=job_id,  # isolated per-run: no cross-contamination from prior runs
            source_routine_id=routine.id,
        )

    async def run_now(self, routine_id: str) -> bool:
        routine = self.get(routine_id)
        if routine is None:
            return False
        await self._fire(routine)
        return True

    def reload_cron(self) -> None:
        for job in self._scheduler.get_jobs():
            if job.id.startswith("routine_"):
                self._scheduler.remove_job(job.id)
        for routine in self.load():
            if routine.enabled and routine.type == "cron":
                self._register_cron(routine)

    # --- self-heal ---

    def _on_job_done(self, job: Job) -> None:
        """Sync callback from JobManager. Spawns heal task if needed."""
        if job.status != "failed":
            return
        if not job.source_routine_id:
            return
        # Don't heal heal-jobs (avoid infinite loops)
        if job.id.startswith("heal_"):
            return
        asyncio.create_task(
            self._heal(job.source_routine_id, job),
            name=f"heal_{job.source_routine_id}",
        )

    async def _heal(self, routine_id: str, failed_job: Job) -> None:
        if self._jobs is None:
            return
        routine = self.get(routine_id)
        if routine is None:
            return

        # Prune attempts older than 1 hour
        now = time.time()
        attempts = [t for t in self._heal_attempts.get(routine_id, []) if now - t < 3600]
        attempts.append(now)
        self._heal_attempts[routine_id] = attempts

        if len(attempts) > _HEAL_MAX_PER_HOUR:
            # Too many failures — disable and log
            self.set_enabled(routine_id, False)
            console.print(
                f"[red][routines] {routine_id}:[/red] disabled after "
                f"{_HEAL_MAX_PER_HOUR} failed heal attempts in 1 hour"
            )
            return

        remaining = _HEAL_MAX_PER_HOUR - len(attempts)
        heal_prompt = (
            f"[SELF-HEAL] Routine '{routine_id}' failed. Diagnose and fix it.\n\n"
            f"Routine details:\n"
            f"  id: {routine_id}\n"
            f"  type: {routine.type}\n"
            f"  trigger: {routine.trigger}\n"
            f"  prompt: {routine.prompt}\n\n"
            f"Error from last run:\n  {failed_job.error}\n\n"
            f"You have {remaining} heal attempt(s) left before the routine is auto-disabled.\n\n"
            f"Actions available:\n"
            f"  - routine_add: update the routine's prompt (same id overwrites)\n"
            f"  - routine_disable: disable if unfixable right now\n"
            f"  - routine_enable: re-enable if you think the error was transient\n"
            f"  - memory_set: log a note about the failure for future reference\n\n"
            f"Choose the most appropriate fix and apply it."
        )

        await self._jobs.submit(
            heal_prompt,
            job_id=f"heal_{routine_id}_{int(now)}",
            thread_id=f"heal_{routine.thread_id}",
        )
        console.print(
            f"[yellow][routines] {routine_id}:[/yellow] heal job submitted "
            f"(attempt [cyan]{len(attempts)}/{_HEAL_MAX_PER_HOUR}[/cyan])"
        )
