"""The per-run model hook in ``agent/_run.py`` (EGPT-3.5).

``set_default_model`` resolves once per Agent *object*, so without this hook a long-lived Agent
could never escalate: by the second run its model is already assigned and the seam is never
re-entered. These tests pin that ``apply_run_model`` re-resolves per run, stays a no-op when
nobody configured a provider, and never turns a provider problem into a failed run.

Driven through ``apply_run_model`` directly rather than a full ``Agent.run``: the hook's
contract is "given an agent and this run's signals, re-resolve or leave alone", and asserting it
there keeps the test free of a model server while still exercising the real function the four
run paths call.
"""

from __future__ import annotations

import logging
from typing import Optional

import pytest

from egpt.agent._run import apply_run_model
from egpt.providers import set_default_model_provider
from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.model_provider import ModelCapabilities, ModelResolveRequest, ProviderMode
from egpt.run.base import RunContext
from tests.unit.providers.test_escalation import FRONTIER, SMALL, FakeDetailedProvider, FakeModel


class StubAgent:
    """The three attributes the hook reads. A real Agent would need a model and a session."""

    def __init__(self, model=None, model_provider=None, user_pinned_model=None, tools=None) -> None:
        self.id = "agent-1"
        self.model = model
        self.model_provider = model_provider
        self.parser_model = None
        self.tools = tools
        # What `set_default_model` records before anything can overwrite `model`. None here means
        # "the caller pinned nothing", which is the ordinary case.
        self._user_pinned_model = user_pinned_model


@pytest.fixture
def run_context() -> RunContext:
    return RunContext(run_id="r-1", session_id="s-1", user_id="u-1")


@pytest.fixture
def primary() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)


@pytest.fixture
def frontier() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-b", FRONTIER, ProviderMode.FRONTIER)


class TestNoProviderConfigured:
    def test_model_is_left_alone(self, run_context):
        """The pre-Epic-3 path, byte for byte: no provider means no change."""
        original = FakeModel(id="original")
        agent = StubAgent(model=original)
        apply_run_model(agent, run_context, tools=None)
        assert agent.model is original


class TestPerRunResolution:
    def test_provider_replaces_the_model(self, run_context, primary, frontier):
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, run_context, tools=None)
        assert agent.model.id == "mode-a-model"

    def test_tool_count_is_passed_through(self, run_context, primary, frontier):
        """The signal only exists at run time, which is why the hook lives in the run path."""
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, run_context, tools=[object(), object(), object()])
        assert primary.calls[0].tool_count == 3

    def test_structured_output_signal_is_passed_through(self, run_context, primary, frontier):
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, run_context, tools=None, response_format={"type": "json_object"})
        assert primary.calls[0].requires_structured_output is True

    def test_identity_is_passed_through(self, run_context, primary, frontier):
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, run_context, tools=None)
        seen = primary.calls[0]
        assert seen.agent_id == "agent-1"
        assert seen.user_id == "u-1"
        assert seen.session_id == "s-1"

    def test_a_tool_heavy_run_escalates(self, run_context, primary, frontier):
        """The whole point of the hook: a signal only knowable per run routes the request."""
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, run_context, tools=[object()] * 50)
        assert agent.model.id == "mode-b-model"
        assert frontier.calls[0].escalation_reason == "too_many_tools"

    def test_each_run_re_resolves(self, run_context, primary, frontier):
        """A long-lived Agent must not be pinned to whatever its first run picked."""
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, run_context, tools=None)
        assert agent.model.id == "mode-a-model"
        apply_run_model(agent, run_context, tools=[object()] * 50)
        assert agent.model.id == "mode-b-model"

    def test_the_process_default_provider_is_used(self, run_context, primary, frontier):
        set_default_model_provider(EscalatingModelProvider(primary=primary, escalation_target=frontier))
        agent = StubAgent(model=FakeModel(id="stale"))
        apply_run_model(agent, run_context, tools=None)
        assert agent.model.id == "mode-a-model"

    def test_a_per_agent_provider_outranks_the_default(self, run_context, primary, frontier):
        other = FakeDetailedProvider("per-agent", SMALL, ProviderMode.LOCAL)
        set_default_model_provider(EscalatingModelProvider(primary=primary, escalation_target=frontier))
        agent = StubAgent(model=FakeModel(id="stale"), model_provider=other)
        apply_run_model(agent, run_context, tools=None)
        assert agent.model.id == "per-agent-model"


class TestFailuresAreNotFatal:
    def test_a_raising_provider_leaves_the_model_intact(self, run_context, caplog):
        """A misconfigured provider must not take down a run that would have succeeded."""

        class Exploding:
            name = "boom"

            def resolve(self, request: ModelResolveRequest):
                raise RuntimeError("provider is misconfigured")

            def capabilities(self) -> ModelCapabilities:
                return ModelCapabilities()

        caplog.set_level(logging.WARNING, logger="egpt")
        original = FakeModel(id="original")
        agent = StubAgent(model=original, model_provider=Exploding())
        apply_run_model(agent, run_context, tools=None)
        assert agent.model is original
        assert any("misconfigured" in r.getMessage() for r in caplog.records)

    def test_a_provider_returning_none_leaves_the_model_intact(self, run_context):
        class ReturnsNone:
            name = "none"

            def resolve(self, request: ModelResolveRequest) -> Optional[object]:
                return None

            def capabilities(self) -> ModelCapabilities:
                return ModelCapabilities()

        original = FakeModel(id="original")
        agent = StubAgent(model=original, model_provider=ReturnsNone())
        apply_run_model(agent, run_context, tools=None)
        assert agent.model is original


class TestTeamsReachTheHook:
    """A Team runs turns against a model, so it must be able to escalate too.

    The gap this covers is the same shape as the seam bypass EGPT-3.1 fixed at init: the hook
    was wired into ``agent/_run.py`` while ``team/_run.py`` -- a separate module with its own
    run paths -- was left out. The consequence is narrower than an egress leak (a Team still
    resolves correctly at init) but it is the wrong way round: a team run carries every
    member's output, so it is the *larger* context, and therefore the one most likely to need
    the frontier.
    """

    class StubTeam:
        """A Team as the hook sees it: the same three attributes an Agent exposes."""

        def __init__(self, model=None, model_provider=None) -> None:
            self.id = "team-1"
            self.model = model
            self.model_provider = model_provider

    def test_a_team_escalates_on_the_run_path(self, run_context, primary, frontier):
        team = self.StubTeam(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        # Enough tools to trip the threshold rule, exactly as the agent-path test does.
        apply_run_model(team, run_context, tools=[object()] * 50)
        assert team.model.id == "mode-b-model"
        assert frontier.calls[0].escalation_reason == "too_many_tools"

    def test_a_team_stays_put_when_no_rule_fires(self, run_context, primary, frontier):
        team = self.StubTeam(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(team, run_context, tools=None)
        assert team.model.id == "mode-a-model"

    def test_a_team_without_a_provider_is_untouched(self, run_context):
        original = FakeModel(id="team-original")
        team = self.StubTeam(model=original)
        apply_run_model(team, run_context, tools=None)
        assert team.model is original

    def test_every_team_run_path_calls_the_hook(self):
        """Non-vacuity for the tests above, and a guard against the next run path.

        ``team/_run.py`` has sync, async, streaming and non-streaming variants; wiring only
        some of them would leave escalation working in tests and silently dead in whichever
        path production actually takes.
        """
        import inspect

        from egpt.team import _run as team_run

        source = inspect.getsource(team_run)
        # Counts both hooks and tolerates the multi-line call form. `apply_run_model(team` no
        # longer appears literally: the calls take keyword arguments now (run_response for the
        # attribution re-stamp, a tool pre-count for the routing signal) and are wrapped, and the
        # async paths call `aapply_run_model`. Matching on the bare `(team` prefix silently
        # counted zero once that happened, which is the failure mode this guard exists to catch --
        # so match the call *names* and assert the split as well as the total.
        sync_calls = source.count("apply_run_model(\n") - source.count("aapply_run_model(\n")
        async_calls = source.count("await aapply_run_model(\n")
        binds = source.count("_determine_tools_for_model(")

        assert sync_calls + async_calls == binds, (
            "every team run path that determines tools must first re-resolve the model; "
            f"otherwise tools are bound against the pre-escalation model "
            f"(sync={sync_calls} async={async_calls} binds={binds})."
        )
        # Every async path must use the async hook: the sync one calls provider.resolve() and
        # would block the event loop on a per-tenant policy read or a gateway probe.
        assert async_calls > 0 and sync_calls > 0, "expected both sync and async team paths to be wired"


# --------------------------------------------------------------------------- #
# Review round (PR #49): regressions for nandha-saturam's findings
# --------------------------------------------------------------------------- #


class TestUserPinnedModelSurvives:
    def test_an_explicit_agent_model_is_offered_to_the_provider_as_requested(self, run_context):
        """The frozen contract defines `requested` as "an explicit Agent(model=...)" and says a
        provider receiving one MUST return it unchanged. The hook passed `requested=None`
        unconditionally, so a configured default policy silently replaced a model the user had
        named -- the provider was never even told a pin existed."""
        pinned = FakeModel(id="user-pinned")
        provider = FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)
        agent = StubAgent(model=pinned, model_provider=provider, user_pinned_model=pinned)

        apply_run_model(agent, run_context, tools=None)

        assert provider.calls, "the provider was never consulted"
        assert provider.calls[0].requested is pinned, "the user's pin must reach the provider"

    def test_a_provider_assigned_model_is_not_reported_as_a_pin(self, run_context):
        """The other half: when the caller pinned nothing, `agent.model` is whatever the last
        resolution produced, and passing *that* as `requested` would re-pin every subsequent run
        to the model the first run happened to pick."""
        provider = FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)
        agent = StubAgent(model=FakeModel(id="resolved-earlier"), model_provider=provider)

        apply_run_model(agent, run_context, tools=None)

        assert provider.calls, "the provider was never consulted"
        assert provider.calls[0].requested is None


class TestRoutingSignalsAreSupplied:
    def test_images_populate_requires_vision(self, run_context):
        """`vision_unsupported` could never fire: `requires_vision` was left at its default, so
        an image request against a text-only primary stayed on the primary and failed at the
        model instead of routing to a vision-capable tier."""
        provider = FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)
        agent = StubAgent(model=FakeModel(id="small"), model_provider=provider)

        apply_run_model(agent, run_context, tools=None, images=["<an image>"])

        assert provider.calls, "the provider was never consulted"
        assert provider.calls[0].requires_vision is True

    def test_no_images_leaves_requires_vision_false(self, run_context):
        provider = FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)
        agent = StubAgent(model=FakeModel(id="small"), model_provider=provider)

        apply_run_model(agent, run_context, tools=None, images=None)

        assert provider.calls, "the provider was never consulted"
        assert provider.calls[0].requires_vision is False

    def test_estimated_input_tokens_are_passed_through(self, run_context):
        """`input_exceeds_context_window` returns None on its `tokens is None` guard, so it was
        dead for the same reason."""
        provider = FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)
        agent = StubAgent(model=FakeModel(id="small"), model_provider=provider)

        apply_run_model(agent, run_context, tools=None, estimated_input_tokens=250_000)

        assert provider.calls, "the provider was never consulted"
        assert provider.calls[0].estimated_input_tokens == 250_000

    def test_a_team_style_pre_count_reaches_tool_count(self, run_context):
        """Every Team call site passed `tools=None` because a Team's tools are assembled inside
        the binding call that must run after resolution -- so `tool_count` was always 0 and
        neither tool rule could fire for a Team at all."""
        provider = FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)
        agent = StubAgent(model=FakeModel(id="small"), model_provider=provider)

        apply_run_model(agent, run_context, tools=None, estimated_tool_count=20)

        assert provider.calls, "the provider was never consulted"
        assert provider.calls[0].tool_count == 20


class TestToolPreCount:
    def test_declared_tools_and_members_are_counted(self):
        from egpt.agent._run import estimate_component_tool_count

        class StubTeamShape:
            tools = ["a", "b", "c"]
            members = ["m1", "m2"]

        # Three declared tools plus ONE delegation tool for the whole member list -- the binding
        # builds a single `delegate_task_func` from the resolved members, not one per member.
        # This assertion said 5 while that was still counted per-member; see
        # TestMemberTermDoesNotOverCount for why over-counting is the unsafe direction.
        assert estimate_component_tool_count(StubTeamShape()) == 4

    def test_a_component_with_neither_counts_zero(self):
        from egpt.agent._run import estimate_component_tool_count

        class Bare:
            tools = None
            members = None

        assert estimate_component_tool_count(Bare()) == 0

    def test_a_callable_tools_attribute_is_not_invoked(self):
        """Resolving a callable `tools` here would duplicate work the binding does properly
        moments later, so the pre-count must skip rather than call it."""
        from egpt.agent._run import estimate_component_tool_count

        called = {"n": 0}

        def tools_factory():
            called["n"] += 1
            return ["x"]

        class Lazy:
            tools = tools_factory
            members = ["m1"]

        assert estimate_component_tool_count(Lazy()) == 1  # the member only
        assert called["n"] == 0


class TestModelAttributionIsRestamped:
    def test_run_response_names_the_model_that_actually_served_the_run(self, run_context, primary, frontier):
        """`run_response.model`/`model_provider` are stamped before the run body is entered, so an
        escalating run reported the pre-escalation model: cost dashboards under-counted frontier
        usage and the audit trail claimed an out-of-VPC request was served in-VPC."""

        class StubRunOutput:
            model = "small"
            model_provider = "mode-a"

        run_response = StubRunOutput()
        agent = StubAgent(
            model=FakeModel(id="small"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )

        apply_run_model(agent, run_context, tools=None, run_response=run_response)

        assert run_response.model == agent.model.id, "the stamped id must match the served model"

    def test_no_run_response_is_not_an_error(self, run_context, primary, frontier):
        """The hook is called from paths that have no RunOutput yet; re-stamping must be optional."""
        agent = StubAgent(
            model=FakeModel(id="small"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )

        apply_run_model(agent, run_context, tools=None, run_response=None)

        assert agent.model.id == "mode-a-model"


class TestConfigErrorsAreLoud:
    def test_a_config_error_propagates(self, run_context):
        """The init path raises when a configured provider cannot answer, because "a silent
        fall-through would ship tenant prompts to api.openai.com with nothing in the log to say
        why". The per-run seam swallowed everything, so an agent holding an explicit frontier
        model plus a Mode A provider whose policy denied the tenant proceeded against the frontier
        model -- prompts leaving the VPC on exactly the request the provider existed to prevent,
        and the run returning success."""
        from egpt.providers.model_provider import ModelProviderConfigError

        class DeniedByPolicy:
            name = "mode-a"

            def resolve(self, request):
                raise ModelProviderConfigError("tenant is not permitted", field_name="tenant_id")

            def capabilities(self):
                return ModelCapabilities()

        agent = StubAgent(model=FakeModel(id="frontier-pinned"), model_provider=DeniedByPolicy())

        with pytest.raises(ModelProviderConfigError):
            apply_run_model(agent, run_context, tools=None)

    def test_a_transient_error_is_still_swallowed(self, run_context, caplog):
        """The other side of the line: a missing SDK or an SDK-level failure must not fail a run
        that would have succeeded on the already-assigned model."""

        class MissingSdk:
            name = "boom"

            def resolve(self, request):
                raise ImportError("no such package")

            def capabilities(self):
                return ModelCapabilities()

        caplog.set_level(logging.WARNING, logger="egpt")
        original = FakeModel(id="original")
        agent = StubAgent(model=original, model_provider=MissingSdk())

        apply_run_model(agent, run_context, tools=None)

        assert agent.model is original
        assert any("no such package" in r.getMessage() for r in caplog.records)


class TestAsyncHook:
    async def test_the_async_hook_prefers_the_providers_aresolve(self, run_context):
        """`apply_run_model` is sync and calls `provider.resolve()`, yet it was wired into `_arun`,
        `_arun_stream` and ten async Team paths -- blocking the event loop on a per-tenant policy
        read, and never calling `aresolve` on a provider that implements it."""
        from egpt.agent._run import aapply_run_model

        class AsyncCapable:
            name = "async-provider"

            def __init__(self):
                self.sync_calls = 0
                self.async_calls = 0

            def resolve(self, request):
                self.sync_calls += 1
                return FakeModel(id="from-sync")

            async def aresolve(self, request):
                self.async_calls += 1
                return FakeModel(id="from-async")

            def capabilities(self):
                return ModelCapabilities()

        provider = AsyncCapable()
        agent = StubAgent(model=FakeModel(id="small"), model_provider=provider)

        await aapply_run_model(agent, run_context, tools=None)

        assert provider.async_calls == 1, "the async path must reach aresolve"
        assert provider.sync_calls == 0, "the sync path must not be used from an async run"
        assert agent.model.id == "from-async"

    async def test_the_async_hook_also_raises_on_a_config_error(self, run_context):
        from egpt.agent._run import aapply_run_model
        from egpt.providers.model_provider import ModelProviderConfigError

        class Denied:
            name = "mode-a"

            def resolve(self, request):
                raise ModelProviderConfigError("denied", field_name="tenant_id")

            def capabilities(self):
                return ModelCapabilities()

        agent = StubAgent(model=FakeModel(id="x"), model_provider=Denied())

        with pytest.raises(ModelProviderConfigError):
            await aapply_run_model(agent, run_context, tools=None)


class TestDetailedResolveProbe:
    def test_a_resolve_only_provider_does_not_crash_escalation(self, frontier):
        """`BaseModelProvider` defines `resolve_detailed` as a stub that raises, so a `getattr`
        probe always found it. A provider written the way that stub's message sanctions --
        subclass the base, override only `resolve()` -- crashed with NotImplementedError."""
        from egpt.providers.model_provider import BaseModelProvider

        class ResolveOnly(BaseModelProvider):
            name = "resolve-only"

            def resolve(self, request):
                return FakeModel(id="resolve-only-model")

            def capabilities(self):
                return SMALL

        provider = EscalatingModelProvider(primary=ResolveOnly(), escalation_target=frontier)

        result = provider.resolve_detailed(ModelResolveRequest())

        assert result.model.id == "resolve-only-model"
        assert result.mode is ProviderMode.LOCAL


class TestTeamPinIsAlsoRecorded:
    def test_a_team_records_its_caller_supplied_model(self):
        """`Team(model=...)` needs the same protection as `Agent(model=...)`: without a recorded
        pin the hook reports `requested=None` and a default policy can replace it."""
        from egpt.team._init import _set_default_model
        from egpt.team.team import Team

        pinned = FakeModel(id="team-pinned")
        team = Team(members=[], model=pinned)

        _set_default_model(team)

        assert team._user_pinned_model is pinned

    def test_a_team_with_no_model_records_no_pin(self):
        from egpt.team._init import _set_default_model
        from egpt.team.team import Team

        team = Team(members=[], model=FakeModel(id="x"))
        team.model = None
        team._user_pinned_model = None

        _set_default_model(team)

        assert team._user_pinned_model is None


# --------------------------------------------------------------------------- #
# Review round 2 (PR #49): regressions for the follow-up findings
# --------------------------------------------------------------------------- #


class TestThePinLatchesOnlyAtConstruction:
    """The first fix recorded the pin in `set_default_model` behind `if ... is None`. Because
    `None` also means "the caller pinned nothing", the guard never latched for that common case:
    `initialize_agent` runs on every entrypoint, so by run 2 the record captured whatever the
    provider had just assigned -- re-pinning every later run to run 1's answer, which is the exact
    failure the seam exists to prevent. These tests call init twice with a resolution in between."""

    def test_a_pinless_agent_never_acquires_a_pin(self, primary, frontier):
        from egpt.agent._init import set_default_model
        from egpt.agent.agent import Agent

        agent = Agent(model=None)
        agent.model_provider = EscalatingModelProvider(primary=primary, escalation_target=frontier)
        run_context = RunContext(run_id="r-1", session_id="s-1")

        set_default_model(agent)  # run 1 init
        apply_run_model(agent, run_context, [object()] * 50)  # escalates
        assert agent.model.id == "mode-b-model"

        set_default_model(agent)  # run 2 init -- must not latch the escalated model
        assert agent._user_pinned_model is None, "a provider-assigned model must never become 'the user's pin'"

        primary.calls.clear()
        apply_run_model(agent, run_context, None)  # cheap run 3
        assert primary.calls[0].requested is None, "run 3 must not be re-pinned to run 1's answer"

    def test_an_explicit_model_is_recorded_at_construction(self):
        from egpt.agent.agent import Agent
        from egpt.models.base import Model

        pinned = FakeModel(id="user-pinned")
        agent = Agent(model=pinned)

        assert agent._user_pinned_model is pinned
        assert isinstance(agent._user_pinned_model, Model)

    def test_a_team_pin_also_latches_at_construction(self, primary, frontier):
        from egpt.team._init import _set_default_model
        from egpt.team.team import Team

        team = Team(members=[], model=None)
        team.model_provider = EscalatingModelProvider(primary=primary, escalation_target=frontier)
        run_context = RunContext(run_id="r-1", session_id="s-1")

        _set_default_model(team)
        apply_run_model(team, run_context, [object()] * 50)
        _set_default_model(team)

        assert team._user_pinned_model is None


class TestExplicitPolicyFlagIsReachable:
    def test_run_metadata_reaches_the_request(self, primary, frontier):
        """`explicit_policy_flag` is the documented escape hatch (`metadata={"escalate": True}`)
        and could not fire from `run()` at all, because `metadata` was never populated."""
        agent = StubAgent(
            model=FakeModel(id="small"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        run_context = RunContext(run_id="r-1", session_id="s-1", metadata={"escalate": True})

        apply_run_model(agent, run_context, tools=None)

        assert agent.model.id == "mode-b-model"
        assert frontier.calls[0].escalation_reason == "explicit_policy_flag"

    def test_absent_metadata_is_an_empty_mapping_not_none(self, primary, frontier):
        """`ModelResolveRequest.metadata` is read with `.get`, so None would raise."""
        agent = StubAgent(
            model=FakeModel(id="small"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        apply_run_model(agent, RunContext(run_id="r", session_id="s"), tools=None)

        assert primary.calls[0].metadata == {}


class TestToolEstimateCountsBuiltIns:
    def test_a_built_in_tool_alone_is_counted(self):
        """The estimate claimed to be an upper bound but omitted every built-in
        `_determine_tools_for_model` appends -- so `Team(tools=None, read_chat_history=True)`
        reported 0, `tools_unsupported` could not fire, and the binding then attached a real tool
        to a model that cannot call tools."""
        from egpt.agent._run import estimate_component_tool_count

        class TeamShape:
            tools = None
            members = []
            read_chat_history = True

        assert estimate_component_tool_count(TeamShape()) == 1

    def test_the_built_ins_accumulate(self):
        from egpt.agent._run import estimate_component_tool_count

        class TeamShape:
            tools = ["a"]
            members = ["m1"]
            read_chat_history = True
            enable_agentic_state = True
            search_past_sessions = True  # contributes 2

        assert estimate_component_tool_count(TeamShape()) == 6

    def test_a_component_with_no_built_ins_is_unchanged(self):
        from egpt.agent._run import estimate_component_tool_count

        class Bare:
            tools = ["a", "b"]
            members = None

        assert estimate_component_tool_count(Bare()) == 2


class TestRecomputeIsGatedAndSymmetric:
    def test_no_recompute_when_the_model_did_not_change(self):
        """The helper ran on every run with an `output_schema` set, provider or not, costing a
        second `get_response_format` and schema build for an identical answer."""
        from egpt.agent._run import recompute_response_format

        agent = StubAgent(model=FakeModel(id="same"))
        sentinel = {"type": "json_object"}

        out = recompute_response_format(agent, RunContext(run_id="r", session_id="s"), sentinel, model_changed=False)

        assert out is sentinel, "an unchanged model must not trigger a re-derivation"

    def test_a_none_response_format_with_a_live_schema_is_recomputed(self):
        """`get_response_format` legitimately returns None for a live schema (native
        structured-output support + `structured_outputs`), so an `is None` early return swallowed
        the None -> schema direction and left the schema unenforced after escalating."""
        from egpt.agent._run import recompute_response_format

        calls = {"n": 0}

        class SchemaAgent(StubAgent):
            pass

        agent = SchemaAgent(model=FakeModel(id="frontier"))
        run_context = RunContext(run_id="r", session_id="s")
        run_context.output_schema = None  # no schema anywhere -> still a no-op

        assert recompute_response_format(agent, run_context, None, model_changed=True) is None
        assert calls["n"] == 0

    def test_hooks_report_whether_the_model_changed(self, primary, frontier):
        """The flag is identity-based, which is what the gate needs: it answers "is the object the
        run will use different from the one the derivations were computed against". A provider
        handing back a fresh instance each call is therefore a change even when the id matches --
        the derivations do have to be redone, because the capabilities are read off the object."""
        agent = StubAgent(
            model=FakeModel(id="stale"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )
        changed = apply_run_model(agent, RunContext(run_id="r", session_id="s"), tools=None)
        assert changed is True

    def test_no_change_is_reported_when_the_provider_returns_the_assigned_model(self):
        """The case the gate exists for: a provider that hands back the very object already
        assigned must not trigger a re-derivation."""
        assigned = FakeModel(id="already-assigned")

        class ReturnsTheSameObject:
            name = "steady"

            def resolve(self, request):
                return assigned

            def capabilities(self):
                return ModelCapabilities()

        agent = StubAgent(model=assigned, model_provider=ReturnsTheSameObject())

        assert apply_run_model(agent, RunContext(run_id="r", session_id="s"), tools=None) is False


class TestFallbackConfigResolvesOnce:
    def test_one_resolve_three_distinct_instances(self, primary, frontier):
        """Three `resolve()` calls bought three copies of one answer -- and could return three
        *different* models from a non-deterministic provider, which is not what the distinctness
        argument asks for."""
        provider = EscalatingModelProvider(primary=primary, escalation_target=frontier)
        frontier.calls.clear()

        config = provider.to_fallback_config()

        assert len(frontier.calls) == 1, "the target must be resolved once, not once per list"
        instances = {id(config.on_error[0]), id(config.on_rate_limit[0]), id(config.on_context_overflow[0])}
        assert len(instances) == 3, "each list still needs its own instance"

    async def test_the_async_variant_exists_and_matches(self, primary, frontier):
        """`resolve` may read a per-tenant policy row or probe a gateway, so the sync-only
        docstring's pure-construction exemption did not hold."""
        provider = EscalatingModelProvider(primary=primary, escalation_target=frontier)
        frontier.calls.clear()

        config = await provider.ato_fallback_config()

        assert len(frontier.calls) == 1
        instances = {id(config.on_error[0]), id(config.on_rate_limit[0]), id(config.on_context_overflow[0])}
        assert len(instances) == 3


# --------------------------------------------------------------------------- #
# Review round 3 (PR #49)
# --------------------------------------------------------------------------- #


class TestDeepCopyDoesNotLaunderThePin:
    """`deep_copy` is a second construction path, and it reconstructs through `__init__` with
    `model=<the component's current model>` -- which by then is whatever a provider assigned. On
    the AgentOS path that is every request (startup calls `initialize_agent`, each request calls
    `deep_copy`), so a config-driven provider would see `requested` set on every run and return it
    unchanged, bypassing per-tenant routing for the whole deployment."""

    def test_a_copy_of_a_resolved_agent_carries_no_pin(self, primary, frontier):
        from egpt.agent.agent import Agent

        agent = Agent(model=None)
        agent.model_provider = EscalatingModelProvider(primary=primary, escalation_target=frontier)
        agent.initialize_agent()  # what EgptOS does at startup
        assert agent.model.id == "mode-a-model"

        fresh = agent.deep_copy()  # what every AgentOS request does

        assert fresh._user_pinned_model is None, "a provider-assigned model must not become the copy's pin"

    def test_a_copy_preserves_a_genuine_pin(self):
        from egpt.agent.agent import Agent

        pinned = FakeModel(id="user-pinned")
        agent = Agent(model=pinned)

        fresh = agent.deep_copy()

        assert fresh._user_pinned_model is not None
        assert fresh._user_pinned_model.id == "user-pinned"

    def test_a_team_copy_carries_no_pin_and_keeps_its_provider(self, primary, frontier):
        from egpt.team.team import Team

        team = Team(members=[], model=None)
        team.model_provider = EscalatingModelProvider(primary=primary, escalation_target=frontier)
        team.initialize_team()

        fresh = team.deep_copy()

        assert fresh._user_pinned_model is None
        # `model_provider` is a plain attribute too, and was being dropped entirely -- a copied
        # Team resolved as if no provider were configured.
        assert getattr(fresh, "model_provider", None) is not None, "the copy lost its provider"


class TestMemberTermDoesNotOverCount:
    def test_a_member_list_contributes_one_delegation_tool(self):
        """`_determine_tools_for_model` builds ONE `delegate_task_func` from the whole member list.
        Counting one per member over-counted by `len(members) - 1`, so a 10-member team cleared the
        threshold of 8 and escalated on every run for a binding that produces one tool."""
        from egpt.agent._run import estimate_component_tool_count
        from egpt.providers.escalation import DEFAULT_TOOL_THRESHOLD

        class TeamShape:
            tools = None
            members = [f"m{i}" for i in range(10)]

        estimate = estimate_component_tool_count(TeamShape())

        assert estimate == 1
        assert estimate <= DEFAULT_TOOL_THRESHOLD, "a member list alone must not trip the threshold"

    def test_the_member_information_tool_adds_one_more(self):
        from egpt.agent._run import estimate_component_tool_count

        class TeamShape:
            tools = None
            members = ["m1", "m2"]
            get_member_information_tool = True

        assert estimate_component_tool_count(TeamShape()) == 2

    def test_an_empty_member_list_contributes_nothing(self):
        from egpt.agent._run import estimate_component_tool_count

        class TeamShape:
            tools = None
            members = []

        assert estimate_component_tool_count(TeamShape()) == 0


class TestModelChangedIsNotIdentityOnly:
    def test_a_fresh_equivalent_instance_is_not_a_change(self):
        """Every in-tree provider builds a fresh `Model` per resolve, so a pure `is not` check
        reported a change on every run and the gate never saved the work it was added to save."""
        from egpt.agent._run import _stamp_resolved_model

        agent = StubAgent(model=FakeModel(id="same"))

        assert _stamp_resolved_model(agent, None, FakeModel(id="same")) is False

    def test_a_different_model_is_still_a_change(self):
        from egpt.agent._run import _stamp_resolved_model

        agent = StubAgent(model=FakeModel(id="small"))

        assert _stamp_resolved_model(agent, None, FakeModel(id="frontier")) is True


class TestTenantReachesTheProvider:
    """EGPT-3.6 unit 1. Nothing populated `ModelResolveRequest.tenant_id` before this, so
    `ProviderConfig.lookup` fell through to the default policy on every request and EGPT-3.2's
    per-tenant resolution -- its headline capability -- was inert."""

    def test_the_run_contexts_tenant_is_passed_to_the_provider(self, primary, frontier):
        agent = StubAgent(
            model=FakeModel(id="small"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )

        apply_run_model(agent, RunContext(run_id="r", session_id="s", tenant_id="acme"), tools=None)

        assert primary.calls[0].tenant_id == "acme"

    def test_an_untenanted_run_passes_none(self, primary, frontier):
        """None selects the default policy -- the behaviour of every deployment that has not
        configured tenancy."""
        agent = StubAgent(
            model=FakeModel(id="small"),
            model_provider=EscalatingModelProvider(primary=primary, escalation_target=frontier),
        )

        apply_run_model(agent, RunContext(run_id="r", session_id="s"), tools=None)

        assert primary.calls[0].tenant_id is None


class TestTokenEstimate:
    """EGPT-3.6 unit 1. EGPT-3.5 threaded `estimated_input_tokens` but nothing computed it, so
    `input_exceeds_context_window` could not fire from run() at all."""

    def test_a_string_input_is_estimated(self):
        from egpt.agent._run import estimate_request_input_tokens

        class RunInput:
            input_content = "x" * 400

        estimate = estimate_request_input_tokens(StubAgent(model=FakeModel(id="gpt-4o")), RunInput())

        assert estimate is not None and estimate > 0

    def test_a_non_string_input_is_measured_from_its_serialized_form(self):
        """Originally this returned None on the reasoning that declining beats guessing. That was
        right for routing and wrong once budgets landed: `estimated_input_tokens` is what the seam
        *reserves*, so None reserved zero and N concurrent structured-input requests from one
        tenant each passed a check none of them should have. An approximate charge that
        reconciliation corrects beats a zero charge that nothing corrects."""
        from egpt.agent._run import estimate_request_input_tokens

        class ListInput:
            input_content = ["a", "b"]

        estimate = estimate_request_input_tokens(StubAgent(model=FakeModel(id="gpt-4o")), ListInput())

        assert estimate is not None and estimate > 0

    def test_absent_input_is_unknown(self):
        from egpt.agent._run import estimate_request_input_tokens

        class NoInput:
            input_content = None

        assert estimate_request_input_tokens(StubAgent(model=FakeModel(id="gpt-4o")), NoInput()) is None

    def test_a_non_string_model_id_does_not_crash_the_run(self):
        """A routing estimate must never fail a call it is only advising on.

        `AsyncMock().id` is a *coroutine*, and `count_text_tokens` calls `.lower()`/`.startswith()`
        on whatever model id it is handed -- so passing it through raised
        `'coroutine' object has no attribute 'startswith'` from inside the run. Caught in CI by
        `tests/unit/telemetry/`, which builds its agent with an AsyncMock model; it passed locally
        because that suite was not in the set I ran before pushing.
        """
        from unittest.mock import AsyncMock, MagicMock

        from egpt.agent._run import estimate_request_input_tokens

        class RunInput:
            input_content = "a test run"

        for label, model in (("AsyncMock", AsyncMock()), ("MagicMock", MagicMock()), ("None", None)):
            estimate = estimate_request_input_tokens(StubAgent(model=model), RunInput())
            assert estimate is None or estimate >= 0, f"{label} model should degrade, not raise"
