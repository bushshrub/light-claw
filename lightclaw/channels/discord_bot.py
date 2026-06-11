"""Discord channel: responds to DMs and @mentions via the agent loop."""

from __future__ import annotations

import traceback

import discord

from lightclaw.agent import AgentLoop
from lightclaw.config import Config, get_config
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
    def __init__(
        self,
        token: str,
        config: Config | None = None,
        workspace: Workspace | None = None,
        registry: Registry | None = None,
    ) -> None:
        self._token = token
        cfg = config or get_config()
        self._agent = AgentLoop(cfg, registry or get_default_registry(), workspace)

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        self._setup()

    def _setup(self) -> None:
        client = self._client
        agent = self._agent

        @client.event
        async def on_ready() -> None:
            print(f"[Discord] logged in as {client.user} ({client.user.id})")

        @client.event
        async def on_message(message: discord.Message) -> None:
            # Ignore own messages and other bots
            if message.author == client.user:
                return
            if message.author.bot:
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            is_mention = client.user in message.mentions
            if not (is_dm or is_mention):
                return

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
            except Exception as exc:
                # Never silently drop — always reply with the error.
                tb = traceback.format_exc()
                print(f"[Discord] error handling message from {message.author}: {exc}\n{tb}")
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
