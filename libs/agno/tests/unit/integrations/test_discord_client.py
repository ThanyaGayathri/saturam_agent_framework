"""Unit tests for the Discord integration (egpt.integrations.discord)."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("discord")

from egpt.integrations.discord.client import DiscordClient, _context_kwargs  # noqa: E402


def test_context_kwargs_does_not_force_visibility_when_entity_has_its_own_dependencies():
    """An entity built with its own ``dependencies`` (and possibly
    ``add_dependencies_to_context=False`` to keep them tool-only) must not have that
    choice overridden -- forcing the flag on would merge its own dependency values into
    the prompt alongside Discord's.
    """
    entity = SimpleNamespace(dependencies={"customer_record": "sensitive"})
    discord_deps = {"discord_username": "alice", "discord_userid": 1, "discord_url": "https://x"}

    kwargs = _context_kwargs(entity, discord_deps)

    assert kwargs == {"dependencies": discord_deps}
    assert "add_dependencies_to_context" not in kwargs


def test_context_kwargs_forces_visibility_when_entity_has_no_dependencies():
    """With nothing of the entity's own to expose, forcing the flag on is safe and
    preserves the always-visible Discord identity context."""
    entity = SimpleNamespace(dependencies=None)
    discord_deps = {"discord_username": "alice", "discord_userid": 1, "discord_url": "https://x"}

    kwargs = _context_kwargs(entity, discord_deps)

    assert kwargs == {"dependencies": discord_deps, "add_dependencies_to_context": True}


async def test_handle_hitl_forwards_dependencies_to_the_resumed_run():
    """The resumed (post-confirmation) run must still receive this message's Discord
    identity -- it is no longer carried as persistent entity state, so it has to be
    passed explicitly on the ``acontinue_run`` call."""
    agent = SimpleNamespace(dependencies=None, acontinue_run=AsyncMock())
    client = DiscordClient.__new__(DiscordClient)
    client.agent = agent
    client.team = None

    run_response = SimpleNamespace(is_paused=True, tools_requiring_confirmation=[])
    dependencies = {"discord_username": "alice", "discord_userid": 1, "discord_url": "https://x"}

    await client.handle_hitl(run_response, thread=SimpleNamespace(), dependencies=dependencies)

    agent.acontinue_run.assert_awaited_once_with(run_response=run_response, dependencies=dependencies)


def test_on_message_reads_attachments_without_blocking_the_event_loop():
    """The async ``on_message`` handler must download attachments without a
    synchronous, blocking network call.

    ``requests.get`` is blocking; calling it inside a coroutine freezes the
    whole asyncio event loop for the duration of the download. The handler must
    instead use discord.py's native async ``await media.read()``.
    """
    # ``on_message`` is a closure defined inside ``_setup_events``; its source
    # is captured when we read the enclosing method's source.
    source = inspect.getsource(DiscordClient._setup_events)

    assert "requests.get(" not in source
    assert "await media.read()" in source


def test_discord_client_module_does_not_import_requests():
    """The blocking ``requests`` dependency is no longer needed in this module."""
    import egpt.integrations.discord.client as discord_client

    source = inspect.getsource(discord_client)
    assert "import requests" not in source
