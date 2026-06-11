"""Textual TUI for light-claw: persistent right-side jobs panel and bottom status bar."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.suggester import SuggestFromList
from textual.widgets import Input, RichLog, Static

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
        color: #777777;
        overflow-y: auto;
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

    #active.streaming {
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
        height: 1;
        background: #111111;
        color: #555555;
        padding: 0 1;
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
        self._streaming_chars: int = 0  # live char count; used for bar estimate during stream

    def compose(self) -> ComposeResult:
        with Horizontal(id="main"):
            yield RichLog(id="chat", markup=True, highlight=True, wrap=True)
            with Vertical(id="sidebar"):
                yield Static("jobs", id="jobs-title")
                yield Static("─" * 22, id="jobs-divider")
                yield Static("[dim]no jobs yet[/dim]", id="jobs-list", markup=True)
        yield Static("", id="active", markup=True)
        # statusbar first → docked to very bottom; Input second → just above statusbar
        yield Static("", id="statusbar", markup=True)
        yield Input(
            placeholder=f"({self._session.thread_id})",
            suggester=SuggestFromList(_SLASH_SUGGESTIONS, case_sensitive=False),
            id="input",
        )

    def on_mount(self) -> None:
        from lightclaw.console import set_tui_writer
        set_tui_writer(self._write_chat)
        self._update_status()
        self._update_jobs()
        self.set_interval(2.0, self._update_jobs)
        self.set_interval(0.5, self._update_status)
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

    def _write_chat(self, content: Any) -> None:
        self.query_one("#chat", RichLog).write(content)

    def _update_jobs(self) -> None:
        jobs = self._session.job_manager.list_all(limit=12)
        if not jobs:
            content = "[dim]no jobs yet[/dim]"
        else:
            lines: list[str] = []
            for j in jobs:
                if j.status == "completed":
                    icon, color = "✓", "green"
                elif j.status == "failed":
                    icon, color = "✗", "red"
                else:
                    icon, color = "⏳", "cyan"
                name = j.id if len(j.id) <= 22 else j.id[:19] + "..."
                lines.append(f"[{color}]{icon}[/{color}] [dim]{name}[/dim]")
                lines.append(f"  [dim]{j.elapsed}[/dim]")
            content = "\n".join(lines)
        self.query_one("#jobs-list", Static).update(content)

    def _update_status(self) -> None:
        stats = self._session.agent.token_stats
        # During streaming, add a rough char→token estimate so the bar moves live
        total = stats.get("total", 0) + self._streaming_chars // 4
        ctx_known = self._session.agent.context_length is not None
        ctx = self._session.agent.context_length or 128_000
        pct = min(total / ctx * 100, 100) if total else 0.0
        bar_w = 20
        filled = round(pct / 100 * bar_w)
        bar = "█" * filled + " " * (bar_w - filled)
        est = "~" if not ctx_known else ""
        tok_label = f"{est}{total / 1000:.1f}K/{ctx / 1000:.1f}K"
        color = "green" if pct < 50 else "yellow" if pct < 80 else "red"
        ttft_str = (
            f"  ttft {self._session.last_ttft:.2f}s"
            if self._session.last_ttft is not None
            else ""
        )
        attach_str = (
            f"  📎 {len(self._pending_attachments)}"
            if self._pending_attachments
            else ""
        )
        self.query_one("#statusbar", Static).update(
            f" {tok_label}  [{color}]{bar}[/{color}]{ttft_str}{attach_str}"
        )

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

        # Handle /quit before handle_slash so no KeyboardInterrupt propagates
        if text.lower() in ("/quit", "/exit"):
            await self.action_quit()
            return

        # Expand /suggest to its full prompt
        if text.lower() == "/suggest":
            text = _SUGGEST_PROMPT

        # /paste in TUI — handled here (not via handle_slash)
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

        # Other slash commands
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
            # Refresh placeholder in case thread changed
            self._update_prompt_placeholder()
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
        active.add_class("streaming")

        start = time.perf_counter()
        first_token_time: float | None = None
        response = ""
        last_render = 0.0  # wall-clock time of last active.update() call
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
                response += chunk
                self._streaming_chars += len(chunk)
                # Re-render the streaming preview at most every 50 ms to avoid flicker
                if now - last_render >= 0.05:
                    active.update(Markdown(response))
                    last_render = now
                # Update status bar on every chunk — keeps bar moving live
                self._update_status()
        except Exception as e:
            chat.write(f"[red bold]Error:[/red bold] {e}")
        finally:
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
            self._update_jobs()

            # Auto-compact if context is high
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


async def run_tui(session: "ReplSession") -> None:
    """Launch the Textual TUI with an already-started ReplSession."""
    app = LightClawTUI(session)
    await app.run_async()
