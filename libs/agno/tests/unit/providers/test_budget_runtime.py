"""Reserve-then-reconcile across the seam/chokepoint boundary (EGPT-3.6 unit 6) -- AC3.

AC3 is "usage and cost are attributed per tenant (and per agent)". These tests cover the carrier
that makes it possible: the seam charges an estimate, the chokepoint settles it against real usage,
and the attribution reports tokens *and* cost -- the latter having a real source only because
aicoe-tracker#163 populates `MessageMetrics.cost`.
"""

from __future__ import annotations

import gc

import pytest

from egpt.models.metrics import MessageMetrics
from egpt.providers.budget import BudgetEnforcer, BudgetLimits, InMemoryCounterStore
from egpt.providers.budget_runtime import (
    BudgetReservation,
    reconcile_reservation,
    record_reservation,
    usage_by_tenant,
)


class FakeResolvedModel:
    """Stands in for the model a provider returns from a resolve.

    The reservation is keyed on *this* object in production -- the seam calls
    `record_reservation(result.model, ...)` and the chokepoint calls
    `reconcile_reservation(metrics, model=self)`. These tests previously passed a bare
    `MessageMetrics` instead, which landed in a metrics-keyed fallback whose only caller was this
    file: green tests pinning a path production never took. The fallback is gone and these drive the
    real API.
    """

    def __init__(self, model_id: str = "fake") -> None:
        self.id = model_id


@pytest.fixture
def enforcer() -> BudgetEnforcer:
    return BudgetEnforcer(InMemoryCounterStore(), warn_on_in_memory=False)


@pytest.fixture
def limits() -> BudgetLimits:
    return BudgetLimits(max_tokens=10_000)


class TestTheCarrierWorksWithRealMetrics:
    def test_a_reservation_survives_on_a_real_message_metrics(self, enforcer, limits):
        """`MessageMetrics` is a dataclass with `eq=True` and therefore **unhashable**, so the
        first cut of this module -- a WeakKeyDictionary -- raised TypeError and silently stored
        nothing. Reconciliation then never ran and every call was charged its estimate forever.
        This is the regression pin for that."""
        metrics = MessageMetrics()
        model = FakeResolvedModel()

        record_reservation(model, BudgetReservation("acme", 100, enforcer, limits, settled_tokens=100))

        assert usage_by_tenant(metrics, model=model)["tenant_id"] == "acme"

    def test_message_metrics_is_indeed_unhashable(self):
        """Pins the premise the fix rests on. If this ever becomes hashable the id()-keying could
        be simplified, and this test says so out loud rather than leaving the workaround
        unexplained."""
        with pytest.raises(TypeError):
            hash(MessageMetrics())


class TestReserveThenReconcile:
    def test_an_over_estimate_is_refunded_against_real_usage(self, enforcer, limits):
        enforcer.reserve("acme", limits, estimated_tokens=500)
        metrics = MessageMetrics(input_tokens=100, output_tokens=50)
        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 500, enforcer, limits, settled_tokens=500))

        reconcile_reservation(metrics, model=model)

        assert enforcer.usage("acme", limits)["tokens"] == 150

    def test_an_under_estimate_charges_the_remainder(self, enforcer, limits):
        enforcer.reserve("acme", limits, estimated_tokens=100)
        metrics = MessageMetrics(input_tokens=300, output_tokens=150)
        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 100, enforcer, limits, settled_tokens=100))

        reconcile_reservation(metrics, model=model)

        assert enforcer.usage("acme", limits)["tokens"] == 450

    def test_settling_the_same_metrics_object_twice_does_not_double_charge(self, enforcer, limits):
        """A double-settle of ONE turn must be a no-op.

        Idempotency is keyed on the identity of the metrics object, not on its totals. Comparing
        totals cannot work: two turns of a run may legitimately report the same numbers, so
        "unchanged totals" is indistinguishable from "a second turn that happened to match".
        Every turn builds a fresh `MessageMetrics`, so identity separates the two cases exactly.
        """
        enforcer.reserve("acme", limits, estimated_tokens=100)
        metrics = MessageMetrics(input_tokens=300, output_tokens=0)
        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 100, enforcer, limits))

        assert reconcile_reservation(metrics, model=model) is not None
        assert enforcer.usage("acme", limits)["tokens"] == 300, "the estimate is netted out once"
        reconcile_reservation(metrics, model=model)  # the very same object again
        assert enforcer.usage("acme", limits)["tokens"] == 300, "a repeat settle must not double-charge"

    def test_two_turns_reporting_identical_totals_both_charge(self, enforcer, limits):
        """The case that makes totals-comparison unusable as an idempotency key.

        Two turns of a run can report the same token counts. Under the delta scheme this looked
        like "nothing changed" and charged zero for the second turn; under identity keying each
        distinct metrics object charges its own usage.
        """
        enforcer.reserve("acme", limits, estimated_tokens=100)
        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 100, enforcer, limits))

        reconcile_reservation(MessageMetrics(input_tokens=300), model=model)
        reconcile_reservation(MessageMetrics(input_tokens=300), model=model)

        assert enforcer.usage("acme", limits)["tokens"] == 600, "identical-but-distinct turns both count"

    def test_reconciling_an_unreserved_call_is_a_no_op(self, enforcer, limits):
        """Every model call reaches the chokepoint; only budgeted ones have a reservation."""
        assert reconcile_reservation(MessageMetrics(input_tokens=999), model=FakeResolvedModel()) is None

    def test_a_store_failure_leaves_the_reservation_standing(self, limits):
        """Accounting must not fail a call that already succeeded. The reservation standing
        over-counts rather than under, which is the safe direction for a spend ceiling."""

        class BrokenEnforcer:
            def reconcile(self, *args, **kwargs):
                raise ConnectionError("redis is down")

        metrics = MessageMetrics(input_tokens=100)

        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 50, BrokenEnforcer(), limits, settled_tokens=50))

        assert reconcile_reservation(metrics, model=model) is not None  # returns, does not raise


class TestAC3Attribution:
    def test_usage_and_cost_are_attributed_to_the_tenant(self, enforcer, limits):
        """AC3. The cost half has a real source only because #163 populates MessageMetrics.cost;
        before that it was always None and this assertion could not have been written."""
        metrics = MessageMetrics(input_tokens=300, output_tokens=150)
        metrics.cost = 0.004
        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 100, enforcer, limits, settled_tokens=100))

        attribution = usage_by_tenant(metrics, model=model)

        assert attribution == {
            "tenant_id": "acme",
            "agent_id": None,
            "input_tokens": 300,
            "output_tokens": 150,
            "cost": 0.004,
        }

    def test_an_unbudgeted_call_attributes_nothing(self, enforcer):
        assert usage_by_tenant(MessageMetrics(input_tokens=100)) == {}


class TestNoLeak:
    def test_an_abandoned_reservation_is_collected(self, enforcer, limits):
        """A call that raises before usage lands leaves a reservation nobody settles. Without the
        finalizer that is one leaked entry per failure, growing with the error rate.

        The lifetime is tied to the **resolved model** -- the object the reservation is keyed on,
        and the one whose scope matches the run. An earlier version deleted the metrics object,
        which is no longer what keeps the entry alive."""
        from egpt.providers import budget_runtime

        model = FakeResolvedModel()
        record_reservation(model, BudgetReservation("acme", 100, enforcer, limits, settled_tokens=100))
        assert len(budget_runtime._RESERVATIONS) >= 1

        del model
        gc.collect()

        assert len(budget_runtime._RESERVATIONS) == 0, "the entry should die with the model it is keyed on"

    def test_a_reused_id_cannot_collide_with_a_stale_reservation(self, enforcer, limits):
        """`id()` values are reused after collection, which is why keying on id alone would be
        unsound and why the finalizer is load-bearing rather than tidy."""
        from egpt.providers import budget_runtime

        first = MessageMetrics()
        record_reservation(first, BudgetReservation("acme", 100, enforcer, limits, settled_tokens=100))
        del first
        gc.collect()

        second = MessageMetrics(input_tokens=10)
        assert usage_by_tenant(second) == {}, "a fresh object must not inherit a dead reservation"
        assert len(budget_runtime._RESERVATIONS) == 0


class TestDenialsAreClassifiedByMeaning:
    """`ProviderDenial` is what makes a refusal fatal to a run (aicoe-tracker#169).

    `apply_run_model` re-raises denials and swallows everything else as a transient provider
    fault. Before this marker existed that split was by concrete type -- only
    `ModelProviderConfigError` was fatal -- so a budget denial was logged as though the gateway
    had hiccuped and the turn was served anyway, on the agent's own model and against no
    tenant's counter.

    These pin the classification itself rather than one call site's behaviour: a future denial
    type is fatal by construction if it inherits the marker, and these tests fail if someone
    re-parents one of them onto a plain `AgnoError`.
    """

    def test_both_denials_are_provider_denials(self):
        from egpt.exceptions import BudgetExceededError, ProviderDenial, RateLimitExceededError

        assert issubclass(BudgetExceededError, ProviderDenial)
        assert issubclass(RateLimitExceededError, ProviderDenial)

    def test_a_denial_is_not_a_provider_fault(self):
        """The other half of the contract, and the reason the marker is not on `ModelProviderError`.

        `Model._is_retryable_error` treats a 429 as retryable and `models/fallback.py` answers a
        `ModelProviderError` by trying the next (costlier) model. A denial routed through either
        would spend more money in response to being told to stop spending.
        """
        from egpt.exceptions import (
            AgnoError,
            BudgetExceededError,
            ModelProviderError,
            ProviderDenial,
            RateLimitExceededError,
        )

        assert issubclass(ProviderDenial, AgnoError)
        assert not issubclass(ProviderDenial, ModelProviderError)
        for denial in (BudgetExceededError, RateLimitExceededError):
            assert not issubclass(denial, ModelProviderError)

    def test_the_denials_carry_a_429(self):
        """The status the OS layer maps to HTTP. A denial that surfaced as a 500 would read as a
        framework bug rather than an answer the operator configured."""
        from egpt.exceptions import BudgetExceededError, RateLimitExceededError

        assert BudgetExceededError("over budget").status_code == 429
        assert RateLimitExceededError("too fast").status_code == 429
