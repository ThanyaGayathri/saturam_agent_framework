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
from egpt.memory.types import (
    FUSION_KEYS,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
    Substrate,
)
from egpt.utils.dttm import now_epoch_s
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

    with pytest.raises(MemoryOperationError, match="no persistent substrate"):
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


def test_the_merge_is_a_score_sum_and_names_itself():
    """Not "fusion" -- a sum of what the legs reported, and the diagnostics say so.

    Weighted and RRF need weights, and picking weights needs a measurement nobody can run until a
    real backend exists. SM-R8 measured dense+BM25, never vector+graph. Naming the method
    `score_sum` keeps a reader from assuming a tuned ranking; SM-H2 (#132) owns the real one.
    """
    p, _, _ = provider()
    p.write(ACME, [event("alpha beta")])

    result = p.read(ACME, MemoryQuery(text="alpha"))

    assert result.fusion["method"] == "score_sum"
    # Each leg scores the same record 0.5 (one shared token of two), and the merge adds them.
    assert result.records[0].score == pytest.approx(1.0)


def test_excluding_a_wired_graph_is_refused_rather_than_answered_without_it():
    """The graph is the authority for validity, so excluding it produces a *wrong* answer.

    Refusing only when `as_of` was set guarded the query shape that exposed the bug rather than the
    precondition behind it: a plain `read(substrates=[VECTOR])` still answered from the bound-free
    projection and returned facts the graph had recorded as false.
    """
    p, _, graph = provider()
    p.write(ACME, [event("alpha beta")])

    with pytest.raises(MemoryOperationError, match="authority for bi-temporal validity"):
        p.read(ACME, MemoryQuery(text="alpha", substrates=[Substrate.VECTOR]))

    assert "traverse" not in graph.calls


def test_excluding_a_substrate_the_provider_does_not_have_is_fine():
    """The refusal is about excluding an *authority*, not about naming substrates at all."""
    vector_only = CoLocatedMemoryProvider(vector=FakeVector())
    vector_only.write(ACME, [event("alpha beta")])

    result = vector_only.read(ACME, MemoryQuery(text="alpha", substrates=[Substrate.VECTOR]))

    assert len(result.records) == 1


def test_the_limit_caps_returned_records_and_fusion_reports_what_the_probe_saw():
    """`fused` counts what the substrates returned, which is `limit` -- not the full corpus.

    A limit-honouring backend never shows the provider the whole corpus, and the provider no longer
    over-fetches: validity is filtered by the substrate before ranking, so the page it returns is
    already the page the caller gets.
    """
    p, _, _ = provider()
    p.write(ACME, [event(f"alpha item {i}") for i in range(5)])

    result = p.read(ACME, MemoryQuery(text="alpha", limit=2))

    assert len(result.records) == 2
    assert result.fusion["returned"] == 2
    assert result.fusion["fused"] == 2


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


def test_equal_scoring_records_rank_deterministically_across_providers():
    """Ties break on content, so identical content ranks identically everywhere.

    The earlier version asserted the *substrate's* order survived a tie. That read well and was
    unusable: the substrate's order was itself UUID-driven, so the same corpus ranked three
    different ways across eight cookbook runs and two providers disagreed inside one process.
    Content is the only key stable across runs, workers and deployments.
    """
    first, _, _ = provider()
    second, _, _ = provider()
    for identifier in ("zzz", "aaa", "mmm"):
        first.write(ACME, [event("alpha beta gamma", id=identifier)])
    for identifier in ("mmm", "zzz", "aaa"):  # written in a different order
        second.write(ACME, [event("alpha beta gamma", id=identifier)])

    query = MemoryQuery(text="alpha beta gamma")

    assert [r.content for r in first.read(ACME, query).records] == [r.content for r in second.read(ACME, query).records]


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


def test_a_leg_that_ignores_validity_is_contained_by_the_post_condition():
    """The ports require substrates to filter validity; the provider still re-checks.

    Written against a *non-conforming* vector leg on purpose. The conforming doubles now filter
    before ranking, so the only way to exercise the post-condition is a backend that does not --
    which is exactly the case it exists for.
    """
    p, vector, graph = provider()
    bounded = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="ada works at acme", scope=ACME, t_invalid=200)
    unbounded = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="ada works at acme", scope=ACME)

    class SloppyVector(FakeVector):
        """Returns rows regardless of validity -- a backend that dropped the predicate."""

        def search(self, scope, query, tier=None):
            from dataclasses import replace as _replace

            from tests.unit.memory._fakes import overlap

            return [_replace(r, score=overlap(query.text, r.content) or 1.0) for r in self._matching(scope)]

    sloppy = SloppyVector()
    sloppy.upsert(ACME, [unbounded])
    p.vector = sloppy
    graph.rows.setdefault("acme", []).append(bounded)

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
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1", occurred_at=100)])

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
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1", occurred_at=100)])
    assert p.read(ACME, MemoryQuery(text="ada acme", as_of=200)).records

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

    with pytest.raises(MemoryOperationError, match="enumerate episodes"):
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


# ---------------------------------------------------------------------------
# Regressions from the PR #60 review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expired,live", [(5, 5), (20, 5), (50, 10)])
def test_a_read_returns_limit_records_when_limit_valid_records_exist(expired, live):
    """Critical 3. The cap is applied after the validity filters, so a page of invalidated rows
    ranking above the live ones returned nothing.

    Invalidated rows carry the query text verbatim so they genuinely outrank the live ones, and
    the ratio is parametrised because the defect was defined by a ratio -- the old fixed x3
    over-fetch could not satisfy 50:10 at any limit.

    It now passes because the substrate filters validity *before* ranking, so the invalidated rows
    never occupy the page at all. That is the contract, not a mitigation: no ratio defeats it.
    """
    p, vector, _ = provider()
    vector.upsert(
        ACME,
        [
            MemoryRecord(
                id=f"expired-{i}",
                tier=MemoryTier.SEMANTIC,
                content="alpha beta gamma",  # exact query match -> ranks first
                scope=ACME,
                expired_at=100,
            )
            for i in range(expired)
        ]
        + [
            MemoryRecord(
                id=f"live-{i}",
                tier=MemoryTier.SEMANTIC,
                content="alpha beta gamma delta epsilon",  # weaker match -> ranks below
                scope=ACME,
            )
            for i in range(live)
        ],
    )

    result = p.read(ACME, MemoryQuery(text="alpha beta gamma", limit=live))

    assert len(result.records) == live
    assert all(r.id.startswith("live-") for r in result.records)


def test_a_default_read_hides_a_fact_the_graph_recorded_as_false():
    """`read()` and `read(as_of=now)` must agree at the same instant.

    Under SCD-2 the successor row carries a past `t_invalid` *with* `expired_at = None`, so a
    current-belief-only check let an invalidated fact render straight into a prompt.
    """
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1", occurred_at=50)])
    graph.invalidate(ACME, "f1", t_invalid=100, at=110)

    plain = p.read(ACME, MemoryQuery(text="ada acme"))
    explicit = p.read(ACME, MemoryQuery(text="ada acme", as_of=now_epoch_s()))

    assert plain.records == [], "a fact invalidated in the past must not render into a prompt"
    assert [r.id for r in plain.records] == [r.id for r in explicit.records]


def test_a_vector_only_tenant_has_a_query_that_is_both_accepted_and_correct():
    """The default read must stay usable without a graph: unbounded records pass unchanged."""
    vector_only = CoLocatedMemoryProvider(vector=FakeVector())
    vector_only.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme")])

    assert len(vector_only.read(ACME, MemoryQuery(text="ada acme")).records) == 1


def test_as_of_selects_the_instant_rather_than_enabling_the_predicate():
    """A record valid only in the past is hidden now and visible then."""
    p, vector, graph = provider()
    record = MemoryRecord(
        id="f1", tier=MemoryTier.SEMANTIC, content="ada works at acme", scope=ACME, t_valid=100, t_invalid=200
    )
    vector.upsert(ACME, [record])
    graph.rows.setdefault("acme", []).append(record)

    assert p.read(ACME, MemoryQuery(text="ada acme")).records == []  # not true now
    assert len(p.read(ACME, MemoryQuery(text="ada acme", as_of=150)).records) == 1  # was true then


def test_consolidating_twice_does_not_duplicate_the_semantic_tier():
    """Major 4. Each pass minted a fresh uuid4, so N scheduled cycles produced N copies."""
    p, vector, graph = provider()
    p.write(ACME, [event("the deploy failed on friday")])

    first = p.consolidate(ACME)
    second = p.consolidate(ACME)
    third = p.consolidate(ACME)

    semantic = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert first.semantic_written == second.semantic_written == third.semantic_written == 1
    assert len(semantic) == 1, "three passes over one episode must upsert one row, not three"
    assert len({fact.record.id for fact in graph.facts}) == 1


def test_consolidation_bounds_its_scan():
    """The cap must reach the substrate, or one pass can pull an unbounded tier into memory.

    Asserting on the request rather than the result: `"scan" in vector.calls` holds whether or not
    the scan is bounded, so removing `limit=` entirely left the suite green.
    """
    from egpt.memory.adapters.co_located import _MAX_EPISODES_PER_PASS

    p, vector, _ = provider()
    p.write(ACME, [event(f"episode number {i}") for i in range(3)])

    p.consolidate(ACME)

    # Two scans: the episodic tier to distil, then the semantic tier to find facts this pass
    # supersedes. Both bounded by the same cap.
    assert vector.scan_limits == [_MAX_EPISODES_PER_PASS, _MAX_EPISODES_PER_PASS]


def test_one_pass_consolidates_every_episode_it_scanned():
    """No cursor, no resumption protocol: one bounded pass, and it covers what it read.

    Three rounds of findings lived in the cursor *protocol* -- nothing passed it, then a time-only
    cursor could not page past co-second episodes, then a runaway guard fired on a legitimate
    no-progress batch. Draining a tier larger than one pass is SM-L1's (#122); this reports
    `complete` and stops.
    """
    p, vector, _ = provider()
    for i in range(9):
        p.write(ACME, [event(f"topic{i} unique content", id=f"e{i}")])

    report = p.consolidate(ACME)

    seen = set()
    for fact in vector.rows["acme"].values():
        if fact.tier is MemoryTier.SEMANTIC:
            seen.update(fact.provenance.get("source_episode_ids", []))
    assert {f"e{i}" for i in range(9)} == seen
    assert report.episodic_scanned == 9
    assert report.details["complete"] is True


def test_a_pass_that_hits_the_cap_says_it_is_incomplete():
    """`complete` is the one thing a scheduler needs, so it must not be hardcoded True.

    Mutation guard: with `complete=len(episodes) < cap` replaced by `complete=True`, this fails.
    """
    vector, graph = FakeVector(), FakeGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph, max_episodes_per_pass=3)
    p.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(5)])

    report = p.consolidate(ACME)

    assert report.episodic_scanned == 3
    assert report.details["complete"] is False
    # `complete=False` alone reads like ordinary pagination. It is not: nothing marks an episode
    # consumed and `scan` is oldest-first, so the tail past the cap is never consolidated at all.
    assert report.details["tail_unreachable"] is True


async def test_async_consolidation_covers_the_same_episodes():
    p, _, _ = provider()
    for i in range(5):
        p.write(ACME, [event(f"topic{i} unique content", id=f"e{i}")])

    report = await p.aconsolidate(ACME)

    assert report.episodic_scanned == 5
    assert report.details["complete"] is True


def test_consolidation_never_groups_two_users_episodes_into_one_fact():
    """A tenant-wide pass legitimately scans every user, and content alone must not merge them.

    Before this, two users saying the same thing produced **one** fact carrying both episode ids
    and attributed to whichever user won the content tiebreak -- so the other user read their
    colleague's episode back as their own. `_assert_scope_isolation` cannot see it: same tenant.
    """
    tenant = Scope(tenant_id="acme")
    alice, bob = Scope(tenant_id="acme", user_id="alice"), Scope(tenant_id="acme", user_id="bob")
    p, vector, _ = provider()
    p.write(alice, [event("salary review outcome is confidential", scope=alice, id="a1")])
    p.write(bob, [event("salary review outcome is confidential", scope=bob, id="b1")])

    report = p.consolidate(tenant)

    facts = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert report.semantic_written == 2, "one fact per user, not one shared fact"
    assert report.duplicates_merged == 0, "identical content across users is not a duplicate"
    assert {f.scope.user_id: f.provenance["source_episode_ids"] for f in facts} == {
        "alice": ["a1"],
        "bob": ["b1"],
    }


def test_a_derived_fact_is_stored_under_its_own_scope_not_the_pass_scope():
    """`upsert(scope, records)` stores *within* the call scope, so the pass scope would widen it.

    Filing alice's distilled fact under the bare tenant makes it readable by every user in it.
    """
    tenant = Scope(tenant_id="acme")
    alice, bob = Scope(tenant_id="acme", user_id="alice"), Scope(tenant_id="acme", user_id="bob")
    p, vector, _ = provider()
    p.write(alice, [event("alice prefers async standups", scope=alice, id="a1")])

    p.consolidate(tenant)

    assert vector.search(bob, MemoryQuery(text="alice prefers async standups")) == []
    assert len(vector.search(alice, MemoryQuery(text="alice prefers async standups"))) == 2


def test_an_empty_substrate_set_is_refused_on_every_wiring():
    """Not "search nothing" -- a request no provider could answer, so never a silent empty."""
    for wiring in ({"vector": FakeVector(), "graph": FakeGraph()}, {"vector": FakeVector()}, {"graph": FakeGraph()}):
        p = CoLocatedMemoryProvider(**wiring)

        with pytest.raises(MemoryOperationError, match="substrates is empty"):
            p.read(ACME, MemoryQuery(text="x", substrates=[]))


def test_a_point_in_time_read_without_the_graph_is_refused_not_answered():
    """Major 7. The vector projection has no bounds, so it would return invalidated facts as true."""
    vector_only = CoLocatedMemoryProvider(vector=FakeVector())

    with pytest.raises(MemoryOperationError, match="point-in-time"):
        vector_only.read(ACME, MemoryQuery(text="anything", as_of=400))


def test_excluding_the_graph_from_a_point_in_time_read_is_refused():
    """The repro from the review: substrates=[VECTOR] returned an invalidated fact as current."""
    p, _, _ = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1")])

    with pytest.raises(MemoryOperationError, match="point-in-time"):
        p.read(ACME, MemoryQuery(text="ada acme", as_of=400, substrates=[Substrate.VECTOR]))


def test_a_graph_only_provider_refuses_a_procedural_write_instead_of_discarding_it():
    """Major 8. The write vanished -- vector branch skipped, graph branch not routed -- and the
    receipt said accepted=1."""
    graph_only = CoLocatedMemoryProvider(graph=FakeGraph())

    with pytest.raises(MemoryOperationError, match="No wired substrate serves"):
        graph_only.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm")])


def test_a_graph_only_provider_still_accepts_an_episodic_write():
    """The control: the refusal above is tier-specific, not a blanket graph-only failure."""
    graph_only = CoLocatedMemoryProvider(graph=FakeGraph())

    assert graph_only.write(ACME, [event("something happened")]).accepted == 1


def test_a_partial_write_raises_with_the_stamped_ids_so_a_retry_stays_idempotent():
    """Major 9. The exception propagated with no receipt, so a retry minted fresh ids (breaking F1)."""
    from egpt.memory.adapters.co_located import PartialWriteError

    p, vector, graph = provider()

    def explode(scope, event_):
        raise RuntimeError("graph down")

    graph.upsert_episode = explode  # type: ignore[method-assign]

    with pytest.raises(PartialWriteError) as exc:
        p.write(ACME, [event("w1"), event("w2")])

    ids = exc.value.receipt.event_ids
    assert len(ids) == 2
    # Retrying with those ids reuses the rows the vector leg already committed.
    graph.upsert_episode = FakeGraph.upsert_episode.__get__(graph)  # type: ignore[method-assign]
    p.write(ACME, [event("w1", id=ids[0]), event("w2", id=ids[1])])
    assert set(vector.rows["acme"]) == set(ids)


def test_a_returned_record_does_not_alias_the_substrates_stored_row():
    """Major 10. A consumer annotating a result was writing into the backend."""
    p, vector, _ = provider()
    receipt = p.write(ACME, [event("alpha beta")])

    returned = p.read(ACME, MemoryQuery(text="alpha")).records[0]
    returned.metadata["seen_by"] = "agent-1"
    returned.provenance["touched"] = True

    stored = vector.rows["acme"][receipt.event_ids[0]]
    assert stored.metadata == {}
    assert stored.provenance == {}


def test_a_returned_record_does_not_alias_the_stores_nested_values():
    """One level of copying is not enough -- `dict()` left nested values shared.

    The realistic instance is a consumer appending to `provenance["source_episode_ids"]`, the
    exact shape consolidation writes and the one FactWrite documents as load-bearing.
    """
    p, vector, _ = provider()
    vector.upsert(
        ACME,
        [
            MemoryRecord(
                id="r1",
                tier=MemoryTier.SEMANTIC,
                content="alpha",
                scope=ACME,
                provenance={"source_episode_ids": ["e1"]},
                metadata={"tags": ["a"]},
            )
        ],
    )

    returned = p.read(ACME, MemoryQuery(text="alpha")).records[0]
    returned.provenance["source_episode_ids"].append("INJECTED")
    returned.metadata["tags"].append("INJECTED")

    stored = vector.rows["acme"]["r1"]
    assert stored.provenance == {"source_episode_ids": ["e1"]}
    assert stored.metadata == {"tags": ["a"]}


def test_a_callers_topics_list_is_copied_on_write():
    """The same aliasing on the write path: metadata was defensive, topics was not."""
    p, vector, _ = provider()
    topics = ["billing"]

    receipt = p.write(ACME, [event("alpha", topics=topics)])
    topics.append("mutated-after-write")

    assert vector.rows["acme"][receipt.event_ids[0]].topics == ["billing"]


def test_the_fusion_payload_carries_every_contract_key():
    """Major 13. Adapters disagreed on keys, so a provider-agnostic consumer KeyError'd."""
    p, _, _ = provider()
    p.write(ACME, [event("alpha")])

    assert set(FUSION_KEYS) <= set(p.read(ACME, MemoryQuery(text="alpha")).fusion)


def test_reading_the_working_tier_is_empty_while_writing_it_is_refused():
    """One rule across adapters: reads report absence, writes refuse it.

    The same logical request used to get three answers -- `co_located.read_tier(WORKING)` refused,
    `seed.read_tier(WORKING)` returned `[]`, and `co_located.read(tiers=[WORKING])` returned an
    empty slice -- so no caller could write one handler. Writes still refuse, because writing where
    nothing will store it loses data while reading there simply finds nothing.
    """
    p, _, _ = provider()

    assert p.read_tier(ACME, MemoryTier.WORKING, MemoryQuery(text="q")) == []
    assert p.read(ACME, MemoryQuery(text="q", tiers=[MemoryTier.WORKING])).records == []

    with pytest.raises(MemoryOperationError, match="no persistent substrate"):
        p.write_tier(ACME, MemoryTier.WORKING, [event("scratchpad note")])


def test_an_injected_substrate_that_misses_the_port_fails_at_build():
    """Both ports are runtime_checkable; the check costs one isinstance and names what is missing."""

    class NotASubstrate:
        def upsert(self, scope, records): ...

    with pytest.raises(TypeError, match="does not satisfy VectorSubstrate"):
        build_memory_provider(
            MemoryProviderConfig(provider="co_located", options={"vector_substrate": NotASubstrate()})
        )


def test_duplicate_ids_in_one_batch_are_refused_before_anything_is_written():
    """Major 13. The vector leg upserts by id (last wins) and the graph leg appends, so the two
    substrates ended up holding different content under one id -- which then collides in fusion's
    dedup -- and the receipt still said accepted=2.
    """
    p, vector, graph = provider()

    with pytest.raises(MemoryOperationError, match="Duplicate event id"):
        p.write(ACME, [event("first", id="dup"), event("second", id="dup")])

    assert vector.rows.get("acme", {}) == {}
    assert graph.rows.get("acme", []) == []


def test_the_graph_leg_does_not_alias_the_callers_lists():
    """`replace()` copies references, so the event handed to the graph still aliased the caller."""
    p, _, graph = provider()
    topics = ["billing"]

    p.write(ACME, [event("alpha", topics=topics)])
    topics.append("MUTATED-AFTER-WRITE")

    assert graph.rows["acme"][0].topics == ["billing"]


def test_a_semantic_write_reaches_the_graph_through_the_fact_door():
    """Major 5. F9's failure class on the write path: a fact stored as a flat pseudo-episode.

    Deviation D3 added `upsert_fact` for exactly this and only wired it inside `consolidate`.
    """
    p, _, graph = provider()

    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme")])

    assert "upsert_fact" in graph.calls
    assert "upsert_episode" not in graph.calls
    assert graph.facts[0].derived_by == "co_located.direct_write"


def test_an_episodic_write_still_uses_the_episode_door():
    p, _, graph = provider()

    p.write(ACME, [event("something happened")])

    assert graph.calls == ["upsert_episode"]


def test_the_vector_leg_honours_tier_routing_like_the_graph_leg_does():
    """Latent until SM-C2 adds a graph-only tier, at which point it becomes a silent extra write."""
    import egpt.memory.adapters.co_located as module
    from egpt.memory import tiers as tiers_module

    p, vector, graph = provider()
    original = dict(tiers_module.SUBSTRATES_BY_TIER)
    tiers_module.SUBSTRATES_BY_TIER[MemoryTier.SEMANTIC] = frozenset({"graph"})
    try:
        p.write_tier(ACME, MemoryTier.SEMANTIC, [event("graph only fact")])
        assert vector.rows.get("acme", {}) == {}, "a graph-only tier must not reach the vector leg"
    finally:
        tiers_module.SUBSTRATES_BY_TIER.clear()
        tiers_module.SUBSTRATES_BY_TIER.update(original)
        assert module is not None  # keep the import meaningful for readers


def test_consolidation_does_not_restamp_created_at_on_a_stable_fact():
    """created_at is "when the system recorded it" and is the key scan's watermark sorts on."""
    p, vector, _ = provider()
    receipt = p.write(ACME, [event("the deploy failed")])
    vector.rows["acme"][receipt.event_ids[0]].created_at = 1000

    p.consolidate(ACME)
    fact = next(r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC)

    assert fact.created_at == 1000


# ---------------------------------------------------------------------------
# Round-3 review regressions
# ---------------------------------------------------------------------------


def test_a_zero_limit_does_not_over_fetch():
    """Asserts what the substrate was *asked* for, not what came back.

    A result-only assertion here is `[] == []`: `_assemble` caps at `query.limit`, so the result is
    empty whatever the probe requested. This test was also lost entirely in a round-2 block
    replacement and its follow-up edit silently matched nothing -- hence asserting the recorder.
    """
    p, vector, _ = provider()
    p.write(ACME, [event("alpha")])

    p.read(ACME, MemoryQuery(text="alpha", limit=0))

    assert vector.search_limits == [0], "limit=0 must ask the substrate for nothing"


def test_a_record_from_another_tenant_is_refused_not_returned():
    """Defence in depth: the seam refuses to take a substrate's tenant predicate on faith.

    Needs a leaky backend by construction -- the conforming double scopes correctly, which is why
    a post-condition rather than a test is what protects against the non-conforming one.
    """
    p, _, _ = provider()

    class LeakyVector(FakeVector):
        """Drops the tenant predicate, as a bad migration or a hand-written query would."""

        def _matching(self, scope):
            return [r for bucket in self.rows.values() for r in bucket.values()]

    leaky = LeakyVector()
    leaky.upsert(OTHER, [MemoryRecord(id="foreign", tier=MemoryTier.SEMANTIC, content="globex secret", scope=OTHER)])
    p.vector = leaky

    with pytest.raises(MemoryOperationError, match="dropped a scope predicate"):
        p.read(ACME, MemoryQuery(text="globex secret"))


def test_consolidation_refuses_a_leaked_record_rather_than_persisting_it():
    """Critical (r4). The read guard alone was not enough: `_distil` stamps the *caller's* scope,
    so a leaked row was written back as a genuine row under the caller's tenant -- and then passed
    the read guard forever after."""
    p, _, _ = provider()

    class LeakyVector(FakeVector):
        def _matching(self, scope):
            return [r for bucket in self.rows.values() for r in bucket.values()]

    leaky = LeakyVector()
    leaky.upsert(
        OTHER,
        [MemoryRecord(id="foreign", tier=MemoryTier.EPISODIC, content="acme deployment secret", scope=OTHER)],
    )
    p.vector = leaky

    with pytest.raises(MemoryOperationError, match="dropped a scope predicate"):
        p.consolidate(ACME)


def test_an_event_whose_scope_differs_from_the_write_scope_is_refused():
    """MemoryEvent.scope was required and read by nothing, so the call scope silently won."""
    p, _, _ = provider()
    foreign = MemoryEvent(content="hello", scope=OTHER)

    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        p.write(ACME, [foreign])


def test_a_repeat_episode_write_upserts_rather_than_duplicating():
    """PartialWriteError tells callers to retry with the stamped ids; that only works if the graph
    upserts by id, which the port now states and the doubles now honour."""
    p, vector, graph = provider()

    p.write(ACME, [event("first version", id="dup")])
    p.write(ACME, [event("second version", id="dup")])

    assert [r.content for r in graph.rows["acme"]] == ["second version"]
    assert [r.content for r in vector.rows["acme"].values()] == ["second version"]


# ---------------------------------------------------------------------------
# Round-6 review: invariants the suite documented but did not test
# ---------------------------------------------------------------------------
#
# Each of the first three survived mutation with all 364 tests green. They are load-bearing and
# were carried into this module by the reset without their coverage.


def test_grouping_is_decided_by_content_not_by_scan_order():
    """The round-4 determinism fix: the cookbook's byte-identical hash depends on it.

    `scan` orders on `(created_at, id)`, and ids are UUIDs in ordinary use, so scan order varies
    between two stores holding the same corpus. If grouping followed it, which episode heads a
    group -- and so the fact's content and its `uuid5` id -- would vary with it.

    Driven through `_distil` directly with two orderings of one list, because going through
    `write` cannot produce differing scan orders: the double sorts them back into id order.
    """
    p, _, _ = provider()
    episodes = [
        MemoryRecord(id=i, tier=MemoryTier.EPISODIC, content=c, scope=ACME, created_at=t)
        for i, c, t in [
            ("z9", "the server in rack 12 is down", 300),
            ("a1", "the server in rack 12 is down now", 100),
            ("m5", "billing runs on friday", 200),
        ]
    ]

    forward, _ = p._distil(ACME, episodes)
    backward, _ = p._distil(ACME, list(reversed(episodes)))

    assert [f.record.id for f in forward] == [f.record.id for f in backward]
    assert [f.record.content for f in forward] == [f.record.content for f in backward]
    assert [f.source_episode_ids for f in forward] == [f.source_episode_ids for f in backward]


def test_consolidation_skips_an_episode_the_graph_recorded_as_false():
    """`scan` reads the vector projection, which is stale about validity.

    Without the filter an invalidated episode was distilled into a brand-new semantic fact carrying
    **no bounds at all** -- unsuppressable afterwards by any read, at any limit, with any `as_of`.
    Strictly worse than the read-path version, which at least stops when the corpus shrinks.
    """
    p, _, graph = provider()
    p.write(ACME, [event("the server is in rack 12", id="ep1", occurred_at=50)])
    graph.invalidate(ACME, "ep1", t_invalid=100, at=110)

    report = p.consolidate(ACME)

    assert report.episodic_scanned == 1
    assert report.details["episodes_skipped_as_invalid"] == 1
    assert report.semantic_written == 0
    assert [r for r in p.read(ACME, MemoryQuery(text="server rack 12")).records if r.tier is MemoryTier.SEMANTIC] == []


def test_a_duplicate_id_within_one_leg_folds_rather_than_double_counting():
    """One leg contributes one score per id, so a repeated hit cannot buy rank.

    A backend returning the same id twice -- a join fan-out, a union over revisions -- otherwise
    doubles that record's score and outranks genuinely better matches.
    """
    p, vector, _ = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha beta", id="dup")])
    single = p.read(ACME, MemoryQuery(text="alpha")).records[0].score

    original = vector.search
    vector.search = lambda scope, query, tier=None: original(scope, query, tier) * 2  # type: ignore[assignment]
    doubled = p.read(ACME, MemoryQuery(text="alpha")).records[0].score

    assert doubled == single, "a repeated id within one leg must not accumulate score"


def test_the_read_path_asks_the_graph_for_bounds_it_could_not_have_ranked():
    """Asserting on the *request*: a result-only assertion cannot see a missing reconciliation."""
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1", occurred_at=100)])

    p.read(ACME, MemoryQuery(text="ada acme"))

    assert graph.bounds_requests == [["f1"]]


def test_a_substrate_returning_an_unrequested_tier_is_refused():
    """The provider re-checks scope and validity and used to trust tier entirely."""
    p, vector, _ = provider()
    p.write(ACME, [event("alpha", id="ep")])

    class TierIgnoring(FakeVector):
        def search(self, scope, query, tier=None):
            return [r for r in self._matching(scope)]

    p.vector = TierIgnoring()
    p.vector.rows = vector.rows

    with pytest.raises(MemoryOperationError, match="did not ask for"):
        p.read_tier(ACME, MemoryTier.SEMANTIC, MemoryQuery(text="alpha"))


def test_consolidation_honours_tier_routing_like_the_write_path_does():
    """`write` filters by SUBSTRATES_BY_TIER; consolidate wrote wherever a substrate was wired."""
    import egpt.memory.tiers as tiers_module

    p, vector, graph = provider()
    p.write(ACME, [event("alpha beta gamma", id="ep")])
    original = tiers_module.SUBSTRATES_BY_TIER[MemoryTier.SEMANTIC]
    tiers_module.SUBSTRATES_BY_TIER[MemoryTier.SEMANTIC] = frozenset({"graph"})
    try:
        p.consolidate(ACME)
        semantic_in_vector = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
        assert semantic_in_vector == [], "graph-only SEMANTIC must not reach the vector substrate"
    finally:
        tiers_module.SUBSTRATES_BY_TIER[MemoryTier.SEMANTIC] = original


def test_a_substrate_refusal_is_not_reported_as_a_partial_write():
    """A per-call refusal wrapped as PartialWriteError sends the caller down a retry that cannot win."""

    class RefusingGraph(FakeGraph):
        def upsert_episode(self, scope, event):
            raise MemoryOperationError("this backend refuses episodes", field_name="tier")

    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=RefusingGraph())

    with pytest.raises(MemoryOperationError, match="refuses episodes"):
        p.write(ACME, [event("alpha")])


def test_a_scope_narrower_than_the_tenant_is_checked_too():
    """The guard compared one of five slots while the port requires all five."""
    alice = Scope(tenant_id="acme", user_id="alice")
    bob = Scope(tenant_id="acme", user_id="bob")

    class UserBlindVector(FakeVector):
        def _matching(self, scope):
            return [r for r in self._bucket(scope).values() if r.scope.tenant_id == scope.tenant_id]

    p = CoLocatedMemoryProvider(vector=UserBlindVector())
    p.write_tier(alice, MemoryTier.SEMANTIC, [event("ada salary is 200k", scope=alice, id="a1")])

    with pytest.raises(MemoryOperationError, match="dropped a scope predicate"):
        p.read(bob, MemoryQuery(text="ada salary"))
