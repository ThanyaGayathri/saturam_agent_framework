"""Tests for the memory provider feed-forward/write-back hooks (SM-X3), Team side.

Mirrors tests/unit/agent/test_memory_provider_hooks.py. These test team/_managers.py's
glue functions in isolation -- events_from_run/start_writeback_future/assemble_context
are already covered by tests/unit/memory/; not re-tested here.
"""

from concurrent.futures import Future
from unittest.mock import AsyncMock, MagicMock

import pytest

from egpt.memory.types import MemoryQuery, Scope
from egpt.models.message import Message
from egpt.run.base import RunContext
from egpt.run.messages import RunMessages
from egpt.team._managers import (
    _aget_memory_context,
    _astart_memory_writeback_task,
    _get_memory_context,
    _start_memory_writeback_future,
)


def _make_team(*, memory_provider=None) -> MagicMock:
    team = MagicMock()
    team.id = "team-1"
    team.memory_provider = memory_provider
    team.background_executor.submit.return_value = MagicMock(spec=Future)
    return team


def _run_context(*, tenant_id=None) -> RunContext:
    return RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id=tenant_id)


class TestGetMemoryContext:
    def test_fires_when_provider_and_tenant_set(self) -> None:
        provider = MagicMock()
        provider.assemble_context.return_value = "TEAM MEMORY CONTEXT"
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = _get_memory_context(team, run_context, run_messages, user_id="user-1")

        assert result == "TEAM MEMORY CONTEXT"
        provider.assemble_context.assert_called_once()
        scope, working, query = provider.assemble_context.call_args[0]
        # The read scope is deliberately wider than the write scope: scope narrowing is
        # conjunctive, so pinning `session_id` here makes cross-session recall impossible, and
        # `team_id` is uuid4-derived per construction for a team with no id/name. Both stay on
        # the write (see TestStartMemoryWritebackFuture) as provenance.
        assert scope == Scope(tenant_id="acme", user_id="user-1")
        assert scope.team_id is None
        assert scope.session_id is None
        assert query == MemoryQuery(text="hi")

    def test_noop_when_provider_is_none(self) -> None:
        team = _make_team(memory_provider=None)
        run_context = _run_context(tenant_id="acme")
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = _get_memory_context(team, run_context, run_messages)

        assert result is None

    def test_noop_when_tenant_id_is_none(self) -> None:
        provider = MagicMock()
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id=None)
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = _get_memory_context(team, run_context, run_messages)

        assert result is None
        provider.assemble_context.assert_not_called()

    def test_exception_is_caught_and_returns_none(self) -> None:
        provider = MagicMock()
        provider.assemble_context.side_effect = RuntimeError("backend down")
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = _get_memory_context(team, run_context, run_messages)

        assert result is None


class TestAGetMemoryContext:
    @pytest.mark.asyncio
    async def test_fires_when_provider_and_tenant_set(self) -> None:
        provider = MagicMock()
        captured_args = {}

        async def aassemble_context(*args, **kwargs):
            captured_args["args"] = args
            return "ASYNC TEAM MEMORY CONTEXT"

        provider.aassemble_context = aassemble_context
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = await _aget_memory_context(team, run_context, run_messages, user_id="user-1")

        assert result == "ASYNC TEAM MEMORY CONTEXT"
        scope, working, query = captured_args["args"]
        # The read scope is deliberately wider than the write scope: scope narrowing is
        # conjunctive, so pinning `session_id` here makes cross-session recall impossible, and
        # `team_id` is uuid4-derived per construction for a team with no id/name. Both stay on
        # the write (see TestStartMemoryWritebackFuture) as provenance.
        assert scope == Scope(tenant_id="acme", user_id="user-1")
        assert scope.team_id is None
        assert scope.session_id is None
        assert query == MemoryQuery(text="hi")

    @pytest.mark.asyncio
    async def test_noop_when_provider_is_none(self) -> None:
        team = _make_team(memory_provider=None)
        run_context = _run_context(tenant_id="acme")
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = await _aget_memory_context(team, run_context, run_messages)

        assert result is None

    @pytest.mark.asyncio
    async def test_noop_when_tenant_id_is_none(self) -> None:
        provider = MagicMock()
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id=None)
        run_messages = RunMessages(user_message=Message(role="user", content="hi"))

        result = await _aget_memory_context(team, run_context, run_messages)

        assert result is None


def _run_response_with(*, messages=None, tools=None) -> MagicMock:
    run_response = MagicMock()
    run_response.messages = messages
    run_response.tools = tools
    return run_response


class TestStartMemoryWritebackFuture:
    def test_fires_when_provider_and_tenant_set_with_content(self) -> None:
        provider = MagicMock()
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_response = _run_response_with(messages=[Message(role="assistant", content="hi there")])

        result = _start_memory_writeback_future(team, run_context, run_response, user_id="user-1")

        assert result is not None
        team.background_executor.submit.assert_called_once()
        _, _, scope, _ = team.background_executor.submit.call_args[0]
        assert scope == Scope(tenant_id="acme", team_id="team-1", user_id="user-1", session_id="s1")

    def test_noop_when_provider_is_none(self) -> None:
        team = _make_team(memory_provider=None)
        run_context = _run_context(tenant_id="acme")
        run_response = _run_response_with(messages=[Message(role="assistant", content="hi there")])

        result = _start_memory_writeback_future(team, run_context, run_response)

        assert result is None
        team.background_executor.submit.assert_not_called()

    def test_noop_when_no_events(self) -> None:
        provider = MagicMock()
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_response = _run_response_with(messages=[], tools=None)

        result = _start_memory_writeback_future(team, run_context, run_response)

        assert result is None
        team.background_executor.submit.assert_not_called()

    def test_cancels_existing_future_before_dispatching(self) -> None:
        provider = MagicMock()
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_response = _run_response_with(messages=[Message(role="assistant", content="hi there")])
        existing_future = MagicMock(spec=Future)
        existing_future.done.return_value = False

        _start_memory_writeback_future(team, run_context, run_response, existing_future=existing_future)

        existing_future.cancel.assert_called_once()


class TestAStartMemoryWritebackTask:
    @pytest.mark.asyncio
    async def test_fires_when_provider_and_tenant_set_with_content(self) -> None:
        provider = MagicMock()
        provider.awrite = AsyncMock()
        team = _make_team(memory_provider=provider)
        run_context = _run_context(tenant_id="acme")
        run_response = _run_response_with(messages=[Message(role="assistant", content="hi there")])

        result = await _astart_memory_writeback_task(team, run_context, run_response, user_id="user-1")

        assert result is not None
        await result
        provider.awrite.assert_called_once()
        scope, _events = provider.awrite.call_args[0]
        assert scope == Scope(tenant_id="acme", team_id="team-1", user_id="user-1", session_id="s1")

    @pytest.mark.asyncio
    async def test_noop_when_provider_is_none(self) -> None:
        team = _make_team(memory_provider=None)
        run_context = _run_context(tenant_id="acme")
        run_response = _run_response_with(messages=[Message(role="assistant", content="hi there")])

        result = await _astart_memory_writeback_task(team, run_context, run_response)

        assert result is None
