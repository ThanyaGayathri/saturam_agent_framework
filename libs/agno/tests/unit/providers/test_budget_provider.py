"""Budget enforcement at the seam (EGPT-3.6 unit 5) -- AC1, AC2 and AC5.

These are the tests that make the acceptance criteria true rather than plausible: a tenant over
budget is *blocked before the model call*, a tenant over its rate cap is throttled, and both
denials arrive as typed errors carrying the fields a caller needs to act on.
"""

from __future__ import annotations

import pytest

from egpt.exceptions import BudgetExceededError, ModelProviderError, RateLimitExceededError
from egpt.providers.budget import BudgetEnforcer, BudgetLimits, InMemoryCounterStore
from egpt.providers.budget_provider import BudgetAwareModelProvider
from egpt.providers.escalation import EscalatingModelProvider
from egpt.providers.model_provider import ModelResolveRequest, ProviderMode
from tests.unit.providers.test_escalation import FRONTIER, SMALL, FakeDetailedProvider


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def enforcer(clock) -> BudgetEnforcer:
    return BudgetEnforcer(InMemoryCounterStore(clock=clock), clock=clock, warn_on_in_memory=False)


@pytest.fixture
def inner() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL)


def _provider(inner, enforcer, limits):
    return BudgetAwareModelProvider(inner, enforcer=enforcer, limits_for=lambda request: limits)


class TestAC1BlockedAtTheSeam:
    """AC1: "A tenant over budget is blocked or throttled at the seam (before the model call)"."""

    def test_an_exhausted_tenant_is_refused(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(BudgetExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

    def test_the_refusal_happens_before_the_inner_provider_is_consulted(self, inner, enforcer):
        """ "Before the model call" is the substance of AC1: resolving first would build a model,
        and possibly import an SDK, for a request that cannot be served."""
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(BudgetExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert inner.calls == [], "the inner provider must not be reached for a denied request"

    def test_a_tenant_with_headroom_resolves_normally(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=1_000)
        provider = _provider(inner, enforcer, limits)

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert result.model.id == "mode-a-model"

    def test_one_tenant_exhausting_its_budget_does_not_block_another(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(BudgetExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert provider.resolve_detailed(ModelResolveRequest(tenant_id="globex")).model is not None


class TestAC2RateThrottling:
    """AC2: "A tenant over its rate cap (RPM/TPM) is throttled"."""

    def test_a_tenant_over_its_request_rate_is_refused(self, inner, enforcer):
        limits = BudgetLimits(max_requests=2)
        for _ in range(2):
            enforcer.reserve("acme", limits, estimated_tokens=0)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(RateLimitExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

    def test_the_window_rolling_over_restores_service(self, inner, enforcer, clock):
        limits = BudgetLimits(max_requests=1, window="minute")
        enforcer.reserve("acme", limits, estimated_tokens=0)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(RateLimitExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        clock.advance(61)

        assert provider.resolve_detailed(ModelResolveRequest(tenant_id="acme")).model is not None


class TestAC5TypedDenials:
    """AC5: "Denials return a clear, typed error"."""

    def test_a_budget_denial_carries_the_numbers_a_caller_needs(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=100, window="hour")
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(BudgetExceededError) as exc:
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        error = exc.value
        assert error.tenant_id == "acme"
        assert error.limit == 100
        assert error.used == 100
        assert error.window == "hour"
        assert error.retry_after == 3_600
        assert error.status_code == 429
        assert "acme" in str(error)

    def test_a_rate_denial_is_a_distinct_type(self, inner, enforcer):
        """Distinct because the two need different handling: a rate limit is a back-off, a spend
        ceiling usually needs a plan change."""
        limits = BudgetLimits(max_requests=1)
        enforcer.reserve("acme", limits, estimated_tokens=0)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(RateLimitExceededError) as exc:
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert not isinstance(exc.value, BudgetExceededError)

    @pytest.mark.parametrize(
        "limits, error_type",
        [
            (BudgetLimits(max_tokens=1), BudgetExceededError),
            (BudgetLimits(max_requests=1), RateLimitExceededError),
        ],
    )
    def test_neither_denial_is_a_model_provider_error(self, inner, enforcer, limits, error_type):
        """The property both types exist to guarantee, asserted where it is actually raised.

        `_is_retryable_error` treats 429 as retryable, so a denial raised as a ModelProviderError
        would be re-attempted for a condition that cannot succeed; and `fallback.py` catches
        ModelProviderError and would answer "out of budget" by calling a costlier model.
        """
        enforcer.reserve("acme", limits, estimated_tokens=5)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(error_type) as exc:
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert not isinstance(exc.value, ModelProviderError)


class TestTheNoBudgetPathIsUntouched:
    def test_no_limits_means_straight_through(self, inner, enforcer):
        provider = BudgetAwareModelProvider(inner, enforcer=enforcer, limits_for=lambda r: None)

        assert provider.resolve_detailed(ModelResolveRequest(tenant_id="acme")).model is not None

    def test_an_untenanted_request_is_never_budgeted(self, inner, enforcer):
        """None means "no tenant", which is every deployment that has not configured tenancy."""
        limits = BudgetLimits(max_tokens=1)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = _provider(inner, enforcer, limits)

        assert provider.resolve_detailed(ModelResolveRequest(tenant_id=None)).model is not None


class TestCompositionWithEscalation:
    """Budget wraps escalation, never the reverse: you cannot price the escalation target until
    escalation has chosen it."""

    @pytest.fixture
    def escalating(self):
        return EscalatingModelProvider(
            primary=FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL),
            escalation_target=FakeDetailedProvider("mode-b", FRONTIER, ProviderMode.FRONTIER),
        )

    def test_a_threshold_escalation_is_downgraded_when_headroom_is_thin(self, escalating, enforcer):
        """A threshold is a quality preference, so budget pressure may overrule it."""
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=900)  # 100 left, under the quarter mark
        provider = _provider(escalating, enforcer, limits)

        result = provider.resolve_detailed(
            ModelResolveRequest(tenant_id="acme", tool_count=50)  # would fire too_many_tools
        )

        assert result.model.id == "mode-a-model", "a thin budget should keep the cheap tier"

    def test_a_capability_escalation_is_not_downgraded(self, escalating, enforcer):
        """The load-bearing case. Downgrading a vision request onto a text-only model produces a
        silent wrong answer or an unsupported-content error; that decision does not belong to the
        budget layer."""
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=900)
        provider = _provider(escalating, enforcer, limits)

        result = provider.resolve_detailed(
            ModelResolveRequest(tenant_id="acme", requires_vision=True)  # SMALL has no vision
        )

        assert result.model.id == "mode-b-model", "a capability gap must not be downgraded away"

    def test_ample_headroom_leaves_an_escalation_alone(self, escalating, enforcer):
        limits = BudgetLimits(max_tokens=10_000)
        provider = _provider(escalating, enforcer, limits)

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", tool_count=50))

        assert result.model.id == "mode-b-model"

    def test_capabilities_are_the_inner_stacks_unchanged(self, escalating, enforcer):
        """A budget constrains how much, never what -- so reporting anything of its own would
        misdescribe the stack to the escalation rules that read it."""
        provider = _provider(escalating, enforcer, BudgetLimits(max_tokens=1_000))

        assert provider.capabilities() == escalating.capabilities()


class TestHeadroomIsToldToTheInnerProvider:
    def test_remaining_tokens_reach_the_inner_request(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=250)
        provider = _provider(inner, enforcer, limits)

        provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert inner.calls[0].budget_remaining_tokens == 750


class TestAsync:
    async def test_the_async_path_enforces_the_same_way(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=100)
        provider = _provider(inner, enforcer, limits)

        with pytest.raises(BudgetExceededError):
            await provider.aresolve_detailed(ModelResolveRequest(tenant_id="acme"))

    async def test_the_async_path_resolves_when_in_budget(self, inner, enforcer):
        provider = _provider(inner, enforcer, BudgetLimits(max_tokens=1_000))

        result = await provider.aresolve_detailed(ModelResolveRequest(tenant_id="acme"))

        assert result.model.id == "mode-a-model"

    async def test_aresolve_is_overridden_not_inherited(self):
        """EGPT-3.3's convention, whose docstring records that leaving `aresolve_detailed`
        inherited was a bug: the inherited version calls the sync path, so an inner provider's own
        `aresolve` is never reached and a policy read runs on the event loop."""
        assert "aresolve_detailed" in vars(BudgetAwareModelProvider)
        assert "aresolve" in vars(BudgetAwareModelProvider)


class TestTheProviderIsReachableFromSettings:
    """The gap that made AC1/AC2 untrue in a real deployment.

    `BudgetAwareModelProvider` was constructible, correct, and **never wired**: not exported, never
    built from settings, and nothing read `EGPT_BUDGET_*`. Enforcement passed its own tests while
    no actual request was ever budgeted -- the same inert-capability shape as the finding that
    reframed this ticket. These tests pin the wiring, not the mechanism.
    """

    def test_settings_produce_an_enforcing_provider(self, monkeypatch):
        monkeypatch.setenv("EGPT_BUDGET_MAX_TOKENS", "100")
        monkeypatch.setenv("EGPT_DEFAULT_MODE", "frontier")

        provider = BudgetAwareModelProvider.from_settings()

        assert provider is not None
        limits = provider.limits_for(ModelResolveRequest(tenant_id="acme"))
        assert limits is not None and limits.max_tokens == 100

        provider.enforcer.reserve("acme", limits, estimated_tokens=100)
        with pytest.raises(BudgetExceededError):
            provider.resolve_detailed(ModelResolveRequest(tenant_id="acme"))

    def test_no_budget_configured_yields_no_wrapper_at_all(self, monkeypatch):
        """`None` rather than a pass-through wrapper: a caller installs what it gets back, so the
        no-budget path adds no layer, no indirection and no cost."""
        for var in ("EGPT_BUDGET_MAX_TOKENS", "EGPT_BUDGET_MAX_REQUESTS", "EGPT_POLICY_FILE"):
            monkeypatch.delenv(var, raising=False)

        assert BudgetAwareModelProvider.from_settings() is None

    def test_a_tenant_only_budget_still_builds_the_stack(self, monkeypatch, tmp_path):
        """Checking only the deployment default would leave a per-tenant-only configuration
        unenforced -- which is the shape AC4 is most likely to be used in."""
        import json

        policy = tmp_path / "policy.json"
        policy.write_text(
            json.dumps(
                {
                    "policies": [
                        {"tenant_id": "acme", "mode": "frontier", "model_id": "gpt-5.5", "budget": {"max_tokens": 500}}
                    ]
                }
            )
        )
        monkeypatch.delenv("EGPT_BUDGET_MAX_TOKENS", raising=False)
        monkeypatch.setenv("EGPT_POLICY_FILE", str(policy))

        provider = BudgetAwareModelProvider.from_settings()

        assert provider is not None, "a per-tenant budget with no deployment default must still enforce"
        limits = provider.limits_for(ModelResolveRequest(tenant_id="acme"))
        assert limits is not None and limits.max_tokens == 500

    def test_it_is_exported_from_the_package(self):
        """Not exported meant a deployment could not reach it without a private import path."""
        import egpt.providers as providers

        assert "BudgetAwareModelProvider" in providers.__all__
        from egpt.providers import BudgetAwareModelProvider as Exported  # noqa: F401


class TestPerAgentAttribution:
    """AC3 says "per tenant **(and per agent)**". The agent axis did not exist."""

    def test_usage_is_attributable_to_an_agent_within_a_tenant(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=10_000)
        provider = _provider(inner, enforcer, limits)

        provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", agent_id="agent-a", estimated_input_tokens=100))
        provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", agent_id="agent-b", estimated_input_tokens=250))

        assert enforcer.usage("acme", limits, agent_id="agent-a")["tokens"] == 100
        assert enforcer.usage("acme", limits, agent_id="agent-b")["tokens"] == 250

    def test_the_tenant_total_still_covers_every_agent(self, inner, enforcer):
        """Per-agent counters are attribution, not a second ceiling: the limit stays the
        tenant's, so the tenant-wide counter must still see both agents' spend."""
        limits = BudgetLimits(max_tokens=10_000)
        provider = _provider(inner, enforcer, limits)

        provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", agent_id="agent-a", estimated_input_tokens=100))
        provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", agent_id="agent-b", estimated_input_tokens=250))

        assert enforcer.usage("acme", limits)["tokens"] == 350


class TestStructuredInputIsReserved:
    def test_a_list_input_does_not_reserve_zero(self, inner, enforcer):
        """`estimated_input_tokens` was None for list/dict input -- the common API shape -- so the
        seam reserved 0 and N concurrent structured-input requests each passed a check none of
        them should have."""
        from egpt.agent._run import estimate_request_input_tokens

        class Agent:
            class model:
                id = "gpt-4o"

        class RunInput:
            input_content = [{"role": "user", "content": "hello there"}]

        estimate = estimate_request_input_tokens(Agent(), RunInput())

        assert estimate is not None and estimate > 0


class TestSeamToChokepointEndToEnd:
    """Round 2 item C: the seven round-1 fixes shipped with zero regression tests.

    Every one of those bugs existed *because* a test proved a mechanism in isolation and assumed the
    wiring. These drive seam -> chokepoint and assert the counter equals **actual**, not estimate --
    which is the test that would have caught the original `Model` has-no-`metrics` bug.
    """

    def _settle(self, model, *, input_tokens, output_tokens):
        """Settle one turn through the **real chokepoint**, exactly as the tool loop does.

        Deliberately not a direct `reconcile_reservation` call. An earlier version of this helper
        did that, and it hid the round-4 blocker: reconciliation's contract depends on *what
        `models/base.py` hands it*, and calling the function directly lets the test choose. Driving
        `_populate_assistant_message` means the test cannot invent a contract the framework does
        not honour -- notably, a fresh `MessageMetrics` per turn rather than a cumulative one.
        """
        from egpt.models.message import Message
        from egpt.models.response import ModelResponse

        # A fresh Message per turn, with a fresh MessageMetrics -- this is what the `while True:`
        # tool loop in `models/base.py` builds on every iteration.
        assistant_message = Message(role="assistant")
        model._ensure_message_metrics_initialized(assistant_message)
        assistant_message.metrics.input_tokens = input_tokens
        assistant_message.metrics.output_tokens = output_tokens

        model._populate_assistant_message(
            assistant_message=assistant_message,
            provider_response=ModelResponse(role="assistant", content="done"),
        )
        return assistant_message.metrics

    def test_every_turn_of_a_multi_turn_run_is_counted(self, inner, enforcer):
        """The round-2/3/4 blocker, pinned against the contract production actually delivers.

        A tool-calling loop invokes the model repeatedly against ONE resolution, and each turn
        reports **only its own** usage in a **fresh** `MessageMetrics` -- the cumulative totals go to
        `run_response.metrics`, which reconciliation never sees. Two successive fixes assumed
        otherwise (first popping the entry, then charging a delta against a supposed running total)
        and both left a tool-using agent counted at roughly 1/N.

        This drives the real chokepoint, so it cannot pass under either wrong contract.
        """
        limits = BudgetLimits(max_tokens=100_000)
        provider = _provider(inner, enforcer, limits)

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", estimated_input_tokens=100))
        assert enforcer.usage("acme", limits)["tokens"] == 100, "the estimate is charged up front"

        for _ in range(3):
            self._settle(result.model, input_tokens=500, output_tokens=200)

        assert enforcer.usage("acme", limits)["tokens"] == 2_100, (
            "three 700-token turns must sum to 2100; the estimate is netted out exactly once"
        )

    def test_the_per_agent_counter_settles_alongside_the_tenant(self, inner, enforcer):
        """AC3's per-agent attribution must track real usage, not stay at the estimate.

        `reserve` charges a per-agent counter alongside the tenant one, but `reconcile` used to
        settle only the tenant axis -- so the per-agent figure was frozen at the estimate forever,
        since nothing else ever wrote it. A three-turn run reported 2100 against the tenant and 100
        against the agent that spent it, which defeats the one question per-agent counters exist to
        answer.
        """
        limits = BudgetLimits(max_tokens=100_000)
        provider = _provider(inner, enforcer, limits)

        result = provider.resolve_detailed(
            ModelResolveRequest(tenant_id="acme", agent_id="support-bot", estimated_input_tokens=100)
        )
        for _ in range(3):
            self._settle(result.model, input_tokens=500, output_tokens=200)

        assert enforcer.usage("acme", limits)["tokens"] == 2_100
        assert enforcer.usage("acme", limits, agent_id="support-bot")["tokens"] == 2_100, (
            "the agent that spent the tokens must be attributed all of them"
        )

    def test_a_turn_whose_vendor_reported_no_usage_still_settles(self, inner, enforcer):
        """Accounting used to sit *inside* `if provider_response.response_usage is not None:`.

        Every provider populates `response_usage` under an `if response.usage is not None` guard, so
        a turn whose usage the vendor omits skipped settlement entirely and left the reservation
        standing at its estimate. Here the estimate is 500 and the real turn reports nothing: the
        reservation must settle to 0, not stay at 500.
        """
        from egpt.models.message import Message
        from egpt.models.response import ModelResponse

        limits = BudgetLimits(max_tokens=100_000)
        provider = _provider(inner, enforcer, limits)

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", estimated_input_tokens=500))
        assert enforcer.usage("acme", limits)["tokens"] == 500

        assistant_message = Message(role="assistant")
        result.model._ensure_message_metrics_initialized(assistant_message)
        result.model._populate_assistant_message(  # no response_usage on the ModelResponse
            assistant_message=assistant_message,
            provider_response=ModelResponse(role="assistant", content="done"),
        )

        assert enforcer.usage("acme", limits)["tokens"] == 0, (
            "a usage-less turn must settle its estimate, not leave it standing"
        )

    def test_turns_of_a_growing_run_sum_rather_than_converging_on_the_largest(self, inner, enforcer):
        """The delta scheme's failure shape when turns grow, which is the realistic case: context
        accumulates, so each turn reports more than the last. Charging deltas against a supposed
        cumulative total converged on the largest single turn (900) instead of the sum (1800)."""
        limits = BudgetLimits(max_tokens=100_000)
        provider = _provider(inner, enforcer, limits)

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", estimated_input_tokens=100))
        for tokens in (300, 600, 900):
            self._settle(result.model, input_tokens=tokens, output_tokens=0)

        assert enforcer.usage("acme", limits)["tokens"] == 1_800, "300 + 600 + 900"

    def test_the_counter_ends_at_actual_not_the_estimate(self, inner, enforcer):
        """F1. The estimate is 120; the real call is 1300. Before the fix the counter stayed at 120."""
        limits = BudgetLimits(max_tokens=100_000)
        provider = _provider(inner, enforcer, limits)

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", estimated_input_tokens=120))
        assert enforcer.usage("acme", limits)["tokens"] == 120, "the estimate is charged up front"

        self._settle(result.model, input_tokens=1_000, output_tokens=300)

        assert enforcer.usage("acme", limits)["tokens"] == 1_300, "settled against real usage"

    def test_the_reservation_rides_the_model_actually_served(self, enforcer):
        """F4. With a thin budget the escalation is downgraded, and the *returned* model -- not the
        discarded escalated one -- must be the one carrying the reservation."""
        escalating = EscalatingModelProvider(
            primary=FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL),
            escalation_target=FakeDetailedProvider("mode-b", FRONTIER, ProviderMode.FRONTIER),
        )
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=900)  # thin headroom
        provider = _provider(escalating, enforcer, limits)

        result = provider.resolve_detailed(
            ModelResolveRequest(tenant_id="acme", tool_count=50, estimated_input_tokens=10)
        )
        assert result.model.id == "mode-a-model", "downgraded to the cheap tier"

        before = enforcer.usage("acme", limits)["tokens"]
        self._settle(result.model, input_tokens=40, output_tokens=10)

        assert enforcer.usage("acme", limits)["tokens"] != before, (
            "the served model must carry the reservation, or its call never settles"
        )

    def test_a_broken_store_does_not_fail_the_resolve(self, inner):
        """F5/F6. Driven through `resolve_detailed`, not just `enforcer.check` -- the round-1 hole was
        that `usage()` had no guard and the *caller* had none either."""

        class BrokenStore:
            def increment(self, key, amount, ttl_seconds):
                raise ConnectionError("redis is down")

            def get(self, key):
                raise ConnectionError("redis is down")

            def decrement(self, key, amount):
                raise ConnectionError("redis is down")

        provider = BudgetAwareModelProvider(
            inner,
            enforcer=BudgetEnforcer(BrokenStore(), warn_on_in_memory=False),
            limits_for=lambda request: BudgetLimits(max_tokens=1_000),
        )

        result = provider.resolve_detailed(ModelResolveRequest(tenant_id="acme", estimated_input_tokens=50))

        assert result.model is not None, "a counter outage must not fail the request"

    async def test_the_async_path_settles_the_same_way(self, inner, enforcer):
        limits = BudgetLimits(max_tokens=100_000)
        provider = _provider(inner, enforcer, limits)

        result = await provider.aresolve_detailed(ModelResolveRequest(tenant_id="acme", estimated_input_tokens=120))
        self._settle(result.model, input_tokens=800, output_tokens=200)

        assert enforcer.usage("acme", limits)["tokens"] == 1_000
