"""Memory cost and store-growth metering, per tenant (SM-X8, aicoe-tracker#144).

Everything here is exercised against fakes -- no real LLM or embedding API call. The three things
this pins:

* ``MemoryMetricsAggregator`` sums correctly across entries for the same and different tenants,
  and an unpriced entry (``cost=None``) is skipped by ``cost_by_tenant`` rather than counted as
  free.
* ``meter_embedding_cost``/``meter_extraction_cost`` never raise, and an unpriced model/provider
  yields ``cost=None``, never a coerced ``0``.
* ``embedder_provider_key`` derives the same registry key ``egpt.memory.embedding`` already
  derives internally for its offline-capability check, so the two agree about what an embedder is
  called.
"""

from __future__ import annotations

import pytest

from egpt.memory.metrics import (
    MemoryCostEntry,
    MemoryGrowthEntry,
    MemoryMetricsAggregator,
    embedder_provider_key,
    get_default_aggregator,
    meter_embedding_cost,
    meter_extraction_cost,
    record_growth,
    reset_default_aggregator,
)
from egpt.memory.types import Scope
from egpt.models.pricing import ModelPrice

ACME = Scope(tenant_id="acme", agent_id="agent-1")
GLOBEX = Scope(tenant_id="globex", agent_id="agent-2")


@pytest.fixture(autouse=True)
def clean_default_aggregator():
    """The default aggregator is one process-wide instance (module docstring) -- reset it before
    and after every test in this module so entries never leak into or out of another test."""
    reset_default_aggregator()
    yield
    reset_default_aggregator()


class FakeEmbedder:
    """A minimal double living at a module whose last segment is a stand-in registry key."""

    id = "fake-embed-1"


class FakeModel:
    """A minimal double for a live ``Model`` instance -- unregistered, so
    ``BaseModelProvider._registry_key_for`` resolves it to ``None`` unless a test patches it."""

    id = "fake-chat-1"


class FakeUsage:
    def __init__(self, input_tokens=0, output_tokens=0, cache_read_tokens=0, reasoning_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.reasoning_tokens = reasoning_tokens


# ---------------------------------------------------------------------------
# MemoryMetricsAggregator
# ---------------------------------------------------------------------------


class TestAggregator:
    def test_cost_by_tenant_sums_across_entries_for_the_same_tenant(self):
        agg = MemoryMetricsAggregator()
        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", "m", cost=1.5))
        agg.record_cost(MemoryCostEntry("acme", None, "extraction", "openai", "m", cost=2.5))

        assert agg.cost_by_tenant() == {"acme": 4.0}

    def test_cost_by_tenant_keeps_tenants_separate(self):
        agg = MemoryMetricsAggregator()
        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", "m", cost=1.0))
        agg.record_cost(MemoryCostEntry("globex", None, "embedding", "openai", "m", cost=9.0))

        totals = agg.cost_by_tenant()
        assert totals["acme"] == 1.0
        assert totals["globex"] == 9.0

    def test_an_unpriced_entry_is_skipped_not_counted_as_free(self):
        """A missing number is a reporting gap; treating it as 0 would report a tenant with an
        unpriced model as cheaper than one that made no calls at all."""
        agg = MemoryMetricsAggregator()
        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "unknown", "m", cost=None))

        assert agg.cost_by_tenant() == {}

    def test_growth_by_tenant_sums_across_operations(self):
        agg = MemoryMetricsAggregator()
        agg.record_growth(MemoryGrowthEntry("acme", None, "write", "episodic", accepted=3))
        agg.record_growth(MemoryGrowthEntry("acme", None, "consolidate", "episodic->semantic", accepted=2))
        agg.record_growth(MemoryGrowthEntry("globex", None, "write", "episodic", accepted=7))

        totals = agg.growth_by_tenant()
        assert totals["acme"] == 5
        assert totals["globex"] == 7


class TestDefaultAggregator:
    def test_get_default_aggregator_is_a_single_shared_instance(self):
        assert get_default_aggregator() is get_default_aggregator()

    def test_reset_clears_both_lists(self):
        agg = get_default_aggregator()
        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", "m", cost=1.0))
        agg.record_growth(MemoryGrowthEntry("acme", None, "write", "episodic", accepted=1))

        reset_default_aggregator()

        assert agg.costs == []
        assert agg.growth == []


# ---------------------------------------------------------------------------
# Bounding, draining, and thread-safety (review finding #3, PR #159) -- the default aggregator is
# genuinely fed from background threads in production (memory/writeback.py's ThreadPoolExecutor).
# ---------------------------------------------------------------------------


class TestAggregatorBoundsAndConcurrency:
    def test_recording_past_max_entries_evicts_the_oldest(self):
        agg = MemoryMetricsAggregator(max_entries=3)
        for i in range(5):
            agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", f"m{i}", cost=float(i)))

        entries = agg.costs
        assert len(entries) == 3
        # The two oldest (m0, m1) were evicted -- only the most recent max_entries remain.
        assert [e.model_id for e in entries] == ["m2", "m3", "m4"]

    def test_growth_is_bounded_the_same_way(self):
        agg = MemoryMetricsAggregator(max_entries=2)
        for i in range(4):
            agg.record_growth(MemoryGrowthEntry("acme", None, "write", "episodic", accepted=i))

        assert len(agg.growth) == 2
        assert [e.accepted for e in agg.growth] == [2, 3]

    def test_drain_returns_everything_and_empties_the_aggregator(self):
        agg = MemoryMetricsAggregator()
        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", "m", cost=1.0))
        agg.record_growth(MemoryGrowthEntry("acme", None, "write", "episodic", accepted=1))

        costs, growth = agg.drain()

        assert len(costs) == 1 and len(growth) == 1
        assert agg.costs == []
        assert agg.growth == []

    def test_recording_after_a_drain_still_works(self):
        agg = MemoryMetricsAggregator()
        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", "m", cost=1.0))
        agg.drain()

        agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", "m2", cost=2.0))

        assert len(agg.costs) == 1
        assert agg.costs[0].model_id == "m2"

    def test_concurrent_record_cost_from_many_threads_loses_nothing(self):
        """Matches the real shape: ``memory/writeback.py`` submits writes onto a real
        ``ThreadPoolExecutor``, each eventually calling ``record_cost``/``record_growth`` on the
        one shared default aggregator."""
        import concurrent.futures

        agg = MemoryMetricsAggregator(max_entries=1000)
        threads, calls_per_thread = 8, 50

        def record_many(n):
            for i in range(calls_per_thread):
                agg.record_cost(MemoryCostEntry("acme", None, "embedding", "openai", f"{n}-{i}", cost=1.0))

        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(record_many, range(threads)))

        assert len(agg.costs) == threads * calls_per_thread

    def test_max_entries_zero_is_rejected_rather_than_silently_discarding_everything(self):
        """Minor finding, PR #159 review round 3 (``embedding.py:146``): ``deque(maxlen=0)``
        would silently discard every entry appended, indistinguishable from "recording works but
        nothing has happened yet" -- refuse construction instead."""
        with pytest.raises(ValueError):
            MemoryMetricsAggregator(max_entries=0)

    def test_max_entries_negative_is_rejected_too(self):
        with pytest.raises(ValueError):
            MemoryMetricsAggregator(max_entries=-1)


# ---------------------------------------------------------------------------
# embedder_provider_key
# ---------------------------------------------------------------------------


def test_embedder_provider_key_is_the_last_dotted_module_segment():
    """Mirrors ``egpt.memory.embedding``'s own derivation for the four offline-capable embedders
    (``sentence_transformer``, ``fastembed``, ``vllm``, ``ollama``) -- concrete embedders live at
    ``egpt.integrations.knowledge.embedder.<key>``, so the leaf segment *is* the registry key."""
    from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder

    embedder = OpenAIEmbedder.__new__(OpenAIEmbedder)  # no client needed, only __module__
    assert embedder_provider_key(embedder) == "openai"


def test_embedder_provider_key_on_a_fake_is_its_own_module_leaf():
    assert embedder_provider_key(FakeEmbedder()) == __name__.rsplit(".", 1)[-1]


# ---------------------------------------------------------------------------
# meter_embedding_cost
# ---------------------------------------------------------------------------


class TestMeterEmbeddingCost:
    def test_a_priced_call_records_a_real_cost(self, monkeypatch):
        provider_key = embedder_provider_key(FakeEmbedder())
        monkeypatch.setattr(
            "egpt.models.pricing.PRICES",
            {(provider_key, "fake-embed-1"): ModelPrice(input=2.0, output=0.0)},
        )
        agg = MemoryMetricsAggregator()

        meter_embedding_cost(
            agg,
            tenant_id="acme",
            agent_id="agent-1",
            embedder=FakeEmbedder(),
            model_id=None,
            usage={"prompt_tokens": 500_000, "total_tokens": 500_000},
        )

        assert len(agg.costs) == 1
        entry = agg.costs[0]
        assert entry.tenant_id == "acme"
        assert entry.agent_id == "agent-1"
        assert entry.kind == "embedding"
        assert entry.provider_key == provider_key
        assert entry.model_id == "fake-embed-1"
        assert entry.input_tokens == 500_000
        assert entry.cost == pytest.approx(1.0)  # 500_000 / 1_000_000 * $2.00

    def test_an_unpriced_model_records_cost_none_not_zero_and_does_not_raise(self):
        agg = MemoryMetricsAggregator()

        meter_embedding_cost(
            agg,
            tenant_id="acme",
            agent_id=None,
            embedder=FakeEmbedder(),
            model_id="nobody-prices-this",
            usage={"prompt_tokens": 100},
        )

        assert len(agg.costs) == 1
        assert agg.costs[0].cost is None

    def test_missing_usage_records_an_entry_with_zero_tokens_and_cost_none(self):
        """Usage can be unavailable (a plain ``get_embedding`` fallback, or an embedder returning
        ``None`` usage) -- metering must still record *something* rather than silently drop the
        call, so downstream reporting can distinguish "we don't know the usage" from "no call was
        made"."""
        agg = MemoryMetricsAggregator()

        meter_embedding_cost(agg, tenant_id="acme", agent_id=None, embedder=FakeEmbedder(), model_id="m", usage=None)

        assert len(agg.costs) == 1
        assert agg.costs[0].input_tokens == 0
        assert agg.costs[0].cost is None

    def test_never_raises_even_when_usage_is_malformed(self):
        agg = MemoryMetricsAggregator()

        meter_embedding_cost(
            agg,
            tenant_id="acme",
            agent_id=None,
            embedder=FakeEmbedder(),
            model_id="m",
            usage={"prompt_tokens": "not-a-number"},
        )

        assert len(agg.costs) == 1
        assert agg.costs[0].cost is None

    def test_uses_the_default_aggregator_when_given_it(self):
        meter_embedding_cost(
            get_default_aggregator(), tenant_id="acme", agent_id=None, embedder=FakeEmbedder(), model_id="m", usage=None
        )
        assert len(get_default_aggregator().costs) == 1


# ---------------------------------------------------------------------------
# meter_extraction_cost
# ---------------------------------------------------------------------------


class TestMeterExtractionCost:
    def test_a_priced_call_records_a_real_cost(self, monkeypatch):
        monkeypatch.setattr(
            "egpt.providers.model_provider.BaseModelProvider._registry_key_for",
            staticmethod(lambda model: "fake-provider"),
        )
        monkeypatch.setattr(
            "egpt.models.pricing.PRICES",
            {("fake-provider", "fake-chat-1"): ModelPrice(input=1.0, output=3.0)},
        )
        agg = MemoryMetricsAggregator()

        meter_extraction_cost(
            agg,
            scope=ACME,
            model=FakeModel(),
            response_usage=FakeUsage(input_tokens=1_000_000, output_tokens=1_000_000),
        )

        assert len(agg.costs) == 1
        entry = agg.costs[0]
        assert entry.tenant_id == "acme"
        assert entry.agent_id == "agent-1"
        assert entry.kind == "extraction"
        assert entry.provider_key == "fake-provider"
        assert entry.model_id == "fake-chat-1"
        assert entry.cost == pytest.approx(4.0)  # 1.0 + 3.0

    def test_an_unregistered_model_is_unpriced_not_an_exception(self):
        """``FakeModel`` is not in the model registry, so ``_registry_key_for`` resolves it to
        ``None`` -- ``compute_cost`` must then report ``None``, not raise or guess."""
        agg = MemoryMetricsAggregator()

        meter_extraction_cost(
            agg, scope=ACME, model=FakeModel(), response_usage=FakeUsage(input_tokens=100, output_tokens=50)
        )

        assert len(agg.costs) == 1
        assert agg.costs[0].cost is None
        assert agg.costs[0].input_tokens == 100
        assert agg.costs[0].output_tokens == 50

    def test_a_none_response_usage_records_zero_tokens_and_does_not_raise(self):
        agg = MemoryMetricsAggregator()

        meter_extraction_cost(agg, scope=ACME, model=FakeModel(), response_usage=None)

        assert len(agg.costs) == 1
        assert agg.costs[0].input_tokens == 0
        assert agg.costs[0].output_tokens == 0
        assert agg.costs[0].cost is None

    def test_agent_id_is_optional_on_scope(self):
        bare = Scope(tenant_id="acme")
        agg = MemoryMetricsAggregator()

        meter_extraction_cost(agg, scope=bare, model=FakeModel(), response_usage=None)

        assert agg.costs[0].agent_id is None

    def test_a_magicmock_spec_model_is_not_treated_as_a_resolvable_provider_key(self):
        """Minor finding, PR #159 round-2 review (``metrics.py:104``): ``hasattr(model,
        "_resolved_provider_key")`` is ``True`` for ``MagicMock(spec=Model)`` (the spec
        auto-generates every attribute the real class has), and calling it returns an
        unconfigured ``MagicMock`` rather than ``None``/``str``. ``isinstance(model, Model)``
        would not have caught this either -- ``MagicMock(spec=Model)`` deliberately makes that
        check pass too (verified directly: ``isinstance`` is ``True`` here). The actual fix
        validates the *result* of ``_resolved_provider_key()``, coercing anything that isn't a
        ``str``/``None`` to ``None`` rather than handing a garbage value to the pricing lookup."""
        from unittest.mock import MagicMock

        from egpt.models.base import Model

        mock_model = MagicMock(spec=Model)
        mock_model.id = "mocked-model"
        assert isinstance(mock_model, Model)  # the exact trap a bare `isinstance` guard falls into
        agg = MemoryMetricsAggregator()

        meter_extraction_cost(agg, scope=ACME, model=mock_model, response_usage=None)

        assert len(agg.costs) == 1
        assert agg.costs[0].provider_key is None
        assert agg.costs[0].cost is None


# ---------------------------------------------------------------------------
# record_growth
# ---------------------------------------------------------------------------


class TestRecordGrowth:
    def test_records_one_entry_per_call(self):
        agg = MemoryMetricsAggregator()

        record_growth(agg, ACME, "write", tier="episodic", accepted=3)
        record_growth(agg, ACME, "consolidate", tier="episodic->semantic", accepted=1)
        record_growth(agg, GLOBEX, "write", tier="episodic", accepted=5)

        assert len(agg.growth) == 3
        assert agg.growth_by_tenant() == {"acme": 4, "globex": 5}

    def test_carries_the_operation_and_tier(self):
        agg = MemoryMetricsAggregator()

        record_growth(agg, ACME, "write", tier="episodic", accepted=2)

        entry = agg.growth[0]
        assert entry.operation == "write"
        assert entry.tier == "episodic"
        assert entry.accepted == 2
        assert entry.agent_id == "agent-1"

    def test_event_ids_default_to_empty_and_pass_through_when_given(self):
        """Critical finding, PR #159 round-2 review (``co_located.py:766``): ``event_ids`` lets a
        downstream consumer detect a retried, deterministically-re-ided batch that would otherwise
        double-count growth. Defaults to empty so every pre-existing caller is unaffected."""
        agg = MemoryMetricsAggregator()

        record_growth(agg, ACME, "write", tier="episodic", accepted=2)
        record_growth(agg, ACME, "write", tier="episodic", accepted=1, event_ids=("ev-1", "ev-2"))

        assert agg.growth[0].event_ids == ()
        assert agg.growth[1].event_ids == ("ev-1", "ev-2")
