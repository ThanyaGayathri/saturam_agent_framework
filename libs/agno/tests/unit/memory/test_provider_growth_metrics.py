"""Store-growth metering on the co-located provider's write/consolidate paths (SM-X8,
aicoe-tracker#144).

No substrate in this tree exposes a ``count()``/``size()`` (``VectorSubstrate``/``GraphSubstrate``
have neither -- see ``egpt.memory.substrates``), so "store growth" is a per-call delta: how many
events one ``write`` accepted, and how many semantic facts one ``consolidate`` pass wrote. This
file pins that both calls populate the shared default aggregator with the right ``accepted`` count
for the scope that made the call, against the same ``FakeVector``/``FakeGraph`` doubles
``test_provider_co_located.py`` uses.
"""

from __future__ import annotations

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import MemoryProviderConfig
from egpt.memory.metrics import get_default_aggregator, reset_default_aggregator
from egpt.memory.types import MemoryEvent, MemoryOperationError, PartialWriteError, Scope
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")
GLOBEX = Scope(tenant_id="globex", user_id="u1")


@pytest.fixture(autouse=True)
def clean_default_aggregator():
    reset_default_aggregator()
    yield
    reset_default_aggregator()


def provider():
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


class TestWriteGrowth:
    def test_a_write_records_growth_for_its_own_tenant(self):
        p, _, _ = provider()

        receipt = p.write(ACME, [event("the deploy failed on friday"), event("the rollback finished")])

        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == receipt.accepted == 2

    def test_an_awrite_records_growth_too(self):
        p, _, _ = provider()

        async def run():
            return await p.awrite(ACME, [event("the deploy failed on friday")])

        import asyncio

        receipt = asyncio.run(run())

        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == receipt.accepted == 1

    def test_two_tenants_stay_separate(self):
        p, _, _ = provider()

        p.write(ACME, [event("acme's own episode")])
        p.write(GLOBEX, [event("globex's own episode", scope=GLOBEX)])

        totals = get_default_aggregator().growth_by_tenant()
        assert totals == {"acme": 1, "globex": 1}

    def test_the_growth_entry_carries_the_write_operation_and_tier(self):
        p, _, _ = provider()

        p.write(ACME, [event("the deploy failed on friday")])

        entries = [e for e in get_default_aggregator().growth if e.tenant_id == "acme"]
        assert len(entries) == 1
        assert entries[0].operation == "write"
        assert entries[0].accepted == 1

    def test_write_with_no_events_records_no_growth_entry(self):
        """A degenerate `MemoryGrowthEntry(tier="", accepted=0)` for an empty batch is noise, not
        signal (nit, review finding #2) -- an empty write never reaches `record_growth` at all."""
        p, _, _ = provider()

        receipt = p.write(ACME, [])

        assert receipt.accepted == 0
        assert get_default_aggregator().growth == []

    async def test_awrite_with_no_events_records_no_growth_entry(self):
        p, _, _ = provider()

        receipt = await p.awrite(ACME, [])

        assert receipt.accepted == 0
        assert get_default_aggregator().growth == []


class TestPartialWriteStillRecordsGrowth:
    """Review finding #2 (PR #159): `record_growth` used to sit after the try/except entirely, so
    a `PartialWriteError` -- raised once the vector leg has already committed the full batch, per
    `_raise_write_failure`'s own docstring -- recorded no growth at all. These pin that growth is
    now captured on that failure path too, using the same `OnceFailingGraph` shape
    `test_provider_co_located.py` already proves out for the cursor-on-failure tests.
    """

    class OnceFailingGraph(FakeGraph):
        fail = True

        def upsert_episodes(self, scope, events):
            if self.fail:
                raise MemoryOperationError("backend unavailable", field_name="tier")
            super().upsert_episodes(scope, events)

        def upsert_facts(self, scope, facts):
            if self.fail:
                raise MemoryOperationError("backend unavailable", field_name="tier")
            super().upsert_facts(scope, facts)

    def _provider(self):
        vector, graph = FakeVector(), self.OnceFailingGraph()
        config = MemoryProviderConfig(provider="co_located")
        return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph

    def test_a_write_whose_graph_leg_fails_after_the_vector_leg_committed_still_records_growth(self):
        p, vector, _ = self._provider()

        with pytest.raises(PartialWriteError):
            p.write(ACME, [event("the deploy failed on friday")])

        assert "upsert" in vector.calls  # the vector leg genuinely committed before the graph leg raised
        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == 1

    async def test_an_awrite_whose_graph_leg_fails_still_records_growth(self):
        p, vector, _ = self._provider()

        with pytest.raises(PartialWriteError):
            await p.awrite(ACME, [event("the deploy failed on friday")])

        assert "upsert" in vector.calls
        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == 1

    def test_a_consolidate_pass_that_partially_fails_still_records_growth(self):
        p, _, graph = self._provider()
        graph.fail = False
        p.write(ACME, [event("the deploy failed on friday")])
        reset_default_aggregator()
        graph.fail = True

        with pytest.raises(PartialWriteError):
            p.consolidate(ACME)

        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == 1

    async def test_an_aconsolidate_pass_that_partially_fails_still_records_growth(self):
        p, _, graph = self._provider()
        graph.fail = False
        p.write(ACME, [event("the deploy failed on friday")])
        reset_default_aggregator()
        graph.fail = True

        with pytest.raises(PartialWriteError):
            await p.aconsolidate(ACME)

        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == 1


class TestConsolidateGrowth:
    def test_a_consolidate_pass_records_the_semantic_facts_written(self):
        p, _, graph = provider()
        p.write(ACME, [event("the deploy failed on friday")])
        reset_default_aggregator()  # drop the write's own growth entry -- only consolidate's matters here

        report = p.consolidate(ACME)

        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == report.semantic_written == len(graph.facts) == 1

    def test_an_aconsolidate_pass_records_growth_too(self):
        p, _, graph = provider()
        p.write(ACME, [event("the deploy failed on friday")])
        reset_default_aggregator()

        async def run():
            return await p.aconsolidate(ACME)

        import asyncio

        report = asyncio.run(run())

        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == report.semantic_written == 1

    def test_a_pass_with_nothing_to_consolidate_still_records_a_zero_entry(self):
        """Recording a zero-accepted entry, rather than skipping the call outright, is what makes
        a scope that never consolidates anything distinguishable from one that was never asked."""
        p, _, _ = provider()

        report = p.consolidate(ACME)

        assert report.semantic_written == 0
        entries = [e for e in get_default_aggregator().growth if e.operation == "consolidate"]
        assert len(entries) == 1
        assert entries[0].accepted == 0
        assert entries[0].tenant_id == "acme"

    def test_write_and_consolidate_growth_both_accumulate_for_the_same_tenant(self):
        p, _, _ = provider()

        p.write(ACME, [event("the deploy failed on friday")])
        p.consolidate(ACME)

        totals = get_default_aggregator().growth_by_tenant()
        # 1 episodic write + 1 semantic fact from consolidation.
        assert totals["acme"] == 2
