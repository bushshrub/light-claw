"""Discord channel: responds to DMs and @mentions via the agent loop."""

from __future__ import annotations

import json
import os
import time
import traceback

import discord
from discord import app_commands

from lightclaw.console import console

from lightclaw.agent import AgentLoop
from lightclaw.config import Config, config_dir, get_config
from lightclaw.memory import Workspace
from lightclaw.tools.registry import Registry, get_default_registry
from lightclaw.tools.shell_guard import reset_approval_handler, set_approval_handler


def _split(text: str, limit: int = 2000) -> list[str]:
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


class _ApprovalView(discord.ui.View):
    """Discord button UI for shell command approval."""

    def __init__(self, command: str) -> None:
        super().__init__(timeout=60)
        self.command = command
        self.decision: str = "deny"  # default on timeout

    async def _respond(
        self, interaction: discord.Interaction, decision: str, label: str
    ) -> None:
        self.decision = decision
        await interaction.response.edit_message(content=label, view=None)
        self.stop()

    @discord.ui.button(label="▶ Run once", style=discord.ButtonStyle.green)
    async def run_once(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._respond(interaction, "run", f"✅ Running: `{self.command}`")

    @discord.ui.button(label="✅ Always allow", style=discord.ButtonStyle.blurple)
    async def always_allow(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._respond(
            interaction, "allow_always", f"✅ Whitelisted: `{self.command}`"
        )

    @discord.ui.button(label="🚫 Always block", style=discord.ButtonStyle.red)
    async def always_block(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._respond(
            interaction, "block_always", f"🚫 Blocked: `{self.command}`"
        )

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.grey)
    async def deny(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._respond(interaction, "deny", f"❌ Denied: `{self.command}`")


class DiscordBot:
    SESSION_TTL = 600  # 10 minutes

    def __init__(
        self,
        token: str,
        config: Config | None = None,
        workspace: Workspace | None = None,
        registry: Registry | None = None,
    ) -> None:
        self._token = token
        cfg = config or get_config()
        self._cfg = cfg
        self._agent = AgentLoop(cfg, registry or get_default_registry(), workspace)
        self._sessions_path = os.path.join(config_dir(), "discord_sessions.json")
        self._sessions: dict[str, float] = self._load_sessions()

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._setup()

    def _load_sessions(self) -> dict[str, float]:
        try:
            with open(self._sessions_path) as f:
                raw: dict[str, float] = json.load(f)
            now = time.time()
            return {k: v for k, v in raw.items() if now - v < self.SESSION_TTL}
        except Exception:
            return {}

    def _save_sessions(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._sessions_path), exist_ok=True)
            now = time.time()
            active = {k: v for k, v in self._sessions.items() if now - v < self.SESSION_TTL}
            with open(self._sessions_path, "w") as f:
                json.dump(active, f)
        except Exception:
            pass

    def _setup(self) -> None:
        client = self._client
        agent = self._agent
        cfg = self._cfg
        tree = app_commands.CommandTree(client)

        def _session_lines() -> str:
            stats = agent.token_stats
            ctx = agent.context_length
            lines = [
                "**Session info**",
                f"Prompt tokens: {stats['prompt']:,}",
                f"Completion tokens: {stats['completion']:,}",
                f"Total tokens: {stats['total']:,}",
            ]
            if ctx:
                pct = stats["total"] / ctx * 100
                lines.append(f"Context length: {ctx:,} ({pct:.1f}% used)")
            return "\n".join(lines)

        def _model_lines() -> str:
            ctx = agent.context_length
            lines = [f"**Model info**", f"Model: `{agent._llm.model}`"]
            lines.append(f"Context length: {ctx:,} tokens" if ctx else "Context length: unknown")
            return "\n".join(lines)

        @tree.command(name="session", description="Show token usage for this session")
        async def slash_session(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(_session_lines())

        @tree.command(name="model", description="Show model name and context length")
        async def slash_model(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(_model_lines())

        @client.event
        async def on_ready() -> None:
            if cfg.discord_guild_id:
                guild = discord.Object(id=cfg.discord_guild_id)
                tree.copy_global_to(guild=guild)
                await tree.sync(guild=guild)
                console.print(f"[green][Discord][/green] slash commands synced to guild {cfg.discord_guild_id}")
            else:
                await tree.sync()
                console.print("[green][Discord][/green] slash commands synced globally (may take up to 1h)")
            console.print(f"[green][Discord][/green] logged in as [cyan]{client.user}[/cyan] ({client.user.id})")

        @client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore own messages and other bots
            if message.author == client.user:
                return
            if message.author.bot:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            is_mention = client.user in message.mentions
            session_key = f"{message.channel.id}:{message.author.id}"
            now = time.time()

            # Prune expired sessions lazily
            if len(self._sessions) > 500:
                cutoff = now - self.SESSION_TTL
                self._sessions = {k: v for k, v in self._sessions.items() if v > cutoff}

            has_active_session = (
                session_key in self._sessions
                and now - self._sessions[session_key] < self.SESSION_TTL
            )

            if not (is_dm or is_mention or has_active_session):
                return

            if has_active_session and not (is_dm or is_mention):
                console.print(f"[dim][Discord] active session for {message.author} in channel {message.channel.id}[/dim]")

            # Activate / refresh session on DM or mention
            if is_dm or is_mention:
                self._sessions[session_key] = now
                self._save_sessions()

            content = message.content
            if is_mention:
                # Strip both <@ID> and <@!ID> (nickname mention) forms
                content = content.replace(f"<@{client.user.id}>", "")
                content = content.replace(f"<@!{client.user.id}>", "")
                content = content.strip()

            # Collect attachments for multimodal support.
            attachments: list[dict] = []
            for att in message.attachments:
                ct = att.content_type or ""
                mime = ct.split(";")[0].strip()  # strip params like "; charset=utf-8"
                if ct.startswith("image/"):
                    try:
                        data = await att.read()
                        attachments.append({
                            "type": "image",
                            "data": data,
                            "mime_type": mime,
                            "filename": att.filename,
                        })
                    except Exception:
                        # Download failed — fall back to a text note.
                        attachments.append({
                            "type": "other",
                            "data": b"",
                            "mime_type": mime,
                            "filename": att.filename,
                        })
                elif ct.startswith("audio/") or ct.startswith("video/"):
                    att_type = "audio" if ct.startswith("audio/") else "video"
                    attachments.append({
                        "type": att_type,
                        "data": b"",
                        "mime_type": mime,
                        "filename": att.filename,
                    })
                else:
                    attachments.append({
                        "type": "other",
                        "data": b"",
                        "mime_type": mime,
                        "filename": att.filename,
                    })

            # Also collect images from embeds (e.g. URL-unfurled images).
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    attachments.append({
                        "type": "image",
                        "data": b"",
                        "mime_type": "image/jpeg",  # unknown; let the API decide
                        "filename": embed.image.url,
                        "_url": embed.image.url,  # signal to use URL directly
                    })

            if not content and not attachments:
                return

            channel = message.channel
            thread_id = f"discord_{message.author.id}"

            parts = content.strip().split()
            if len(parts) >= 2 and parts[0].lower() == f"!{cfg.discord_prefix}":
                subcmd = parts[1].lower()
                if subcmd == "session":
                    await channel.send(_session_lines())
                    return
                if subcmd == "model":
                    await channel.send(_model_lines())
                    return

            # Fetch recent channel history so the agent can see prior messages.
            channel_context = ""
            try:
                recent: list[str] = []
                async for msg in channel.history(limit=20, before=message, oldest_first=False):
                    if msg.content:
                        name = msg.author.display_name
                        recent.append(f"{name}: {msg.content}")
                if recent:
                    recent.reverse()  # oldest first
                    channel_context = (
                        "Recent channel messages (oldest → newest, before this one):\n"
                        + "\n".join(recent)
                    )
            except Exception:
                pass  # history unavailable — continue without it

            async def _discord_approver(command: str) -> str:
                view = _ApprovalView(command)
                await channel.send(
                    f"🔒 **Shell approval needed**\n```\n{command}\n```\n"
                    f"*(times out in 60s → deny)*",
                    view=view,
                )
                await view.wait()
                return view.decision

            token = set_approval_handler(_discord_approver)
            try:
                async with channel.typing():
                    response = await agent.run(
                        content,
                        thread_id=thread_id,
                        extra_system=channel_context,
                        attachments=attachments or None,
                    )
                for chunk in _split(response):
                    await channel.send(chunk)
                self._sessions[session_key] = time.time()
                self._save_sessions()
            except Exception as exc:
                # Never silently drop — always reply with the error.
                tb = traceback.format_exc()
                console.print(f"[red][Discord] error handling message from {message.author}:[/red] {exc}\n{tb}")
                try:
                    await channel.send(f"⚠️ Error: {exc}")
                except Exception:
                    pass
            finally:
                reset_approval_handler(token)

    async def start(self) -> None:
        await self._client.start(self._token)

    async def close(self) -> None:
        await self._client.close()
