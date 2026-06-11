"""Scheduler: cron + interval jobs that run agent tasks in the background."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


class Scheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict[str, Any] = {}

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def add_cron(
        self,
        job_id: str,
        fn: Callable,
        cron_expr: str,
        args: tuple = (),
        kwargs: dict | None = None,
    ) -> None:
        """Add a cron job. cron_expr: '* * * * *' (min hr dom mon dow)."""
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"Expected 5-part cron, got: {cron_expr!r}")
        minute, hour, day, month, day_of_week = parts
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
        job = self._scheduler.add_job(
            fn, trigger, id=job_id, args=args, kwargs=kwargs or {}, replace_existing=True
        )
        self._jobs[job_id] = job

    def add_interval(
        self,
        job_id: str,
        fn: Callable,
        seconds: int,
        args: tuple = (),
        kwargs: dict | None = None,
    ) -> None:
        trigger = IntervalTrigger(seconds=seconds)
        job = self._scheduler.add_job(
            fn, trigger, id=job_id, args=args, kwargs=kwargs or {}, replace_existing=True
        )
        self._jobs[job_id] = job

    def remove(self, job_id: str) -> bool:
        if job_id in self._jobs:
            self._scheduler.remove_job(job_id)
            del self._jobs[job_id]
            return True
        return False

    def list_jobs(self) -> list[dict[str, str]]:
        return [
            {
                "id": j.id,
                "next_run": str(j.next_run_time),
                "trigger": str(j.trigger),
            }
            for j in self._scheduler.get_jobs()
        ]

    def add_agent_task(
        self,
        job_id: str,
        prompt: str,
        cron_expr: str,
        agent_loop: Any,
        thread_id: str = "scheduler",
    ) -> None:
        """Schedule an agent run on a cron schedule."""
        async def _run() -> None:
            await agent_loop.run(prompt, thread_id=thread_id)

        self.add_cron(job_id, _run, cron_expr)
