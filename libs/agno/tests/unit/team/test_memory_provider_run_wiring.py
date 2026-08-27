"""Integration tests: memory provider hooks (SM-X3) actually wired into team.run()/arun().

Mirrors tests/unit/agent/test_memory_provider_run_wiring.py. Runs the full run()/arun()
path (plain mode -- not tasks mode) against a mock model, asserting on the actual messages
sent to the model and whether the memory provider was touched.
"""

from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from egpt.memory.types import WriteReceipt
from egpt.models.base import Model
from egpt.models.message import MessageMetrics
from egpt.models.response import ModelResponse
from egpt.run.base import RunContext
from egpt.team.team import Team


class MockModel(Model):
    """Minimal offline model: returns a canned text response without any network call.

    Records the ``messages`` it was invoked with, so tests can assert on what was actually
    sent to the model -- ``response.messages`` alone is not that: it is filtered to only
    ``add_to_agent_memory=True`` entries, so an injected memory-context message (which must
    be excluded from that filter, see SM-X3's feedback-loop fix) is invisible there even
    though the model saw it.
    """

    def __init__(self):
        super().__init__(id="test-model", name="test-model", provider="test")
        self.instructions = None
        self.invoked_messages: list = []
        self._mock_response = ModelResponse(
            content="ok",
            role="assistant",
            response_usage=MessageMetrics(),
        )

    def get_instructions_for_model(self, *args, **kwargs):
        return None

    def get_system_message_for_model(self, *args, **kwargs):
        return None

    async def aget_instructions_for_model(self, *args, **kwargs):
        return None

    async def aget_system_message_for_model(self, *args, **kwargs):
        return None

    def parse_args(self, *args, **kwargs):
        return {}

    def invoke(self, *args, **kwargs) -> ModelResponse:
        self.invoked_messages = kwargs.get("messages", [])
        return self._mock_response

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        self.invoked_messages = kwargs.get("messages", [])
        return self._mock_response

    def invoke_stream(self, *args, **kwargs) -> Iterator[ModelResponse]:
        # Recorded on the streaming paths too: without this the streaming and tasks-mode hook
        # sites are unobservable, which is why 8 of the 12 wired sites were previously deletable
        # with the suite still green.
        self.invoked_messages = kwargs.get("messages", [])
        yield self._mock_response

    async def ainvoke_stream(self, *args, **kwargs) -> AsyncIterator[ModelResponse]:
        self.invoked_messages = kwargs.get("messages", [])
        yield self._mock_response
        return

    def _parse_provider_response(self, response: Any, **kwargs) -> ModelResponse:
        return self._mock_response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return self._mock_response


def _make_memory_provider(context: str = "TEAM INJECTED MEMORY CONTEXT") -> MagicMock:
    provider = MagicMock()
    provider.assemble_context.return_value = context
    provider.write.return_value = WriteReceipt(accepted=1)

    async def aassemble_context(*args, **kwargs):
        return context

    provider.aassemble_context = aassemble_context
    provider.awrite = AsyncMock(return_value=WriteReceipt(accepted=1))
    return provider


class TestRunInjectsMemoryContext:
    def test_injected_context_lands_in_messages_sent_to_model(self):
        provider = _make_memory_provider()
        model = MockModel()
        team = Team(model=model, members=[], memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        team.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.assemble_context.assert_called_once()
        provider.write.assert_called_once()
        invoked_contents = [m.content for m in model.invoked_messages]
        assert "TEAM INJECTED MEMORY CONTEXT" in invoked_contents

    def test_injected_memory_context_is_not_written_back(self):
        """Regression guard for the feedback loop: the read hook's own output must not be
        eligible for write-back, or every turn re-writes what it just retrieved and the store
        compounds while retrieval degrades. `run_response.messages` (what write-back reads)
        is filtered to `add_to_agent_memory=True` -- the injected message must not carry it."""
        provider = _make_memory_provider()
        team = Team(model=MockModel(), members=[], memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        team.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.write.assert_called_once()
        _scope, events = provider.write.call_args[0]
        written_contents = [e.content for e in events]
        assert "TEAM INJECTED MEMORY CONTEXT" not in written_contents

    def test_no_injection_when_memory_provider_unset(self):
        """Regression guard: every existing Team has memory_provider=None by default --
        a run must be byte-for-byte unaffected when it's not configured."""
        team = Team(model=MockModel(), members=[], telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        response = team.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        contents = [m.content for m in response.messages]
        assert "TEAM INJECTED MEMORY CONTEXT" not in contents

    def test_no_injection_when_tenant_id_unset(self):
        provider = _make_memory_provider()
        team = Team(model=MockModel(), members=[], memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id=None)

        response = team.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.assemble_context.assert_not_called()
        provider.write.assert_not_called()
        contents = [m.content for m in response.messages]
        assert "TEAM INJECTED MEMORY CONTEXT" not in contents

    @pytest.mark.asyncio
    async def test_async_parity(self):
        provider = _make_memory_provider("ASYNC TEAM INJECTED MEMORY CONTEXT")
        model = MockModel()
        team = Team(model=model, members=[], memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        await team.arun("hi", session_id="s1", user_id="u1", run_context=run_context)

        invoked_contents = [m.content for m in model.invoked_messages]
        assert "ASYNC TEAM INJECTED MEMORY CONTEXT" in invoked_contents
        provider.awrite.assert_called_once()
