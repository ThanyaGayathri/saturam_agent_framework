"""The co-located provider: fusion, write fan-out, and bi-temporal reads (SM-C1, phase 4).

Every finding the SM-R11 prototype recorded is a test here, because each was invisible until it
was wrong: one id across substrates (F1), point-in-time on the fused set (F2), identical tier
resolution (F3), two-sided validity (F6), dual-substrate consolidation (F7), and SCD-2
invalidation rather than in-place mutation (F10).
"""

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    build_memory_provider,
    registered_providers,
)
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryEvent, MemoryQuery, MemoryRecord, Scope, Substrate
from tests.unit.memory._fakes import FakeGraph, FakeVector, assert_ports_satisfied

ACME = Scope(tenant_id="acme", user_id="u1")
OTHER = Scope(tenant_id="globex", user_id="u1")


def provider(**config_kwargs):
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located", **config_kwargs)
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


def test_the_doubles_satisfy_the_real_ports():
    assert_ports_satisfied()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_the_adapter_self_registers_so_config_can_select_it():
    """Importing egpt.memory is enough; no caller needs to know the adapter module exists."""
    assert "co_located" in registered_providers()


def test_build_wires_the_injected_substrates():
    """The pre-backend path: substrates arrive through options until SM-V1/SM-G1 register real ones."""
    config = MemoryProviderConfig(
        provider="co_located",
        options={"vector_substrate": FakeVector(), "graph_substrate": FakeGraph()},
    )

    built = build_memory_provider(config)

    assert isinstance(built, CoLocatedMemoryProvider)
    assert built.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: True}


def test_a_provider_with_no_substrate_is_a_misconfiguration():
    with pytest.raises(MemoryProviderConfigError, match="at least one substrate"):
        CoLocatedMemoryProvider()


def test_substrates_reports_what_is_wired():
    """A vector-only tenant is supported; callers must be able to tell absent from empty."""
    vector_only = CoLocatedMemoryProvider(vector=FakeVector())

    assert vector_only.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: False}


# ---------------------------------------------------------------------------
# Write fan-out
# ---------------------------------------------------------------------------


def test_one_episode_gets_one_id_across_both_substrates():
    """Finding F1: a second id turns one episode into two fused results."""
    p, vector, graph = provider()

    receipt = p.write(ACME, [event("deployed the billing service")])

    assert receipt.accepted == 1
    written_id = receipt.event_ids[0]
    assert list(vector.rows["acme"]) == [written_id]
    assert [row.id for row in graph.rows["acme"]] == [written_id]


def test_a_caller_supplied_id_is_honored_as_an_idempotency_key():
    p, vector, graph = provider()

    p.write(ACME, [event("hello", id="fixed-1")])

    assert "fixed-1" in vector.rows["acme"]
    assert graph.rows["acme"][0].id == "fixed-1"


def test_writes_default_to_episodic_and_stamp_valid_time():
    p, vector, _ = provider()

    receipt = p.write(ACME, [event("something happened")])
    record = vector.rows["acme"][receipt.event_ids[0]]

    assert receipt.tiers_written == [MemoryTier.EPISODIC]
    assert record.tier is MemoryTier.EPISODIC
    assert record.t_valid is not None and record.created_at is not None


def test_a_supplied_occurred_at_is_preserved_as_valid_time():
    """Valid time is when it happened, not when we recorded it."""
    p, vector, _ = provider()

    receipt = p.write(ACME, [event("last tuesday's incident", occurred_at=1_700_000_000)])

    assert vector.rows["acme"][receipt.event_ids[0]].t_valid == 1_700_000_000


def test_procedural_writes_skip_the_graph_per_the_ratified_tier_map():
    """ADR 0003 §1: procedural is vector-only. Writing it to the graph would be inventing a shape."""
    p, vector, graph = provider()

    p.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm before deleting")])

    assert len(vector.rows["acme"]) == 1
    assert graph.rows.get("acme", []) == []


def test_writing_the_working_tier_is_refused():
    """WORKING has no persistent substrate; the buffer is SM-C6's, not this seam's."""
    p, _, _ = provider()

    with pytest.raises(MemoryProviderConfigError, match="no persistent substrate"):
        p.write_tier(ACME, MemoryTier.WORKING, [event("scratchpad note")])


def test_write_tier_overrides_the_events_own_tier():
    p, vector, _ = provider()

    receipt = p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ships on fridays")])

    assert receipt.tiers_written == [MemoryTier.SEMANTIC]
    assert vector.rows["acme"][receipt.event_ids[0]].tier is MemoryTier.SEMANTIC


# ---------------------------------------------------------------------------
# Read and fusion
# ---------------------------------------------------------------------------


def test_a_hit_from_both_substrates_fuses_into_one_record():
    p, _, _ = provider()
    p.write(ACME, [event("the billing service was deployed")])

    result = p.read(ACME, MemoryQuery(text="billing service"))

    assert len(result.records) == 1
    assert result.fusion["vector_hits"] == 1
    assert result.fusion["graph_hits"] == 1
    assert result.fusion["fused"] == 1
    assert result.records[0].substrate is None  # no longer from one place


def test_fusion_defaults_to_weighted_and_reports_it():
    p, _, _ = provider()
    p.write(ACME, [event("alpha beta")])

    assert p.read(ACME, MemoryQuery(text="alpha")).fusion["method"] == "weighted"


def test_rrf_is_selected_by_config_and_actually_used():
    """Finding F5 was a fusion setting that was accepted and then silently ignored."""
    p, _, _ = provider(fusion={"method": "rrf"})
    p.write(ACME, [event("alpha beta")])

    result = p.read(ACME, MemoryQuery(text="alpha"))

    assert result.fusion["method"] == "rrf"
    # 2 legs, rank 1 each, k=60 -> 2 * 1/61
    assert result.records[0].score == pytest.approx(2 / 61)


def test_a_query_can_exclude_a_substrate():
    p, _, graph = provider()
    p.write(ACME, [event("alpha beta")])

    result = p.read(ACME, MemoryQuery(text="alpha", substrates=[Substrate.VECTOR]))

    assert result.fusion["graph_hits"] == 0
    assert "traverse" not in graph.calls


def test_the_limit_caps_returned_records_but_fusion_reports_the_full_set():
    p, _, _ = provider()
    p.write(ACME, [event(f"alpha item {i}") for i in range(5)])

    result = p.read(ACME, MemoryQuery(text="alpha", limit=2))

    assert len(result.records) == 2
    assert result.fusion["fused"] == 5
    assert result.fusion["returned"] == 2


def test_records_are_grouped_by_tier():
    p, _, _ = provider()
    p.write(ACME, [event("alpha episodic")])
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha semantic")])

    result = p.read(ACME, MemoryQuery(text="alpha"))

    assert set(result.by_tier) == {MemoryTier.EPISODIC, MemoryTier.SEMANTIC}
    assert sum(len(v) for v in result.by_tier.values()) == len(result.records)


def test_both_legs_resolve_tiers_identically():
    """Finding F3: cross-tier leakage that only appears after fusion is hard to trace."""
    p, _, _ = provider()
    p.write(ACME, [event("alpha episodic")])
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha semantic")])

    result = p.read(ACME, MemoryQuery(text="alpha", tiers=[MemoryTier.SEMANTIC]))

    assert [r.tier for r in result.records] == [MemoryTier.SEMANTIC]


def test_read_tier_narrows_without_cross_tier_fusion():
    p, _, _ = provider()
    p.write(ACME, [event("alpha episodic")])
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha semantic")])

    records = p.read_tier(ACME, MemoryTier.EPISODIC, MemoryQuery(text="alpha"))

    assert [r.tier for r in records] == [MemoryTier.EPISODIC]


def test_ranking_is_reproducible_across_runs():
    """Ties break on id, so the same corpus and query cannot reorder between calls."""
    p, _, _ = provider()
    p.write(ACME, [event(f"alpha beta gamma {i}", id=f"e{i}") for i in range(4)])
    query = MemoryQuery(text="alpha beta")

    assert [r.id for r in p.read(ACME, query).records] == [r.id for r in p.read(ACME, query).records]


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_a_read_never_crosses_tenants():
    p, _, _ = provider()
    p.write(ACME, [event("acme deployment secret")])
    p.write(OTHER, [event("globex deployment secret", scope=OTHER)])

    leaked = [r for r in p.read(OTHER, MemoryQuery(text="deployment")).records if r.content.startswith("acme")]

    assert leaked == []


def test_consolidation_never_crosses_tenants():
    p, _, _ = provider()
    p.write(ACME, [event("acme only")])

    assert p.consolidate(OTHER).episodic_scanned == 0


# ---------------------------------------------------------------------------
# Bi-temporal reads
# ---------------------------------------------------------------------------


def as_of_provider():
    """A provider whose graph holds one fact that was true only between t=100 and t=200."""
    p, vector, graph = provider()
    record = MemoryRecord(
        id="f1",
        tier=MemoryTier.SEMANTIC,
        content="ada works at acme",
        scope=ACME,
        t_valid=100,
        t_invalid=200,
        created_at=100,
    )
    vector.upsert(ACME, [record])
    graph.rows.setdefault("acme", []).append(record)
    return p


@pytest.mark.parametrize("at,visible", [(50, False), (100, True), (199, True), (200, False), (500, False)])
def test_point_in_time_is_two_sided(at, visible):
    """Finding F6: checking only t_invalid lets a future-dated record answer about the past."""
    p = as_of_provider()

    found = p.read(ACME, MemoryQuery(text="ada acme", as_of=at)).records

    assert bool(found) is visible


def test_the_vector_leg_cannot_readmit_what_the_graph_bounded():
    """Finding F2: as_of is applied after fusion because only the graph knows about validity."""
    p, vector, graph = provider()
    bounded = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="ada works at acme", scope=ACME, t_invalid=200)
    unbounded = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="ada works at acme", scope=ACME)

    vector.upsert(ACME, [unbounded])  # the projection, with no notion of validity
    graph.rows.setdefault("acme", []).append(bounded)  # the authority

    assert p.read(ACME, MemoryQuery(text="ada acme", as_of=500)).records == []


def test_a_superseded_row_is_not_current_belief_even_without_as_of():
    p, vector, _ = provider()
    vector.upsert(
        ACME,
        [MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="stale fact", scope=ACME, expired_at=300)],
    )

    assert p.read(ACME, MemoryQuery(text="stale fact")).records == []


def test_invalidation_is_expire_and_reinsert_not_an_in_place_edit():
    """Finding F10 / SM-R10 §6: an in-place bound is clobbered by the next invalidation."""
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1")])

    graph.invalidate(ACME, "f1", t_invalid=300, at=310)
    graph.invalidate(ACME, "f1", t_invalid=250, at=320)

    rows = graph.rows["acme"]
    current = [row for row in rows if row.is_current_belief()]

    assert len(rows) == 3  # nothing deleted: original + two bounded copies
    assert len(current) == 1  # exactly one current belief
    assert current[0].t_invalid == 250  # tightest bound wins
    assert rows[0].t_invalid is None  # the original was never edited


def test_an_invalidated_fact_leaves_the_read_path():
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1")])
    assert p.read(ACME, MemoryQuery(text="ada acme")).records

    graph.invalidate(ACME, "f1", t_invalid=300, at=310)

    assert p.read(ACME, MemoryQuery(text="ada acme", as_of=400)).records == []


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


def test_consolidation_writes_the_fact_to_both_substrates():
    """Finding F7: the report claimed a dual write while only the vector leg was written."""
    p, vector, graph = provider()
    p.write(ACME, [event("the deploy failed on friday")])

    report = p.consolidate(ACME)

    assert report.semantic_written == 1
    assert len(graph.facts) == 1
    assert any(r.tier is MemoryTier.SEMANTIC for r in vector.rows["acme"].values())


def test_a_distilled_fact_goes_through_the_fact_path_not_the_episode_path():
    """Finding F9: a fact written as a pseudo-episode loses the shape SM-R7 needs."""
    p, _, graph = provider()
    p.write(ACME, [event("the deploy failed on friday")])

    p.consolidate(ACME)

    assert "upsert_fact" in graph.calls
    assert graph.facts[0].derived_by == "co_located.deterministic_dedup"


def test_provenance_links_the_fact_back_to_every_source_episode():
    p, _, graph = provider()
    receipt = p.write(
        ACME,
        [event("the deploy failed on friday"), event("the deploy failed on friday again")],
    )

    p.consolidate(ACME)

    assert sorted(graph.facts[0].source_episode_ids) == sorted(receipt.event_ids)


def test_near_duplicate_episodes_merge_into_one_fact():
    p, _, _ = provider()
    p.write(ACME, [event("the deploy failed on friday"), event("the deploy failed on friday again")])

    report = p.consolidate(ACME)

    assert report.episodic_scanned == 2
    assert report.semantic_written == 1
    assert report.duplicates_merged == 1


def test_distinct_episodes_stay_distinct():
    p, _, _ = provider()
    p.write(ACME, [event("the deploy failed"), event("customer asked about invoicing")])

    report = p.consolidate(ACME)

    assert report.semantic_written == 2
    assert report.duplicates_merged == 0


def test_contradictions_resolved_stays_zero_and_says_why():
    """Resolution needs extraction to know two facts disagree -- that is SM-L5's."""
    p, _, _ = provider()
    p.write(ACME, [event("anything at all")])

    assert p.consolidate(ACME).contradictions_resolved == 0


def test_consolidation_without_a_vector_substrate_fails_loudly():
    graph_only = CoLocatedMemoryProvider(graph=FakeGraph())

    with pytest.raises(MemoryProviderConfigError, match="enumerate episodes"):
        graph_only.consolidate(ACME)


# ---------------------------------------------------------------------------
# Sync / async parity
# ---------------------------------------------------------------------------


async def test_the_async_path_uses_the_async_substrate_methods():
    """Not a to_thread wrapper: an async read must not block the loop on a sync backend call."""
    p, vector, graph = provider()

    await p.awrite(ACME, [event("alpha beta")])
    await p.aread(ACME, MemoryQuery(text="alpha"))
    await p.aconsolidate(ACME)

    assert {"aupsert", "aupsert_episode", "asearch", "atraverse", "ascan", "aupsert_fact"} <= set(
        vector.calls + graph.calls
    )


async def test_sync_and_async_reads_agree():
    p, _, _ = provider()
    p.write(ACME, [event(f"alpha beta {i}", id=f"e{i}") for i in range(3)])
    query = MemoryQuery(text="alpha beta")

    sync_ids = [r.id for r in p.read(ACME, query).records]
    async_ids = [r.id for r in (await p.aread(ACME, query)).records]

    assert sync_ids == async_ids


async def test_sync_and_async_consolidation_agree():
    p, _, _ = provider()
    p.write(ACME, [event("the deploy failed on friday")])

    sync_report = p.consolidate(ACME)
    async_report = await p.aconsolidate(ACME)

    assert sync_report.episodic_scanned == async_report.episodic_scanned
    assert sync_report.duplicates_merged == async_report.duplicates_merged
