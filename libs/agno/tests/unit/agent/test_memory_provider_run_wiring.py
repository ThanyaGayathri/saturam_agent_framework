"""Integration tests: memory provider hooks (SM-X3) actually wired into agent.run()/arun().

Unlike test_memory_provider_hooks.py (which tests the _managers.py glue functions in
isolation), these run the full run() / arun() path against a mock model (no network) and
assert on the actual messages sent to the model and whether the memory provider was
touched -- proving the hooks are reachable from a live run, not just correctly implemented
as standalone functions.
"""

from collections import deque
from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from egpt.agent.agent import Agent
from egpt.memory.types import WriteReceipt
from egpt.models.base import Model
from egpt.models.message import MessageMetrics
from egpt.models.response import ModelResponse
from egpt.run.agent import RunOutput
from egpt.run.base import RunContext


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


def _make_memory_provider(context: str = "INJECTED MEMORY CONTEXT") -> MagicMock:
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
        agent = Agent(model=model, memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        agent.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.assemble_context.assert_called_once()
        provider.write.assert_called_once()
        invoked_contents = [m.content for m in model.invoked_messages]
        assert "INJECTED MEMORY CONTEXT" in invoked_contents

    def test_injected_memory_context_is_not_written_back(self):
        """Regression guard for the feedback loop: the read hook's own output must not be
        eligible for write-back, or every turn re-writes what it just retrieved and the store
        compounds while retrieval degrades. `run_response.messages` (what write-back reads)
        is filtered to `add_to_agent_memory=True` -- the injected message must not carry it."""
        provider = _make_memory_provider()
        agent = Agent(model=MockModel(), memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        agent.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.write.assert_called_once()
        _scope, events = provider.write.call_args[0]
        written_contents = [e.content for e in events]
        assert "INJECTED MEMORY CONTEXT" not in written_contents

    def test_no_injection_when_memory_provider_unset(self):
        """Regression guard: every existing Agent has memory_provider=None by default --
        a run must be byte-for-byte unaffected when it's not configured."""
        agent = Agent(model=MockModel(), telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        response = agent.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        contents = [m.content for m in response.messages]
        assert "INJECTED MEMORY CONTEXT" not in contents

    def test_no_injection_when_tenant_id_unset(self):
        provider = _make_memory_provider()
        agent = Agent(model=MockModel(), memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id=None)

        response = agent.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.assemble_context.assert_not_called()
        provider.write.assert_not_called()
        contents = [m.content for m in response.messages]
        assert "INJECTED MEMORY CONTEXT" not in contents

    @pytest.mark.asyncio
    async def test_async_parity(self):
        provider = _make_memory_provider("ASYNC INJECTED MEMORY CONTEXT")
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        await agent.arun("hi", session_id="s1", user_id="u1", run_context=run_context)

        invoked_contents = [m.content for m in model.invoked_messages]
        assert "ASYNC INJECTED MEMORY CONTEXT" in invoked_contents
        provider.awrite.assert_called_once()

    def test_coexists_with_legacy_memory_manager(self):
        """memory_manager (legacy) and memory_provider (SM-X3) are separate fields on
        separate code paths -- both configured on the same Agent must dispatch
        independently with no shared/clobbered future handle."""
        provider = _make_memory_provider()
        memory_manager = MagicMock()
        model = MockModel()
        agent = Agent(
            model=model,
            memory_provider=provider,
            memory_manager=memory_manager,
            update_memory_on_run=True,
            telemetry=False,
        )
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        agent.run("hi", session_id="s1", user_id="u1", run_context=run_context)

        provider.assemble_context.assert_called_once()
        provider.write.assert_called_once()
        memory_manager.create_user_memories.assert_called_once()
        invoked_contents = [m.content for m in model.invoked_messages]
        assert "INJECTED MEMORY CONTEXT" in invoked_contents


class TestWriteBackPayloadIsTheTurnNotThePrompt:
    """Regression guards for the write-back payload (SM-X3 review round 2).

    ``run_response.messages`` is the whole model input, so passing it to ``events_from_run``
    unfiltered persists the system prompt, every replayed history message, and a duplicate of
    every tool result. Ids are minted per write, so nothing collapses the replays.
    """

    def test_history_is_not_rewritten_on_every_turn(self):
        """Without the `from_history` filter this grows 2 -> 4 -> 6 as history replays."""
        from egpt.integrations.db.in_memory import InMemoryDb

        provider = _make_memory_provider()
        batches: list = []
        provider.write.side_effect = lambda scope, events: batches.append(list(events)) or WriteReceipt(accepted=1)

        agent = Agent(
            model=MockModel(),
            memory_provider=provider,
            db=InMemoryDb(),
            add_history_to_context=True,
            telemetry=False,
            id="agent-x",
        )
        for i in (1, 2, 3):
            agent.run(
                f"turn {i}",
                session_id="s1",
                user_id="u1",
                run_context=RunContext(run_id=f"r{i}", session_id="s1", user_id="u1", tenant_id="acme"),
            )

        assert [len(b) for b in batches] == [2, 2, 2]
        # The third turn carries turn 3 only -- no replay of turns 1 and 2.
        assert [e.content for e in batches[2]] == ["turn 3", "ok"]

    def test_a_tool_result_is_written_once_not_twice(self):
        """`messages` carries a role="tool" copy of what `tool_executions` already holds; the
        ToolExecution is the richer record (it alone has tool_name/tool_call_id)."""
        from egpt.models.message import MessageMetrics
        from egpt.tools import tool

        class ToolCallingModel(MockModel):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def invoke(self, *args, **kwargs):
                self.invoked_messages = kwargs.get("messages", [])
                self.calls += 1
                if self.calls == 1:
                    return ModelResponse(
                        content="",
                        role="assistant",
                        tool_calls=[
                            {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}
                        ],
                        response_usage=MessageMetrics(),
                    )
                return ModelResponse(content="done", role="assistant", response_usage=MessageMetrics())

        @tool
        def get_weather() -> str:
            """Weather."""
            return "sunny in paris"

        provider = _make_memory_provider()
        batches: list = []
        provider.write.side_effect = lambda scope, events: batches.append(list(events)) or WriteReceipt(accepted=1)

        agent = Agent(
            model=ToolCallingModel(), memory_provider=provider, tools=[get_weather], telemetry=False, id="agent-t"
        )
        agent.run(
            "weather?",
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
        )

        written = [e.content for e in batches[0]]
        assert written.count("sunny in paris") == 1
        # ...and the copy that survived is the one carrying tool provenance.
        tool_events = [e for e in batches[0] if e.content == "sunny in paris"]
        assert tool_events[0].metadata.get("tool_name") == "get_weather"

    def test_the_agents_own_system_prompt_is_not_persisted(self):
        provider = _make_memory_provider()
        batches: list = []
        provider.write.side_effect = lambda scope, events: batches.append(list(events)) or WriteReceipt(accepted=1)

        agent = Agent(
            model=MockModel(),
            memory_provider=provider,
            instructions="You are a helpful assistant with a very distinctive instruction.",
            telemetry=False,
            id="agent-s",
        )
        agent.run(
            "hi",
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
        )

        roles = [e.metadata.get("role") for e in batches[0]]
        assert "system" not in roles
        assert not any("distinctive instruction" in (e.content or "") for e in batches[0])


class TestReadScopeIsNotSessionLocal:
    """Regression guard: scope narrowing is conjunctive across every set slot, so a read scope
    pinned to `session_id` (or to an auto-generated `agent_id`) can never see what another
    session -- or another process -- wrote."""

    def test_the_read_scope_is_identical_across_sessions(self):
        provider = _make_memory_provider()
        read_scopes: list = []
        provider.assemble_context.side_effect = lambda scope, working, query: (
            read_scopes.append(scope) or "INJECTED MEMORY CONTEXT"
        )

        agent = Agent(model=MockModel(), memory_provider=provider, telemetry=False, id="agent-x")
        for session_id in ("s1", "s2"):
            agent.run(
                "hi",
                session_id=session_id,
                user_id="u1",
                run_context=RunContext(run_id=f"r-{session_id}", session_id=session_id, user_id="u1", tenant_id="acme"),
            )

        assert read_scopes[0] == read_scopes[1]
        assert all(s.session_id is None and s.agent_id is None for s in read_scopes)

    def test_the_write_scope_keeps_session_and_agent_provenance(self):
        """The read widening must not cost the stored row its provenance."""
        provider = _make_memory_provider()
        write_scopes: list = []
        provider.write.side_effect = lambda scope, events: write_scopes.append(scope) or WriteReceipt(accepted=1)

        agent = Agent(model=MockModel(), memory_provider=provider, telemetry=False, id="agent-x")
        agent.run(
            "hi",
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
        )

        assert write_scopes[0].session_id == "s1"
        assert write_scopes[0].agent_id == "agent-x"


class TestBlankScopeValuesDegradeRatherThanFailTheRun:
    """`Scope.__post_init__` rejects blank/whitespace slots. The hooks' contract is
    degrade-don't-fail, so a stray "" from a programmatic caller must no-op, not error the run."""

    @pytest.mark.parametrize(
        "tenant_id,user_id",
        [("", "u1"), ("   ", "u1"), ("acme", "  ")],
        ids=["blank-tenant", "whitespace-tenant", "whitespace-user"],
    )
    def test_run_completes_with_memory_skipped(self, tenant_id, user_id):
        from egpt.run.base import RunStatus

        provider = _make_memory_provider()
        agent = Agent(model=MockModel(), memory_provider=provider, telemetry=False, id="agent-x")

        response = agent.run(
            "hi",
            session_id="s1",
            user_id=user_id,
            run_context=RunContext(run_id="r1", session_id="s1", user_id=user_id, tenant_id=tenant_id),
        )

        assert response.status == RunStatus.completed
        provider.assemble_context.assert_not_called()
        provider.write.assert_not_called()


def _hitl_agent(provider, model):
    from egpt.tools import tool

    @tool(requires_confirmation=True)
    def act() -> str:
        """Act."""
        return "action performed"

    return Agent(model=model, memory_provider=provider, tools=[act], telemetry=False, id="agent-h")


class _PausingModel(MockModel):
    """Calls a confirmation-gated tool on the first turn, answers on the second."""

    def __init__(self):
        super().__init__()
        self.calls = 0
        self.seen: list = []

    def _next(self, messages):
        self.invoked_messages = messages
        self.seen.append(list(messages))
        self.calls += 1
        if self.calls == 1:
            from egpt.models.message import MessageMetrics

            return ModelResponse(
                content="",
                role="assistant",
                tool_calls=[{"id": "c1", "type": "function", "function": {"name": "act", "arguments": "{}"}}],
                response_usage=MessageMetrics(),
            )
        from egpt.models.message import MessageMetrics

        return ModelResponse(content="done", role="assistant", response_usage=MessageMetrics())

    def invoke(self, *args, **kwargs):
        return self._next(kwargs.get("messages", []))

    async def ainvoke(self, *args, **kwargs):
        return self._next(kwargs.get("messages", []))

    def invoke_stream(self, *args, **kwargs):
        yield self._next(kwargs.get("messages", []))

    async def ainvoke_stream(self, *args, **kwargs):
        yield self._next(kwargs.get("messages", []))
        return


class TestHitlResumedTurnsEngageMemory:
    """A HITL-gated run splits one exchange across two legs. Both legs must behave:

    * the paused leg must not write, or memory records the question and discards the answer;
    * the resumed leg must re-assemble the feed-forward block, because it is injected with
      ``add_to_agent_memory=False`` and so is absent from ``run_response.messages``.
    """

    def _confirm(self, run_response):
        for tool_call in run_response.tools or []:
            if tool_call.is_paused:
                tool_call.confirmed = True

    def test_sync(self):
        from egpt.run.base import RunStatus

        provider = _make_memory_provider()
        batches: list = []
        provider.write.side_effect = lambda scope, events: batches.append(list(events)) or WriteReceipt(accepted=1)
        model = _PausingModel()
        agent = _hitl_agent(provider, model)
        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

        paused = agent.run("please act", session_id="s1", user_id="u1", run_context=run_context)
        assert paused.status == RunStatus.paused
        assert batches == [], "a paused turn must not persist half an exchange"

        self._confirm(paused)
        resumed = agent.continue_run(
            run_response=paused,
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
        )

        # The resumed leg saw memory...
        resumed_contents = [m.content for m in model.seen[1]]
        assert "INJECTED MEMORY CONTEXT" in resumed_contents
        # ...without persisting the block as history (add_to_agent_memory=False on the
        # continue-run injection site)...
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in resumed.messages]
        # ...and persisted the completed exchange exactly once.
        assert len(batches) == 1
        written = [e.content for e in batches[0]]
        assert "action performed" in written
        assert "done" in written

    @pytest.mark.asyncio
    async def test_async(self):
        from egpt.run.base import RunStatus

        provider = _make_memory_provider()
        batches: list = []

        async def _awrite(scope, events):
            batches.append(list(events))
            return WriteReceipt(accepted=1)

        provider.awrite = _awrite
        model = _PausingModel()
        agent = _hitl_agent(provider, model)

        paused = await agent.arun(
            "please act",
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
        )
        assert paused.status == RunStatus.paused
        assert batches == []

        self._confirm(paused)
        resumed = await agent.acontinue_run(
            run_response=paused,
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
        )

        resumed_contents = [m.content for m in model.seen[1]]
        assert "INJECTED MEMORY CONTEXT" in resumed_contents
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in resumed.messages]
        assert len(batches) == 1
        assert "action performed" in [e.content for e in batches[0]]

    def test_sync_stream(self):
        from egpt.run.base import RunStatus

        provider = _make_memory_provider()
        batches: list = []
        provider.write.side_effect = lambda scope, events: batches.append(list(events)) or WriteReceipt(accepted=1)
        model = _PausingModel()
        agent = _hitl_agent(provider, model)

        paused = None
        for event in agent.run(
            "please act",
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
            stream=True,
            yield_run_output=True,
        ):
            if isinstance(event, RunOutput):
                paused = event
        assert paused.status == RunStatus.paused
        assert batches == []

        self._confirm(paused)
        resumed = None
        for event in agent.continue_run(
            run_response=paused,
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
            stream=True,
            yield_run_output=True,
        ):
            if isinstance(event, RunOutput):
                resumed = event

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.seen[1]]
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in resumed.messages]
        assert len(batches) == 1
        assert "action performed" in [e.content for e in batches[0]]

    @pytest.mark.asyncio
    async def test_async_stream(self):
        from egpt.run.base import RunStatus

        provider = _make_memory_provider()
        batches: list = []

        async def _awrite(scope, events):
            batches.append(list(events))
            return WriteReceipt(accepted=1)

        provider.awrite = _awrite
        model = _PausingModel()
        agent = _hitl_agent(provider, model)

        paused = None
        async for event in agent.arun(
            "please act",
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
            stream=True,
            yield_run_output=True,
        ):
            if isinstance(event, RunOutput):
                paused = event
        assert paused.status == RunStatus.paused
        assert batches == []

        self._confirm(paused)
        resumed = None
        async for event in agent.acontinue_run(
            run_response=paused,
            session_id="s1",
            user_id="u1",
            run_context=RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme"),
            stream=True,
            yield_run_output=True,
        ):
            if isinstance(event, RunOutput):
                resumed = event

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.seen[1]]
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in resumed.messages]
        assert len(batches) == 1
        assert "action performed" in [e.content for e in batches[0]]


class TestStreamingAndAsyncSitesAreGuarded:
    """Per-site coverage for the wired hooks.

    Before this, 8 of the 12 injection sites (every streaming path, every tasks-mode path) could
    be deleted with the suite still green, because `MockModel.invoke_stream`/`ainvoke_stream`
    recorded nothing. `invoked_messages` is now recorded on all four entry points, so each
    variant below fails if its own site loses the hook.
    """

    def _run_context(self):
        return RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

    def test_sync_stream(self):
        provider = _make_memory_provider()
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False, id="agent-x")

        deque(
            agent.run("hi", session_id="s1", user_id="u1", run_context=self._run_context(), stream=True),
            maxlen=0,
        )

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.invoked_messages]
        provider.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_stream(self):
        provider = _make_memory_provider()
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False, id="agent-x")

        async for _ in agent.arun("hi", session_id="s1", user_id="u1", run_context=self._run_context(), stream=True):
            pass

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.invoked_messages]
        provider.awrite.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_writeback_payload_is_the_turn(self):
        """`test_async_parity` asserted only that `awrite` fired, never what it carried -- so the
        payload fix was unguarded on the async path."""
        provider = _make_memory_provider()
        batches: list = []

        async def _awrite(scope, events):
            batches.append(list(events))
            return WriteReceipt(accepted=1)

        provider.awrite = _awrite
        agent = Agent(
            model=MockModel(),
            memory_provider=provider,
            instructions="A very distinctive instruction.",
            telemetry=False,
            id="agent-x",
        )

        await agent.arun("hi", session_id="s1", user_id="u1", run_context=self._run_context())

        assert [e.content for e in batches[0]] == ["hi", "ok"]
        assert "system" not in [e.metadata.get("role") for e in batches[0]]


class TestInjectedBlockIsNotPersistedToSessionHistory:
    """`add_to_agent_memory=False` on the injected block is what keeps it out of
    `run_response.messages` -- the list that is stored on the session and replayed as history.

    Write-back has its own defence (``turn_messages`` drops ``role="system"``), so dropping this
    flag no longer leaks the block into the memory payload. What it *does* leak is session
    history: the block would be persisted and replayed on later turns, growing the prompt and
    re-showing stale retrieved context as if the user had said it. These assertions fail if the
    flag is removed from the site each variant exercises.
    """

    def _run_context(self):
        return RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")

    def test_sync(self):
        provider = _make_memory_provider()
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False, id="agent-x")

        response = agent.run("hi", session_id="s1", user_id="u1", run_context=self._run_context())

        # The model saw it...
        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.invoked_messages]
        # ...but it is not carried forward as history.
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in response.messages]
        assert "system" not in [m.role for m in response.messages]

    def test_sync_stream(self):
        provider = _make_memory_provider()
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False, id="agent-x")

        response = None
        for event in agent.run(
            "hi",
            session_id="s1",
            user_id="u1",
            run_context=self._run_context(),
            stream=True,
            yield_run_output=True,
        ):
            if isinstance(event, RunOutput):
                response = event

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.invoked_messages]
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in response.messages]

    @pytest.mark.asyncio
    async def test_async(self):
        provider = _make_memory_provider()
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False, id="agent-x")

        response = await agent.arun("hi", session_id="s1", user_id="u1", run_context=self._run_context())

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.invoked_messages]
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in response.messages]

    @pytest.mark.asyncio
    async def test_async_stream(self):
        provider = _make_memory_provider()
        model = MockModel()
        agent = Agent(model=model, memory_provider=provider, telemetry=False, id="agent-x")

        response = None
        async for event in agent.arun(
            "hi",
            session_id="s1",
            user_id="u1",
            run_context=self._run_context(),
            stream=True,
            yield_run_output=True,
        ):
            if isinstance(event, RunOutput):
                response = event

        assert "INJECTED MEMORY CONTEXT" in [m.content for m in model.invoked_messages]
        assert "INJECTED MEMORY CONTEXT" not in [m.content for m in response.messages]
