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
from egpt.memory.tiers import CONSOLIDATION_TARGET
from egpt.memory.types import (
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryRecord,
    PartialWriteError,
    Scope,
)
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


class TestGenericFailureAfterVectorCommitStillRecordsGrowth:
    """Major finding, PR #159 review round 3 (``co_located.py:778``/``:813``): the generic
    ``except Exception`` branch in ``write``/``awrite`` sits below the vector leg's own atomic
    commit (``_write``/``_awrite`` are documented all-or-nothing, one transaction) with no way to
    tell "nothing committed" (the vector call itself raised) apart from "the vector leg committed
    and something else broke after it" (a bug in the graph-batching plumbing itself, not a
    substrate refusal -- those are caught by ``attempt``/``_gather_writes`` and turned into
    ``PartialWriteError`` already, covered by ``TestPartialWriteStillRecordsGrowth`` above). It
    used to silently drop growth in the latter case. ``vector_committed`` now distinguishes them.
    """

    def test_a_write_whose_graph_batching_breaks_after_the_vector_leg_committed_still_records_growth(self):
        p, vector, _ = provider()
        p._graph_batches = lambda stamped, records: (_ for _ in ()).throw(RuntimeError("boom"))

        with pytest.raises(PartialWriteError):
            p.write(ACME, [event("the deploy failed on friday")])

        assert "upsert" in vector.calls  # the vector leg genuinely committed before the bug fired
        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == 1

    async def test_an_awrite_whose_graph_batching_breaks_after_the_vector_leg_committed_still_records_growth(self):
        p, vector, _ = provider()
        p._graph_batches = lambda stamped, records: (_ for _ in ()).throw(RuntimeError("boom"))

        with pytest.raises(PartialWriteError):
            await p.awrite(ACME, [event("the deploy failed on friday")])

        assert "upsert" in vector.calls
        totals = get_default_aggregator().growth_by_tenant()
        assert totals["acme"] == 1

    def test_a_write_whose_vector_leg_itself_raises_records_no_growth(self):
        """Contrast case: the vector call raising means nothing committed (atomic, per
        ``_write``'s own docstring), so ``vector_committed`` stays ``False`` and no growth entry
        appears -- this must keep holding, not just the committed-then-broke case above."""
        vector, graph = FakeVector(), FakeGraph()
        vector.upsert = lambda scope, records: (_ for _ in ()).throw(RuntimeError("boom"))
        config = MemoryProviderConfig(provider="co_located")
        p = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)

        with pytest.raises(PartialWriteError):
            p.write(ACME, [event("the deploy failed on friday")])

        assert get_default_aggregator().growth == []


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


def _fact(scope, suffix):
    """A minimal, valid ``FactWrite`` for the multi-group tests below -- content/subject/etc.
    don't matter, only that each carries its own ``scope`` and a unique id."""
    record = MemoryRecord(
        id=f"fact-{suffix}",
        tier=CONSOLIDATION_TARGET,
        content=f"fact {suffix}",
        scope=scope,
    )
    return FactWrite(record=record, source_episode_ids=[f"episode-{suffix}"], derived_by="test")


class ScopedFailingGraph(FakeGraph):
    """Fails ``upsert_facts`` only when called for one specific scope -- lets a multi-group
    ``_by_scope`` fan-out commit some groups for real before a later one raises, without depending
    on call order the way a plain "fail on Nth call" double would."""

    def __init__(self, fail_for_agent_id):
        super().__init__()
        self.fail_for_agent_id = fail_for_agent_id

    def upsert_facts(self, scope, facts):
        if scope.agent_id == self.fail_for_agent_id:
            raise MemoryOperationError("backend unavailable for this scope", field_name="tier")
        super().upsert_facts(scope, facts)


class TestConsolidateGrowthAcrossMultipleFactScopeGroups:
    """Critical finding, PR #159 round-2 review (``co_located.py:1835``): a multi-``fact_scope``
    consolidate pass used to record growth for the full pass-wide ``facts`` count on a partial
    failure, over-counting by exactly the groups the loop never even reached. Growth must reflect
    only the group(s) actually attempted, attributed to each group's own scope -- including a
    group's own partial commit (its vector leg landing before its graph leg raises), which the
    round-1 fix already established matters and must not regress.
    """

    GROUP_A = Scope(tenant_id="acme", user_id="u1", agent_id="agent-a")
    GROUP_B = Scope(tenant_id="acme", user_id="u1", agent_id="agent-b")
    GROUP_C = Scope(tenant_id="acme", user_id="u1", agent_id="agent-c")

    def _provider(self, fail_for_agent_id):
        vector, graph = FakeVector(), ScopedFailingGraph(fail_for_agent_id)
        config = MemoryProviderConfig(provider="co_located")
        p = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)
        return p, vector, graph

    def test_a_group_the_loop_never_reaches_is_excluded_but_the_failing_groups_own_partial_commit_counts(self):
        """3 groups, sorted agent-a < agent-b < agent-c: A fully commits, B's vector leg lands and
        then its graph leg raises (a real partial commit for B), and C is never attempted at all
        because the raise stops the loop before it gets there. Growth must total A + B, never the
        full pass-wide 5 facts (the old bug) and never just A's 2 (which would silently drop B's
        genuine partial commit -- the exact regression the round-1 fix was for)."""
        p, _, _ = self._provider(fail_for_agent_id="agent-b")
        facts = [
            _fact(self.GROUP_A, "a1"),
            _fact(self.GROUP_A, "a2"),
            _fact(self.GROUP_B, "b1"),
            _fact(self.GROUP_C, "c1"),
            _fact(self.GROUP_C, "c2"),
        ]
        p._distil_facts = lambda scope, episodes: (facts, 0, 0, "test", "test")

        with pytest.raises(PartialWriteError):
            p.consolidate(ACME)

        entries = sorted(
            (e for e in get_default_aggregator().growth if e.operation == "consolidate"),
            key=lambda e: e.agent_id,
        )
        assert [(e.agent_id, e.accepted) for e in entries] == [("agent-a", 2), ("agent-b", 1)]
        assert get_default_aggregator().growth_by_tenant()["acme"] == 3

    def test_every_group_succeeding_records_growth_for_each(self):
        p, _, _ = self._provider(fail_for_agent_id=None)
        facts = [_fact(self.GROUP_A, "a1"), _fact(self.GROUP_B, "b1"), _fact(self.GROUP_B, "b2")]
        p._distil_facts = lambda scope, episodes: (facts, 0, 0, "test", "test")

        p.consolidate(ACME)

        entries = sorted(
            (e for e in get_default_aggregator().growth if e.operation == "consolidate"),
            key=lambda e: e.agent_id,
        )
        assert [(e.agent_id, e.accepted) for e in entries] == [("agent-a", 1), ("agent-b", 2)]


class TestGrowthEntryCarriesEventIdsForDedup:
    """Critical finding, PR #159 round-2 review (``co_located.py:766``): a caller that retries
    ``write`` after a ``PartialWriteError`` and succeeds gets growth recorded twice for a store
    that (thanks to deterministic ids) only grew once. This doesn't stop at the double record --
    it pins that a downstream consumer *can* detect it, via overlapping ``event_ids``.
    """

    class OnceFailingGraph(FakeGraph):
        fail = True

        def upsert_episodes(self, scope, events):
            if self.fail:
                raise MemoryOperationError("backend unavailable", field_name="tier")
            super().upsert_episodes(scope, events)

    def test_a_failed_then_retried_write_produces_two_entries_with_overlapping_event_ids(self):
        vector, graph = FakeVector(), self.OnceFailingGraph()
        config = MemoryProviderConfig(provider="co_located")
        p = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)
        events = [event("the deploy failed on friday", id="ev-1")]

        with pytest.raises(PartialWriteError):
            p.write(ACME, events)
        graph.fail = False
        p.write(ACME, events)  # same deterministic id -- upserts the same row, doesn't add a new one

        entries = [e for e in get_default_aggregator().growth if e.tenant_id == "acme"]
        assert len(entries) == 2
        assert set(entries[0].event_ids) & set(entries[1].event_ids)

    def test_a_consolidate_growth_entry_carries_its_facts_event_ids(self):
        p, _, _ = provider()
        p.write(ACME, [event("the deploy failed on friday")])
        reset_default_aggregator()

        report = p.consolidate(ACME)

        entries = [e for e in get_default_aggregator().growth if e.operation == "consolidate"]
        assert len(entries) == 1
        assert len(entries[0].event_ids) == report.semantic_written == 1
