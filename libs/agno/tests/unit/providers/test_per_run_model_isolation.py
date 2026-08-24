"""Per-run model resolution must not write to the shared component (aicoe-tracker#166).

``apply_run_model`` used to assign the resolved model to ``component.model``. That attribute is
per-*object*, and CLAUDE.md tells users to reuse agents rather than build one per request, so
per-run state was landing on shared state. Two things broke, and both are pinned here:

* **Concurrency.** Under ``asyncio.gather(agent.arun(a), agent.arun(b))``, a run that escalates
  changed the model the *other* run's in-flight call would use -- metered frontier egress the
  second run never asked for, and in a per-tenant deployment possibly the wrong ``base_url``.
* **Persistence.** The mutation outlived the run, so a later run whose resolution failed took
  the "keep the current model" branch and kept the *escalated* model, silently pinning a reused
  component to the expensive tier.

The fix carries the resolved model on ``RunContext`` -- already the per-run carrier, already
threaded to every model-call site -- and leaves ``component.model`` as the caller's
configuration. These tests assert the component is never mutated and that each run reads its own
model, which is what makes the two symptoms above impossible rather than merely unlikely.
"""

from __future__ import annotations

import asyncio

import pytest

from egpt.agent._run import aapply_run_model, apply_run_model, run_model
from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.model_provider import ProviderMode
from egpt.run.agent import ModelRequestCompletedEvent, ModelRequestStartedEvent
from egpt.run.base import RunContext
from egpt.run.team import ModelRequestCompletedEvent as TeamModelRequestCompletedEvent
from egpt.run.team import ModelRequestStartedEvent as TeamModelRequestStartedEvent
from tests.unit.providers.test_escalation import FRONTIER, SMALL, FakeDetailedProvider, FakeModel
from tests.unit.providers.test_run_model_hook import StubAgent


@pytest.fixture
def primary() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)


@pytest.fixture
def frontier() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-b", FRONTIER, ProviderMode.FRONTIER)


def _ctx(run_id: str = "r-1") -> RunContext:
    return RunContext(run_id=run_id, session_id="s-1", user_id="u-1")


def _escalating_agent(primary, frontier, model_id: str = "configured"):
    return StubAgent(
        model=FakeModel(id=model_id),
        model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
    )


class TestTheComponentIsNotMutated:
    """The core of the fix: resolution is readable per run without touching shared state."""

    def test_escalation_does_not_write_to_the_component(self, primary, frontier):
        agent = _escalating_agent(primary, frontier)
        configured = agent.model
        ctx = _ctx()

        apply_run_model(agent, ctx, tools=[object()] * 50)

        assert run_model(agent, ctx).id == "mode-b-model", "the run must see the escalated model"
        assert agent.model is configured, "the shared component must keep the caller's model"

    def test_a_non_escalating_run_also_leaves_the_component_alone(self, primary, frontier):
        agent = _escalating_agent(primary, frontier)
        configured = agent.model
        ctx = _ctx()

        apply_run_model(agent, ctx, tools=None)

        assert run_model(agent, ctx).id == "mode-a-model"
        assert agent.model is configured

    def test_without_a_provider_the_run_reads_the_components_model(self):
        """The pre-Epic-3 path: nothing resolves, so the run serves the configured model."""
        agent = StubAgent(model=FakeModel(id="configured"))
        ctx = _ctx()

        assert apply_run_model(agent, ctx, tools=None) is False
        assert run_model(agent, ctx) is agent.model


class TestConcurrentRunsDoNotSeeEachOther:
    """Symptom 1. Two runs sharing one component must resolve independently."""

    def test_two_run_contexts_hold_their_own_models(self, primary, frontier):
        agent = _escalating_agent(primary, frontier)
        escalating, plain = _ctx("r-escalate"), _ctx("r-plain")

        apply_run_model(agent, escalating, tools=[object()] * 50)
        apply_run_model(agent, plain, tools=None)

        assert run_model(agent, escalating).id == "mode-b-model"
        assert run_model(agent, plain).id == "mode-a-model", "the plain run must not inherit the escalating run's model"

    def test_a_later_escalation_cannot_reach_an_in_flight_run(self, primary, frontier):
        """The gather() case: run B resolves, then run A escalates. B must be unaffected."""
        agent = _escalating_agent(primary, frontier)
        b, a = _ctx("r-b"), _ctx("r-a")

        apply_run_model(agent, b, tools=None)
        b_model = run_model(agent, b)
        apply_run_model(agent, a, tools=[object()] * 50)

        assert run_model(agent, b) is b_model
        assert run_model(agent, b).id == "mode-a-model"

    @pytest.mark.asyncio
    async def test_interleaved_async_runs_keep_their_own_models(self, primary, frontier):
        agent = _escalating_agent(primary, frontier)
        escalating, plain = _ctx("r-escalate"), _ctx("r-plain")

        async def resolve(ctx, tools):
            await aapply_run_model(agent, ctx, tools=tools)
            await asyncio.sleep(0)  # yield, so the other run interleaves before we read
            return run_model(agent, ctx).id

        escalated_id, plain_id = await asyncio.gather(
            resolve(escalating, [object()] * 50),
            resolve(plain, None),
        )

        assert escalated_id == "mode-b-model"
        assert plain_id == "mode-a-model"


class TestEscalationDoesNotOutliveTheRun:
    """Symptom 2. A resolution failure must fall back to the component, not to a stale escalation."""

    def test_a_later_failing_run_falls_back_to_the_configured_model(self, primary, frontier):
        agent = _escalating_agent(primary, frontier)
        escalated = _ctx("r-1")
        apply_run_model(agent, escalated, tools=[object()] * 50)
        assert run_model(agent, escalated).id == "mode-b-model"

        class Boom:
            def resolve(self, request):
                raise RuntimeError("gateway unreachable")

        agent.model_provider = Boom()
        failed = _ctx("r-2")

        assert apply_run_model(agent, failed, tools=None) is False
        assert run_model(agent, failed) is agent.model, (
            "a failed resolution must keep the *configured* model, not the previous escalation"
        )
        assert run_model(agent, failed).id == "configured"

    def test_a_fresh_run_context_starts_from_the_component(self, primary, frontier):
        """No resolution at all: the run serves what the caller configured."""
        agent = _escalating_agent(primary, frontier)
        apply_run_model(agent, _ctx("r-1"), tools=[object()] * 50)

        assert run_model(agent, _ctx("r-2")).id == "configured"


class TestRunModelAccessor:
    def test_it_prefers_the_run_context_over_the_component(self):
        import weakref

        agent = StubAgent(model=FakeModel(id="configured"))
        ctx = _ctx()
        ctx.resolved_model = FakeModel(id="per-run")
        # The owner stamp is part of the contract: a resolution is readable only by the
        # component that produced it. `apply_run_model` sets both together.
        ctx.resolved_model_owner = weakref.ref(agent)

        assert run_model(agent, ctx).id == "per-run"

    def test_an_unowned_resolution_is_ignored(self):
        """A resolved_model with no owner belongs to nobody, so it must not be served.

        Fails safe in the one direction that matters: the component's own model, never
        somebody else's resolution.
        """
        agent = StubAgent(model=FakeModel(id="configured"))
        ctx = _ctx()
        ctx.resolved_model = FakeModel(id="orphan")

        assert run_model(agent, ctx).id == "configured"

    def test_it_falls_back_to_the_component(self):
        agent = StubAgent(model=FakeModel(id="configured"))
        assert run_model(agent, _ctx()).id == "configured"

    def test_a_missing_run_context_is_not_an_error(self):
        """Some run-path helpers are reachable without one; they must still find a model."""
        agent = StubAgent(model=FakeModel(id="configured"))
        assert run_model(agent, None).id == "configured"


# ---------------------------------------------------------------------------
# Live-run coverage
#
# Everything above drives `apply_run_model` directly, which pins the *storage* decision but says
# nothing about whether the run path then *reads* the per-run model. Mutation-testing showed that
# gap concretely: reverting the tool binding and the Team model call back to `component.model`
# left the whole suite green. These exercise a real turn so those read sites are pinned too.
# ---------------------------------------------------------------------------

from egpt.agent.agent import Agent  # noqa: E402
from egpt.providers import set_default_model_provider  # noqa: E402
from egpt.team.team import Team  # noqa: E402
from tests.unit.providers.test_dual_mode_e2e import RecordingModel, _RecordingProvider  # noqa: E402


@pytest.fixture(autouse=True)
def _no_leaked_default_provider():
    """Reset the process-wide state a live run mutates, so ordering cannot decide an outcome.

    Two globals, both of which outlive the test that set them:

    * the default model provider, and
    * ``egpt.utils.log.logger`` -- ``Team.run``/``Agent.run`` call ``use_team_logger()`` /
      ``use_agent_logger()`` and never restore it. A later test doing
      ``caplog.set_level(logger="egpt")`` then captures nothing, because records are going to
      ``egpt-team``. That is what turned the whole of ``test_escalation_logging.py`` red in a
      full-tree run while every targeted run stayed green.
    """
    import egpt.utils.log as _log

    before = _log.logger
    yield
    set_default_model_provider(None)
    _log.logger = before


class TestALiveRunServesTheResolvedModel:
    def test_an_agent_turn_serves_the_providers_model_and_leaves_the_component_alone(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(telemetry=False, model=configured)
        agent.run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert served.invocations == 1, "the turn must be served by the model the provider returned"
        assert agent.model is configured, "and the component must keep the caller's model"

    def test_the_tool_binding_sees_the_resolved_model(self):
        """`determine_tools_for_model` binds tools to the model that will serve them.

        Reverting that one argument to `agent.model` left every other test green, so this asserts
        it directly: the object handed to the binding must be the resolved model, not the
        component's.
        """
        seen: list = []
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        # `_run.py` imports this lazily inside the run body, so the patch has to land on the
        # defining module rather than on a name already bound in `_run`'s globals.
        import egpt.agent._tools as agent_tools

        real = agent_tools.determine_tools_for_model

        def spy(agent, *, model, **kw):
            seen.append(model)
            return real(agent, model=model, **kw)

        agent = Agent(telemetry=False, model=configured)
        try:
            agent_tools.determine_tools_for_model = spy
            agent.run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))
        finally:
            agent_tools.determine_tools_for_model = real

        assert seen, "the run path must bind tools for a model"
        assert all(m is served for m in seen), (
            f"tools must bind to the resolved model, got {[getattr(m, 'id', m) for m in seen]}"
        )

    def test_a_team_turn_serves_the_providers_model_and_leaves_the_component_alone(self):
        """The Team path has its own copy of every read site, so the agent proof does not carry."""
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        team = Team(members=[], name="t", model=configured, telemetry=False)
        team.run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert served.invocations == 1, "the team turn must be served by the resolved model"
        assert team.model is configured, "and the component must keep the caller's model"


class TestStreamingServesTheResolvedModel:
    """The streaming path is the default interactive one, and it had its own copy of the bug.

    `_run_stream`/`_arun_stream` resolved the model correctly for tools and response_format, but
    `handle_model_response_stream`/`ahandle_model_response_stream` (and the Team equivalents) then
    passed `component.model` straight to `call_model_stream_with_fallback` -- so an escalated run
    still called the *base* model at the actual API-request boundary. Reverting either call site to
    `agent.model`/`team.model` must turn these red.
    """

    def test_an_agent_stream_serves_the_resolved_model(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(telemetry=False, model=configured)
        list(agent.run("hello", stream=True, run_context=RunContext(run_id="r-1", session_id="s-1")))

        assert served.invocations == 1, "the streamed turn must be served by the resolved model"
        assert configured.invocations == 0, "the component's own model must never be invoked"
        assert agent.model is configured, "and the component must keep the caller's model"

    @pytest.mark.asyncio
    async def test_an_async_agent_stream_serves_the_resolved_model(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(telemetry=False, model=configured)
        async for _ in agent.arun("hello", stream=True, run_context=RunContext(run_id="r-1", session_id="s-1")):
            pass

        assert served.invocations == 1
        assert configured.invocations == 0
        assert agent.model is configured

    def test_a_team_stream_serves_the_resolved_model(self):
        """The Team path carries its own copy of every streaming read site."""
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        team = Team(members=[], name="t", model=configured, telemetry=False)
        list(team.run("hello", stream=True, run_context=RunContext(run_id="r-1", session_id="s-1")))

        assert served.invocations == 1
        assert configured.invocations == 0
        assert team.model is configured


class TestStreamingEventsReportTheResolvedModel:
    """aicoe-tracker#182's sibling gap: the model call itself resolved correctly, but the
    ``model_request_started``/``model_request_completed`` event payloads emitted alongside it
    still read ``agent.model``/``team.model`` directly, so a stream consumer (as opposed to
    telemetry) still saw the caller's configured model for an escalated run. Reverting either
    event-payload site to ``agent.model``/``team.model`` must turn these red.
    """

    def test_an_agent_streams_events_naming_the_resolved_model(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(telemetry=False, model=configured)
        events = list(
            agent.run(
                "hello",
                stream=True,
                stream_events=True,
                run_context=RunContext(run_id="r-1", session_id="s-1"),
            )
        )

        model_events = [e for e in events if isinstance(e, (ModelRequestStartedEvent, ModelRequestCompletedEvent))]
        assert model_events, "the run must emit at least one model-request event"
        for event in model_events:
            assert event.model == served.id, "the streamed event must name the resolved model"
            assert event.model_provider == served.provider

    @pytest.mark.asyncio
    async def test_an_async_agent_streams_events_naming_the_resolved_model(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        agent = Agent(telemetry=False, model=configured)
        events = [
            e
            async for e in agent.arun(
                "hello",
                stream=True,
                stream_events=True,
                run_context=RunContext(run_id="r-1", session_id="s-1"),
            )
        ]

        model_events = [e for e in events if isinstance(e, (ModelRequestStartedEvent, ModelRequestCompletedEvent))]
        assert model_events, "the run must emit at least one model-request event"
        for event in model_events:
            assert event.model == served.id
            assert event.model_provider == served.provider

    def test_a_team_streams_events_naming_the_resolved_model(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        team = Team(members=[], name="t", model=configured, telemetry=False)
        events = list(
            team.run(
                "hello",
                stream=True,
                stream_events=True,
                run_context=RunContext(run_id="r-1", session_id="s-1"),
            )
        )

        model_events = [
            e for e in events if isinstance(e, (TeamModelRequestStartedEvent, TeamModelRequestCompletedEvent))
        ]
        assert model_events, "the run must emit at least one model-request event"
        for event in model_events:
            assert event.model == served.id
            assert event.model_provider == served.provider

    @pytest.mark.asyncio
    async def test_an_async_team_streams_events_naming_the_resolved_model(self):
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        team = Team(members=[], name="t", model=configured, telemetry=False)
        events = [
            e
            async for e in team.arun(
                "hello",
                stream=True,
                stream_events=True,
                run_context=RunContext(run_id="r-1", session_id="s-1"),
            )
        ]

        model_events = [
            e for e in events if isinstance(e, (TeamModelRequestStartedEvent, TeamModelRequestCompletedEvent))
        ]
        assert model_events, "the run must emit at least one model-request event"
        for event in model_events:
            assert event.model == served.id
            assert event.model_provider == served.provider

    def test_events_name_the_configured_model_with_no_escalation(self):
        """The baseline the escalation tests above must not disturb: with no provider set,
        run_model falls back to component.model, so the streamed events must still name the
        caller's configured model -- exactly what they read before this class's fix existed.
        """
        configured = RecordingModel("configured")
        set_default_model_provider(None)

        agent = Agent(telemetry=False, model=configured)
        events = list(
            agent.run(
                "hello",
                stream=True,
                stream_events=True,
                run_context=RunContext(run_id="r-1", session_id="s-1"),
            )
        )

        model_events = [e for e in events if isinstance(e, (ModelRequestStartedEvent, ModelRequestCompletedEvent))]
        assert model_events, "the run must emit at least one model-request event"
        for event in model_events:
            assert event.model == configured.id
            assert event.model_provider == configured.provider

        team = Team(members=[], name="t", model=configured, telemetry=False)
        team_events = list(
            team.run(
                "hello",
                stream=True,
                stream_events=True,
                run_context=RunContext(run_id="r-2", session_id="s-2"),
            )
        )

        team_model_events = [
            e for e in team_events if isinstance(e, (TeamModelRequestStartedEvent, TeamModelRequestCompletedEvent))
        ]
        assert team_model_events, "the team run must emit at least one model-request event"
        for team_event in team_model_events:
            assert team_event.model == configured.id
            assert team_event.model_provider == configured.provider


class TestASharedRunContextDoesNotLeakAcrossComponents:
    """A workflow passes ONE RunContext into every step's executor (`workflow/step.py`).

    So the per-run field that fixed the concurrent-run leak opened a sequential one: step 1's
    escalating agent wrote `resolved_model`, step 2's provider-less agent took
    `apply_run_model`'s no-op path (which writes nothing), and `run_model` handed it step 1's
    frontier model -- wrong model, provider and `base_url`, silently. Raised in review of #81
    and reproduced before this guard existed.

    The resolution is therefore keyed to the component that produced it.
    """

    def test_a_provider_less_component_serves_its_own_model(self, primary, frontier):
        shared = _ctx("workflow-run")
        a = _escalating_agent(primary, frontier, model_id="A-base")
        b = StubAgent(model=FakeModel(id="B-own"))  # no provider: the common case

        apply_run_model(a, shared, tools=[object()] * 50)
        assert run_model(a, shared).id == "mode-b-model", "step 1 escalates"

        apply_run_model(b, shared, tools=None)
        assert run_model(b, shared).id == "B-own", "step 2 must serve its own model, not step 1's escalation"

    def test_the_escalating_component_still_reads_its_own_resolution(self, primary, frontier):
        """Keying must not cost the owner its own result."""
        shared = _ctx("workflow-run")
        a = _escalating_agent(primary, frontier, model_id="A-base")
        b = StubAgent(model=FakeModel(id="B-own"))

        apply_run_model(a, shared, tools=[object()] * 50)
        apply_run_model(b, shared, tools=None)

        assert run_model(a, shared).id == "mode-b-model"

    def test_a_re_entered_component_does_not_serve_a_stale_escalation(self, primary, frontier):
        """The `Loop`/`Router` shape: one component, one shared context, several iterations.

        The owner key stops *cross*-component leaks, but the same component re-entered on the
        same context could still serve its own stale resolution: iteration 1 escalates,
        iteration 2's provider answers "the primary suffices" (`resolve_run_model` returns None,
        so `apply_run_model` returns False without writing), and the frontier model from
        iteration 1 would serve anyway -- against the provider's explicit verdict, and at
        frontier cost. That is aicoe-tracker#166's "escalation outlives the run" one level up.
        """
        shared = _ctx("loop-run")
        a = _escalating_agent(primary, frontier, model_id="A-base")

        apply_run_model(a, shared, tools=[object()] * 50)  # iteration 1 escalates
        assert run_model(a, shared).id == "mode-b-model"

        a.model_provider = None  # iteration 2: nothing to resolve
        apply_run_model(a, shared, tools=None)

        assert run_model(a, shared).id == "A-base", (
            "iteration 2 must re-resolve from a clean slate, not inherit iteration 1's escalation"
        )

    def test_a_failed_re_resolution_falls_back_to_the_configured_model(self, primary, frontier):
        """A transient provider failure must not silently keep an escalation nobody re-verified."""
        shared = _ctx("loop-run")
        a = _escalating_agent(primary, frontier, model_id="A-base")
        apply_run_model(a, shared, tools=[object()] * 50)

        class Boom:
            def resolve(self, request):
                raise RuntimeError("gateway unreachable")

        a.model_provider = Boom()
        assert apply_run_model(a, shared, tools=None) is False
        assert run_model(a, shared).id == "A-base"

    def test_clearing_does_not_disturb_a_sibling_on_the_same_context(self, primary, frontier):
        """Only the re-entering component's own stamp is cleared.

        Two components can be mid-run on one shared context, so clearing indiscriminately would
        clobber a sibling's in-flight resolution -- the very thing the owner key protects.
        """
        shared = _ctx("shared-run")
        a = _escalating_agent(primary, frontier, model_id="A-base")
        b = StubAgent(model=FakeModel(id="B-own"))

        apply_run_model(a, shared, tools=[object()] * 50)
        apply_run_model(b, shared, tools=None)  # B re-entering must not wipe A's stamp blindly

        assert run_model(a, shared).id == "mode-b-model"
        assert run_model(b, shared).id == "B-own"

    def test_each_component_reads_its_own_resolution_within_its_own_step(self, primary, frontier):
        """Two provider-backed components on one shared context, resolved-then-read in turn.

        This is the real ordering: a step resolves and reads its model inside its own turn
        (`_run` calls `apply_run_model` then `run_model` a few lines later) before the next step
        starts. The context holds one resolution at a time by design, so the guarantee being
        pinned is "a component never serves a model resolved by a *different* component", not
        "every past resolution stays readable forever". Parallel steps do not share a context at
        all -- `workflow/parallel.py:331` shallow-copies one per step.
        """
        shared = _ctx("workflow-run")
        a = _escalating_agent(primary, frontier, model_id="A-base")
        b = _escalating_agent(primary, frontier, model_id="B-base")

        apply_run_model(a, shared, tools=[object()] * 50)  # step 1 escalates
        assert run_model(a, shared).id == "mode-b-model"

        apply_run_model(b, shared, tools=None)  # step 2 stays on the primary
        assert run_model(b, shared).id == "mode-a-model"


class TestTheOutOfDiffReadSitesUseTheServingModel:
    """Sites that read `component.model` directly, found across review rounds 2-4.

    Each is the same bug class as the original: a capability check, a clone, or an attribution
    stamp derived from the *configured* model while an escalated one actually serves the run.
    """

    def test_the_structured_output_capability_check_reads_the_serving_model(self):
        """A primary without native structured-output support can escalate to one that has it.

        Deciding off `agent.model` shapes the schema for the wrong tier -- exactly what
        `recompute_response_format` exists to prevent, one function over.
        """
        from egpt.agent._response import model_should_return_structured_output
        from pydantic import BaseModel

        class Out(BaseModel):
            x: int

        configured = FakeModel(id="primary")
        configured.supports_native_structured_outputs = False
        escalated = FakeModel(id="frontier")
        escalated.supports_native_structured_outputs = True

        agent = StubAgent(model=configured)
        agent.use_json_mode = False
        agent.structured_outputs = True

        ctx = _ctx()
        ctx.output_schema = Out
        assert model_should_return_structured_output(agent, ctx) is False, "no resolution: the primary decides"

        import weakref

        ctx.resolved_model = escalated
        ctx.resolved_model_owner = weakref.ref(agent)
        assert model_should_return_structured_output(agent, ctx) is True, (
            "with an escalated model serving, its capabilities decide"
        )

    def test_the_team_dispatch_stamp_is_defensive_only(self):
        """`run_dispatch` stamps attribution before the run body, from the pre-resolution model.

        Reverting that read to `team.model` does NOT fail an end-to-end assertion, because
        `_stamp_resolved_model` re-stamps `run_response.model` after the hook resolves. So the
        fix here is defensive -- it makes the pre-resolution window name the right model for any
        reader in between (and for a run that never reaches the hook) rather than closing an
        observable bug. Recorded honestly rather than pinned with a test that cannot fail.
        """
        configured = RecordingModel("configured")
        served = RecordingModel("provider-chose-this")
        set_default_model_provider(_RecordingProvider("mode-a", served, ProviderMode.LOCAL))

        team = Team(members=[], name="t", model=configured, telemetry=False)
        response = team.run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        # What IS load-bearing and observable: the served model is the resolved one.
        assert served.invocations == 1
        assert response.model == "provider-chose-this"
