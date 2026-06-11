"""Rich-powered interactive REPL and Typer CLI for light-claw."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import platform
import re
import subprocess
import tempfile
import time
from typing import Annotated, Any

import typer
from prompt_toolkit import PromptSession as _PTSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML as _HTML
from prompt_toolkit.key_binding import KeyBindings as _KB
from prompt_toolkit.styles import Style as _PTStyle
from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from lightclaw.agent import AgentLoop
from lightclaw.config import Config, config_dir, get_config, set_config
from lightclaw.console import console
from lightclaw import log as _log  # noqa: F401  — installs RichHandler on import
from lightclaw.jobs import JobManager
from lightclaw.memory import Workspace
from lightclaw.mcp import MCPManager
from lightclaw.routines import RoutineEngine
from lightclaw.scheduler import Scheduler
from lightclaw.tools.registry import get_default_registry
from lightclaw.tools.builtins import set_issue_confirm_handler, reset_issue_confirm_handler

_EXT_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _parse_attachments(line: str) -> tuple[str, list[dict[str, Any]]]:
    """Extract @/absolute/path tokens from line; return (cleaned_line, attachments)."""
    attachments: list[dict[str, Any]] = []
    tokens = re.findall(r"@(/\S+)", line)
    for path in tokens:
        if not os.path.isfile(path):
            console.print(f"[yellow]Warning:[/yellow] not found: {path}")
        else:
            try:
                ext = os.path.splitext(path)[1].lower()
                mime = _EXT_MIME.get(ext) or (mimetypes.guess_type(path)[0] or "application/octet-stream")
                with open(path, "rb") as f:
                    data = f.read()
                attachments.append({
                    "type": "image" if ext in _EXT_MIME else "other",
                    "data": data,
                    "mime_type": mime,
                    "filename": os.path.basename(path),
                })
            except OSError as exc:
                console.print(f"[yellow]Warning:[/yellow] could not read {path}: {exc}")
        line = line.replace(f"@{path}", "").strip()
    return line, attachments


def _read_clipboard_image() -> tuple[bytes, str] | None:
    """Read image from clipboard. Returns (bytes, mime_type) or None.

    macOS: uses osascript to write PNG/JPEG clipboard data to a temp file.
    Linux: uses xclip.
    """
    sys = platform.system()
    if sys == "Darwin":
        for cls, ext, mime in [
            ("«class PNGf»", ".png", "image/png"),
            ("JPEG picture", ".jpg", "image/jpeg"),
        ]:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="lc_clip_") as tmp:
                tmp_path = tmp.name
            script = (
                f"try\n"
                f"  set d to (the clipboard as {cls})\n"
                f"  set f to open for access POSIX file \"{tmp_path}\" with write permission\n"
                f"  set eof f to 0\n"
                f"  write d to f\n"
                f"  close access f\n"
                f"  return \"ok\"\n"
                f"on error\n"
                f"  return \"\"\n"
                f"end try"
            )
            try:
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=5,
                )
                if result.stdout.strip() == "ok":
                    with open(tmp_path, "rb") as f:
                        data = f.read()
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    if data:
                        return data, mime
            except (subprocess.TimeoutExpired, OSError):
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    elif sys == "Linux":
        for mime in ("image/png", "image/jpeg"):
            try:
                result = subprocess.run(
                    ["xclip", "-sel", "clip", "-t", mime, "-o"],
                    capture_output=True, timeout=3,
                )
                if result.returncode == 0 and len(result.stdout) > 8:
                    return result.stdout, mime
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    return None


class _SlashCompleter(Completer):
    _CMDS: list[tuple[str, str]] = [
        ("/clear",          "clear conversation history"),
        ("/help",           "show help"),
        ("/history",        "show conversation history"),
        ("/jobs cancel ",   "/jobs cancel <id>"),
        ("/jobs list",      "list background jobs"),
        ("/jobs logs ",     "/jobs logs <id>"),
        ("/jobs run ",      "/jobs run <prompt>"),
        ("/memory del ",    "/memory del <key>"),
        ("/memory list",    "list stored notes"),
        ("/memory search ", "/memory search <q>"),
        ("/memory set ",    "/memory set <key> <value>"),
        ("/model",          "show model info"),
        ("/paste",          "paste image from clipboard"),
        ("/paste clear",    "clear pending image attachments"),
        ("/quit",           "exit"),
        ("/routines list",   "list routines"),
        ("/routines run ",   "/routines run <id>"),
        ("/schedule add ",   "/schedule add <id> <cron> <prompt>"),
        ("/skills install ", "/skills install <id>"),
        ("/skills list",     "list installed skills"),
        ("/skills remove ",  "/skills remove <id>"),
        ("/skills run ",     "/skills run <id> [key=val ...]"),
        ("/skills search ",  "/skills search <query>"),
        ("/schedule list",  "list scheduled tasks"),
        ("/schedule rm ",   "/schedule rm <id>"),
        ("/session",        "show token usage"),
        ("/suggest",        "ask the agent to analyse its source and suggest improvements"),
        ("/thread ",        "/thread <id>"),
        ("/tools",          "list registered tools"),
    ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for insert, desc in self._CMDS:
            if insert.startswith(text):
                yield Completion(
                    insert,
                    start_position=-len(text),
                    display=insert.rstrip(),
                    display_meta=desc,
                )


app = typer.Typer(help="light-claw: local agent OS", no_args_is_help=False)
mcp_app = typer.Typer(help="Manage MCP servers")
jobs_app = typer.Typer(help="Background jobs")
routines_app = typer.Typer(help="Persistent routines (cron + events)")
skills_app = typer.Typer(help="Manage and download skills")
app.add_typer(mcp_app, name="mcp")
app.add_typer(jobs_app, name="jobs")
app.add_typer(routines_app, name="routines")
app.add_typer(skills_app, name="skills")

SLASH_HELP = """
[bold]Slash commands[/bold]
  [cyan]/help[/cyan]                          show this help
  [cyan]/memory list[/cyan]                   list all stored notes
  [cyan]/memory search <q>[/cyan]             full-text search notes
  [cyan]/memory set <key> <value>[/cyan]      store a note
  [cyan]/memory del <key>[/cyan]              delete a note
  [cyan]/session[/cyan]                       show session token usage
  [cyan]/model[/cyan]                         show model name and context length
  [cyan]/tools[/cyan]                         list registered tools (incl. MCP)
  [cyan]/history[/cyan]                       show conversation history
  [cyan]/clear[/cyan]                         clear conversation history
  [cyan]/paste[/cyan]                         attach image from clipboard (or Ctrl+Y)
  [cyan]/paste clear[/cyan]                   discard pending image attachments
  [cyan]/jobs list[/cyan]                     list background jobs
  [cyan]/jobs logs <id>[/cyan]                show job result/error
  [cyan]/jobs cancel <id>[/cyan]              cancel a running job
  [cyan]/jobs run <prompt>[/cyan]             submit prompt as background job
  [cyan]/routines list[/cyan]                 list persistent routines
  [cyan]/routines run <id>[/cyan]             trigger routine immediately
  [cyan]/skills list[/cyan]                   list installed skills
  [cyan]/skills search <q>[/cyan]             search remote skills registry
  [cyan]/skills install <id>[/cyan]           download and install a skill
  [cyan]/skills remove <id>[/cyan]            delete an installed skill
  [cyan]/skills run <id> [key=val ...][/cyan] run a skill with params
  [cyan]/schedule list[/cyan]                 list in-session scheduled tasks
  [cyan]/schedule add <id> <m h d M dow> <prompt>[/cyan]
                                 schedule agent prompt on cron
  [cyan]/schedule rm <id>[/cyan]              remove scheduled task
  [cyan]/suggest[/cyan]                       ask the agent to analyse its source and suggest improvements
  [cyan]/connectors list[/cyan]              list connectors (discord, signal) and status
  [cyan]/connectors enable <name>[/cyan]     start a connector in the background
  [cyan]/connectors disable <name>[/cyan]    stop a running connector
  [cyan]/thread <id>[/cyan]                   switch conversation thread
  [cyan]/quit[/cyan]                          exit

[bold]Image paste[/bold]
  Copy an image to clipboard, then press [cyan]Ctrl+Y[/cyan] or type [cyan]/paste[/cyan].
  Pending images are shown in the toolbar (📎 N) and sent with your next message.
  Attach files directly with [cyan]@/absolute/path[/cyan] in your message.
"""

_CONNECTOR_NAMES = ("discord", "signal")


class _ConnectorManager:
    """Manages background connector (Discord, Signal) tasks within the REPL."""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def status(self, name: str) -> str:
        entry = self._entries.get(name)
        if entry is None:
            return "stopped"
        return "running" if not entry["task"].done() else "stopped"

    def list(self) -> list[tuple[str, str]]:
        return [(name, self.status(name)) for name in _CONNECTOR_NAMES]

    async def enable(
        self, name: str, cfg: "Config", workspace: "Workspace", registry: Any
    ) -> str:
        if self.status(name) == "running":
            return f"{name} already running"

        if name == "discord":
            token = os.environ.get("DISCORD_BOT_TOKEN")
            if not token:
                return "DISCORD_BOT_TOKEN not set — export it or add to .env"
            from lightclaw.channels import DiscordBot
            bot = DiscordBot(token, cfg, workspace, registry)
            task = asyncio.create_task(bot.start(), name=f"connector_discord")
            self._entries["discord"] = {"bot": bot, "task": task}
            return "discord connector started"

        if name == "signal":
            phone = os.environ.get("SIGNAL_PHONE_NUMBER")
            if not phone:
                return "SIGNAL_PHONE_NUMBER not set — export it or add to .env"
            sig_cfg_dir = os.environ.get("SIGNAL_CONFIG_DIR")
            from lightclaw.channels.signal_bot import SignalBot
            bot = SignalBot(phone, sig_cfg_dir, cfg, workspace, registry)
            task = asyncio.create_task(bot.start(), name=f"connector_signal")
            self._entries["signal"] = {"bot": bot, "task": task}
            return "signal connector started"

        return f"unknown connector {name!r} — available: {', '.join(_CONNECTOR_NAMES)}"

    async def disable(self, name: str) -> str:
        if name not in _CONNECTOR_NAMES:
            return f"unknown connector {name!r} — available: {', '.join(_CONNECTOR_NAMES)}"
        if self.status(name) != "running":
            return f"{name} is not running"
        entry = self._entries[name]
        await entry["bot"].close()
        entry["task"].cancel()
        return f"{name} connector stopped"

    async def stop_all(self) -> None:
        for name in list(self._entries):
            if self.status(name) == "running":
                try:
                    await self._entries[name]["bot"].close()
                    self._entries[name]["task"].cancel()
                except Exception:
                    pass


class ReplSession:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.workspace = Workspace(config)
        self.registry = get_default_registry()
        self.mcp = MCPManager()
        self.agent = AgentLoop(config, self.registry, self.workspace)
        self._job_agent = AgentLoop(config, self.registry, self.workspace)
        self.scheduler = Scheduler()
        self.job_manager = JobManager()
        self.routines = RoutineEngine()
        self.connectors = _ConnectorManager()
        self.thread_id = "default"
        self.last_ttft: float | None = None
        self.job_notification: str | None = None

    async def start(self) -> None:
        await self.workspace.open()
        await self.mcp.start(self.registry)
        self.scheduler.start()
        self.job_manager.set_agent(self._job_agent)
        self.job_manager.on_done(self._on_job_done)
        await self.routines.start(self.job_manager)

    async def stop(self) -> None:
        await self.connectors.stop_all()
        self.routines.stop()
        self.scheduler.stop()
        await self.mcp.stop()
        await self.workspace.close()

    def _on_job_done(self, job) -> None:
        icon = "✓" if job.status == "completed" else "✗"
        self.job_notification = f"{icon} {job.id} {job.status} ({job.elapsed})"

    async def handle_slash(self, line: str) -> bool:
        parts = line.strip().split(None, 3)
        cmd = parts[0].lower()

        if cmd in ("/quit", "/exit"):
            raise KeyboardInterrupt

        if cmd == "/help":
            console.print(SLASH_HELP)
            return True

        if cmd == "/session":
            stats = self.agent.token_stats
            ctx = self.agent.context_length
            t = Table("Metric", "Value", title="Token Usage")
            t.add_row("Prompt tokens", f"{stats['prompt']:,}")
            t.add_row("Completion tokens", f"{stats['completion']:,}")
            t.add_row("Total tokens", f"{stats['total']:,}")
            if ctx:
                t.add_row("Context length", f"{ctx:,}")
                if stats["total"] > 0:
                    t.add_row("Context used", f"{stats['total'] / ctx * 100:.1f}%")
            console.print(t)
            return True

        if cmd == "/model":
            ctx = self.agent.context_length
            console.print(f"model=[yellow]{self.config.model}[/yellow]  context_length=[yellow]{ctx if ctx else 'unknown'}[/yellow]")
            return True

        if cmd == "/tools":
            t = Table("Name", "Description", title="Registered Tools")
            for spec in self.registry.schemas():
                fn = spec["function"]
                t.add_row(fn["name"], fn.get("description", ""))
            console.print(t)
            return True

        if cmd == "/history":
            history = await self.workspace.get_history(self.thread_id)
            for msg in history:
                color = "green" if msg["role"] == "assistant" else "blue"
                console.print(f"[{color}]{msg['role']}:[/{color}] {msg['content']}")
            return True

        if cmd == "/clear":
            await self.workspace.clear_history(self.thread_id)
            console.print("[yellow]History cleared.[/yellow]")
            return True

        if cmd == "/thread" and len(parts) >= 2:
            self.thread_id = parts[1]
            console.print(f"[yellow]Thread → {self.thread_id}[/yellow]")
            return True

        if cmd == "/memory":
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "list":
                notes = await self.workspace.list_notes()
                t = Table("Key", "Value", "Tags", title="Memory")
                for n in notes:
                    t.add_row(n["key"], n["value"][:80], ", ".join(n["tags"]))
                console.print(t)
            elif sub == "search" and len(parts) >= 3:
                results = await self.workspace.search(parts[2])
                t = Table("Key", "Value", title=f"Search: {parts[2]}")
                for r in results:
                    t.add_row(r["key"], r["value"][:80])
                console.print(t)
            elif sub == "set" and len(parts) >= 4:
                await self.workspace.remember(parts[2], parts[3])
                console.print(f"[green]Stored:[/green] {parts[2]}")
            elif sub == "del" and len(parts) >= 3:
                ok = await self.workspace.forget(parts[2])
                console.print("[green]Deleted[/green]" if ok else "[red]Not found[/red]")
            else:
                console.print("[red]Usage: /memory list|search|set|del[/red]")
            return True

        if cmd == "/jobs":
            sub = parts[1].lower() if len(parts) > 1 else "list"
            if sub == "list":
                jobs = self.job_manager.list_all()
                if not jobs:
                    history = self.job_manager.load_history()
                    jobs = [
                        type("J", (), d)()  # type: ignore[call-arg]
                        for d in history[:10]
                    ]
                t = Table("ID", "Status", "Elapsed", "Prompt", title="Jobs")
                for j in self.job_manager.list_all():
                    status_color = {
                        "completed": "green", "failed": "red",
                        "running": "cyan", "cancelled": "yellow",
                    }.get(j.status, "white")
                    t.add_row(
                        j.id,
                        f"[{status_color}]{j.status}[/{status_color}]",
                        j.elapsed,
                        j.prompt[:60],
                    )
                if t.row_count == 0:
                    console.print("[dim]No jobs.[/dim]")
                else:
                    console.print(t)
            elif sub == "logs" and len(parts) >= 3:
                job = self.job_manager.get(parts[2])
                if not job:
                    console.print(f"[red]Job {parts[2]!r} not found[/red]")
                elif job.error:
                    console.print(Panel(job.error, title=f"Error: {job.id}", border_style="red"))
                elif job.result:
                    console.print(Panel(Markdown(job.result), title=f"Result: {job.id}"))
                else:
                    console.print(f"[dim]Job {job.id} status: {job.status}[/dim]")
            elif sub == "cancel" and len(parts) >= 3:
                ok = self.job_manager.cancel(parts[2])
                console.print("[green]Cancelled[/green]" if ok else "[red]Cannot cancel (not running or not found)[/red]")
            elif sub == "run":
                prompt = " ".join(line.strip().split(None)[2:])
                if not prompt:
                    console.print("[red]Usage: /jobs run <prompt>[/red]")
                else:
                    job = await self.job_manager.submit(prompt, thread_id=f"job_{self.thread_id}")
                    console.print(f"[green]Submitted:[/green] {job.id}")
            else:
                console.print("[red]Usage: /jobs list|logs|cancel|run[/red]")
            return True

        if cmd == "/routines":
            sub = parts[1].lower() if len(parts) > 1 else "list"
            if sub == "list":
                routines = self.routines.load()
                t = Table("ID", "Type", "Trigger", "Enabled", "Prompt", title="Routines")
                for r in routines:
                    t.add_row(
                        r.id,
                        r.type,
                        r.trigger,
                        "[green]yes[/green]" if r.enabled else "[dim]no[/dim]",
                        r.prompt[:50],
                    )
                if t.row_count == 0:
                    console.print("[dim]No routines. Add with: lightclaw routines add[/dim]")
                else:
                    console.print(t)
            elif sub == "run" and len(parts) >= 3:
                ok = await self.routines.run_now(parts[2])
                console.print(
                    f"[green]Fired routine {parts[2]!r}[/green]"
                    if ok else f"[red]Routine {parts[2]!r} not found[/red]"
                )
            else:
                console.print("[red]Usage: /routines list|run[/red]")
            return True

        if cmd == "/connectors":
            sub = parts[1].lower() if len(parts) > 1 else "list"
            if sub == "list":
                t = Table("Connector", "Status", title="Connectors")
                for name, status in self.connectors.list():
                    color = "green" if status == "running" else "dim"
                    t.add_row(name, f"[{color}]{status}[/{color}]")
                console.print(t)
            elif sub == "enable" and len(parts) >= 3:
                msg = await self.connectors.enable(
                    parts[2].lower(), self.config, self.workspace, self.registry
                )
                console.print(f"[green]{msg}[/green]" if "started" in msg else f"[red]{msg}[/red]")
            elif sub == "disable" and len(parts) >= 3:
                msg = await self.connectors.disable(parts[2].lower())
                console.print(f"[yellow]{msg}[/yellow]" if "stopped" in msg else f"[red]{msg}[/red]")
            else:
                console.print("[red]Usage: /connectors list|enable <name>|disable <name>[/red]")
            return True

        if cmd == "/skills":
            sub = parts[1].lower() if len(parts) > 1 else "list"
            from lightclaw.tools.skills import _load as _skills_load, _save as _skills_save
            from lightclaw.tools.skills import Skill as _Skill, _extract_params, _fetch_registry
            if sub == "list":
                skills = _skills_load()
                if not skills:
                    console.print("[dim]No skills installed. Try /skills search <query>[/dim]")
                else:
                    t = Table("ID", "Description", "Params", title="Installed Skills")
                    for s in skills:
                        t.add_row(s.id, s.description, ", ".join(s.params) or "—")
                    console.print(t)
            elif sub == "search" and len(parts) >= 3:
                query = parts[2].lower()
                with console.status("Fetching registry..."):
                    data = await _fetch_registry()
                if isinstance(data, str):
                    console.print(f"[red]{data}[/red]")
                else:
                    matches = [s for s in data if query in s["id"].lower() or query in s.get("description", "").lower()]
                    if not matches:
                        console.print(f"[yellow]No results for {query!r}[/yellow]")
                    else:
                        t = Table("ID", "Description", "Params", title=f"Registry: {query!r}")
                        for s in matches:
                            params = s.get("params") or _extract_params(s.get("prompt", ""))
                            t.add_row(s["id"], s.get("description", ""), ", ".join(params) or "—")
                        console.print(t)
            elif sub == "install" and len(parts) >= 3:
                skill_id = parts[2]
                with console.status(f"Installing '{skill_id}'..."):
                    data = await _fetch_registry()
                if isinstance(data, str):
                    console.print(f"[red]{data}[/red]")
                else:
                    entry = next((s for s in data if s["id"] == skill_id), None)
                    if entry is None:
                        console.print(f"[red]'{skill_id}' not found in registry[/red]")
                        console.print(f"[dim]Try: /skills search <keyword>[/dim]")
                    else:
                        params = _extract_params(entry["prompt"])
                        skills = _skills_load()
                        skills = [s for s in skills if s.id != skill_id]
                        skills.append(_Skill(
                            id=entry["id"],
                            description=entry["description"],
                            prompt=entry["prompt"],
                            params=params,
                        ))
                        _skills_save(skills)
                        param_str = f"  params: {params}" if params else ""
                        console.print(f"[green]Installed:[/green] {skill_id}{param_str}")
            elif sub == "remove" and len(parts) >= 3:
                skill_id = parts[2]
                skills = _skills_load()
                before = len(skills)
                skills = [s for s in skills if s.id != skill_id]
                if len(skills) == before:
                    console.print(f"[red]Skill '{skill_id}' not found[/red]")
                else:
                    _skills_save(skills)
                    console.print(f"[green]Removed:[/green] {skill_id}")
            elif sub == "run" and len(parts) >= 3:
                skill_id = parts[2]
                # parse remaining tokens as key=value pairs
                raw_params = line.strip().split(None)[3:]
                params: dict[str, str] = {}
                for token in raw_params:
                    k, _, v = token.partition("=")
                    if k:
                        params[k] = v
                skills = _skills_load()
                skill = next((s for s in skills if s.id == skill_id), None)
                if skill is None:
                    console.print(f"[red]Skill '{skill_id}' not installed[/red]")
                else:
                    try:
                        prompt = skill.prompt.format(**params)
                    except KeyError as exc:
                        console.print(f"[red]Missing param {exc.args[0]!r} — required: {skill.params}[/red]")
                    else:
                        response = await self.agent.run(prompt, thread_id=f"skill_{skill_id}")
                        console.print(Markdown(response))
            else:
                console.print("[red]Usage: /skills list|search <q>|install <id>|remove <id>|run <id> [key=val ...][/red]")
            return True

        if cmd == "/schedule":
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "list":
                jobs = self.scheduler.list_jobs()
                t = Table("ID", "Next Run", "Trigger", title="Scheduled Jobs")
                for j in jobs:
                    t.add_row(j["id"], j["next_run"], j["trigger"])
                console.print(t)
            elif sub == "add":
                # /schedule add <id> <m h d M dow> <prompt>
                # After "add " we need: id(1) + cron(5) + prompt(1) = 7 tokens min
                rest_tokens = line.strip().split(None)[2:]  # skip /schedule add
                if len(rest_tokens) < 7:
                    console.print(
                        "[red]Usage: /schedule add <id> <m h d M dow> <prompt>[/red]"
                    )
                else:
                    job_id = rest_tokens[0]
                    cron = " ".join(rest_tokens[1:6])
                    prompt = " ".join(rest_tokens[6:])
                    self.scheduler.add_agent_task(
                        job_id, prompt, cron, self.agent, self.thread_id
                    )
                    console.print(f"[green]Scheduled:[/green] {job_id} [{cron}]")
            elif sub == "rm" and len(parts) >= 3:
                ok = self.scheduler.remove(parts[2])
                console.print("[green]Removed[/green]" if ok else "[red]Job not found[/red]")
            else:
                console.print("[red]Usage: /schedule list|add|rm[/red]")
            return True

        return False


def _make_image_attachment(data: bytes, mime: str) -> dict[str, Any]:
    return {
        "type": "image",
        "data": data,
        "mime_type": mime,
        "filename": f"clipboard.{'png' if 'png' in mime else 'jpg'}",
    }


async def _repl(config: Config) -> None:
    session = ReplSession(config)
    await session.start()

    console.print(Panel(
        "[bold cyan]light-claw[/bold cyan]  local agent OS\n"
        f"model=[yellow]{config.model}[/yellow]  "
        f"base=[yellow]{config.base_url}[/yellow]  "
        f"db=[yellow]{config.db_path}[/yellow]\n"
        "Type [cyan]/help[/cyan] for commands or [cyan]/quit[/cyan] to exit.\n"
        "Paste images with [cyan]Ctrl+Y[/cyan] or [cyan]/paste[/cyan].",
        title="light-claw",
        border_style="cyan",
    ))

    _pending_image_attachments: list[dict[str, Any]] = []

    def _toolbar() -> _HTML:
        stats = session.agent.token_stats
        total = stats.get("total", 0)
        ctx_known = session.agent.context_length is not None
        ctx = session.agent.context_length or 128_000
        pct = min(total / ctx * 100, 100) if total else 0.0
        bar_w = 20
        filled = round(pct / 100 * bar_w)
        bar = "█" * filled + " " * (bar_w - filled)
        color = "ansigreen" if pct < 50 else "ansiyellow" if pct < 80 else "ansired"
        est = "~" if not ctx_known else ""
        tok_label = f"{est}{total/1000:.1f}K/{ctx/1000:.1f}K"
        ttft_str = f"  ttft {session.last_ttft:.2f}s" if session.last_ttft is not None else ""
        attach_str = f"  📎 {len(_pending_image_attachments)}" if _pending_image_attachments else ""
        return _HTML(
            f" {tok_label}"
            f"  [<style fg='{color}'>{bar}</style>]"
            f"{ttft_str}{attach_str}"
        )

    def _routines_panel() -> Text:
        t = Text()
        t.append("jobs\n", style="cyan bold")
        t.append("─" * 20 + "\n", style="dim")
        jobs = session.job_manager.list_all(limit=8)
        for j in jobs:
            if j.status == "completed":
                icon, style = "✓", "green"
            elif j.status == "failed":
                icon, style = "✗", "red"
            else:
                icon, style = "⏳", "cyan"
            name = j.id if len(j.id) <= 20 else j.id[:17] + "..."
            t.append(f"{icon} ", style=style)
            t.append(f"{name}\n", style="dim")
            t.append(f"  {j.elapsed}\n", style="dim")
        if not jobs:
            t.append("no jobs yet", style="dim")
        return t

    kb = _KB()

    @kb.add("c-y")
    def _paste_image_from_clipboard(event) -> None:
        result = _read_clipboard_image()
        if result is not None:
            data, mime = result
            _pending_image_attachments.append(_make_image_attachment(data, mime))
            event.app.invalidate()

    pt_session: _PTSession = _PTSession(
        completer=_SlashCompleter(),
        bottom_toolbar=_toolbar,
        complete_while_typing=True,
        complete_in_thread=True,
        key_bindings=kb,
        style=_PTStyle.from_dict({
            "bottom-toolbar": "noreverse bg:#111111 #666666",
            "bottom-toolbar.text": "noreverse bg:#111111 #666666",
        }),
    )

    try:
        while True:
            try:
                line = await pt_session.prompt_async(
                    _HTML(f"<ansibrightcyan><b>({session.thread_id})</b></ansibrightcyan> "),
                )
            except (EOFError, KeyboardInterrupt):
                break
            if not line.strip():
                continue

            # Handle /paste before generic slash dispatch (needs _pending_image_attachments).
            if line.strip().lower().startswith("/paste"):
                sub = line.strip().split(None, 1)[1].lower() if len(line.strip().split(None)) > 1 else ""
                if sub == "clear":
                    _pending_image_attachments.clear()
                    console.print("[yellow]Pending attachments cleared.[/yellow]")
                else:
                    result = await asyncio.to_thread(_read_clipboard_image)
                    if result is not None:
                        data, mime = result
                        _pending_image_attachments.append(_make_image_attachment(data, mime))
                        n = len(_pending_image_attachments)
                        console.print(
                            f"[green]Attached[/green] {mime} image "
                            f"({len(data):,} bytes)  —  {n} pending"
                        )
                    else:
                        console.print("[yellow]No image in clipboard.[/yellow]")
                continue

            # /suggest — replace with a canned analysis prompt, then fall through to agent.
            if line.strip().lower() == "/suggest":
                line = (
                    "Use lightclaw_read_source to explore your own source code and identify "
                    "concrete improvements. Start with the project structure (''), then read "
                    "the most relevant modules. Produce a prioritised list of 3–5 specific, "
                    "actionable suggestions (bugs first, then UX friction, then missing "
                    "features). For each: state what the problem is, where in the code it "
                    "lives, and what the fix would be."
                )
                # fall through to agent stream below

            if line.startswith("/"):
                try:
                    await session.handle_slash(line)
                except KeyboardInterrupt:
                    break
                continue

            line, text_attachments = _parse_attachments(line)
            all_attachments = list(_pending_image_attachments) + text_attachments
            _pending_image_attachments.clear()

            if not line and not all_attachments:
                continue

            stats_before = dict(session.agent.token_stats)
            start_time = time.perf_counter()
            first_token_time: float | None = None
            response_text = ""
            chunk_chars = 0

            def _status_line(exact_msg: int | None = None) -> Text:
                ctx_known = session.agent.context_length is not None
                ctx = session.agent.context_length or 128_000
                stats = session.agent.token_stats
                session_total = stats.get("total", 0)
                ttft = f"{first_token_time:.2f}s" if first_token_time is not None else "—"
                if exact_msg is not None:
                    tok_part = f"{exact_msg:,} tok"
                else:
                    approx = max(1, chunk_chars // 4)
                    tok_part = f"~{approx:,} tok"
                pct = min(session_total / ctx * 100, 100)
                bar_width = 20
                filled = round(pct / 100 * bar_width)
                bar = "█" * filled + " " * (bar_width - filled)
                bar_style = "green" if pct < 50 else "yellow" if pct < 80 else "red"
                est = "~" if not ctx_known else ""
                tok_label = f"{est}{session_total/1000:.1f}K/{ctx/1000:.1f}K"
                line = Text(style="dim")
                line.append(f"ttft {ttft}  {tok_part}  {tok_label}  ")
                line.append("[", style="dim")
                line.append(bar, style=bar_style)
                line.append("]", style="dim")
                return line

            live = Live(console=console, refresh_per_second=15)
            live.start()
            _live_stopped = False

            def _live_grid(main, sidebar) -> Table:
                grid = Table.grid(padding=(0, 1), expand=True)
                grid.add_column(ratio=3)
                grid.add_column(ratio=1, min_width=22)
                grid.add_row(main, sidebar)
                return grid

            async def _tick() -> None:
                while first_token_time is None:
                    elapsed = time.perf_counter() - start_time
                    live.update(_live_grid(
                        Text(f"⏱ {elapsed:.1f}s", style="dim"),
                        _routines_panel(),
                    ))
                    await asyncio.sleep(0.05)

            async def _confirm_with_live(
                title: str, body_preview: str, tracker_name: str
            ) -> bool:
                nonlocal _live_stopped
                if not _live_stopped:
                    live.stop()
                    _live_stopped = True
                console.print(
                    f"\n[cyan]\\[issue][/cyan] Ready to file on [bold]{tracker_name}[/bold]:"
                )
                console.print(f"  Title: {title}")
                if body_preview:
                    console.print(f"  Body: [dim]{body_preview[:300]}[/dim]")
                ans = await asyncio.to_thread(
                    lambda: Prompt.ask(
                        "  File this issue?", choices=["y", "n"], default="n"
                    )
                )
                return ans == "y"

            tick_task = asyncio.create_task(_tick())
            _confirm_token = set_issue_confirm_handler(_confirm_with_live)

            try:
                async for chunk in session.agent.stream(
                    line, thread_id=session.thread_id, attachments=all_attachments or None
                ):
                    if not chunk:
                        continue
                    if first_token_time is None:
                        first_token_time = time.perf_counter() - start_time
                    response_text += chunk
                    chunk_chars += len(chunk)
                    if not _live_stopped:
                        live.update(Group(
                            _live_grid(Markdown(response_text), _routines_panel()),
                            _status_line(),
                        ))
                    else:
                        console.print(chunk, end="", highlight=False)
            except Exception:
                tick_task.cancel()
                if not _live_stopped:
                    live.stop()
                raise
            else:
                tick_task.cancel()
                session.last_ttft = first_token_time
                stats_after = session.agent.token_stats
                msg_tok = stats_after.get("completion", 0) - stats_before.get("completion", 0)
                if not _live_stopped:
                    if response_text:
                        live.update(Group(
                            _live_grid(Markdown(response_text), _routines_panel()),
                            _status_line(exact_msg=msg_tok),
                        ))
                    live.stop()
                else:
                    console.print()
                    console.print(_status_line(exact_msg=msg_tok))
            finally:
                reset_issue_confirm_handler(_confirm_token)
    finally:
        await session.stop()
        console.print("[dim]Goodbye.[/dim]")


@app.command()
def repl(
    base_url: Annotated[str | None, typer.Option("--base-url", "-b")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", "-k")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
    db: Annotated[str | None, typer.Option("--db")] = None,
) -> None:
    """Start interactive REPL."""
    cfg = get_config()
    if base_url:
        cfg.base_url = base_url
    if api_key:
        cfg.api_key = api_key
    if model:
        cfg.model = model
    if db:
        cfg.db_path = db
    set_config(cfg)
    asyncio.run(_repl(cfg))


@app.command()
def run(
    message: Annotated[str, typer.Argument(help="Message to send")],
    base_url: Annotated[str | None, typer.Option("--base-url", "-b")] = None,
    api_key: Annotated[str | None, typer.Option("--api-key", "-k")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
    thread: Annotated[str, typer.Option("--thread", "-t")] = "default",
) -> None:
    """Send a single message and print response."""
    cfg = get_config()
    if base_url:
        cfg.base_url = base_url
    if api_key:
        cfg.api_key = api_key
    if model:
        cfg.model = model
    set_config(cfg)

    async def _go() -> None:
        mcp = MCPManager()
        registry = get_default_registry()
        async with Workspace(cfg) as ws:
            await mcp.start(registry)
            try:
                agent = AgentLoop(cfg, registry, ws)
                response = await agent.run(message, thread_id=thread)
                console.print(Markdown(response))
            finally:
                await mcp.stop()

    asyncio.run(_go())


@app.command()
def discord(
    token: Annotated[
        str | None,
        typer.Option("--token", "-t", envvar="DISCORD_BOT_TOKEN", help="Bot token"),
    ] = None,
) -> None:
    """Start Discord bot (responds to DMs and @mentions)."""
    if not token:
        console.print("[red]DISCORD_BOT_TOKEN required (--token or env var)[/red]")
        raise typer.Exit(1)

    async def _go() -> None:
        from lightclaw.channels import DiscordBot

        mcp = MCPManager()
        registry = get_default_registry()
        cfg = get_config()
        async with Workspace(cfg) as ws:
            await mcp.start(registry)
            try:
                bot = DiscordBot(token, cfg, ws, registry)
                console.print("[cyan]Discord bot starting...[/cyan]")
                await bot.start()
            finally:
                await mcp.stop()

    asyncio.run(_go())


@app.command()
def signal(
    phone: Annotated[
        str | None,
        typer.Option("--phone", "-p", envvar="SIGNAL_PHONE_NUMBER", help="Registered Signal phone number"),
    ] = None,
    config_dir: Annotated[
        str | None,
        typer.Option("--config-dir", envvar="SIGNAL_CONFIG_DIR", help="signal-cli config directory"),
    ] = None,
) -> None:
    """Start Signal bot (polls via signal-cli, responds to incoming messages)."""
    if not phone:
        console.print("[red]SIGNAL_PHONE_NUMBER required (--phone or env var)[/red]")
        raise typer.Exit(1)

    async def _go() -> None:
        from lightclaw.channels.signal_bot import SignalBot

        mcp = MCPManager()
        registry = get_default_registry()
        cfg = get_config()
        async with Workspace(cfg) as ws:
            await mcp.start(registry)
            try:
                bot = SignalBot(phone, config_dir, cfg, ws, registry)
                console.print("[cyan]Signal bot starting...[/cyan]")
                await bot.start()
            finally:
                await mcp.stop()

    asyncio.run(_go())


# --- MCP subcommands ---

@mcp_app.command("add")
def mcp_add(
    name: Annotated[str, typer.Argument(help="Server name (e.g. filesystem)")],
    command: Annotated[str, typer.Option("--command", "-c", help="Executable to run")],
    args: Annotated[list[str], typer.Option("--arg", "-a", help="Arg (repeat for each)")] = [],
    env: Annotated[list[str], typer.Option("--env", "-e", help="KEY=VALUE (repeat)")] = [],
) -> None:
    """Add an MCP server."""
    env_dict = {}
    for e in env:
        k, _, v = e.partition("=")
        env_dict[k] = v
    MCPManager().add_server(name, command, args, env_dict or None)
    console.print(f"[green]Added MCP server:[/green] {name}  ({command} {' '.join(args)})")
    console.print(f"Config: [dim]{MCPManager()._config_path}[/dim]")


@mcp_app.command("list")
def mcp_list() -> None:
    """List configured MCP servers."""
    servers = MCPManager().load_config()
    if not servers:
        console.print("[dim]No MCP servers configured.[/dim]")
        return
    t = Table("Name", "Command", "Args", title="MCP Servers")
    for name, cfg in servers.items():
        t.add_row(name, cfg["command"], " ".join(cfg.get("args", [])))
    console.print(t)


@mcp_app.command("remove")
def mcp_remove(
    name: Annotated[str, typer.Argument(help="Server name to remove")],
) -> None:
    """Remove an MCP server."""
    ok = MCPManager().remove_server(name)
    console.print("[green]Removed[/green]" if ok else f"[red]Not found: {name}[/red]")


@mcp_app.command("tools")
def mcp_tools(
    name: Annotated[str, typer.Argument(help="Server name")],
) -> None:
    """Connect to an MCP server and list its tools."""
    servers = MCPManager().load_config()
    if name not in servers:
        console.print(f"[red]Unknown server: {name}[/red]")
        raise typer.Exit(1)

    async def _go() -> None:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        cfg = servers[name]
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env") or None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                t = Table("Tool", "Description", title=f"MCP: {name}")
                for tool in result.tools:
                    t.add_row(tool.name, tool.description or "")
                console.print(t)

    asyncio.run(_go())


@mcp_app.command("test")
def mcp_test(
    name: Annotated[str, typer.Argument(help="Server name to test")],
    tool: Annotated[str | None, typer.Option("--tool", "-t", help="Tool to call directly")] = None,
    args: Annotated[list[str], typer.Option("--arg", "-a", help="key=value arg (repeat)")] = [],
) -> None:
    """Test an MCP server interactively or call a specific tool directly.

    Interactive mode (no --tool):
      Connects, lists tools, lets you pick one, prompts for each argument,
      calls it, and shows the result. Loops until you quit.

    Direct mode (--tool name --arg key=value ...):
      Calls the named tool with the given args and prints the result.

    Examples:
      lightclaw mcp test ddg
      lightclaw mcp test ddg --tool search --arg query="python asyncio"
    """
    servers = MCPManager().load_config()
    if name not in servers:
        console.print(f"[red]Unknown server: {name!r}. Run 'lightclaw mcp list'.[/red]")
        raise typer.Exit(1)

    async def _go() -> None:
        import json as _json

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from rich.panel import Panel
        from rich.syntax import Syntax

        cfg = servers[name]
        params = StdioServerParameters(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env") or None,
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                tool_map = {t.name: t for t in tools_result.tools}

                def _print_tools() -> None:
                    t = Table("#", "Tool", "Description", title=f"MCP: {name}")
                    for i, mcp_tool in enumerate(tools_result.tools, 1):
                        t.add_row(str(i), mcp_tool.name, mcp_tool.description or "")
                    console.print(t)

                def _call_args_from_flags() -> dict:
                    result = {}
                    for a in args:
                        k, _, v = a.partition("=")
                        # try to parse as JSON, fall back to string
                        try:
                            result[k] = _json.loads(v)
                        except _json.JSONDecodeError:
                            result[k] = v
                    return result

                def _prompt_args(mcp_tool) -> dict | None:
                    """Interactively prompt for each required + optional arg."""
                    schema = mcp_tool.inputSchema or {}
                    props = schema.get("properties", {})
                    required = schema.get("required", [])
                    if not props:
                        return {}

                    console.print(f"\n[bold]Arguments for [cyan]{mcp_tool.name}[/cyan]:[/bold]")
                    call_args = {}
                    for param, param_schema in props.items():
                        is_req = param in required
                        desc = param_schema.get("description", "")
                        type_hint = param_schema.get("type", "string")
                        label = (
                            f"  [{'red' if is_req else 'dim'}]{param}[/{'red' if is_req else 'dim'}]"
                            f" [dim]({type_hint}{'*' if is_req else ''})"
                            f"{' — ' + desc if desc else ''}[/dim]"
                        )
                        console.print(label)
                        try:
                            raw = Prompt.ask(f"    {param}", default="" if not is_req else ...)
                        except (EOFError, KeyboardInterrupt):
                            return None
                        if raw == "" and not is_req:
                            continue
                        try:
                            call_args[param] = _json.loads(raw)
                        except _json.JSONDecodeError:
                            call_args[param] = raw
                    return call_args

                def _show_result(result) -> None:
                    parts = []
                    for c in result.content:
                        if hasattr(c, "text"):
                            parts.append(c.text)
                        elif hasattr(c, "data"):
                            parts.append(f"[binary data, {len(c.data)} bytes]")
                    text = "\n".join(parts) if parts else "(no output)"
                    # render as syntax-highlighted JSON if parseable
                    try:
                        parsed = _json.loads(text)
                        pretty = _json.dumps(parsed, indent=2)
                        console.print(Panel(Syntax(pretty, "json", theme="monokai"), title="Result"))
                    except _json.JSONDecodeError:
                        console.print(Panel(text, title="Result"))

                # --- Direct mode ---
                if tool:
                    if tool not in tool_map:
                        console.print(f"[red]Tool {tool!r} not found on {name}[/red]")
                        _print_tools()
                        return
                    call_args = _call_args_from_flags()
                    console.print(
                        f"[dim]Calling [cyan]{tool}[/cyan] "
                        f"with {_json.dumps(call_args)}...[/dim]"
                    )
                    with console.status("running..."):
                        result = await session.call_tool(tool, call_args)
                    _show_result(result)
                    return

                # --- Interactive mode ---
                _print_tools()
                console.print(
                    "\n[dim]Enter tool name or number to call it. "
                    "[cyan]q[/cyan] to quit.[/dim]"
                )
                while True:
                    try:
                        choice = Prompt.ask("\n[bold cyan]tool[/bold cyan]")
                    except (EOFError, KeyboardInterrupt):
                        break
                    if choice.lower() in ("q", "quit", "exit"):
                        break

                    # resolve by number or name
                    selected = None
                    if choice.isdigit():
                        idx = int(choice) - 1
                        if 0 <= idx < len(tools_result.tools):
                            selected = tools_result.tools[idx]
                    elif choice in tool_map:
                        selected = tool_map[choice]

                    if selected is None:
                        console.print(f"[red]Unknown: {choice!r}[/red]")
                        continue

                    call_args = _prompt_args(selected)
                    if call_args is None:
                        break  # user hit ctrl-c during arg prompting

                    console.print(f"\n[dim]Calling [cyan]{selected.name}[/cyan]...[/dim]")
                    try:
                        with console.status("running..."):
                            result = await session.call_tool(selected.name, call_args)
                        _show_result(result)
                    except Exception as exc:
                        console.print(f"[red]Error: {exc}[/red]")

    asyncio.run(_go())


# Known built-in MCP server recipes.
_BUILTINS: dict[str, dict] = {
    "ddg": {
        "command": "uvx",
        "args": ["duckduckgo-mcp-server"],
        "env": {},
        "description": "DuckDuckGo web search + page fetch (nickclyde/duckduckgo-mcp-server)",
    },
    "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "env": {},
        "description": "Filesystem read/write tools — append a path arg after adding",
        "extra_args_hint": "<directory>",
    },
    "git": {
        "command": "uvx",
        "args": ["mcp-server-git"],
        "env": {},
        "description": "Git repository tools",
    },
    "sqlite": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sqlite"],
        "env": {},
        "description": "SQLite read tools — append a DB path arg after adding",
        "extra_args_hint": "<db-path>",
    },
}


@mcp_app.command("add-builtin")
def mcp_add_builtin(
    name: Annotated[
        str,
        typer.Argument(help=f"Built-in name: {', '.join(_BUILTINS)}"),
    ],
    alias: Annotated[
        str | None,
        typer.Option("--as", help="Override the server name stored in config"),
    ] = None,
    extra_args: Annotated[
        list[str],
        typer.Argument(help="Extra args appended after the built-in args (e.g. a path)"),
    ] = [],
    env: Annotated[
        list[str],
        typer.Option("--env", "-e", help="KEY=VALUE env var (repeat)"),
    ] = [],
) -> None:
    """Add a known MCP server by recipe name.

    Available: ddg, filesystem, git, sqlite

    Examples:
      lightclaw mcp add-builtin ddg
      lightclaw mcp add-builtin filesystem /Users/you/projects
      lightclaw mcp add-builtin sqlite /path/to/db.sqlite
    """
    recipe = _BUILTINS.get(name)
    if not recipe:
        console.print(f"[red]Unknown builtin: {name!r}[/red]")
        console.print(f"Available: {', '.join(_BUILTINS)}")
        raise typer.Exit(1)

    env_dict = dict(recipe.get("env", {}))
    for e in env:
        k, _, v = e.partition("=")
        env_dict[k] = v

    server_name = alias or name
    args = list(recipe["args"]) + list(extra_args)
    MCPManager().add_server(server_name, recipe["command"], args, env_dict or None)

    console.print(
        f"[green]Added:[/green] {server_name}  "
        f"({recipe['command']} {' '.join(args)})"
    )
    console.print(f"[dim]{recipe['description']}[/dim]")
    if recipe.get("extra_args_hint") and not extra_args:
        console.print(
            f"[yellow]Tip:[/yellow] this server usually needs a path — "
            f"re-add with: lightclaw mcp add-builtin {name} {recipe['extra_args_hint']}"
        )


@mcp_app.command("list-builtins")
def mcp_list_builtins() -> None:
    """List available built-in MCP server recipes."""
    t = Table("Name", "Command", "Description", title="Built-in MCP Recipes")
    for name, recipe in _BUILTINS.items():
        hint = recipe.get("extra_args_hint", "")
        cmd = f"{recipe['command']} {' '.join(recipe['args'])} {hint}".strip()
        t.add_row(name, cmd, recipe["description"])
    console.print(t)


# ---------------------------------------------------------------------------
# Jobs CLI
# ---------------------------------------------------------------------------

@jobs_app.command("list")
def jobs_list() -> None:
    """List background job history."""
    import time as _time
    from lightclaw.jobs import JobManager as _JM
    history = _JM().load_history()
    if not history:
        console.print("[dim]No job history.[/dim]")
        return
    t = Table("ID", "Status", "Prompt", "Started", title="Job History")
    for j in history:
        status_color = {
            "completed": "green", "failed": "red",
            "cancelled": "yellow",
        }.get(j.get("status", ""), "white")
        import datetime as _dt
        started = _dt.datetime.fromtimestamp(j["started_at"]).strftime("%m-%d %H:%M")
        t.add_row(
            j["id"],
            f"[{status_color}]{j.get('status', '?')}[/{status_color}]",
            j["prompt"][:60],
            started,
        )
    console.print(t)


@jobs_app.command("logs")
def jobs_logs(job_id: Annotated[str, typer.Argument()]) -> None:
    """Show result or error of a past job."""
    from lightclaw.jobs import JobManager as _JM
    history = _JM().load_history()
    entry = next((j for j in history if j["id"] == job_id), None)
    if not entry:
        console.print(f"[red]Job {job_id!r} not in history.[/red]")
        raise typer.Exit(1)
    if entry.get("error"):
        console.print(Panel(entry["error"], title=f"Error: {job_id}", border_style="red"))
    elif entry.get("result"):
        console.print(Panel(Markdown(entry["result"]), title=f"Result: {job_id}"))
    else:
        console.print(f"[dim]Status: {entry.get('status')}[/dim]")


# ---------------------------------------------------------------------------
# Routines CLI
# ---------------------------------------------------------------------------

@routines_app.command("list")
def routines_list() -> None:
    """List all configured routines."""
    from lightclaw.routines import RoutineEngine as _RE
    routines = _RE().load()
    if not routines:
        console.print("[dim]No routines configured.[/dim]")
        return
    t = Table("ID", "Type", "Trigger", "Enabled", "Thread", "Prompt", title="Routines")
    for r in routines:
        t.add_row(
            r.id, r.type, r.trigger,
            "[green]yes[/green]" if r.enabled else "[dim]no[/dim]",
            r.thread_id, r.prompt[:50],
        )
    console.print(t)


@routines_app.command("add")
def routines_add(
    routine_id: Annotated[str, typer.Argument(help="Unique routine ID")],
    prompt: Annotated[str, typer.Option("--prompt", "-p", help="Agent prompt to run")],
    cron: Annotated[str | None, typer.Option("--cron", "-c", help="5-part cron expression")] = None,
    on: Annotated[str | None, typer.Option("--on", help=f"Event trigger: startup")] = None,
    thread: Annotated[str, typer.Option("--thread", help="Thread ID for history")] = "routines",
    disabled: Annotated[bool, typer.Option("--disabled", help="Add but don't enable")] = False,
) -> None:
    """Add a routine triggered by a cron schedule or an event.

    Examples:
      lightclaw routines add morning --cron "0 9 * * 1-5" --prompt "morning briefing"
      lightclaw routines add boot-check --on startup --prompt "check for urgent tasks"
    """
    from lightclaw.routines import Routine, RoutineEngine as _RE

    if cron and on:
        console.print("[red]Specify --cron or --on, not both.[/red]")
        raise typer.Exit(1)
    if not cron and not on:
        console.print("[red]Specify --cron <expr> or --on <event>.[/red]")
        raise typer.Exit(1)
    if on and on not in ("startup",):
        console.print(f"[red]Unknown event: {on!r}. Supported: startup[/red]")
        raise typer.Exit(1)
    if cron and len(cron.split()) != 5:
        console.print("[red]Cron must be 5 parts: minute hour day month dow[/red]")
        raise typer.Exit(1)

    routine = Routine(
        id=routine_id,
        type="cron" if cron else "event",
        trigger=cron or on,
        prompt=prompt,
        enabled=not disabled,
        thread_id=thread,
    )
    _RE().add(routine)
    from rich.markup import escape as _esc
    trigger_desc = f"cron [{cron}]" if cron else f"event [{on}]"
    console.print(f"[green]Added routine:[/green] {routine_id} ({_esc(trigger_desc)})")


@routines_app.command("remove")
def routines_remove(
    routine_id: Annotated[str, typer.Argument()],
) -> None:
    """Remove a routine."""
    from lightclaw.routines import RoutineEngine as _RE
    ok = _RE().remove(routine_id)
    console.print("[green]Removed.[/green]" if ok else f"[red]Not found: {routine_id!r}[/red]")


@routines_app.command("enable")
def routines_enable(routine_id: Annotated[str, typer.Argument()]) -> None:
    """Enable a disabled routine."""
    from lightclaw.routines import RoutineEngine as _RE
    ok = _RE().set_enabled(routine_id, True)
    console.print("[green]Enabled.[/green]" if ok else f"[red]Not found: {routine_id!r}[/red]")


@routines_app.command("disable")
def routines_disable(routine_id: Annotated[str, typer.Argument()]) -> None:
    """Disable a routine without removing it."""
    from lightclaw.routines import RoutineEngine as _RE
    ok = _RE().set_enabled(routine_id, False)
    console.print("[green]Disabled.[/green]" if ok else f"[red]Not found: {routine_id!r}[/red]")


# ---------------------------------------------------------------------------
# Skills CLI
# ---------------------------------------------------------------------------

@skills_app.command("list")
def skills_list() -> None:
    """List locally installed skills."""
    from lightclaw.tools.skills import _load as _sl
    skills = _sl()
    if not skills:
        console.print("[dim]No skills installed. Try: lightclaw skills search <keyword>[/dim]")
        return
    t = Table("ID", "Description", "Params", title="Installed Skills")
    for s in skills:
        t.add_row(s.id, s.description, ", ".join(s.params) or "—")
    console.print(t)


@skills_app.command("search")
def skills_search(
    query: Annotated[str, typer.Argument(help="Keyword to search")],
) -> None:
    """Search the remote skills registry."""
    from lightclaw.tools.skills import _fetch_registry, _extract_params

    async def _go() -> None:
        data = await _fetch_registry()
        if isinstance(data, str):
            console.print(f"[red]{data}[/red]")
            return
        q = query.lower()
        matches = [
            s for s in data
            if q in s["id"].lower() or q in s.get("description", "").lower()
        ]
        if not matches:
            console.print(f"[yellow]No results for {query!r}[/yellow]")
            return
        t = Table("ID", "Description", "Params", title=f"Registry: {query!r}")
        for s in matches:
            params = s.get("params") or _extract_params(s.get("prompt", ""))
            t.add_row(s["id"], s.get("description", ""), ", ".join(params) or "—")
        console.print(t)

    asyncio.run(_go())


@skills_app.command("browse")
def skills_browse() -> None:
    """List all skills in the remote registry."""
    from lightclaw.tools.skills import _fetch_registry, _extract_params

    async def _go() -> None:
        data = await _fetch_registry()
        if isinstance(data, str):
            console.print(f"[red]{data}[/red]")
            return
        t = Table("ID", "Description", "Params", title="Skills Registry")
        for s in data:
            params = s.get("params") or _extract_params(s.get("prompt", ""))
            t.add_row(s["id"], s.get("description", ""), ", ".join(params) or "—")
        console.print(t)

    asyncio.run(_go())


@skills_app.command("install")
def skills_install(
    skill_id: Annotated[str, typer.Argument(help="Skill ID to install")],
) -> None:
    """Download and install a skill from the registry."""
    from lightclaw.tools.skills import _fetch_registry, _load as _sl, _save as _ss, Skill as _SK, _extract_params

    async def _go() -> None:
        with console.status(f"Fetching registry..."):
            data = await _fetch_registry()
        if isinstance(data, str):
            console.print(f"[red]{data}[/red]")
            return
        entry = next((s for s in data if s["id"] == skill_id), None)
        if entry is None:
            console.print(f"[red]Skill '{skill_id}' not found in registry.[/red]")
            console.print("[dim]Try: lightclaw skills browse[/dim]")
            return
        params = _extract_params(entry["prompt"])
        skills = _sl()
        skills = [s for s in skills if s.id != skill_id]
        skills.append(_SK(
            id=entry["id"],
            description=entry["description"],
            prompt=entry["prompt"],
            params=params,
        ))
        _ss(skills)
        console.print(f"[green]Installed:[/green] {skill_id}")
        if params:
            console.print(f"[dim]Params: {params}[/dim]")
        console.print(f"[dim]Run with: lightclaw skills run {skill_id}{' ' + ' '.join(p+'=<value>' for p in params) if params else ''}[/dim]")

    asyncio.run(_go())


@skills_app.command("remove")
def skills_remove(
    skill_id: Annotated[str, typer.Argument(help="Skill ID to remove")],
) -> None:
    """Remove an installed skill."""
    from lightclaw.tools.skills import _load as _sl, _save as _ss
    skills = _sl()
    before = len(skills)
    skills = [s for s in skills if s.id != skill_id]
    if len(skills) == before:
        console.print(f"[red]Skill '{skill_id}' not found.[/red]")
        raise typer.Exit(1)
    _ss(skills)
    console.print(f"[green]Removed:[/green] {skill_id}")


@skills_app.command("show")
def skills_show(
    skill_id: Annotated[str, typer.Argument(help="Skill ID to inspect")],
) -> None:
    """Show full details of an installed skill."""
    from lightclaw.tools.skills import _load as _sl
    skill = next((s for s in _sl() if s.id == skill_id), None)
    if skill is None:
        console.print(f"[red]Skill '{skill_id}' not installed.[/red]")
        raise typer.Exit(1)
    console.print(Panel(
        f"[bold]Description:[/bold] {skill.description}\n"
        f"[bold]Params:[/bold] {skill.params or '(none)'}\n\n"
        f"[bold]Prompt:[/bold]\n{skill.prompt}",
        title=f"Skill: {skill.id}",
        border_style="cyan",
    ))


@skills_app.command("run")
def skills_run(
    skill_id: Annotated[str, typer.Argument(help="Skill ID to run")],
    params: Annotated[list[str], typer.Argument(help="key=value params")] = [],
    model: Annotated[str | None, typer.Option("--model", "-m")] = None,
) -> None:
    """Run an installed skill. Pass params as key=value arguments.

    Example:
      lightclaw skills run summarize text="hello world" audience="children"
    """
    from lightclaw.tools.skills import _load as _sl, _extract_params

    skill = next((s for s in _sl() if s.id == skill_id), None)
    if skill is None:
        console.print(f"[red]Skill '{skill_id}' not installed.[/red]")
        raise typer.Exit(1)

    param_dict: dict[str, str] = {}
    for token in params:
        k, _, v = token.partition("=")
        if k:
            param_dict[k] = v

    try:
        prompt = skill.prompt.format(**param_dict)
    except KeyError as exc:
        console.print(f"[red]Missing param {exc.args[0]!r} — required: {skill.params}[/red]")
        raise typer.Exit(1)

    cfg = get_config()
    if model:
        cfg.model = model
        set_config(cfg)

    async def _go() -> None:
        async with Workspace(cfg) as ws:
            agent = AgentLoop(cfg, get_default_registry(), ws)
            response = await agent.run(prompt, thread_id=f"skill_{skill_id}")
            console.print(Markdown(response))

    asyncio.run(_go())


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        repl()
