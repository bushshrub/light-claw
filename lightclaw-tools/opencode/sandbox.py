"""Docker sandbox: runs opencode tasks in an isolated container."""

from __future__ import annotations

import asyncio
import os
import re
import sys

SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "lightclaw-opencode-sandbox:latest")

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


class Sandbox:
    def __init__(self, image: str = SANDBOX_IMAGE) -> None:
        self.image = image

    async def build_image(self, dockerfile_dir: str | None = None) -> None:
        """Build the sandbox Docker image from Dockerfile.sandbox."""
        context = dockerfile_dir or os.path.dirname(__file__)
        proc = await asyncio.create_subprocess_exec(
            "docker", "build",
            "-f", os.path.join(context, "Dockerfile.sandbox"),
            "-t", self.image,
            context,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(f"docker build failed (exit {rc})")

    async def run_task(
        self,
        task: str,
        workspace: str,
        model: str | None = None,
        timeout: int = 300,
    ) -> str:
        """Run opencode inside Docker with the workspace mounted."""
        if not os.path.isabs(workspace):
            raise ValueError(f"workspace must be an absolute path: {workspace!r}")
        if not os.path.isdir(workspace):
            raise ValueError(f"workspace directory does not exist: {workspace!r}")

        cmd = [
            "docker", "run", "--rm",
            "-v", f"{workspace}:/workspace",
            "--workdir", "/workspace",
            "--memory", "2g",
            "--cpus", "1.5",
            "--security-opt", "no-new-privileges",
            self.image,
            "run", task,
        ]
        if model:
            cmd += ["--model", model]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: task timed out after {timeout}s"

        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        return _strip_ansi(output).strip()
