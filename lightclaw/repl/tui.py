"""Textual TUI for light-claw: persistent right-side jobs panel and bottom status bar."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any

from lightclaw.config import TUI_LOCK_FILE
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.suggester import SuggestFromList
from textual.widgets import Input, ListItem, ListView, RichLog, Static

if TYPE_CHECKING:
    from lightclaw.repl.cli import ReplSession

_COMPACT_THRESHOLD = 0.75

_SLASH_SUGGESTIONS = [
    "/clear", "/connectors disable", "/connectors enable", "/connectors list",
    "/help", "/history", "/jobs cancel", "/jobs list", "/jobs logs", "/jobs run",
    "/memory del", "/memory list", "/memory search", "/memory set", "/model",
    "/paste", "/paste clear", "/quit", "/routines list", "/routines run",
    "/schedule add", "/schedule list", "/schedule rm", "/session",
    "/skills install", "/skills list", "/skills remove", "/skills run",
    "/skills search", "/suggest", "/thread", "/tools",
]

_SUGGEST_PROMPT = (
    "Use lightclaw_read_source to explore your own source code and identify "
    "concrete improvements. Start with the project structure (''), then read "
    "the most relevant modules. Produce a prioritised list of 3–5 specific, "
    "actionable suggestions (bugs first, then UX friction, then missing "
    "features). For each: state what the problem is, where in the code it "
    "lives, and what the fix would be."
)


class LightClawTUI(App):
    CSS = """
    Screen {
        background: #0d0d0d;
        color: #cccccc;
    }

    #main {
        height: 1fr;
    }

    #chat {
        width: 3fr;
        border-right: solid #1a1a1a;
        padding: 0 1;
        scrollbar-color: #333333 #0d0d0d;
        scrollbar-size: 1 1;
    }

    #chat:focus {
        border: none;
    }

    #sidebar {
        width: 26;
        padding: 0 1;
        background: #080808;
    }

    #jobs-title {
        color: cyan;
        text-style: bold;
        height: 1;
    }

    #jobs-divider {
        color: #2a2a2a;
        height: 1;
    }

    #jobs-list {
        height: 1fr;
        overflow-y: auto;
    }

    #jobs-list > .list-item {
        padding: 0 1;
        height: 2;
    }

    #jobs-list > .list-item:hover {
        background: #1a1a1a;
    }

    #jobs-list > .list-item > .list-item__first {
        color: #777777;
    }

    #active {
        height: auto;
        max-height: 20;
        min-height: 0;
        border-top: solid #1a1a1a;
        padding: 0 1;
        background: #0d0d0d;
        overflow-y: auto;
        display: none;
    }

    #active.streaming,
    #active.job-detail {
        display: block;
    }

    Input {
        dock: bottom;
        background: #080808;
        border: none;
        border-top: solid #1a1a1a;
        color: #00cccc;
        height: 3;
    }

    Input:focus {
        border-top: solid #00aaaa;
    }

    Input>.input--placeholder {
        color: #333333;
    }

    #statusbar {
        dock: bottom;
        height: 2;
        background: #1a1a1a;
        color: #aaaaaa;
        padding: 0 1;
        border-top: solid #333333;
        overflow: hidden;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+y", "paste_image", "Paste image"),
    ]

    def __init__(self, session: "ReplSession") -> None:
        super().__init__()
        self._session = session
        self._pending_attachments: list[dict[str, Any]] = []
        self._streaming_chars: int = 0
        self._selected_job_id: str | None = None
        self._stream_start: float | None = None
        self._got_first_token: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield RichLog(id="chat", markup=True, highlight=True, wrap=True)
            with Vertical(id="sidebar"):
                yield Static("jobs", id="jobs-title")
                yield Static("─" * 22, id="jobs-divider")
                yield ListView(id="jobs-list")
        yield Static("", id="active", markup=True)
        yield Input(
            placeholder=f"({self._session.thread_id})",
            suggester=SuggestFromList(_SLASH_SUGGESTIONS, case_sensitive=False),
            id="input",
        )
        yield Static("", id="statusbar", markup=True)

    async def on_mount(self) -> None:
        from lightclaw.console import set_tui_writer
        set_tui_writer(self._write_chat)
        self._update_lock()
        self._update_status()
        await self._update_jobs()
        self.set_interval(2.0, self._update_jobs)
        self.set_interval(0.1, self._update_status)
        self.set_interval(10.0, self._update_lock)
        inp = self.query_one("#input", Input)
        inp.focus()
        self.query_one("#chat", RichLog).write(Panel(
            f"[bold cyan]light-claw[/bold cyan]  local agent OS\n"
            f"model=[yellow]{self._session.config.model}[/yellow]  "
            f"base=[yellow]{self._session.config.base_url}[/yellow]\n"
            "Type [cyan]/help[/cyan] for commands or [cyan]/quit[/cyan] to exit.\n"
            "Paste images with [cyan]Ctrl+Y[/cyan] or [cyan]/paste[/cyan].",
            title="light-claw",
            border_style="cyan",
        ))

    def on_unmount(self) -> None:
        from lightclaw.console import set_tui_writer
        set_tui_writer(None)
        self._remove_lock()

    # ── lock file ───────────────────────────────────────────────────────────

    def _lock_path(self) -> str:
        return TUI_LOCK_FILE

    def _update_lock(self) -> None:
        pid = os.getpid()
        thread = self._session.thread_id
        try:
            os.makedirs(os.path.dirname(self._lock_path()), exist_ok=True)
            with open(self._lock_path(), "w") as f:
                json.dump({"pid": pid, "thread": thread}, f)
        except OSError:
            pass

    def _remove_lock(self) -> None:
        try:
            os.remove(self._lock_path())
        except OSError:
            pass

    # ── chat ────────────────────────────────────────────────────────────────

    def _write_chat(self, content: Any) -> None:
        self.query_one("#chat", RichLog).write(content)

    # ── jobs list ───────────────────────────────────────────────────────────

    async def _update_jobs(self) -> None:
        jobs = self._session.job_manager.list_all(limit=12)
        lv = self.query_one("#jobs-list", ListView)

        if not jobs:
            existing = list(lv.children)
            if len(existing) != 1 or not isinstance(existing[0], ListItem) or getattr(existing[0], "data", None) is not None:
                await lv.clear()
                item = ListItem(Static("[dim]no jobs yet[/dim]", markup=True))
                await lv.append(item)
            return

        existing_map: dict[str, ListItem] = {}
        for child in list(lv.children):
            if isinstance(child, ListItem):
                jid = getattr(child, "data", None)
                if jid is not None:
                    existing_map[jid] = child

        for j in jobs:
            jid = j.id
            if jid in existing_map:
                self._update_list_item(existing_map[jid], j)
            else:
                item = await self._make_list_item(j)
                await lv.append(item)
                existing_map[jid] = item

        # Remove stale items
        seen = {j.id for j in jobs}
        for child in list(lv.children):
            if isinstance(child, ListItem):
                jid = getattr(child, "data", None)
                if jid is not None and jid not in seen:
                    await child.remove()

        # Restore selection
        if self._selected_job_id is not None and self._selected_job_id in existing_map:
            lv.index = list(lv.children).index(existing_map[self._selected_job_id])

    async def _make_list_item(self, job: Any) -> ListItem:
        label = self._job_label(job)
        item = ListItem(Static(label, markup=True))
        item.data = job.id
        return item

    def _update_list_item(self, item: ListItem, job: Any) -> None:
        label = self._job_label(job)
        if item.children:
            item.children[0].update(label)

    @staticmethod
    def _job_label(job: Any) -> str:
        if job.status == "completed":
            icon, color = "✓", "green"
        elif job.status == "failed":
            icon, color = "✗", "red"
        else:
            icon, color = "⏳", "cyan"
        name = job.id if len(job.id) <= 20 else job.id[:17] + "..."
        return f"[{color}]{icon}[/{color}] [dim]{name}[/dim]\n  [dim]{job.elapsed}[/dim]"

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if item is None:
            return
        job_id = item.data if hasattr(item, "data") else None
        if job_id is None:
            return
        self._show_job_details(job_id)

    def _show_job_details(self, job_id: str) -> None:
        jobs = self._session.job_manager.list_all()
        job = next((j for j in jobs if j.id == job_id), None)
        active = self.query_one("#active", Static)
        active.remove_class("streaming")
        if job is None:
            active.update(f"[red]Job {job_id!r} not found[/red]")
            active.add_class("job-detail")
            self._selected_job_id = job_id
            return
        self._selected_job_id = job_id
        lines = [
            f"[bold cyan]Job:[/bold cyan] {job.id}",
            f"[bold]Status:[/bold] [{_job_status_color(job.status)}]{job.status}[/{_job_status_color(job.status)}]",
            f"[bold]Elapsed:[/bold] {job.elapsed}",
        ]
        if job.prompt:
            lines.append(f"[bold]Prompt:[/bold]\n{dim(job.prompt)}")
        if job.error:
            lines.append(f"\n[bold red]Error:[/bold red]\n{job.error}")
        elif job.result:
            lines.append(f"\n[bold]Result:[/bold]")
            text = job.result[:2000]
            lines.append(text)
        active.update("\n".join(lines))
        active.add_class("job-detail")

    # ── status bar ──────────────────────────────────────────────────────────

    def _update_status(self) -> None:
        stats = self._session.agent.token_stats
        total = stats.get("total", 0) + self._streaming_chars // 4
        ctx = self._session.agent.context_length or 128_000
        pct = min(total / ctx * 100, 100) if total else 0.0
        bar_w = 20
        filled = round(pct / 100 * bar_w)
        bar = "█" * filled + "░" * (bar_w - filled)
        ctx_known = self._session.agent.context_length is not None
        est = "~" if not ctx_known else ""
        tok_label = f"{est}{total / 1000:.1f}K/{ctx / 1000:.1f}K"
        color = "green" if pct < 50 else "yellow" if pct < 80 else "red"
        if self._stream_start is not None and not self._got_first_token:
            elapsed = time.perf_counter() - self._stream_start
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = frames[int(elapsed * 10) % len(frames)]
            extra_str = f"  [cyan]{frame}[/cyan] [dim]{elapsed:.1f}s[/dim]"
        elif self._session.last_ttft is not None:
            extra_str = f"  [dim]ttft {self._session.last_ttft:.2f}s[/dim]"
        else:
            extra_str = ""
        attach_str = (
            f"  📎 {len(self._pending_attachments)}"
            if self._pending_attachments
            else ""
        )
        self.query_one("#statusbar", Static).update(
            f" {tok_label}  [{color}]{bar}[/{color}]{extra_str}{attach_str}"
        )

    # ── input helpers ───────────────────────────────────────────────────────

    def _update_prompt_placeholder(self) -> None:
        inp = self.query_one("#input", Input)
        attach_hint = f" [📎 {len(self._pending_attachments)}]" if self._pending_attachments else ""
        inp.placeholder = f"({self._session.thread_id}){attach_hint}"

    def action_paste_image(self) -> None:
        from lightclaw.repl.cli import _read_clipboard_image, _make_image_attachment
        result = _read_clipboard_image()
        if result:
            data, mime = result
            self._pending_attachments.append(_make_image_attachment(data, mime))
            n = len(self._pending_attachments)
            self._write_chat(f"[green]Attached[/green] {mime} ({len(data):,} bytes) — {n} pending")
            self._update_status()
            self._update_prompt_placeholder()
        else:
            self._write_chat("[yellow]No image in clipboard.[/yellow]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()

        chat = self.query_one("#chat", RichLog)

        if text.lower() in ("/quit", "/exit"):
            await self.action_quit()
            return

        if text.lower() == "/suggest":
            text = _SUGGEST_PROMPT

        if text.lower().startswith("/paste"):
            parts = text.split(None, 1)
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub == "clear":
                self._pending_attachments.clear()
                chat.write("[yellow]Pending attachments cleared.[/yellow]")
                self._update_status()
                self._update_prompt_placeholder()
            else:
                self.action_paste_image()
            return

        if text.startswith("/"):
            chat.write(
                Text.assemble(
                    ("(", "dim"),
                    (self._session.thread_id, "cyan bold"),
                    (") ", "dim"),
                    (text, "dim"),
                )
            )
            try:
                handled = await self._session.handle_slash(text)
                if not handled:
                    chat.write(f"[red]Unknown command: {text}[/red]")
            except KeyboardInterrupt:
                await self.action_quit()
            self._update_prompt_placeholder()
            self._update_lock()
            return

        # Normal message — stream
        chat.write(
            Text.assemble(
                ("(", "dim"),
                (self._session.thread_id, "cyan bold"),
                (") ", "dim"),
                text,
            )
        )
        attachments = list(self._pending_attachments)
        self._pending_attachments.clear()
        self._update_status()
        self._update_prompt_placeholder()
        self._stream(text, attachments)

    @work(exclusive=True)
    async def _stream(self, text: str, attachments: list[dict[str, Any]]) -> None:
        from lightclaw.tools.builtins import (
            set_issue_confirm_handler,
            reset_issue_confirm_handler,
        )

        active = self.query_one("#active", Static)
        chat = self.query_one("#chat", RichLog)
        active.remove_class("job-detail")
        active.add_class("streaming")

        start = time.perf_counter()
        self._stream_start = start
        self._got_first_token = False
        first_token_time: float | None = None
        response = ""
        last_render = 0.0
        stats_before = dict(self._session.agent.token_stats)
        self._streaming_chars = 0

        async def _tui_confirm(title: str, body: str, tracker: str) -> bool:
            chat.write(
                f"[cyan]\\[issue][/cyan] [bold]{tracker}[/bold]: {title}\n"
                f"[dim]{body[:200]}...[/dim]\n"
                "[yellow]Issue filing not supported in TUI mode. "
                "Use `lightclaw run` for interactive filing.[/yellow]"
            )
            return False

        confirm_token = set_issue_confirm_handler(_tui_confirm)
        try:
            async for chunk in self._session.agent.stream(
                text,
                thread_id=self._session.thread_id,
                attachments=attachments or None,
            ):
                if not chunk:
                    continue
                now = time.perf_counter()
                if first_token_time is None:
                    first_token_time = now - start
                    self._got_first_token = True
                response += chunk
                self._streaming_chars += len(chunk)
                if now - last_render >= 0.05:
                    active.update(Markdown(response))
                    last_render = now
                self._update_status()
        except Exception as e:
            chat.write(f"[red bold]Error:[/red bold] {e}")
        finally:
            self._stream_start = None
            self._got_first_token = False
            self._streaming_chars = 0
            reset_issue_confirm_handler(confirm_token)
            active.remove_class("streaming")
            active.update("")

            if response:
                chat.write(Markdown(response))

            self._session.last_ttft = first_token_time
            stats_after = self._session.agent.token_stats
            msg_tok = (
                stats_after.get("completion", 0) - stats_before.get("completion", 0)
            )
            ctx = self._session.agent.context_length or 128_000
            total = stats_after.get("total", 0)
            pct = total / ctx * 100
            ttft_str = (
                f"ttft {first_token_time:.2f}s  " if first_token_time is not None else ""
            )
            chat.write(
                Text(
                    f"{ttft_str}{msg_tok:,} tok  "
                    f"{total / 1000:.1f}K/{ctx / 1000:.1f}K",
                    style="dim",
                )
            )
            self._update_status()
            await self._update_jobs()

            # Re-show job details if a job was selected before streaming
            if self._selected_job_id is not None:
                self._show_job_details(self._selected_job_id)

            if total > 0 and pct / 100 > _COMPACT_THRESHOLD:
                chat.write(
                    f"[yellow]Context at {pct:.0f}% — compacting...[/yellow]"
                )
                await self._compact()

    async def _compact(self) -> None:
        chat = self.query_one("#chat", RichLog)

        def _progress(msg: str) -> None:
            chat.write(f"[dim cyan]  ⠙ {msg}[/dim cyan]")

        n = await self._session.agent.compact_history(
            self._session.thread_id, on_progress=_progress
        )
        if n:
            chat.write(f"[green]  ✓ Compacted {n} messages. Context reset.[/green]")
        self._update_status()


def _job_status_color(status: str) -> str:
    return {"completed": "green", "failed": "red", "running": "cyan", "cancelled": "yellow"}.get(status, "white")


def dim(text: str) -> str:
    return f"[dim]{text}[/dim]"


async def run_tui(session: "ReplSession") -> None:
    """Launch the Textual TUI with an already-started ReplSession."""
    app = LightClawTUI(session)
    await app.run_async()
