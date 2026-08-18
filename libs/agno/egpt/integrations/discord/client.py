from os import getenv
from typing import Any, Dict, Optional, Union

from egpt.media import Audio, File, Image, Video
from egpt.registry.protocols import ChatRunnableLike
from egpt.run.agent import RunOutput
from egpt.run.team import TeamRunOutput
from egpt.utils.log import log_error, log_info, log_warning
from egpt.utils.message import get_text_from_message

try:
    import discord

except (ImportError, ModuleNotFoundError):
    raise ImportError("`discord.py` not installed. Please install using `pip install discord.py`")


class RequiresConfirmationView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = True
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self.clear_items()
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.value = False
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self.clear_items()
        self.stop()

    async def on_timeout(self):
        log_warning("Agent Timeout Error")


def _context_kwargs(entity: ChatRunnableLike, dependencies: Dict[str, Any]) -> Dict[str, Any]:
    """Per-call kwargs carrying this message's Discord identity into the run.

    Forcing ``add_dependencies_to_context=True`` is only safe when the entity has no
    ``dependencies`` of its own: ``arun`` merges call-site dependencies over the entity's
    (call-site keys win, the entity's own keys otherwise pass through unchanged), so an
    entity built with ``dependencies={...}, add_dependencies_to_context=False`` --
    deliberately keeping those values available to tools but out of the model context --
    would have them exposed to the model by an unconditional override. When the entity has
    no dependencies of its own, forcing the flag is safe: nothing but this call's Discord
    keys can appear.
    """
    kwargs: Dict[str, Any] = {"dependencies": dependencies}
    if not getattr(entity, "dependencies", None):
        kwargs["add_dependencies_to_context"] = True
    return kwargs


class DiscordClient:
    def __init__(
        self,
        agent: Optional[ChatRunnableLike] = None,
        team: Optional[ChatRunnableLike] = None,
        client: Optional[discord.Client] = None,
    ):
        self.agent = agent
        self.team = team
        if client is None:
            self.intents = discord.Intents.all()
            self.client = discord.Client(intents=self.intents)
        else:
            self.client = client
        self._setup_events()

    def _setup_events(self):
        @self.client.event
        async def on_message(message):
            if message.author == self.client.user:
                log_info(f"sent {message.content}")
                return

            message_image = None
            message_video = None
            message_audio = None
            message_file = None
            media_url = None
            message_text = message.content
            message_url = message.jump_url
            message_user = message.author.name
            message_user_id = message.author.id

            if message.attachments:
                media = message.attachments[0]
                media_type = media.content_type
                media_url = media.url
                if media_type.startswith("image/"):
                    message_image = media_url
                elif media_type.startswith("video/"):
                    message_video = await media.read()
                elif media_type.startswith("application/"):
                    message_file = await media.read()
                elif media_type.startswith("audio/"):
                    message_audio = media_url

            log_info(f"processing message:{message_text} \n with media: {media_url} \n url:{message_url}")
            if isinstance(message.channel, discord.Thread):
                thread = message.channel
            elif isinstance(message.channel, discord.channel.DMChannel):
                thread = message.channel  # type: ignore
            elif isinstance(message.channel, discord.TextChannel):
                thread = await message.create_thread(name=f"{message_user}'s thread")
            else:
                log_info(f"received {message.content} but not in a supported channel")
                return

            async with thread.typing():
                # TODO Unhappy with the duplication here but it keeps MyPy from complaining
                # Per-call kwargs, never `self.agent.additional_context = ...`: discord.py
                # dispatches each on_message as its own task, so mutating the shared entity
                # lets concurrent messages read each other's context.
                dependencies = {
                    "discord_username": message_user,
                    "discord_userid": message_user_id,
                    "discord_url": message_url,
                }
                if self.agent:
                    agent_response: RunOutput = await self.agent.arun(  # type: ignore[misc]
                        input=message_text,
                        user_id=message_user_id,
                        session_id=str(thread.id),
                        images=[Image(url=message_image)] if message_image else None,
                        videos=[Video(content=message_video)] if message_video else None,
                        audio=[Audio(url=message_audio)] if message_audio else None,
                        files=[File(content=message_file)] if message_file else None,
                        **_context_kwargs(self.agent, dependencies),
                    )
                    if agent_response.status == "ERROR":
                        log_error(agent_response.content)
                        agent_response.content = (
                            "Sorry, there was an error processing your message. Please try again later."
                        )
                    await self._handle_response_in_thread(agent_response, thread, dependencies)
                elif self.team:
                    team_response: TeamRunOutput = await self.team.arun(  # type: ignore[misc]
                        input=message_text,
                        user_id=message_user_id,
                        session_id=str(thread.id),
                        images=[Image(url=message_image)] if message_image else None,
                        videos=[Video(content=message_video)] if message_video else None,
                        audio=[Audio(url=message_audio)] if message_audio else None,
                        files=[File(content=message_file)] if message_file else None,
                        **_context_kwargs(self.team, dependencies),
                    )
                    if team_response.status == "ERROR":
                        log_error(team_response.content)
                        team_response.content = (
                            "Sorry, there was an error processing your message. Please try again later."
                        )

                    await self._handle_response_in_thread(team_response, thread, dependencies)

    async def handle_hitl(
        self,
        run_response: RunOutput,
        thread: Union[discord.Thread, discord.TextChannel],
        dependencies: Dict[str, Any],
    ) -> RunOutput:
        """Handles optional Human-In-The-Loop interaction."""
        if run_response.is_paused:
            for tool in run_response.tools_requiring_confirmation:
                view = RequiresConfirmationView()
                await thread.send(f"Tool requiring confirmation: {tool.tool_name}", view=view)
                await view.wait()
                tool.confirmed = view.value if view.value is not None else False

            if self.agent:
                # dependencies carries this message's Discord identity forward so the
                # resumed run isn't composed without it (acontinue_run has no
                # add_dependencies_to_context kwarg to override with, so this is
                # only ever additive to whatever the agent's own config already shows).
                run_response = await self.agent.acontinue_run(  # type: ignore[misc]
                    run_response=run_response,
                    dependencies=dependencies,
                )

        return run_response

    async def _handle_response_in_thread(
        self,
        response: Union[RunOutput, TeamRunOutput],
        thread: Union[discord.TextChannel, discord.Thread],
        dependencies: Dict[str, Any],
    ):
        if isinstance(response, RunOutput):
            response = await self.handle_hitl(response, thread, dependencies)

        if response.reasoning_content:
            await self._send_discord_messages(
                thread=thread, message=f"Reasoning: \n{response.reasoning_content}", italics=True
            )

        # Handle structured outputs properly
        content_message = get_text_from_message(response.content) if response.content is not None else ""

        await self._send_discord_messages(thread=thread, message=content_message)

    async def _send_discord_messages(self, thread: discord.channel, message: str, italics: bool = False):  # type: ignore
        if len(message) < 1500:
            if italics:
                formatted_message = "\n".join([f"_{line}_" for line in message.split("\n")])
                await thread.send(formatted_message)  # type: ignore
            else:
                await thread.send(message)  # type: ignore
            return

        message_batches = [message[i : i + 1500] for i in range(0, len(message), 1500)]

        for i, batch in enumerate(message_batches, 1):
            batch_message = f"[{i}/{len(message_batches)}] {batch}"
            if italics:
                formatted_batch = "\n".join([f"_{line}_" for line in batch_message.split("\n")])
                await thread.send(formatted_batch)  # type: ignore
            else:
                await thread.send(batch_message)  # type: ignore

    def serve(self):
        try:
            token = getenv("DISCORD_BOT_TOKEN")
            if not token:
                raise ValueError("DISCORD_BOT_TOKEN NOT SET")
            return self.client.run(token)
        except Exception as e:
            raise ValueError(f"Failed to run Discord client: {str(e)}")
