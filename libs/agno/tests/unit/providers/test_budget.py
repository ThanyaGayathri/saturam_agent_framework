"""The per-tenant budget limiter (EGPT-3.6 unit 3).

Driven through ``BudgetEnforcer`` and ``InMemoryCounterStore`` directly: the limiter holds no
knowledge of models or providers, so its contract is testable without either, and the seam and the
chokepoint that consume it get their own tests.

**Every time-sensitive test drives an injected clock.** No fake clock existed in this tree before
this ticket and the one time-sensitive test in the suite sleeps for real, so this file establishes
the convention -- ``clock=`` as a keyword-only constructor argument, never a global patch of
``time.time``. A global patch leaks into any concurrently-running test and makes window arithmetic
untestable in parallel; an injected clock is explicit and local.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from egpt.providers.budget import (
    WINDOW_SECONDS,
    BudgetEnforcer,
    BudgetLimits,
    CounterStore,
    InMemoryCounterStore,
)


class FakeClock:
    """A clock the test moves by hand.

    Deliberately not a ``Mock``: window arithmetic is the behaviour under test, and a fake that
    advances only when the test says so makes an off-by-one in expiry a visible assertion rather
    than a flake that shows up under load.
    """

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
def enforcer(clock: FakeClock) -> BudgetEnforcer:
    # warn_on_in_memory=False: the startup warning is asserted in its own test, and leaving it on
    # would emit it once per test for no signal.
    return BudgetEnforcer(InMemoryCounterStore(clock=clock), clock=clock, warn_on_in_memory=False)


# --------------------------------------------------------------------------- #
# Limits validation
# --------------------------------------------------------------------------- #


class TestBudgetLimits:
    def test_an_unknown_window_is_rejected_at_construction(self):
        """The window is part of the counter key, so an unsupported value would silently start a
        new counter series rather than being reported."""
        with pytest.raises(ValueError) as exc:
            BudgetLimits(max_tokens=100, window="90m")
        assert "90m" in str(exc.value)
        assert "minute" in str(exc.value), "the message should name what is supported"

    @pytest.mark.parametrize("field", ["max_tokens", "max_requests"])
    @pytest.mark.parametrize("value", [0, -1])
    def test_a_non_positive_limit_is_rejected(self, field, value):
        """Zero is expressible but is almost always a config mistake; negative is meaningless."""
        with pytest.raises(ValueError):
            BudgetLimits(**{field: value})

    def test_limits_with_no_axis_set_are_not_enabled(self):
        """The hot path reads `.enabled` before touching a store, so this must be false for a
        policy that carries a budget object constraining nothing."""
        assert BudgetLimits().enabled is False
        assert BudgetLimits(max_tokens=1).enabled is True
        assert BudgetLimits(max_requests=1).enabled is True

    def test_limits_are_frozen(self):
        """Configuration shared across concurrent requests must not be mutable -- the same class of
        bug as a shared mutable Model."""
        limits = BudgetLimits(max_tokens=100)
        with pytest.raises(Exception):
            limits.max_tokens = 200  # type: ignore[misc]

    def test_window_seconds_matches_the_table(self):
        for name, seconds in WINDOW_SECONDS.items():
            assert BudgetLimits(max_tokens=1, window=name).window_seconds == seconds


# --------------------------------------------------------------------------- #
# The no-budget path
# --------------------------------------------------------------------------- #


class TestTheNoBudgetPathIsFree:
    def test_an_untenanted_request_is_always_allowed(self, enforcer):
        """None means "no tenant", which is every deployment that has not configured tenancy."""
        assert enforcer.check(None, BudgetLimits(max_tokens=1)).allowed is True

    def test_limits_that_constrain_nothing_are_allowed(self, enforcer):
        assert enforcer.check("acme", BudgetLimits()).allowed is True

    def test_neither_case_touches_the_store(self, clock):
        """The hot-path guarantee: no store access at all when nothing is configured. A store that
        raises on any access proves it."""

        class ExplodingStore:
            def increment(self, key, amount, ttl_seconds):
                raise AssertionError("the no-budget path must not touch the store")

            def get(self, key):
                raise AssertionError("the no-budget path must not touch the store")

            def decrement(self, key, amount):
                raise AssertionError("the no-budget path must not touch the store")

        enforcer = BudgetEnforcer(ExplodingStore(), clock=clock, warn_on_in_memory=False)

        assert enforcer.check(None, BudgetLimits(max_tokens=1)).allowed is True
        assert enforcer.check("acme", BudgetLimits()).allowed is True
        enforcer.reserve(None, BudgetLimits(max_tokens=1), estimated_tokens=10)
        enforcer.reserve("acme", BudgetLimits(), estimated_tokens=10)


# --------------------------------------------------------------------------- #
# Token budget
# --------------------------------------------------------------------------- #


class TestTokenBudget:
    def test_spending_under_the_limit_is_allowed(self, enforcer):
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=400)
        assert enforcer.check("acme", limits, estimated_tokens=400).allowed is True

    def test_an_estimate_that_would_cross_the_ceiling_is_denied_before_spending(self, enforcer):
        """Denied *before* the call, not after: the point of a budget is not to report an overspend
        that already happened."""
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=900)

        decision = enforcer.check("acme", limits, estimated_tokens=200)

        assert decision.denied
        assert decision.reason == "token_budget_exceeded"
        assert decision.limit == 1_000
        assert decision.used == 900
        assert decision.retry_after == 3_600

    def test_a_tenant_at_its_ceiling_is_denied_even_for_a_zero_token_request(self, enforcer):
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=100)

        assert enforcer.check("acme", limits, estimated_tokens=0).denied

    def test_tenants_do_not_share_counters(self, enforcer):
        """The whole point of per-tenant: one tenant exhausting its budget must not affect another."""
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=100)

        assert enforcer.check("acme", limits).denied
        assert enforcer.check("globex", limits).allowed is True


class TestRequestRate:
    def test_requests_are_counted_and_capped(self, enforcer):
        limits = BudgetLimits(max_requests=3)
        for _ in range(3):
            assert enforcer.check("acme", limits).allowed is True
            enforcer.reserve("acme", limits, estimated_tokens=0)

        decision = enforcer.check("acme", limits)

        assert decision.denied
        assert decision.reason == "request_rate_exceeded"
        assert decision.used == 3

    def test_the_request_axis_is_checked_before_the_token_axis(self, enforcer):
        """Both configured and both exceeded: the reason should name the rate, which is the cheaper
        signal for a caller to act on (back off) than a spend ceiling (change plan)."""
        limits = BudgetLimits(max_tokens=10, max_requests=1)
        enforcer.reserve("acme", limits, estimated_tokens=50)

        assert enforcer.check("acme", limits).reason == "request_rate_exceeded"


# --------------------------------------------------------------------------- #
# Windows -- all driven by the injected clock
# --------------------------------------------------------------------------- #


class TestWindows:
    def test_a_window_rolls_over_and_restores_headroom(self, enforcer, clock):
        limits = BudgetLimits(max_tokens=100, window="hour")
        enforcer.reserve("acme", limits, estimated_tokens=100)
        assert enforcer.check("acme", limits).denied

        clock.advance(3_601)

        assert enforcer.check("acme", limits).allowed is True

    def test_a_window_does_not_roll_over_early(self, enforcer, clock):
        """One second before expiry the counter must still hold -- the off-by-one that would make a
        budget leak an extra window's worth of spend."""
        limits = BudgetLimits(max_tokens=100, window="hour")
        enforcer.reserve("acme", limits, estimated_tokens=100)

        clock.advance(3_599)

        assert enforcer.check("acme", limits).denied

    def test_a_window_is_a_fixed_span_from_its_first_write_not_a_sliding_one(self, enforcer, clock):
        """Fixed-window semantics, stated as a test so the burstiness tradeoff is explicit rather
        than discovered: spending late in a window and again early in the next is allowed."""
        limits = BudgetLimits(max_tokens=100, window="hour")
        enforcer.reserve("acme", limits, estimated_tokens=90)
        clock.advance(3_500)  # still the same window
        assert enforcer.check("acme", limits, estimated_tokens=20).denied

        clock.advance(200)  # window rolled over

        assert enforcer.check("acme", limits, estimated_tokens=90).allowed is True

    def test_changing_a_tenants_window_starts_a_fresh_counter(self, enforcer):
        """The window is part of the key, so an hourly total must not be read against a daily
        limit."""
        hourly = BudgetLimits(max_tokens=100, window="hour")
        daily = BudgetLimits(max_tokens=100, window="day")
        enforcer.reserve("acme", hourly, estimated_tokens=100)

        assert enforcer.check("acme", hourly).denied
        assert enforcer.check("acme", daily).allowed is True


# --------------------------------------------------------------------------- #
# Reserve / reconcile
# --------------------------------------------------------------------------- #


class TestReconciliation:
    def test_an_over_estimate_is_refunded(self, enforcer):
        """The common direction: `utils/tokens.py` falls back to len/4, so estimates run high as
        often as low, and an unrefunded over-estimate would silently shrink the budget."""
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=500)

        enforcer.reconcile("acme", limits, estimated_tokens=500, actual_tokens=200)

        assert enforcer.usage("acme", limits)["tokens"] == 200

    def test_an_under_estimate_charges_the_remainder(self, enforcer):
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=100)

        enforcer.reconcile("acme", limits, estimated_tokens=100, actual_tokens=450)

        assert enforcer.usage("acme", limits)["tokens"] == 450

    def test_an_under_estimate_can_overshoot_the_ceiling_by_one_request(self, enforcer):
        """The bounded overshoot this design accepts, asserted rather than left implicit: exactness
        would need the provider round-trip the design avoids."""
        limits = BudgetLimits(max_tokens=100)
        enforcer.reserve("acme", limits, estimated_tokens=10)
        enforcer.reconcile("acme", limits, estimated_tokens=10, actual_tokens=500)

        assert enforcer.usage("acme", limits)["tokens"] == 500
        assert enforcer.check("acme", limits).denied, "the next request is refused"

    def test_reconciliation_adjusts_by_the_delta_so_a_concurrent_charge_survives(self, enforcer):
        """Adjusting by the difference rather than rewriting the total: a second request's charge
        landing between reserve and reconcile must not be clobbered."""
        limits = BudgetLimits(max_tokens=10_000)
        enforcer.reserve("acme", limits, estimated_tokens=100)  # request A
        enforcer.reserve("acme", limits, estimated_tokens=300)  # request B, concurrent

        enforcer.reconcile("acme", limits, estimated_tokens=100, actual_tokens=150)  # A settles

        # A's 150 plus B's still-reserved 300.
        assert enforcer.usage("acme", limits)["tokens"] == 450

    def test_a_refund_cannot_drive_a_counter_negative(self, enforcer):
        """A negative counter would grant free headroom for the rest of the window."""
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=50)

        enforcer.reconcile("acme", limits, estimated_tokens=5_000, actual_tokens=0)

        assert enforcer.usage("acme", limits)["tokens"] == 0

    def test_reconciling_after_the_window_rolled_over_does_not_resurrect_it(self, enforcer, clock):
        """A long-running call whose window expires mid-flight must not write into the dead window
        -- or, worse, create it fresh with a stale value."""
        limits = BudgetLimits(max_tokens=1_000, window="hour")
        enforcer.reserve("acme", limits, estimated_tokens=500)
        clock.advance(3_601)

        enforcer.reconcile("acme", limits, estimated_tokens=500, actual_tokens=100)

        assert enforcer.usage("acme", limits)["tokens"] == 0

    def test_an_exact_estimate_writes_nothing(self, enforcer):
        limits = BudgetLimits(max_tokens=1_000)
        enforcer.reserve("acme", limits, estimated_tokens=200)

        enforcer.reconcile("acme", limits, estimated_tokens=200, actual_tokens=200)

        assert enforcer.usage("acme", limits)["tokens"] == 200


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


class TestFailOpenOnStoreErrors:
    """A counter outage is not evidence of overspend. Denying every request because Redis is
    unreachable converts a metrics problem into an outage -- but a genuine over-limit reading must
    still deny, which is why these are separate conditions rather than one try/except."""

    @pytest.fixture
    def broken(self, clock):
        class BrokenStore:
            def increment(self, key, amount, ttl_seconds):
                raise ConnectionError("redis is down")

            def get(self, key):
                raise ConnectionError("redis is down")

            def decrement(self, key, amount):
                raise ConnectionError("redis is down")

        return BudgetEnforcer(BrokenStore(), clock=clock, warn_on_in_memory=False)

    def test_a_check_against_a_broken_store_allows(self, broken):
        decision = broken.check("acme", BudgetLimits(max_tokens=1), estimated_tokens=10_000)

        assert decision.allowed is True
        assert decision.reason == "store_unavailable", "the reason must distinguish this from a real pass"

    def test_a_reservation_against_a_broken_store_does_not_raise(self, broken):
        broken.reserve("acme", BudgetLimits(max_tokens=1), estimated_tokens=10)

    def test_reconciliation_against_a_broken_store_does_not_raise(self, broken):
        broken.reconcile("acme", BudgetLimits(max_tokens=1), estimated_tokens=10, actual_tokens=20)

    def test_a_real_over_limit_still_denies(self, enforcer):
        """The other half: fail-open must not become never-deny."""
        limits = BudgetLimits(max_tokens=10)
        enforcer.reserve("acme", limits, estimated_tokens=10)

        assert enforcer.check("acme", limits).denied


class TestTheInMemoryStoreWarns:
    def test_constructing_without_a_store_warns_about_per_worker_limits(self, caplog):
        """An operator who configured a budget and runs N workers is getting N times the limit.
        The only honest place to say so is once, at startup."""
        import logging

        caplog.set_level(logging.WARNING, logger="egpt")

        BudgetEnforcer()

        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "per worker" in messages
        assert "RedisCounterStore" in messages, "the warning should name the remedy"

    def test_the_warning_can_be_suppressed_for_a_deliberate_single_worker_deployment(self, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="egpt")

        BudgetEnforcer(warn_on_in_memory=False)

        assert not [r for r in caplog.records if "per worker" in r.getMessage()]


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #


class TestAtomicity:
    def test_concurrent_increments_do_not_lose_updates(self, clock):
        """`increment` returning the post-increment total *atomically* is the store's whole
        contract. A read-then-write implementation passes every single-threaded test above and
        loses updates only under concurrency -- which is exactly the deployment shape a budget
        exists for."""
        store = InMemoryCounterStore(clock=clock)
        workers, per_worker = 16, 200

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda _: [store.increment("k", 1, 3_600) for _ in range(per_worker)], range(workers)))

        assert store.get("k") == workers * per_worker

    def test_concurrent_reservations_sum_correctly(self, clock):
        """The same property through the enforcer: N in-flight requests from one tenant must sum,
        not race to the same total."""
        enforcer = BudgetEnforcer(InMemoryCounterStore(clock=clock), clock=clock, warn_on_in_memory=False)
        limits = BudgetLimits(max_tokens=1_000_000)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: enforcer.reserve("acme", limits, estimated_tokens=10), range(400)))

        assert enforcer.usage("acme", limits)["tokens"] == 4_000


class TestStoreProtocol:
    def test_the_in_memory_store_satisfies_the_protocol(self, clock):
        """`CounterStore` is a Protocol so a deployment can substitute its own without inheritance,
        matching how `ModelProvider` is defined."""
        assert isinstance(InMemoryCounterStore(clock=clock), CounterStore)

    def test_a_duck_typed_store_satisfies_it_too(self):
        class MyStore:
            def increment(self, key, amount, ttl_seconds):
                return 0

            def get(self, key):
                return 0

            def decrement(self, key, amount):
                return 0

        assert isinstance(MyStore(), CounterStore)


class TestPruning:
    def test_expired_keys_are_eventually_dropped(self, clock):
        """Without pruning the dict grows one entry per tenant per window forever -- a leak
        proportional to tenant count, invisible until it is not."""
        store = InMemoryCounterStore(clock=clock)
        for i in range(1_100):
            store.increment(f"tenant-{i}", 1, 60)
        assert len(store._counters) >= 1_024

        clock.advance(61)
        store.increment("trigger", 1, 60)

        assert len(store._counters) < 1_100, "expired keys should have been pruned"
        assert store.get("trigger") == 1, "the live key survives"


# --------------------------------------------------------------------------- #
# Unit 4 -- limits are configurable per tenant (AC4)
# --------------------------------------------------------------------------- #


class TestBudgetInAPolicyDocument:
    """AC4: "Limits are configurable per tenant". A budget expressed in the policy document is
    what makes that true for an operator -- `BudgetLimits` being constructible in Python is not."""

    def test_a_policy_document_can_name_a_budget(self):
        from egpt.providers.config import ModelPolicy

        policy = ModelPolicy.from_dict(
            {"mode": "frontier", "model_id": "gpt-5.5", "budget": {"max_tokens": 100_000, "window": "day"}}
        )

        assert policy.budget is not None
        assert policy.budget.max_tokens == 100_000
        assert policy.budget.window == "day"

    def test_a_policy_without_a_budget_has_none(self):
        from egpt.providers.config import ModelPolicy

        assert ModelPolicy.from_dict({"mode": "frontier"}).budget is None

    def test_two_tenants_can_carry_different_budgets(self):
        """The point of AC4: per *tenant*, not per deployment."""
        from egpt.providers.config import ProviderConfig

        config = ProviderConfig.from_dict(
            {
                "policies": [
                    {"tenant_id": "acme", "mode": "frontier", "model_id": "gpt-5.5", "budget": {"max_tokens": 1_000}},
                    {"tenant_id": "globex", "mode": "frontier", "model_id": "gpt-5.5", "budget": {"max_tokens": 9_000}},
                ]
            }
        )

        acme = config.lookup(tenant_id="acme", agent_id=None, role="model")
        globex = config.lookup(tenant_id="globex", agent_id=None, role="model")

        assert acme is not None and acme.budget is not None and acme.budget.max_tokens == 1_000
        assert globex is not None and globex.budget is not None and globex.budget.max_tokens == 9_000

    @pytest.mark.parametrize(
        "block, expected_field",
        [
            ({"max_tokens": 0}, "max_tokens"),
            ({"max_requests": -1}, "max_requests"),
            ({"window": "90m"}, "window"),
            ({"max_tokns": 5}, "max_tokns"),
        ],
    )
    def test_a_malformed_budget_is_rejected_with_its_field_named(self, block, expected_field):
        """A bare ValueError with no field name is invisible to every
        `except ModelProviderConfigError` handler -- the same gap the `config` mapping check
        exists to close. A typo'd key in particular must not be silently dropped, or an operator
        believes a limit is in force that is not."""
        from egpt.providers.config import ModelPolicy
        from egpt.providers.model_provider import ModelProviderConfigError

        with pytest.raises(ModelProviderConfigError) as exc:
            ModelPolicy.from_dict({"mode": "frontier", "budget": block})

        assert exc.value.field_name == expected_field

    def test_a_non_mapping_budget_is_rejected(self):
        from egpt.providers.config import ModelPolicy
        from egpt.providers.model_provider import ModelProviderConfigError

        with pytest.raises(ModelProviderConfigError) as exc:
            ModelPolicy.from_dict({"mode": "frontier", "budget": "lots"})

        assert exc.value.field_name == "budget"


class TestDeploymentWideDefaultBudget:
    def test_settings_produce_a_budget(self, monkeypatch):
        from egpt.settings.providers import get_provider_settings

        monkeypatch.setenv("EGPT_BUDGET_MAX_TOKENS", "50000")
        monkeypatch.setenv("EGPT_BUDGET_WINDOW", "day")

        budget = get_provider_settings().default_budget

        assert budget is not None
        assert budget.max_tokens == 50_000
        assert budget.window == "day"

    def test_nothing_configured_yields_none_not_a_disabled_object(self, monkeypatch):
        """`None` rather than a `BudgetLimits` constraining nothing: the caller's `is None` check
        is the whole hot-path guard, and allocating an object per resolve for every no-budget
        deployment would defeat it."""
        from egpt.settings.providers import get_provider_settings

        for var in ("EGPT_BUDGET_MAX_TOKENS", "EGPT_BUDGET_MAX_REQUESTS"):
            monkeypatch.delenv(var, raising=False)

        assert get_provider_settings().default_budget is None


# --------------------------------------------------------------------------- #
# Unit 7 -- accurate across workers (AC6)
# --------------------------------------------------------------------------- #


class TestRedisCounterStore:
    """AC6: "Enforcement is accurate across multiple workers".

    Two `BudgetEnforcer` instances sharing one `FakeStrictRedis` is genuinely two workers against
    one Redis -- separate enforcer objects, separate in-process state, one shared counter. That is
    the property AC6 names, and it is exactly what the in-memory store cannot provide.
    """

    @pytest.fixture
    def shared_redis(self):
        fakeredis = pytest.importorskip("fakeredis", reason="RedisCounterStore tests need fakeredis")
        return fakeredis.FakeStrictRedis()

    @pytest.fixture
    def store(self, shared_redis):
        from egpt.providers.budget import RedisCounterStore

        return RedisCounterStore(client=shared_redis)

    def test_two_workers_share_one_counter(self, shared_redis):
        """The AC6 property. With the in-memory store these two enforcers would each count to 100
        and the tenant would get 200 tokens against a 150 limit."""
        from egpt.providers.budget import RedisCounterStore

        limits = BudgetLimits(max_tokens=150)
        worker_a = BudgetEnforcer(RedisCounterStore(client=shared_redis), warn_on_in_memory=False)
        worker_b = BudgetEnforcer(RedisCounterStore(client=shared_redis), warn_on_in_memory=False)

        worker_a.reserve("acme", limits, estimated_tokens=100)

        # Worker B, a different process, sees worker A's spend.
        assert worker_b.usage("acme", limits)["tokens"] == 100
        assert worker_b.check("acme", limits, estimated_tokens=100).denied, (
            "the second worker must refuse what the first already spent"
        )

    def test_a_multi_turn_run_settles_correctly_over_redis(self, shared_redis):
        """The per-turn settlement contract, over the store AC6 actually deploys on.

        The in-memory store covers this in `test_budget_provider.py`, but `RedisCounterStore` is a
        separate implementation whose `decrement` has its own existence check and clamping. Both
        halves of the reconcile -- charging a turn's actual, and netting the estimate out once --
        route through it, so the multi-turn sum is worth pinning here rather than assumed to carry
        over from the in-memory path.
        """
        from egpt.models.message import Message
        from egpt.models.response import ModelResponse
        from egpt.providers.budget import RedisCounterStore
        from egpt.providers.budget_provider import BudgetAwareModelProvider
        from egpt.providers.model_provider import ModelResolveRequest, ProviderMode
        from tests.unit.providers.test_escalation import SMALL, FakeDetailedProvider

        limits = BudgetLimits(max_tokens=100_000)
        enforcer = BudgetEnforcer(RedisCounterStore(client=shared_redis), warn_on_in_memory=False)
        provider = BudgetAwareModelProvider(
            FakeDetailedProvider("mode-a", SMALL, ProviderMode.LOCAL),
            enforcer=enforcer,
            limits_for=lambda request: limits,
        )

        result = provider.resolve_detailed(
            ModelResolveRequest(tenant_id="acme", agent_id="bot", estimated_input_tokens=100)
        )

        turns = []  # held, so no metrics object is collected mid-run
        for _ in range(3):
            message = Message(role="assistant")
            result.model._ensure_message_metrics_initialized(message)
            message.metrics.input_tokens = 500
            message.metrics.output_tokens = 200
            turns.append(message)
            result.model._populate_assistant_message(
                assistant_message=message,
                provider_response=ModelResponse(role="assistant", content="done"),
            )

        assert enforcer.usage("acme", limits)["tokens"] == 2_100
        assert enforcer.usage("acme", limits, agent_id="bot")["tokens"] == 2_100

    def test_increments_are_atomic_across_workers(self, shared_redis):
        """INCRBY is atomic server-side; a read-then-write store loses updates here."""
        from concurrent.futures import ThreadPoolExecutor

        from egpt.providers.budget import RedisCounterStore

        stores = [RedisCounterStore(client=shared_redis) for _ in range(4)]

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda s: [s.increment("k", 1, 3_600) for _ in range(100)], stores))

        assert stores[0].get("k") == 400

    def test_the_ttl_is_set_only_on_creation(self, store, shared_redis):
        """Extending the TTL on every write turns a fixed window into a sliding one that never
        expires under sustained traffic -- so a continuously-spending tenant would never get its
        allowance back."""
        store.increment("k", 1, 3_600)
        first_ttl = shared_redis.ttl("k")

        shared_redis.expire("k", 60)  # simulate time passing
        store.increment("k", 1, 3_600)

        assert shared_redis.ttl("k") <= 60, "a later write must not push expiry forward"
        assert first_ttl > 0

    def test_a_decrement_never_creates_a_key(self, store, shared_redis):
        """DECRBY on a missing key creates it negative, which would grant free headroom for the
        rest of the window -- and reconciling after a window rolls over is exactly when the key is
        missing."""
        assert store.decrement("never-existed", 500) == 0
        assert not shared_redis.exists("never-existed")

    def test_a_decrement_is_clamped_at_zero(self, store):
        store.increment("k", 10, 3_600)

        assert store.decrement("k", 999) == 0
        assert store.get("k") == 0

    def test_get_on_a_missing_key_is_zero(self, store):
        assert store.get("absent") == 0

    def test_it_satisfies_the_counter_store_protocol(self, store):
        assert isinstance(store, CounterStore)

    def test_reserve_and_reconcile_work_through_redis(self, shared_redis):
        from egpt.providers.budget import RedisCounterStore

        limits = BudgetLimits(max_tokens=1_000)
        enforcer = BudgetEnforcer(RedisCounterStore(client=shared_redis), warn_on_in_memory=False)

        enforcer.reserve("acme", limits, estimated_tokens=500)
        enforcer.reconcile("acme", limits, estimated_tokens=500, actual_tokens=200)

        assert enforcer.usage("acme", limits)["tokens"] == 200

    def test_a_missing_redis_package_names_the_remedy(self, monkeypatch):
        """An optional dependency must fail with an actionable message, not an ImportError from
        three frames down."""
        import builtins

        from egpt.providers.budget import RedisCounterStore

        real_import = builtins.__import__

        def no_redis(name, *args, **kwargs):
            if name == "redis":
                raise ImportError("no module named redis")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_redis)

        with pytest.raises(ImportError) as exc:
            RedisCounterStore("redis://localhost:6379/0")

        assert "pip install redis" in str(exc.value)
        assert "EGPT_BUDGET_REDIS_URL" in str(exc.value), "the message should name the alternative"
