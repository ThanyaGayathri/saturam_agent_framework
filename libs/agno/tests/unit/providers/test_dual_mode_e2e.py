"""Dual-mode composition through a live ``Agent.run()`` (EGPT-3.7) -- AC1, AC2, AC3, AC5.

Epic 3 shipped six tickets, each with its own tests: the seam (3.1), the config resolver (3.2),
Mode A (3.3), Mode B (3.4), escalation (3.5) and budget (3.6). Those tests are good and this file
does not duplicate them -- every assertion here is about **composition**, and each one is reached
by running a real agent turn.

Why that distinction is the whole point of this file. Before it, no test in
``tests/unit/providers/`` drove an agent turn at all: the only ``.run(`` calls in the package were
``asyncio.run(...)``. The units were proven pairwise and the composition was not. The one test that
claimed to prove the wiring did it by reading source as text::

    # tests/unit/agent/test_provider_seam.py:259
    run_path = Path(agent_init.__file__).parent / "_run.py"
    assert "resolve_run_model" in run_path.read_text(encoding="utf-8")

That is a grep, not an execution test. It passes on any of the four *docstring* mentions of the
name in ``_run.py``, so deleting the real call at ``_run.py:445`` and leaving a comment behind
would keep it green. These tests fail instead, because they observe which backend actually served
the turn.

**Offline by construction.** Fake models only, no SDK import, no network, no database. The model
double follows ``tests/unit/agent/test_dependencies_merge.py``, which already runs the full
``run()`` path against a canned ``ModelResponse``.

The doubles here are local rather than imported from ``test_escalation.py`` -- reusing that file's
``FakeModel`` was the plan, and it does not fit: it is a ``Model`` that raises on invocation, which
is right for tests that only read ``.id`` off a resolution and wrong for tests that must *serve a
turn*. ``RecordingModel`` exists to be invoked and to count it, which is the observation every
assertion in this file rests on.

**Cleanup is inherited.** The package ``conftest.py`` restores the discovery table, the
process-wide default provider and the resolution counter around every test, so a test here may
register a backend or install a provider without teardown of its own.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

import pytest

from egpt.agent.agent import Agent
from egpt.exceptions import BudgetExceededError, ModelProviderError
from egpt.models.base import Model
from egpt.models.message import MessageMetrics
from egpt.models.response import ModelResponse
from egpt.providers import set_default_model_provider
from egpt.providers.budget import BudgetEnforcer, BudgetLimits, InMemoryCounterStore
from egpt.providers.budget_provider import BudgetAwareModelProvider
from egpt.providers.config import ModelPolicy, ProviderConfig
from egpt.providers.config_provider import ConfigDrivenModelProvider
from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.model_provider import (
    ModelCapabilities,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)
from egpt.registry import discovery
from egpt.run.base import RunContext, RunStatus

# --------------------------------------------------------------------------- #
# Offline model double. Records that it was the backend the turn actually used,
# which is what makes "which provider handled it" (AC5) observable end to end.
# --------------------------------------------------------------------------- #


class RecordingModel(Model):
    """A ``Model`` that serves a canned response and counts its own invocations.

    Deliberately a real ``Model`` subclass rather than a stub: the run path calls
    ``response()``, metrics accumulation, and the fallback wrapper on it, so anything shallower
    would stop the turn before it reached the code under test.

    **Known duplication.** The sixteen boilerplate members below are the tenth verbatim copy of
    the offline ``Model`` double in this tree (``grep -rl 'def get_instructions_for_model'
    libs/agno/tests/`` matches ten files); only ``invocations``, ``fail_with`` and the
    identifying ``_canned`` content are genuinely new. When ``Model``'s abstract surface changes,
    all ten break independently.

    Not consolidated here, deliberately: lifting one parameterised double into a shared helper
    touches nine other files and belongs in its own change rather than riding along in a
    test-only ticket. Tracked as a follow-up -- see the PR discussion.
    """

    def __init__(self, id: str, *, fail_with: Optional[Exception] = None) -> None:
        super().__init__(id=id, name=id, provider="fake")
        self.instructions = None
        self.invocations = 0
        self._fail_with = fail_with
        # The content names the model that served the turn. `invocations` alone cannot answer
        # "which tier ran this?" wherever the run path copies the model -- `FallbackConfig`
        # deep-copies its list, so the object a test holds is not the object invoked and its
        # counter stays 0 however the turn was actually served. Content survives the copy, so
        # asserting on it is what makes AC5 ("assert which provider handled the request")
        # observable on the fallback path rather than merely asserted about.
        self._canned = ModelResponse(content=f"served-by:{id}", role="assistant", response_usage=MessageMetrics())

    # -- the offline contract, matching tests/unit/agent/test_dependencies_merge.py -- #

    def get_instructions_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_system_message_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def aget_instructions_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def aget_system_message_for_model(self, *args: Any, **kwargs: Any) -> None:
        return None

    def parse_args(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {}

    def _serve(self) -> ModelResponse:
        self.invocations += 1
        if self._fail_with is not None:
            raise self._fail_with
        return self._canned

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._serve()

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._serve()

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._serve()

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._serve()
        return

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


class _RecordingProvider:
    """A ``ModelProvider`` that hands back one fixed model and logs every request it saw.

    Satisfies the Protocol by shape, which is the property ``model_provider.py`` chose a Protocol
    for. The ``requests`` log is how a test asserts what the run path *asked* for, not merely what
    came back -- the distinction that catches a hook wired to the wrong signals.
    """

    def __init__(self, name: str, model: Model, mode: ProviderMode = ProviderMode.LOCAL) -> None:
        self.name = name
        self.model = model
        self._mode = mode
        self.requests: List[ModelResolveRequest] = []

    def resolve(self, request: ModelResolveRequest) -> Model:
        self.requests.append(request)
        return self.model

    def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
        return ModelResolveResult(
            model=self.resolve(request),
            mode=self._mode,
            provider_key=self.name,
            reason="recording provider",
        )

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(modes=(self._mode,), supports_tools=True)


def _agent(**kwargs: Any) -> Agent:
    """An agent with everything off that would reach for a database or a network."""
    return Agent(telemetry=False, **kwargs)


def _distinct_tools(count: int) -> List[Any]:
    """``count`` tools with distinct names, which is the only way to give an agent N tools.

    ``[a_tool] * N`` does **not** work: ``Agent`` deduplicates by name and logs
    ``Duplicate tool name ... skipping the duplicate`` for each repeat, so the agent ends up
    exposing exactly one callable. Escalation still fired, because ``_run_model_request``
    counts the raw pre-dedup list (``_run.py:169``) -- meaning the earlier version of these
    tests proved that a *miscounted duplicate list* escalates, and never exercised the real
    AC2 scenario of an agent with more distinct tools than ``DEFAULT_TOOL_THRESHOLD`` (8).

    That also made the tests a trap: correcting the production count to use deduplicated tools
    would have turned them red while escalation kept working, leaving AC2 with no coverage.
    """

    def _make(index: int) -> Any:
        def tool(x: str) -> str:
            return x

        tool.__name__ = f"tool_{index}"
        tool.__doc__ = f"Distinct tool {index}."
        return tool

    return [_make(i) for i in range(count)]


# --------------------------------------------------------------------------- #
# AC1 -- the headline. A↔B by config alone, through a live turn.
# --------------------------------------------------------------------------- #


class TestAC1ModeSwitchByConfigOnly:
    """AC1: "the A↔B switch is driven by config only".

    `test_config_provider.py` already proves this at the resolver -- `policy_for()` and
    `resolve_detailed().provider_key` differ per tenant. What it cannot show is that a *run*
    honours the resolver: the switch is only real if an agent turn lands on the resolved backend.
    """

    @pytest.fixture
    def local_and_frontier(self):
        """Two registered backends, distinguishable by which object was invoked."""
        local = RecordingModel("qwen3-8b")
        frontier = RecordingModel("gpt-5.5")
        discovery.register("model", "fake-local", obj=lambda **cfg: local)
        discovery.register("model", "fake-frontier", obj=lambda **cfg: frontier)
        return local, frontier

    @pytest.fixture
    def two_tenant_config(self) -> ProviderConfig:
        """One config document. 'acme' is data-resident; everyone else gets the frontier tier."""
        return ProviderConfig(
            default=ModelPolicy(
                mode=ProviderMode.FRONTIER,
                model_id="gpt-5.5",
                provider_key="fake-frontier",
                reason="default tier",
            ),
            policies={
                ("acme",): ModelPolicy(
                    mode=ProviderMode.LOCAL,
                    model_id="qwen3-8b",
                    provider_key="fake-local",
                    reason="acme is data-resident",
                ),
            },
        )

    def test_one_agent_two_tenants_two_backends(self, local_and_frontier, two_tenant_config):
        """The ticket's headline, and the gap this file exists to close.

        **One** agent object, **one** code path, two turns that differ only by the tenant on the
        run context -- and each turn is served by a different backend. Nothing about the agent is
        reconfigured between the two runs.
        """
        local, frontier = local_and_frontier
        set_default_model_provider(ConfigDrivenModelProvider(two_tenant_config))
        agent = _agent()

        agent.run("hello", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme"))
        assert (local.invocations, frontier.invocations) == (1, 0), "acme must be served locally"

        agent.run("hello", run_context=RunContext(run_id="r-2", session_id="s-2", tenant_id="globex"))
        assert (local.invocations, frontier.invocations) == (1, 1), "globex must reach the frontier"

    def test_a_residency_pinned_tenant_never_resolves_the_out_of_region_tier(
        self, local_and_frontier, two_tenant_config
    ):
        """The residency guarantee #173 exists to make true, asserted end to end.

        With `acme -> local` and a frontier default, *both* of acme's resolves must select the
        local tier. Before #173 the first one ran under the default policy and instantiated the
        frontier backend, which for a real provider means constructing and authenticating an
        out-of-region client for a tenant contractually pinned to in-VPC inference.

        Asserted on the frontier's invocation count rather than on the request log, because the
        client being *built* is the thing a residency policy precludes -- not merely the request
        being routed.
        """
        local, frontier = local_and_frontier
        set_default_model_provider(ConfigDrivenModelProvider(two_tenant_config))

        _agent().run("hello", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme"))

        assert local.invocations == 1, "the residency-pinned tenant must be served locally"
        assert frontier.invocations == 0, (
            "and the out-of-region tier must never be reached -- before #173 the initialisation "
            "resolve picked it under the default policy"
        )

    async def test_the_async_run_path_switches_the_same_way(self, local_and_frontier, two_tenant_config):
        """`arun` is the path the HTTP routers take, so proving `run` alone would leave the
        deployed path unproven. Same assertion, other half of the sync/async pair."""
        local, frontier = local_and_frontier
        set_default_model_provider(ConfigDrivenModelProvider(two_tenant_config))
        agent = _agent()

        # Stepwise, mirroring the sync sibling. Asserting only the final `(1, 1)` is equally
        # satisfied by *fully inverted* routing -- acme to the frontier, globex to the local
        # backend -- which is the data-residency violation this pair exists to catch: the
        # data-resident tenant's prompts leaving the VPC. On `arun`, the path the HTTP routers
        # take, that inversion would have shipped green.
        await agent.arun("hello", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme"))
        assert (local.invocations, frontier.invocations) == (1, 0), "acme is pinned to the local tier"

        await agent.arun("hello", run_context=RunContext(run_id="r-2", session_id="s-2", tenant_id="globex"))
        assert (local.invocations, frontier.invocations) == (1, 1), "globex takes the frontier tier"

    def test_an_untenanted_run_takes_the_default_policy(self, local_and_frontier, two_tenant_config):
        """No tenant means the default tier, which is every deployment that has not configured
        tenancy. Pinned because "no tenant" silently matching a named policy would be a
        cross-tenant routing bug, not a defaulting nicety."""
        local, frontier = local_and_frontier
        set_default_model_provider(ConfigDrivenModelProvider(two_tenant_config))

        _agent().run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert (local.invocations, frontier.invocations) == (0, 1)


# --------------------------------------------------------------------------- #
# AC2 -- escalation, observed through a turn rather than through the hook.
# --------------------------------------------------------------------------- #


class TestAC2EscalationThroughALiveRun:
    """AC2: an eligible run reaches the frontier tier; a non-eligible run stays on the SLM.

    `test_escalation.py` and `test_run_model_hook.py` already cover the rules and the hook. What
    they call directly is `apply_run_model`; this drives the same decision through `run()`, so a
    hook that stopped being called would fail here and pass there.
    """

    def test_a_tool_heavy_run_lands_on_the_frontier_model(self):
        """The signal only exists at run time, which is why the seam is re-entered per run."""
        slm, frontier = RecordingModel("slm"), RecordingModel("frontier")
        set_default_model_provider(
            EscalatingModelProvider(
                primary=_RecordingProvider("mode-a", slm, ProviderMode.LOCAL),
                escalation_target=_RecordingProvider("mode-b", frontier, ProviderMode.FRONTIER),
            )
        )

        _agent(tools=_distinct_tools(9)).run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert frontier.invocations == 1, "a tool-heavy run must be served by the frontier tier"
        assert slm.invocations == 0

    def test_a_plain_run_stays_on_the_small_model(self):
        """The other half, and the one that matters for cost: nothing about a plain turn should
        reach the expensive tier. A rule that escalated everything would pass the test above."""
        slm, frontier = RecordingModel("slm"), RecordingModel("frontier")
        set_default_model_provider(
            EscalatingModelProvider(
                primary=_RecordingProvider("mode-a", slm, ProviderMode.LOCAL),
                escalation_target=_RecordingProvider("mode-b", frontier, ProviderMode.FRONTIER),
            )
        )

        _agent().run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert slm.invocations == 1, "an ordinary run must stay on the small model"
        assert frontier.invocations == 0

    def test_a_long_lived_agent_re_decides_every_run(self):
        """One agent, two turns, opposite outcomes. `set_default_model` resolves once per Agent
        *object*, so without per-run re-entry the second turn would inherit the first turn's
        model and escalation would be unreachable after the first call.

        **One** agent, reused. Two separately-constructed agents each resolve on their own first
        run and run once, so they exercise nothing this test's name promises -- and leave the
        hazard `apply_run_model` documents (`_run.py:417-426`: the resolved model written back
        onto `component.model`, outliving the run) untested. A regression that skipped re-entry
        for an already-resolved agent -- stranding a reused production agent on whichever tier
        its first turn happened to pick -- passed the earlier version of this test.
        """
        slm, frontier = RecordingModel("slm"), RecordingModel("frontier")
        set_default_model_provider(
            EscalatingModelProvider(
                primary=_RecordingProvider("mode-a", slm, ProviderMode.LOCAL),
                escalation_target=_RecordingProvider("mode-b", frontier, ProviderMode.FRONTIER),
            )
        )

        agent = _agent()

        agent.run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))
        assert (slm.invocations, frontier.invocations) == (1, 0), "a plain first turn stays local"

        # Same object, now tool-heavy. The second turn must re-decide rather than inherit.
        agent.tools = _distinct_tools(9)
        agent.run("hello", run_context=RunContext(run_id="r-2", session_id="s-2"))

        assert (slm.invocations, frontier.invocations) == (1, 1), (
            "the same agent object must re-enter the seam on its second turn; inheriting the "
            "first turn's model would leave this at (2, 0)"
        )

    async def test_escalation_also_fires_on_the_async_run_path(self):
        """``aapply_run_model`` is a separate implementation, not a wrapper, so escalation has to
        be proven on it independently.

        ``_run.py:460-499`` carries its own copy of the
        ``except ModelProviderConfigError: raise / except Exception: log_warning`` block and
        routes through ``aresolve_run_model``/``aresolve_via`` rather than the sync resolver. A
        change that made the async branch return early, or left ``aresolve_via`` preferring
        ``resolve()`` over a provider's ``aresolve()``, would silently disable escalation for
        every request served through the HTTP routers -- which is the deployed path -- while the
        sync tests above stayed green.
        """
        slm, frontier = RecordingModel("slm"), RecordingModel("frontier")
        set_default_model_provider(
            EscalatingModelProvider(
                primary=_RecordingProvider("mode-a", slm, ProviderMode.LOCAL),
                escalation_target=_RecordingProvider("mode-b", frontier, ProviderMode.FRONTIER),
            )
        )

        agent = _agent()
        await agent.arun("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))
        assert (slm.invocations, frontier.invocations) == (1, 0), "a plain async turn stays local"

        agent.tools = _distinct_tools(9)
        await agent.arun("hello", run_context=RunContext(run_id="r-2", session_id="s-2"))
        assert (slm.invocations, frontier.invocations) == (1, 1), (
            "a tool-heavy async turn must escalate; the async seam has its own resolver and its "
            "own exception handling, so the sync proof does not carry over"
        )


# --------------------------------------------------------------------------- #
# AC3 -- fallback. The half no existing test reaches.
# --------------------------------------------------------------------------- #


class TestAC3FallbackFromAProviderResolvedModel:
    """AC3: a failure on the resolved model is served by the fallback tier, and it is reported.

    Split across two files before this one, and never joined: `test_escalation.py:490` composes
    `to_fallback_config()` without inducing a failure, and `test_fallback.py:181` induces a
    failure on a hand-built model that no provider resolved. This drives both halves at once --
    the model under failure is the one the *seam* chose.

    **On "logged event".** The ticket's wording is satisfied here by `FallbackConfig.callback`,
    which is what actually fires on fallback today. A structured `FallbackEvent` does not exist:
    `providers/events.py` has only `EscalationEvent`, hardcoding `"event": "model_escalation"`,
    and it is emitted only from the up-front escalation path. Adding one is a production change
    and does not belong in a test-only ticket -- see the PR description.
    """

    def test_a_retryable_failure_is_served_by_the_frontier_tier(self):
        """The composition: the seam resolves the SLM, the SLM 5xxs, and the escalation target
        serves the turn -- which is escalation's answer to a failure that has already happened."""
        failing_slm = RecordingModel("slm", fail_with=ModelProviderError("upstream is down", status_code=503))
        frontier = RecordingModel("frontier")
        escalating = EscalatingModelProvider(
            primary=_RecordingProvider("mode-a", failing_slm, ProviderMode.LOCAL),
            escalation_target=_RecordingProvider("mode-b", frontier, ProviderMode.FRONTIER),
        )
        set_default_model_provider(escalating)

        fired: List[tuple] = []
        fallback = escalating.to_fallback_config()
        fallback.callback = lambda primary, fb, error: fired.append((primary, fb, type(error).__name__))

        response = _agent(fallback_config=fallback).run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert failing_slm.invocations >= 1, "the resolved model must have been tried first"

        # The load-bearing assertion, and the one this test lacked: that the *frontier tier
        # served the turn*. `frontier.invocations` cannot answer it -- `FallbackConfig`
        # deep-copies its model list, so this object is never the one invoked and its counter
        # reads 0 no matter what happened. Without a content check, the three assertions below
        # are equally satisfied by the fallback list being built from the wrong resolution, or
        # by the callback reporting correctly while something else served the request -- which
        # is the exact class of bug this file exists to catch.
        assert response.content == "served-by:frontier", (
            "AC3 requires the escalation target to serve the turn, not merely to be listed as "
            f"the fallback. Served by: {response.content!r}"
        )

        assert fired, "FallbackConfig.callback is the reporting AC3 asks for; it must fire"
        primary_id, fallback_id, error_name = fired[0]
        assert primary_id == "slm"
        assert fallback_id == "frontier"
        assert error_name == "ModelProviderError"

    def test_a_successful_run_does_not_report_a_fallback(self):
        """Non-vacuity for the callback assertion above: a healthy turn must leave it silent,
        or the test would pass for a callback that fired unconditionally."""
        slm, frontier = RecordingModel("slm"), RecordingModel("frontier")
        escalating = EscalatingModelProvider(
            primary=_RecordingProvider("mode-a", slm, ProviderMode.LOCAL),
            escalation_target=_RecordingProvider("mode-b", frontier, ProviderMode.FRONTIER),
        )
        set_default_model_provider(escalating)

        fired: List[tuple] = []
        fallback = escalating.to_fallback_config()
        fallback.callback = lambda primary, fb, error: fired.append((primary, fb, error))

        _agent(fallback_config=fallback).run("hello", run_context=RunContext(run_id="r-1", session_id="s-1"))

        assert slm.invocations == 1
        assert fired == [], "no failure, no fallback report"


# --------------------------------------------------------------------------- #
# Budget. Composition of 3.6 with the run path: refused before the backend.
# --------------------------------------------------------------------------- #


class TestBudgetDenialInsideARealRun:
    """A budget denial raised at the seam does **not** reach the caller of ``run()``.

    This is the finding this file was written to be capable of producing, and it is why the
    composition layer was worth building. ``test_budget_provider.py`` proves the enforcer refuses
    an exhausted tenant, and it does. What no test asked was whether that refusal survives the run
    path -- and it does not.

    ``apply_run_model`` (``_run.py:444-453``) re-raises ``ModelProviderConfigError`` and swallows
    every other exception, on the reasoning that a transient provider fault should not fail a run
    that would otherwise succeed. ``BudgetExceededError`` derives from ``AgnoError``, not from
    ``ModelProviderConfigError``, so it lands in the swallowing branch::

        WARNING  Per-run model resolution failed; keeping the current model:
                 Tenant 'acme' exhausted its token budget (100/100 per hour).

    The run then proceeds on the model the agent was constructed with. EGPT-3.6's AC1 -- "a tenant
    over budget is blocked or throttled at the seam (before the model call)" -- holds at the seam
    and is defeated one layer up: the tenant is refused a *resolution* and served a *call*.

    These tests pin the behaviour as it actually is, deliberately, because #69 is a test-only
    ticket and making the denial propagate is a production change with its own blast radius (which
    exceptions should be fatal to a run is a policy decision, not a bug fix). Filed separately; see
    the PR description.

    **Pinned as ``xfail(strict=True)``, not as passing assertions.** The distinction is the whole
    value of the pin. A passing assertion of broken behaviour turns the *correct* follow-up red:
    whoever makes the denial propagate sees two failing tests, reads it as "you broke something",
    and the likely outcome is a revert -- leaving exhausted tenants consuming frontier-tier tokens
    unbilled. A strict xfail is green today and fails on XPASS precisely when the behaviour
    changes, so the same author gets a signal that reads "you fixed it, now update these".
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Known defect (residue after #173): an agent with no model is now refused at the "
            "initialisation resolve, but one constructed with an explicit model skips that "
            "guard, and apply_run_model still swallows BudgetExceededError. Asserted on the run's observable outcome rather "
            "than on a raised exception, because _run.py's run-level handler converts anything "
            "escaping the seam into a run error event -- so making the denial fatal flips this "
            "to XPASS, while a pytest.raises never would. XPASS means AC1 now holds: delete "
            "the marker. Tracked as aicoe-tracker#169."
        ),
    )
    def test_the_denial_reaches_the_caller(self):
        """What EGPT-3.6 AC1 requires, asserted as the *desired* behaviour rather than the broken
        one, so this file states the invariant instead of freezing its violation.

        Asserted on **what the caller observes**, not on an exception escaping ``run()``. There
        are two swallows, not one: ``apply_run_model`` catches the denial, and ``_run.py:1694``
        catches anything that escapes it and converts it into a run error event. So the
        documented follow-up -- making the denial fatal at the seam -- produces
        ``status=RunStatus.error`` with the reason in ``content``, and ``run()`` still returns
        normally. A ``pytest.raises`` here could never XPASS, which would leave the author of
        that fix with no signal at all: the exact trap this xfail exists to avoid, one layer out.
        """
        backend = RecordingModel("slm")
        inner = _RecordingProvider("mode-a", backend, ProviderMode.LOCAL)
        limits = BudgetLimits(max_tokens=100)
        enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
        enforcer.reserve("acme", limits, estimated_tokens=100)  # already spent
        set_default_model_provider(
            BudgetAwareModelProvider(inner, enforcer=enforcer, limits_for=lambda request: limits)
        )

        out = _agent().run("hello", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme"))

        assert out.status == RunStatus.error, "an over-budget turn must not report success"
        assert "exhausted its token budget" in str(out.content), "the caller has to be told why the turn was refused"
        assert backend.invocations == 0, "an exhausted tenant's turn must not reach the backend"

    def test_an_agent_with_a_preset_model_is_still_served_over_budget(self):
        """The residue of the swallow that #173's fix does **not** close.

        Threading the tenant into the initialisation resolve (#173) means an over-budget tenant
        is now refused *there*, and `set_default_model` has no swallowing handler, so the denial
        reaches the caller -- see `test_the_denial_reaches_the_caller` above, which now passes.

        But that resolve is guarded by `if agent.model is None`. An agent constructed with an
        explicit model skips it entirely, so the only seam that runs is `apply_run_model` --
        which still swallows `BudgetExceededError` (`_run.py:449`). That agent's turn is served
        over budget, and the enforcer's refusal appears only as a log line.

        This is the remaining half of aicoe-tracker#169, and it is why that xfail stays: the
        propagation defect is real for any agent that carries its own model, which is every
        `Agent(model=...)` in the codebase.
        """
        backend = RecordingModel("preset")
        limits = BudgetLimits(max_tokens=100)
        enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        set_default_model_provider(
            BudgetAwareModelProvider(
                _RecordingProvider("mode-a", RecordingModel("slm"), ProviderMode.LOCAL),
                enforcer=enforcer,
                limits_for=lambda request: limits,
            )
        )

        out = _agent(model=backend).run(
            "hello", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme")
        )

        assert out.status == RunStatus.completed, "today the over-budget turn still completes"
        assert backend.invocations == 1, "served by the preset model, over budget -- the #169 residue"

    def test_the_enforcer_itself_refuses_the_same_tenant(self):
        """Isolates blame. The seam is correct; only the propagation is broken. Without this, the
        test above could be read as the budget failing to fire at all."""
        limits = BudgetLimits(max_tokens=100)
        enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = BudgetAwareModelProvider(
            _RecordingProvider("mode-a", RecordingModel("slm"), ProviderMode.LOCAL),
            enforcer=enforcer,
            limits_for=lambda request: limits,
        )

        with pytest.raises(BudgetExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

    def test_a_tenant_with_headroom_runs_normally(self):
        """The control. A budget that refused everyone would satisfy the test above."""
        backend = RecordingModel("slm")
        limits = BudgetLimits(max_tokens=100_000)
        set_default_model_provider(
            BudgetAwareModelProvider(
                _RecordingProvider("mode-a", backend, ProviderMode.LOCAL),
                enforcer=BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False),
                limits_for=lambda request: limits,
            )
        )

        _agent().run("hello", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme"))

        assert backend.invocations == 1

    def test_one_tenants_exhaustion_does_not_starve_anothers_resolution(self):
        """Isolation: an exhausted tenant is refused, and a tenant with headroom still resolves.

        Since #173 threaded identity into the initialisation resolve, acme's exhaustion is now
        caught *there* and propagates, so this asserts a raise rather than a swallowed denial.
        The isolation claim is unchanged and is what the two assertions carry: acme never
        reaches the inner resolver, globex does.
        """
        backend = RecordingModel("slm")
        inner = _RecordingProvider("mode-a", backend, ProviderMode.LOCAL)
        limits = BudgetLimits(max_tokens=100)
        enforcer = BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        set_default_model_provider(
            BudgetAwareModelProvider(inner, enforcer=enforcer, limits_for=lambda request: limits)
        )

        with pytest.raises(BudgetExceededError):
            _agent().run("hi", run_context=RunContext(run_id="r-1", session_id="s-1", tenant_id="acme"))

        _agent().run("hi", run_context=RunContext(run_id="r-2", session_id="s-2", tenant_id="globex"))

        # Two assertions, each load-bearing on its own axis: a bare `not in` would pass if globex
        # never resolved either, and a bare `in` would pass if acme resolved too.
        seen = [r.tenant_id for r in inner.requests]
        assert "acme" not in seen, (
            "the exhausted tenant's identity must never reach the inner resolver -- it is refused "
            "at the budget layer first"
        )
        assert "globex" in seen, (
            "and the tenant with headroom must still resolve: one tenant's exhaustion does not "
            "starve another's resolution, which is the isolation claim"
        )


# --------------------------------------------------------------------------- #
# AC5 -- which provider handled it, and the anti-grep guard.
# --------------------------------------------------------------------------- #


class TestAC5TheSeamIsActuallyOnTheRunPath:
    """AC5: "assert which provider handled the request", and the reason this file exists.

    `tests/unit/agent/test_provider_seam.py:259` asserts the *string* `resolve_run_model` appears
    in `_run.py`. That name occurs in four docstrings there, so the assertion survives the real
    call at `_run.py:445` being deleted. This asserts the observable consequence instead.
    """

    def test_the_run_path_consults_the_provider_and_serves_its_model(self):
        served = RecordingModel("provider-chose-this")
        provider = _RecordingProvider("mode-a", served, ProviderMode.LOCAL)
        set_default_model_provider(provider)

        _agent(model=RecordingModel("agent-was-built-with-this")).run(
            "hello", run_context=RunContext(run_id="r-1", session_id="s-1")
        )

        assert provider.requests, "the run path must consult the provider"
        assert served.invocations == 1, "and must serve the model the provider returned"

    def test_the_request_carries_this_runs_identity(self):
        """AC5 is about attribution, so the request has to carry who asked -- otherwise a
        per-tenant policy or a budget cannot be applied to the right party.

        Asserted on the **last** request, not the first: a provider is consulted twice per turn.
        The first resolve goes through ``_init.set_default_model`` and carries no tenant; the run
        path calls the seam again via ``apply_run_model`` with this run's identity. Reading
        ``requests[0]`` would report ``tenant_id=None`` for a correctly-threaded run, which is
        exactly the wrong conclusion.

        This test deliberately says nothing about whether the *first* resolve should be
        untenanted -- see ``test_every_resolve_during_a_tenanted_run_carries_the_tenant`` below,
        which states that as the invariant it is rather than asserting the current behaviour is
        correct.
        """
        provider = _RecordingProvider("mode-a", RecordingModel("slm"), ProviderMode.LOCAL)
        set_default_model_provider(provider)

        _agent().run(
            "hello",
            run_context=RunContext(run_id="r-1", session_id="s-1", user_id="u-1", tenant_id="acme"),
        )

        assert len(provider.requests) >= 2, "the first resolve is untenanted; the run resolves again"
        this_run = provider.requests[-1]
        assert this_run.tenant_id == "acme"
        assert this_run.user_id == "u-1"
        assert this_run.session_id == "s-1"

    def test_every_resolve_during_a_tenanted_run_carries_the_tenant(self):
        """The residency invariant, stated rather than asserted-away.

        A data-residency policy (``acme -> local``, default ``frontier``) is only honoured if
        *every* resolution performed on acme's behalf carries acme's identity. Today the first one
        does not: it runs under the default policy and instantiates the out-of-region backend
        before ``apply_run_model`` corrects the choice. For a real provider that means
        constructing and authenticating a non-resident client for a tenant contractually pinned to
        local inference.

        The earlier version of this file asserted ``requests[0].tenant_id is None`` with the
        message "no run exists at construction" (itself wrong: the resolve happens inside
        ``run()``) -- freezing the gap as expected behaviour and
        guaranteeing that whoever threads ``run_context.tenant_id`` into ``set_default_model``
        gets a red CI for the fix.
        """
        provider = _RecordingProvider("mode-a", RecordingModel("slm"), ProviderMode.LOCAL)
        set_default_model_provider(provider)

        _agent().run(
            "hello",
            run_context=RunContext(run_id="r-1", session_id="s-1", user_id="u-1", tenant_id="acme"),
        )

        assert [r.tenant_id for r in provider.requests] == ["acme"] * len(provider.requests), (
            "every resolve performed for a tenanted run must carry that tenant; the first one "
            f"does not: {[r.tenant_id for r in provider.requests]}"
        )
