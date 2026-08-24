"""The co-located provider: fusion, write fan-out, and bi-temporal reads (SM-C1, phase 4).

Every finding the SM-R11 prototype recorded is a test here, because each was invisible until it
was wrong: one id across substrates (F1), point-in-time on the fused set (F2), identical tier
resolution (F3), two-sided validity (F6), dual-substrate consolidation (F7), and SCD-2
invalidation rather than in-place mutation (F10).
"""

from dataclasses import replace

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.config import (
    MemoryProviderConfig,
    MemoryProviderConfigError,
    build_memory_provider,
    registered_providers,
)
from egpt.memory.consolidation.extract import FactExtractionReport
from egpt.memory.graph.relations import ExtractedRelation, RelationExtractionPipeline
from egpt.memory.graph.temporal import RuleBasedValidTimeExtractor, ValidTimeSpan
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    FUSION_KEYS,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    PartialWriteError,
    Scope,
    Substrate,
)
from egpt.utils.dttm import now_epoch_s
from tests.unit.memory._fakes import FakeGraph, FakeVector, _yield, assert_ports_satisfied

ACME = Scope(tenant_id="acme", user_id="u1")
OTHER = Scope(tenant_id="globex", user_id="u1")


def provider(**config_kwargs):
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located", **config_kwargs)
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


@pytest.fixture
def graph_only_semantic():
    """Re-route the semantic tier to the graph alone, for the duration of one test.

    The provider reads `TIER_POLICY` on every routing decision, so this is how a test asks "what
    happens when a tier stops being dual-substrate" without waiting for a ticket to make one so.
    Patching the table rather than the adapter is deliberate: a path that consults the table is
    exercised, and a path that hard-codes a tier is not, which is the whole assertion.

    **The four derived constants do not follow.** `PERSISTENT_TIERS`, `DEFAULT_WRITE_TIER`,
    `CONSOLIDATION_SOURCE` and `CONSOLIDATION_TARGET` are computed once at import, so they keep the
    real table's answers here. Nothing under test reads them for *substrate* routing -- that goes
    through `substrates_for_tier`, which reads the table live -- but a test that needs a rerouted
    tier to change one of those four has to patch it as well.
    """
    from dataclasses import replace as replace_row

    from egpt.memory import tiers as tiers_module

    original = tiers_module.TIER_POLICY[MemoryTier.SEMANTIC]
    tiers_module.TIER_POLICY[MemoryTier.SEMANTIC] = replace_row(original, substrates=frozenset({"graph"}))
    try:
        yield
    finally:
        tiers_module.TIER_POLICY[MemoryTier.SEMANTIC] = original


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
    """Deviation D7: ties break on (content, tier, id), never on arrival order.

    Asserted on **ids**, which are what actually differ between the two stores. The previous
    version compared contents of three records written with identical content, so both sides were
    the same list under any ordering and a score-only sort passed the whole suite.
    """
    query = MemoryQuery(text="alpha")
    first, _, _ = provider()
    second, _, _ = provider()
    for eid in ("mmm", "zzz", "aaa"):
        first.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha", id=eid)])
    for eid in ("zzz", "aaa", "mmm"):  # same corpus, different arrival order
        second.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha", id=eid)])

    assert [r.id for r in first.read(ACME, query).records] == ["aaa", "mmm", "zzz"]
    assert [r.id for r in second.read(ACME, query).records] == ["aaa", "mmm", "zzz"]


def test_a_read_never_crosses_tenants():
    p, _, _ = provider()
    p.write(ACME, [event("acme deployment secret")])
    p.write(OTHER, [event("globex deployment secret", scope=OTHER)])

    leaked = [r for r in p.read(OTHER, MemoryQuery(text="deployment")).records if r.content.startswith("acme")]

    assert leaked == []


def test_consolidation_never_crosses_tenants():  # noqa: D401 - see the leaked-record test
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


def test_consolidations_vector_leg_carries_the_same_confidence_and_derived_by_the_graph_leg_does():
    """SM-S1 (#118): a fact retrieved through `search` must carry what `recall_facts` already
    surfaces from the graph leg, or the same fact reads as two different shapes depending which
    substrate answered."""
    p, vector, graph = provider()
    p.write(ACME, [event("the deploy failed on friday")])

    p.consolidate(ACME)

    stored = next(r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC)
    assert stored.metadata["derived_by"] == graph.facts[0].derived_by == "co_located.deterministic_dedup"
    assert stored.metadata["confidence"] == graph.facts[0].confidence


def test_a_distilled_fact_goes_through_the_fact_path_not_the_episode_path():
    """Finding F9: a fact written as a pseudo-episode loses the shape SM-R7 needs."""
    p, _, graph = provider()
    p.write(ACME, [event("the deploy failed on friday")])

    p.consolidate(ACME)

    assert "upsert_facts" in graph.calls
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


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_facts_that_differ_only_by_punctuation_still_merge(half, monkeypatch):
    """SM-L4 (#125): `_distil`'s raw ``text.lower().split()`` tokenizer treats "failed!" and
    "failed" as unrelated tokens, so two episodes that are the same fact once normalized would
    form two Jaccard groups on their own. ``consolidation.dedup.merge_near_duplicate_facts`` folds
    them after ``_distil`` runs -- forced apart here (rather than relying on real near-punctuation
    content) by monkeypatching ``_DUPLICATE_THRESHOLD`` to 1.1, so only that later stage can be
    responsible for the merge this test asserts. Parametrised over both halves because the fold
    is wired into `consolidate` and `aconsolidate` separately, and a divergence between them would
    otherwise only surface in the one this test happened not to run.
    """
    import egpt.memory.adapters.co_located as co_located

    monkeypatch.setattr(co_located, "_DUPLICATE_THRESHOLD", 1.1)
    p, vector, graph = provider()
    p.write(ACME, [event("the deploy failed!", id="a"), event("the deploy failed", id="b")])

    report = p.consolidate(ACME) if half == "sync" else await p.aconsolidate(ACME)

    assert report.semantic_written == 1
    # `_distil` groups nothing here (threshold forced above 1.0): the merge is entirely this
    # stage's, so it must show up as `near_duplicate_facts_folded`, not `duplicates_merged` --
    # the two are different units (facts folded vs. episodes absorbed) and must not be conflated.
    assert report.duplicates_merged == 0
    assert report.near_duplicate_facts_folded == 1
    (fact,) = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert sorted(fact.provenance["source_episode_ids"]) == ["a", "b"]
    assert sorted(graph.facts[0].source_episode_ids) == ["a", "b"]


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_near_duplicate_facts_from_different_users_never_merge(half, monkeypatch):
    """The fold must respect scope isolation exactly like `_distil`'s own grouping does.

    Parametrised like the adjacent punctuation test: the fold is wired into `consolidate` and
    `aconsolidate` independently, so only exercising one half leaves the other unchecked.
    """
    import egpt.memory.adapters.co_located as co_located

    monkeypatch.setattr(co_located, "_DUPLICATE_THRESHOLD", 1.1)
    p, vector, _ = provider()
    other = Scope(tenant_id="acme", user_id="u2")
    p.write(ACME, [event("the deploy failed!", id="a")])
    p.write(other, [event("the deploy failed", id="b", scope=other)])

    tenant = Scope(tenant_id="acme")
    report = p.consolidate(tenant) if half == "sync" else await p.aconsolidate(tenant)

    assert report.semantic_written == 2
    assert report.duplicates_merged == 0
    assert report.near_duplicate_facts_folded == 0


def test_contradictions_resolved_stays_zero_with_no_relation_pipeline_wired():
    """No `relation_pipeline` means no graph-edge contradiction detection ever runs -- SM-L5
    (#126) surfaces `relation_pipeline`'s own count here, but has nothing to surface when the
    pipeline was never configured (`NO_RELATIONS`, not a dict)."""
    p, _, _ = provider()
    p.write(ACME, [event("anything at all")])

    assert p.consolidate(ACME).contradictions_resolved == 0


class _RelationStub:
    """Returns one relation per episode, keyed on content -- same shape as
    ``test_graph_relations.py``'s ``StubExtractor``, kept local since only two tests in this file
    use it (review finding, PR #126 round 1: move to ``conftest.py`` once a *second* file needs a
    stub ``RelationExtractor``, not before)."""

    def __init__(self, by_content):
        self.by_content = by_content

    def extract(self, episode):
        return list(self.by_content.get(episode.content, ()))

    async def aextract(self, episode):
        return self.extract(episode)


class _FixedValidTime:
    """A :class:`ValidTimeExtractor` double that returns an explicit, pre-assigned span per
    episode content, bypassing date-prose parsing entirely (review finding, PR #126 round 2):
    coupling a contradiction-detection test's pass/fail to ``RuleBasedValidTimeExtractor``
    correctly parsing "since Jan 2024" would make a date-parsing regression and a
    `_contradicted_within` regression indistinguishable from the same failing assertion."""

    def __init__(self, by_content):
        self.by_content = by_content

    def extract(self, content, ingest_time=None):
        return self.by_content[content]

    async def aextract(self, content, ingest_time=None):
        return self.extract(content, ingest_time)


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_contradictions_resolved_reflects_a_real_graph_edge_invalidation(half):
    """SM-L5 (#126): `ConsolidationReport.contradictions_resolved` used to stay hard-coded at 0
    even once a wired `relation_pipeline` (SM-G3/SM-G5) genuinely invalidated an edge -- the count
    existed on `RelationExtractionReport` but `_relations`/`_arelations` discarded it, keeping only
    `.details`. Two consolidation passes over contradicting facts must now report a nonzero count,
    end to end through `consolidate`/`aconsolidate`, not just through `RelationExtractionPipeline`
    directly (already covered in `test_graph_relations.py`).

    **Cross-pass only** (review finding, PR #126 round 1): the second `consolidate` call's batch
    holds exactly one new candidate, so this exercises `_contradictions_for` against the graph's
    pre-existing edge -- SM-G5 behaviour, unchanged by this ticket. It does not exercise
    `_contradicted_within` (SM-L5's own same-pass addition), which exits immediately on a
    one-candidate batch. See
    :func:`test_contradictions_resolved_counts_a_same_pass_invalidation_too` for that path."""
    extractor = _RelationStub(
        by_content={
            "Ada works at Acme since Jan 2024": [
                ExtractedRelation(source="Ada", relation="works_at", target="Acme Corp")
            ],
            "Ada works at Beta since Jan 2025": [
                ExtractedRelation(source="Ada", relation="works_at", target="Beta Inc")
            ],
        }
    )
    vector, graph = FakeVector(), FakeGraph()
    relation_pipeline = RelationExtractionPipeline(
        graph=graph, extractor=extractor, valid_time_extractor=RuleBasedValidTimeExtractor()
    )
    p = CoLocatedMemoryProvider(
        vector=vector,
        graph=graph,
        config=MemoryProviderConfig(provider="co_located"),
        relation_pipeline=relation_pipeline,
    )

    # Explicit, lexicographically-ordered ids: `_advance_cursor` orders `(created_at, id)`, and
    # both writes land within the same wall-clock second under a fast test run -- a random
    # per-event id (the default `event()` gives none, so the provider mints a UUID) would leave
    # the cursor's tiebreak a coin flip on whether the second write is even visible to the second
    # consolidate call.
    if half == "sync":
        p.write(ACME, [event("Ada works at Acme since Jan 2024", id="e1")])
        p.consolidate(ACME)
        p.write(ACME, [event("Ada works at Beta since Jan 2025", id="e2")])
        report = p.consolidate(ACME)
    else:
        await p.awrite(ACME, [event("Ada works at Acme since Jan 2024", id="e1")])
        await p.aconsolidate(ACME)
        await p.awrite(ACME, [event("Ada works at Beta since Jan 2025", id="e2")])
        report = await p.aconsolidate(ACME)

    assert report.contradictions_resolved == 1
    assert report.details["relations"]["edges_invalidated"] == 1


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_contradictions_resolved_counts_a_same_pass_invalidation_too(half):
    """Review finding (PR #126, round 1): the previous test writes and consolidates in two
    separate passes, so the second `consolidate` call's batch holds exactly one new candidate --
    `_contradictions_for` (cross-pass, vs. the graph's pre-existing edge) is what fires, and
    `_contradicted_within` (same-pass, SM-L5's own addition) exits immediately on a one-candidate
    batch without ever comparing anything. A regression that completely disabled
    `_contradicted_within` would not have failed that test.

    Both contradicting episodes land in **one** `write()`/`awrite()` call here, so one
    `consolidate`/`aconsolidate` pass hands `relation_pipeline.run`/`arun` a batch with both
    candidates -- the same shape `test_graph_relations.py`'s
    `test_two_same_pass_candidates_contradicting_each_other_invalidate_the_earlier_one` already
    covers at the pipeline level, exercised here end to end through the provider instead.

    Valid times are explicit (`_FixedValidTime`), not parsed from content (review finding, PR
    #126 round 2): the assertion below must be sensitive to `_contradicted_within`, not to
    whether `RuleBasedValidTimeExtractor` still parses a given date phrase.
    """
    extractor = _RelationStub(
        by_content={
            "Ada works at Acme": [ExtractedRelation(source="Ada", relation="works_at", target="Acme Corp")],
            "Ada works at Beta": [ExtractedRelation(source="Ada", relation="works_at", target="Beta Inc")],
        }
    )
    valid_time = _FixedValidTime(
        by_content={
            "Ada works at Acme": ValidTimeSpan(t_valid=100),
            "Ada works at Beta": ValidTimeSpan(t_valid=200),
        }
    )
    vector, graph = FakeVector(), FakeGraph()
    relation_pipeline = RelationExtractionPipeline(graph=graph, extractor=extractor, valid_time_extractor=valid_time)
    p = CoLocatedMemoryProvider(
        vector=vector,
        graph=graph,
        config=MemoryProviderConfig(provider="co_located"),
        relation_pipeline=relation_pipeline,
    )
    episodes = [
        event("Ada works at Acme", id="e1"),
        event("Ada works at Beta", id="e2"),
    ]

    if half == "sync":
        p.write(ACME, episodes)
        report = p.consolidate(ACME)
    else:
        await p.awrite(ACME, episodes)
        report = await p.aconsolidate(ACME)

    assert report.contradictions_resolved == 1
    assert report.details["relations"]["edges_invalidated"] == 1


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

    assert {"aupsert", "aupsert_episodes", "asearch", "atraverse", "ascan", "aupsert_facts"} <= set(
        vector.calls + graph.calls
    )


async def test_sync_and_async_reads_agree():
    p, _, _ = provider()
    p.write(ACME, [event(f"alpha beta {i}", id=f"e{i}") for i in range(3)])
    query = MemoryQuery(text="alpha beta")

    sync_ids = [r.id for r in p.read(ACME, query).records]
    async_ids = [r.id for r in (await p.aread(ACME, query)).records]

    assert sync_ids == async_ids


async def test_aconsolidations_vector_leg_carries_the_same_confidence_and_derived_by_the_graph_leg_does():
    """The async half of `test_consolidations_vector_leg_carries_...` -- the two call sites in
    `aconsolidate` and `consolidate` must make the same decision (SM-S1 #118)."""
    p, vector, graph = provider()
    await p.awrite(ACME, [event("the deploy failed on friday")])

    await p.aconsolidate(ACME)

    stored = next(r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC)
    assert stored.metadata["derived_by"] == graph.facts[0].derived_by == "co_located.deterministic_dedup"
    assert stored.metadata["confidence"] == graph.facts[0].confidence


async def test_sync_and_async_consolidation_agree():
    """Two independent instances, not one -- SM-L1's (#122) cursor advances after the sync call,
    so a second call on the *same* instance would scan nothing new and this would stop comparing
    what it claims to."""
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    event_ = event("the deploy failed on friday")
    CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).write(ACME, [event_])

    sync_report = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).consolidate(ACME)
    async_report = await CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).aconsolidate(ACME)

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
    """Major 4. Each pass minted a fresh uuid4, so N scheduled cycles produced N copies.

    Three independent provider instances over the same substrates -- not three calls on one --
    because SM-L1's (#122) internal cursor would otherwise have the second and third calls scan
    nothing new and this test would stop exercising the id-derivation fix. Three fresh instances
    is the scenario that fix actually guards: no shared cursor state (a worker restart, or several
    replicas), so every one independently rescans the same window.
    """
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    event_ = event("the deploy failed on friday")
    CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).write(ACME, [event_])

    first = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).consolidate(ACME)
    second = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).consolidate(ACME)
    third = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config).consolidate(ACME)

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

    # One scan: the episodic tier. SM-C1's pass distils and writes; it does not read the semantic
    # tier back, because retiring what it subsumes is SM-L3's (#124).
    assert vector.scan_limits == [_MAX_EPISODES_PER_PASS]
    # A first call on a fresh instance has no cursor yet.
    assert vector.scan_afters == [None]


def test_one_pass_consolidates_every_episode_it_scanned():
    """One bounded pass, and it covers what it read.

    Three rounds of findings lived in the cursor *protocol* SM-L1 (#122) eventually shipped --
    nothing passed it, then a time-only cursor could not page past co-second episodes, then a
    runaway guard fired on a legitimate no-progress batch. A tier under the cap still completes
    in one call; draining a tier *larger* than one pass is covered separately, by
    `test_a_second_call_on_the_same_instance_resumes_from_the_cursor_and_drains_the_tail`.
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
    # `complete=False` alone reads like ordinary pagination. `tail_pending` is the same signal
    # under its post-SM-L1 name -- the tail is reachable (a second call on this instance resumes
    # from the cursor), just not read by this pass.
    assert report.details["tail_pending"] is True


def test_a_second_call_on_the_same_instance_resumes_from_the_cursor_and_drains_the_tail():
    """SM-L1 (#122): the cursor is internal, so the caller does nothing but call `consolidate`
    again -- no `since`, no page token, matching the interface's "no cursor in this signature".

    Mutation guard: with `_advance_cursor` made a no-op, the second call re-reads the same first
    3 episodes, `second.episodic_scanned` stays 3, and the assertion on the union of source ids
    fails because e3/e4 never appear.
    """
    vector, graph = FakeVector(), FakeGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph, max_episodes_per_pass=3)
    p.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(5)])

    first = p.consolidate(ACME)
    second = p.consolidate(ACME)

    assert first.episodic_scanned == 3
    assert first.details["complete"] is False
    assert second.episodic_scanned == 2
    assert second.details["complete"] is True
    seen = set()
    for fact in vector.rows["acme"].values():
        if fact.tier is MemoryTier.SEMANTIC:
            seen.update(fact.provenance.get("source_episode_ids", []))
    assert {f"e{i}" for i in range(5)} == seen


def test_a_third_call_with_nothing_new_written_scans_nothing():
    """Once drained, the cursor sits at the tier's last row -- a call that finds no new episodes
    costs a bounded, empty `scan`, not a walk over rows already consolidated."""
    vector, graph = FakeVector(), FakeGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph, max_episodes_per_pass=3)
    p.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(5)])
    p.consolidate(ACME)
    p.consolidate(ACME)

    third = p.consolidate(ACME)

    assert third.episodic_scanned == 0
    assert third.details["complete"] is True


def test_a_backdated_write_below_the_cursor_is_never_consolidated():
    """Named limitation, not a bug: the cursor assumes non-decreasing `created_at`.

    A writer lagging the clock that produced the cursor's episode can land a row with
    `(created_at, id) <= cursor` -- `scan`'s `after` bound then excludes it on every future pass
    on this instance, permanently, not merely until the next call. Pinned so a future change to
    this behaviour (e.g. a real watermark that tolerates skew) updates this test deliberately
    rather than by surprise.
    """
    vector, graph = FakeVector(), FakeGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph)
    p.write(ACME, [event("the deploy failed on friday", id="e_ontime")])
    first = p.consolidate(ACME)
    assert first.episodic_scanned == 1

    # Bypasses `write()`'s `now_epoch_s()` stamp to simulate a writer whose clock lagged behind
    # the one that produced `e_ontime` -- `write()` itself offers no way to backdate a record.
    cursor_created_at, _ = p._scan_cursors[(ACME.key(), MemoryTier.EPISODIC)]
    backdated = MemoryRecord(
        id="e_backdated",
        tier=MemoryTier.EPISODIC,
        content="a customer complaint from before the pass advanced",
        scope=ACME,
        created_at=cursor_created_at - 100,
    )
    vector.upsert(ACME, [backdated])

    second = p.consolidate(ACME)

    assert second.episodic_scanned == 0, "the backdated row is below the cursor and never scanned"
    seen = {
        id_
        for fact in vector.rows["acme"].values()
        if fact.tier is MemoryTier.SEMANTIC
        for id_ in fact.provenance.get("source_episode_ids", [])
    }
    assert "e_backdated" not in seen


def test_a_failed_write_does_not_advance_the_cursor():
    """A pass whose fact write raises must leave the cursor where it was -- advancing it first
    would have the retry skip exactly the episodes that failed to consolidate.

    Mutation guard: moving `_advance_cursor` before the write's `try` makes this pass with the
    retry's `episodic_scanned` at 0 instead of 3.
    """

    class OnceFailingGraph(FakeGraph):
        fail = True

        def upsert_facts(self, scope, facts):
            if self.fail:
                raise MemoryOperationError("backend unavailable", field_name="tier")
            super().upsert_facts(scope, facts)

    vector, graph = FakeVector(), OnceFailingGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph, max_episodes_per_pass=3)
    p.write(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(3)])

    with pytest.raises(PartialWriteError):
        p.consolidate(ACME)

    graph.fail = False
    retry = p.consolidate(ACME)
    assert retry.episodic_scanned == 3


async def test_a_failed_awrite_does_not_advance_the_async_cursor():
    """The async mirror of :func:`test_a_failed_write_does_not_advance_the_cursor` -- pinned
    separately because `ConsolidationWorker` calls only `aconsolidate`, never the sync half, so
    that is the code path whose cursor-on-failure behaviour actually matters in production.

    Mutation guard: moving `_advance_cursor` before `aconsolidate`'s write `try` makes this pass
    with the retry's `episodic_scanned` at 0 instead of 3, exactly as the sync version's does.
    """

    class OnceFailingGraph(FakeGraph):
        fail = True

        async def aupsert_facts(self, scope, facts):
            if self.fail:
                raise MemoryOperationError("backend unavailable", field_name="tier")
            await super().aupsert_facts(scope, facts)

    vector, graph = FakeVector(), OnceFailingGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph, max_episodes_per_pass=3)
    await p.awrite(ACME, [event(f"topic{i} unique content", id=f"e{i}") for i in range(3)])

    with pytest.raises(PartialWriteError):
        await p.aconsolidate(ACME)

    graph.fail = False
    retry = await p.aconsolidate(ACME)
    assert retry.episodic_scanned == 3


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
    p, vector, graph = provider()

    def explode(scope, events_):
        raise RuntimeError("graph down")

    graph.upsert_episodes = explode  # type: ignore[method-assign]

    with pytest.raises(PartialWriteError) as exc:
        p.write(ACME, [event("w1"), event("w2")])

    ids = exc.value.receipt.event_ids
    assert len(ids) == 2
    # Retrying with those ids reuses the rows the vector leg already committed.
    graph.upsert_episodes = FakeGraph.upsert_episodes.__get__(graph)  # type: ignore[method-assign]
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
    # `entity_ids` is the floor the write path always fills (SM-G6 #104); `touched` is the write-back
    # this test exists to catch.
    assert stored.provenance == {"entity_ids": []}


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

    Deviation D3 added `upsert_facts` for exactly this and only wired it inside `consolidate`.
    """
    p, _, graph = provider()

    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme")])

    assert "upsert_facts" in graph.calls
    assert "upsert_episodes" not in graph.calls
    assert graph.facts[0].derived_by == "co_located.direct_write"


def test_an_episodic_write_still_uses_the_episode_door():
    p, _, graph = provider()

    p.write(ACME, [event("something happened")])

    assert graph.calls == ["upsert_episodes"]


def test_the_vector_leg_honours_tier_routing_like_the_graph_leg_does(graph_only_semantic):
    """Latent while no tier is graph-only; a silent extra write the moment one is."""
    p, vector, graph = provider()

    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("graph only fact")])

    assert vector.rows.get("acme", {}) == {}, "a graph-only tier must not reach the vector leg"


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

    forward, _ = p._distil(episodes)
    backward, _ = p._distil(list(reversed(episodes)))

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


def test_consolidation_honours_tier_routing_like_the_write_path_does(graph_only_semantic):
    """`write` filters by TIER_POLICY; consolidate wrote wherever a substrate was wired."""
    p, vector, graph = provider()
    p.write(ACME, [event("alpha beta gamma", id="ep")])

    p.consolidate(ACME)

    semantic_in_vector = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert semantic_in_vector == [], "graph-only SEMANTIC must not reach the vector substrate"


def test_a_refusal_before_the_fan_out_propagates_bare():
    """Nothing was written, so there is nothing for a receipt to help the caller reconcile."""
    p, vector, _ = provider()

    with pytest.raises(MemoryOperationError, match="no persistent substrate"):
        p.write_tier(ACME, MemoryTier.WORKING, [event("scratchpad note")])

    assert vector.rows.get("acme", {}) == {}


def test_a_refusal_during_the_fan_out_is_a_partial_write_carrying_the_receipt():
    """Once the vector leg has committed, "a refusal is not a partial write" is simply false.

    Raising it bare denied the caller the ids, and the documented retry then re-stamps fresh
    `uuid4`s -- finding F1 arriving through the error path. The refusal survives as `__cause__`, so
    a caller can still tell "this will never work" from "retry me".
    """

    class RefusingGraph(FakeGraph):
        def upsert_episodes(self, scope, events):
            raise MemoryOperationError("this backend refuses episodes", field_name="tier")

    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=RefusingGraph())

    with pytest.raises(PartialWriteError) as exc:
        p.write(ACME, [event("alpha", id="a1")])

    assert isinstance(exc.value.__cause__, MemoryOperationError)
    assert exc.value.receipt.event_ids == ["a1"]
    assert p.vector.rows["acme"], "the vector leg had already committed, which is the whole point"


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


async def test_a_refusal_among_concurrent_failures_wins_over_a_transport_error():
    """Which failure surfaces must be a decision, not a race.

    A refusal means "this will never work"; a transport error means "retry me". Bare `gather`
    propagates whichever arrived first, so the same batch could report either on different runs.
    Settling the batch is what makes the choice deterministic.
    """

    class MixedFailures(FakeGraph):
        """The two write verbs fan out concurrently, and they fail differently."""

        async def aupsert_episodes(self, scope, events):
            await _yield()
            raise RuntimeError("transport blip")

        async def aupsert_facts(self, scope, facts):
            await _yield()
            raise MemoryOperationError("this backend refuses facts", field_name="tier")

    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=MixedFailures())

    with pytest.raises(PartialWriteError) as exc:
        await p.awrite(
            ACME,
            [
                event("an episode", id="e0"),
                event("a fact", id="e1", tier=MemoryTier.SEMANTIC),
            ],
        )

    cause = exc.value.__cause__
    assert isinstance(cause, MemoryOperationError) and "refuses facts" in str(cause)


def test_consolidate_does_not_mutate_the_rows_the_substrate_handed_it():
    """`_reconcile_bounds` writes bounds onto whatever it is given, and `scan` may return live rows.

    The read path detaches in `_merge`; the consolidate path did not, so one pass moved a stored
    episode's `t_invalid` from None to the graph's bound, in place.
    """

    class LiveRowVector(FakeVector):
        """Hands back the stored objects, as a backend with an identity map does.

        `FakeVector.scan` returns copies, which is the *stricter* behaviour and right for every
        other test -- but it also means it cannot observe a provider that mutates what it scanned.
        This double is deliberately permissive on exactly that axis.
        """

        def scan(self, scope, tier, limit=None, after=None):
            self.calls.append("scan")
            self.scan_limits.append(limit)
            rows = [r for r in self._matching(scope) if r.tier is tier]
            rows.sort(key=lambda r: ((r.created_at or 0), r.id))
            if after is not None:
                rows = [r for r in rows if ((r.created_at or 0), r.id) > after]
            return rows if limit is None else rows[:limit]

    vector, graph = LiveRowVector(), FakeGraph()
    p = CoLocatedMemoryProvider(vector=vector, graph=graph)
    p.write(ACME, [event("the server is in rack 12", id="ep1", occurred_at=50)])
    graph.invalidate(ACME, "ep1", t_invalid=100, at=110)

    p.consolidate(ACME)

    stored = vector.rows["acme"]["ep1"]
    assert (stored.t_invalid, stored.expired_at) == (None, None)


@pytest.mark.parametrize("bad", [0, -1, "lots", 3.5, True])
def test_a_bound_that_reaches_the_substrate_is_validated_at_construction(bad):
    """`cap=0` is the one that matters: it scans nothing while reporting the tier as undrained,
    so a scheduler retries forever and nothing is ever consolidated."""
    with pytest.raises(MemoryProviderConfigError, match="max_episodes_per_pass"):
        CoLocatedMemoryProvider(vector=FakeVector(), max_episodes_per_pass=bad)


def test_the_bounds_are_reported_as_unreconciled_when_no_graph_participated():
    """A vector-only slice carries whatever the projection had, and a caller cannot otherwise tell."""
    vector_only = CoLocatedMemoryProvider(vector=FakeVector())
    vector_only.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha", id="a1")])

    assert vector_only.read(ACME, MemoryQuery(text="alpha")).fusion["bounds_reconciled"] is False
    p, _, _ = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha", id="a1")])
    assert p.read(ACME, MemoryQuery(text="alpha")).fusion["bounds_reconciled"] is True


# ---------------------------------------------------------------------------
# Round-8 review: the async concurrency the doubles could not observe
# ---------------------------------------------------------------------------


async def test_the_two_read_legs_actually_run_concurrently():
    """Serialising `aread`'s legs left all 429 tests green -- the property had no observer.

    The doubles delegate every `a*` to its sync twin, so "did the async path fall through" was
    unexpressable. This double makes it a liveness question instead: `asearch` blocks until
    `atraverse` sets the event, so sequential awaits deadlock and concurrent ones complete.
    """
    import asyncio

    started = asyncio.Event()

    class BlockingVector(FakeVector):
        async def asearch(self, scope, query, tier=None):
            await asyncio.wait_for(started.wait(), timeout=1.0)
            return self.search(scope, query, tier)

    class SignallingGraph(FakeGraph):
        async def atraverse(self, scope, query, tier=None):
            started.set()
            return self.traverse(scope, query, tier)

    p = CoLocatedMemoryProvider(vector=BlockingVector(), graph=SignallingGraph())
    p.write(ACME, [event("alpha beta", id="a1")])

    slice_ = await asyncio.wait_for(p.aread(ACME, MemoryQuery(text="alpha")), timeout=2.0)

    assert [r.id for r in slice_.records] == ["a1"]


async def test_concurrent_graph_writes_respect_the_configured_bound():
    """Every fan-out must go through `_gather_writes`; the fact branch was the one that did not.

    The batched verbs shrank the live fan-out from `len(events)` to the two write verbs, so the
    bound is exercised at 1 rather than at 40. Set to 2 it could no longer fail, which is worse
    than no test: the semaphore could be deleted and this would still pass.
    """
    import asyncio

    class CountingGraph(FakeGraph):
        def __init__(self):
            super().__init__()
            self.live = 0
            self.peak = 0

        async def _counted(self, work):
            self.live += 1
            self.peak = max(self.peak, self.live)
            try:
                await asyncio.sleep(0)
                return work()
            finally:
                self.live -= 1

        async def aupsert_episodes(self, scope, events):
            return await self._counted(lambda: FakeGraph.upsert_episodes(self, scope, events))

        async def aupsert_facts(self, scope, facts):
            return await self._counted(lambda: FakeGraph.upsert_facts(self, scope, facts))

    graph = CountingGraph()
    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=graph, max_concurrent_writes=1)

    # Mixed, so both verbs are in flight at once -- unbounded, the peak would be 2.
    await p.awrite(
        ACME,
        [event("an episode", id="e0"), event("a fact", id="e1", tier=MemoryTier.SEMANTIC)],
    )

    assert graph.episode_batch_sizes == [1] and graph.fact_batch_sizes == [1], "both verbs must be in flight"
    assert graph.peak == 1, f"peak concurrency {graph.peak} exceeded max_concurrent_writes=1"


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_a_write_is_one_round_trip_per_verb(half):
    """The `write` half of the same property, which `consolidate` does not cover.

    Both verbs, with more than one event each, because a per-event fan-out and a batched call are
    indistinguishable at a batch of one -- and one is exactly what the mixed-batch tests use.
    """
    graph = FakeGraph()
    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=graph)
    batch = [event(f"episode {i}", id=f"e{i}") for i in range(3)] + [
        event(f"fact {i}", id=f"f{i}", tier=MemoryTier.SEMANTIC) for i in range(2)
    ]

    if half == "sync":
        p.write(ACME, batch)
    else:
        await p.awrite(ACME, batch)

    assert graph.episode_batch_sizes == [3], "three episodes must be one call"
    assert graph.fact_batch_sizes == [2], "two facts must be one call"


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_a_batch_of_facts_is_one_round_trip_per_scope_group(half):
    """SPEC section 11.3: a write is one round trip, not `len(events)` of them.

    Both halves, because they reach the substrate through different code -- `_graph_fact_batch` and
    `_gather_writes` -- and an assertion on one leaves the other free to fan out per fact.

    Asserted on what the substrate was *asked for* rather than on what landed: the stored rows are
    identical either way, so a result-only assertion cannot tell the two apart.
    """
    graph = FakeGraph()
    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=graph, max_concurrent_writes=4)
    p.write(ACME, [event(f"unique topic {i} here", id=f"e{i}") for i in range(40)])
    graph.fact_batch_sizes.clear()

    if half == "sync":
        p.consolidate(ACME)
    else:
        await p.aconsolidate(ACME)

    assert len(graph.fact_batch_sizes) == 1, "one scope group must be one call, not one call per fact"
    assert graph.fact_batch_sizes[0] > 1, "the batch must be non-trivial, or the property is untested"


def test_bounds_are_not_requested_for_a_tier_the_graph_does_not_hold():
    """A procedural read is vector-only per TIER_POLICY, so the round trip is guaranteed empty --
    and a network hop in Mode B."""
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm", id="pr1")])

    p.read_tier(ACME, MemoryTier.PROCEDURAL, MemoryQuery(text="always confirm"))

    assert graph.bounds_requests == []


@pytest.mark.parametrize(
    "wiring,served",
    [
        ({"vector": FakeVector(), "graph": FakeGraph()}, ["episodic", "semantic", "procedural"]),
        ({"vector": FakeVector()}, ["episodic", "semantic", "procedural"]),
        ({"graph": FakeGraph()}, ["episodic", "semantic"]),
    ],
)
def test_which_tiers_are_served_follows_the_wiring(wiring, served):
    """A graph-only provider cannot serve procedural memory -- the table routes it to the vector
    substrate alone. Reads of it are empty, so `tiers()` is the only way a caller can tell that
    from an empty tier."""
    p = CoLocatedMemoryProvider(**wiring)

    assert [t.value for t, on in p.tiers().items() if on] == served


def test_a_row_the_graph_reports_expired_is_not_served():
    """Transaction time, filtered after reconciliation -- found untested by a mutation sweep.

    The vector projection carries no `expired_at`, so its leg returns the row looking live; the
    graph's `bounds_for_ids` is the only place the supersession shows up. Dropping the
    `is_current_belief` filter here served a row the graph had already replaced, and the two
    validity filters are separate: `_valid_at` is about the world, this is about belief.
    """
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("ada works at acme", id="f1")])
    graph.rows["acme"][0].expired_at = now_epoch_s()  # a newer revision exists; this row is not it

    slice_ = p.read(ACME, MemoryQuery(text="ada works at acme"))

    assert slice_.records == []
    assert slice_.fusion["dropped_as_superseded"] == 1
    assert slice_.fusion["dropped_by_validity"] == 0, "this is transaction time, not valid time"


def test_a_leg_no_requested_tier_routes_to_is_not_consulted_at_all():
    """The same reasoning one step earlier: not just the bounds call, the search itself.

    Asserted on what the substrate was **asked**, because the result cannot show it -- the graph
    holds no procedural rows either way, so a read that consults it looks identical to one that
    does not, right up until Mode B bills for the hop.
    """
    p, vector, graph = provider()
    p.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm", id="pr1")])
    vector.calls.clear()
    graph.calls.clear()

    p.read(ACME, MemoryQuery(text="always confirm", tiers=[MemoryTier.PROCEDURAL]))

    assert graph.calls == []
    assert vector.calls == ["search"], "the leg that does hold the tier is still consulted"


async def test_the_async_read_skips_the_same_leg_the_sync_one_does():
    """A skip on one half only is two different backends behind one seam."""
    p, _, graph = provider()
    p.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm", id="pr1")])
    graph.calls.clear()

    await p.aread(ACME, MemoryQuery(text="always confirm", tiers=[MemoryTier.PROCEDURAL]))

    assert graph.calls == []


def test_a_read_of_a_graph_backed_tier_still_consults_both_legs():
    """The skip is tier-driven, not a blanket optimisation -- the negative alone would pass on a
    provider that never called the graph at all."""
    p, vector, graph = provider()
    p.write_tier(ACME, MemoryTier.SEMANTIC, [event("the customer prefers email", id="s1")])
    vector.calls.clear()
    graph.calls.clear()

    p.read(ACME, MemoryQuery(text="the customer", tiers=[MemoryTier.SEMANTIC]))

    assert "traverse" in graph.calls and "search" in vector.calls


def test_excluding_the_graph_is_allowed_for_a_tier_the_graph_does_not_hold():
    """The refusal is right for episodic and semantic; for procedural the graph has no validity
    opinion to withhold, so it blocked the one read shape where skipping the leg is legitimate."""
    p, _, _ = provider()
    p.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm", id="pr1")])

    records = p.read(
        ACME, MemoryQuery(text="always confirm", tiers=[MemoryTier.PROCEDURAL], substrates=[Substrate.VECTOR])
    ).records

    assert [r.id for r in records] == ["pr1"]

    with pytest.raises(MemoryOperationError, match="authority for bi-temporal validity"):
        p.read(ACME, MemoryQuery(text="x", tiers=[MemoryTier.SEMANTIC], substrates=[Substrate.VECTOR]))


def test_consolidation_does_not_retire_and_says_so():
    """SM-C1's pass distils and writes. Retiring what it subsumes is SM-L3's (#124).

    A fact id is `uuid5` over its sorted source-episode ids, so a group whose membership changes
    mints a new id and the previous row stays current belief -- one logical fact, two rows. That is
    a real limitation, so it is **pinned** rather than left for a caller to discover: the report
    says `supersede: not implemented`, and this test fails the moment SM-L3 changes the behaviour,
    which is when the SPEC row and this test should both be updated.
    """
    # Two independent instances over shared substrates, not two calls on one: SM-L1's (#122)
    # cursor would have the second call scan only `g2`, which still leaves two live facts but no
    # longer for the reason this test pins -- group membership changing under a *full* rescan.
    # Two fresh instances reproduce that rescan, matching a scheduler with no cursor continuity
    # (a restart, or a second replica).
    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located")
    grow = "ada prefers async standups"
    first_pass = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)
    first_pass.write(ACME, [event(grow, id="g1")])
    first_pass.consolidate(ACME)
    second_pass = CoLocatedMemoryProvider(vector=vector, graph=graph, config=config)
    second_pass.write(ACME, [event(grow, id="g2")])

    report = second_pass.consolidate(ACME)

    assert report.details["supersede"].startswith("not implemented")
    assert "facts_superseded" not in report.details
    live = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC and r.is_current_belief()]
    assert len(live) == 2, "the known limitation: SM-L3 (#124) makes this 1"
    assert "invalidate" not in graph.calls and "upsert_facts" in graph.calls


def test_consolidation_reports_provenance_validity_for_the_deterministic_dedup_default():
    """SM-L7 (#128): `consolidate`'s report now carries `details["provenance_validity"]`, computed
    against the episodes this pass actually scanned -- a real production call site for
    `provenance_report`, not only a library function exercised in its own unit tests. The
    deterministic-dedup default path's facts all cite real, just-scanned episodes, so this is the
    ordinary, expected 1.0 case."""
    p, vector, graph = provider()
    p.write(ACME, [event("ada prefers async standups", id="e1")])

    report = p.consolidate(ACME)

    assert report.details["provenance_validity"] == 1.0
    assert report.details["provenance_dangling"] == []


class _StubPipelineWithADanglingCitation:
    """A `fact_pipeline` double that hands back one fact citing an episode id no scan ever
    produced -- the only way to exercise `provenance_report`'s `< 1.0` branch through
    `consolidate` without a real corruption, since the deterministic-dedup default always cites
    real, just-read episodes by construction."""

    def distil(self, scope, episodes):
        fact = FactWrite(
            record=MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=scope),
            source_episode_ids=["ghost-episode"],
        )
        return [fact], FactExtractionReport(scope=scope, episodes_read=len(episodes))

    async def adistil(self, scope, episodes):
        return self.distil(scope, episodes)


def test_consolidation_reports_a_dangling_citation_without_raising():
    """The negative case: a fact_pipeline that produces a dangling citation is *reported*, not
    refused -- `provenance_report` here is detection only (round-3 review: "policy on violation
    stays in aicoe-tracker#194"), so `consolidate` must still complete and write the fact."""
    vector, graph = FakeVector(), FakeGraph()
    provider = CoLocatedMemoryProvider(vector=vector, graph=graph, fact_pipeline=_StubPipelineWithADanglingCitation())
    provider.write(ACME, [event("ada prefers async standups", id="e1")])

    report = provider.consolidate(ACME)

    assert report.details["provenance_validity"] == 0.0
    assert report.details["provenance_dangling"] == ["f1"]
    assert report.semantic_written == 1, "detection does not block the write"


def test_a_refusal_during_consolidation_is_a_partial_write():
    """The same rule as `write`: by the time the graph refuses, the facts are committed."""

    class RefusingGraph(FakeGraph):
        def upsert_facts(self, scope, facts):
            raise MemoryOperationError("this backend refuses facts", field_name="tier")

    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=RefusingGraph())
    p.write(ACME, [event("alpha beta gamma", id="ep1")])

    with pytest.raises(PartialWriteError) as exc:
        p.consolidate(ACME)

    assert isinstance(exc.value.__cause__, MemoryOperationError)
    assert exc.value.receipt.tiers_written == [MemoryTier.SEMANTIC]


def test_the_limit_is_applied_after_the_merge_not_only_by_the_substrates():
    """Each leg honours `limit` on its own, so the provider's cap only bites when they disagree.

    Every existing limit test writes one corpus both legs return, making merged == limit and the
    post-merge cap redundant -- so removing it left the whole suite green. A graph traversal
    surfacing rows the vector leg did not is the ordinary case, not a contrived one.
    """
    p, vector, graph = provider()
    for i in range(3):
        vector.rows.setdefault("acme", {})[f"v{i}"] = MemoryRecord(
            id=f"v{i}", tier=MemoryTier.SEMANTIC, content=f"shared token {i}", scope=ACME
        )
        graph.rows.setdefault("acme", []).append(
            MemoryRecord(id=f"g{i}", tier=MemoryTier.SEMANTIC, content=f"shared token {i}", scope=ACME)
        )

    slice_ = p.read(ACME, MemoryQuery(text="shared token", limit=2))

    assert slice_.fusion["fused"] == 4, "the legs must disagree, or this proves nothing"
    assert len(slice_.records) == 2


def test_bounds_reconciled_reports_what_happened_not_what_was_wired():
    """`bounds_reconciled` is in `FUSION_KEYS` because it is the one a caller branches on.

    Inferring it from the wiring claimed `True` for a procedural slice, where the reconciliation
    correctly makes no call at all -- so the flag said a graph vouched for bounds no graph was
    asked about. It is now reported by the reconciliation itself:

    * graph-backed tier, graph wired  -> True  (asked and answered)
    * graph-backed tier, no graph     -> False (rows that could be bounded went unchecked)
    * vector-only tier, graph wired   -> True  (vacuous: nothing routes to the graph, so the
                                                vector row's own bounds are the whole truth)
    """
    both, _, _ = provider()
    both.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha", id="a1")])
    both.write_tier(ACME, MemoryTier.PROCEDURAL, [event("always confirm", id="pr1")])
    vector_only = CoLocatedMemoryProvider(vector=FakeVector())
    vector_only.write_tier(ACME, MemoryTier.SEMANTIC, [event("alpha", id="a1")])

    assert both.read(ACME, MemoryQuery(text="alpha", tiers=[MemoryTier.SEMANTIC])).fusion["bounds_reconciled"] is True
    assert vector_only.read(ACME, MemoryQuery(text="alpha")).fusion["bounds_reconciled"] is False

    before = len(both.graph.bounds_requests)
    procedural = both.read(ACME, MemoryQuery(text="always confirm", tiers=[MemoryTier.PROCEDURAL]))

    assert procedural.fusion["bounds_reconciled"] is True
    assert both.graph.bounds_requests[before:] == [], "no graph call was made, and none was needed"

    # The discriminating case: graph wired, graph *excluded*, but the tier does not use it anyway.
    # Inferring the flag from `_uses(GRAPH)` reports False here and is wrong -- nothing routes to
    # the graph, so the vector row's bounds are complete.
    excluded = both.read(
        ACME,
        MemoryQuery(text="always confirm", tiers=[MemoryTier.PROCEDURAL], substrates=[Substrate.VECTOR]),
    )
    assert excluded.fusion["bounds_reconciled"] is True


# ---------------------------------------------------------------------------
# Round-9 review
# ---------------------------------------------------------------------------


def test_a_derived_fact_inherits_its_sources_upper_bound():
    """`_distil` propagated `min(t_valid)` and dropped `t_invalid`, so a fact outlived its evidence.

    `_visible_episodes` keeps an episode valid *at the pass instant*, so one bounded in the future
    passes the filter and used to be minted with no upper bound at all -- unsuppressable afterwards
    by any read, at any limit, with any `as_of`. The trigger needs `t_invalid > now`: an
    invalidation dated now-or-earlier is correctly dropped by `_valid_at` before distillation.
    """
    future = 3_000_000_000
    p, vector, graph = provider()
    p.write(ACME, [event("the server is in rack 12", id="ep1", occurred_at=100)])
    graph.invalidate(ACME, "ep1", t_invalid=future, at=future)

    p.consolidate(ACME)

    fact = next(r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC)
    assert (fact.t_valid, fact.t_invalid) == (100, future)
    assert p.read(ACME, MemoryQuery(text="server rack 12", as_of=future + 1)).records == []
    assert p.read(ACME, MemoryQuery(text="server rack 12", as_of=future - 1)).records != []


def test_a_group_with_one_open_ended_episode_stays_open_ended():
    """A fact is true for as long as *any* of its evidence is; `min`/`max` would cut it short.

    Identical content, so the two episodes land in **one** group -- the mixed-boundedness case the
    rule exists for. Near-duplicates below `_DUPLICATE_THRESHOLD` form separate groups and never
    exercise it, which is how a first attempt at this test passed under mutation.
    """
    p, vector, graph = provider()
    p.write(
        ACME, [event("rack 12 is down", id="a", occurred_at=100), event("rack 12 is down", id="b", occurred_at=100)]
    )
    graph.invalidate(ACME, "a", t_invalid=3_000_000_000, at=3_000_000_000)

    p.consolidate(ACME)

    facts = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert len(facts) == 1, "identical content must form one group, or the mixed case is untested"
    assert facts[0].provenance["source_episode_ids"] == ["a", "b"]
    assert facts[0].t_invalid is None, "one unbounded member keeps the whole fact open"


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_consolidation_refuses_two_facts_sharing_an_id(half, monkeypatch):
    """The guard `write` has always had, on the path that did not -- reached from **both** halves.

    Driven through `consolidate`, not by calling the helper: the first attempt asserted on the
    helper directly and passed happily with the guard deleted from the async entry point, which is
    the divergence this parametrisation exists to catch.

    `_distil` cannot produce a duplicate id today -- its grouping partitions the input -- so the
    strategy is stubbed to produce one. That is the point of the guard: it holds whatever SM-L3
    (#124) replaces the grouping with.
    """
    p, _, graph = provider()
    p.write(ACME, [event("alpha beta gamma", id="ep1")])
    record = MemoryRecord(id="same", tier=MemoryTier.SEMANTIC, content="a fact", scope=ACME)
    duplicated = [FactWrite(record=record), FactWrite(record=replace(record, content="another fact"))]
    monkeypatch.setattr(type(p), "_distil", lambda self, episodes: (duplicated, 0))

    with pytest.raises(MemoryOperationError, match="Duplicate event id") as exc:
        if half == "sync":
            p.consolidate(ACME)
        else:
            await p.aconsolidate(ACME)

    assert not isinstance(exc.value, PartialWriteError), "a refusal before any write propagates bare"
    assert "upsert_facts" not in graph.calls and "aupsert_facts" not in graph.calls, "nothing was written"


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_closing_the_provider_closes_every_substrate_even_if_one_raises(half):
    """Latent only until SM-V1 (#95) brings a second connection pool.

    A closer that raises must not stop the loop: the substrate that never got closed is the one
    still holding a pool, and nothing will reopen the provider to try again. The failure is still
    reported -- closing everything is not the same as swallowing.
    """
    closed = []

    class _Raising(FakeVector):
        def close(self):
            closed.append("vector")
            raise RuntimeError("vector closer failed")

    class _Closing(FakeGraph):
        def close(self):
            closed.append("graph")

    p = CoLocatedMemoryProvider(vector=_Raising(), graph=_Closing())

    with pytest.raises(RuntimeError, match="vector closer failed"):
        if half == "sync":
            p.close()
        else:
            await p.aclose()

    assert closed == ["vector", "graph"], f"every substrate must be closed, got {closed}"


class _CountingFailureGraph(FakeGraph):
    """Fails on the n-th fact *inside* a batch, having committed the ones before it.

    ``upsert_facts`` is explicitly not required to be atomic, so this is a shape a real backend
    produces: part of the batch is durable and the call still raises. A double that failed the
    whole batch cleanly would make the interesting half of the parity question disappear.
    """

    def __init__(self, plan):
        super().__init__()
        self.plan, self.n = plan, 0

    def upsert_facts(self, scope, facts):
        self.calls.append("upsert_facts")
        self.fact_batch_sizes.append(len(facts))
        for fact in facts:
            index, self.n = self.n, self.n + 1
            failure = self.plan.get(index)
            if failure is not None:
                raise failure
            self._upsert_fact(scope, fact)

    async def aupsert_facts(self, scope, facts):
        await _yield()
        return self.upsert_facts(scope, facts)


@pytest.mark.parametrize(
    "plan,label",
    [
        ({0: RuntimeError("down")}, "first-fact-transport"),
        ({1: RuntimeError("down")}, "second-fact-transport"),
        ({0: MemoryOperationError("refuses facts", field_name="tier")}, "first-fact-refusal"),
    ],
)
async def test_both_consolidate_halves_commit_the_same_state_and_choose_the_same_cause(plan, label):
    """The sync fan-out aborted on the first raise while the async half settled the batch.

    One identical failure left the two verbs with different durable state, and a refusal gave them
    opposite retry advice -- "retry me" versus "this will never work". `write` carries a shared
    `_raise_write_failure` for exactly this; consolidate never had it carried across.

    Which cause *wins* among several is no longer decidable here -- one batched call can only raise
    once -- so that half of the rule is exercised on the write path, where the two verbs genuinely
    fail concurrently (`test_a_refusal_among_concurrent_failures_wins_over_a_transport_error`).
    """

    def build():
        p = CoLocatedMemoryProvider(vector=FakeVector(), graph=_CountingFailureGraph(dict(plan)))
        for i in range(3):
            p.write(ACME, [event(f"unique topic {i} here", id=f"e{i}")])
        return p

    def outcome(p, exc):
        committed = sorted(r.id for r in p.graph.rows.get("acme", []) if r.tier is MemoryTier.SEMANTIC)
        return committed, type(exc.__cause__), exc.receipt.accepted, sorted(exc.receipt.event_ids)

    sync_provider = build()
    with pytest.raises(PartialWriteError) as sync_exc:
        sync_provider.consolidate(ACME)
    async_provider = build()
    with pytest.raises(PartialWriteError) as async_exc:
        await async_provider.aconsolidate(ACME)

    assert outcome(sync_provider, sync_exc.value) == outcome(async_provider, async_exc.value)


def test_the_consolidation_receipt_names_the_facts_a_retry_must_reproduce():
    """`accepted=0, event_ids=[]` with rows already committed is factually wrong output.

    The counters advanced *after* both legs, so for the single-scope case -- every ordinary pass --
    they were unreachable on any failure, and the error text read "retry with these ids: []".
    """
    p = CoLocatedMemoryProvider(vector=FakeVector(), graph=_CountingFailureGraph({2: RuntimeError("down")}))
    for i in range(3):
        p.write(ACME, [event(f"unique topic {i} here", id=f"e{i}")])

    with pytest.raises(PartialWriteError) as exc:
        p.consolidate(ACME)

    committed = [r.id for r in p.vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert committed, "the vector leg must have committed, or this proves nothing"
    assert exc.value.receipt.accepted == 3
    assert sorted(exc.value.receipt.event_ids) == sorted(committed)
    assert "[]" not in str(exc.value)


def test_a_first_write_refusal_in_consolidate_propagates_bare():
    """Nothing landed, so there is no partial state -- matching `write`, where a vector refusal
    precedes any commit."""

    class RefusingVector(FakeVector):
        def upsert(self, scope, records):
            if any(r.tier is MemoryTier.SEMANTIC for r in records):
                raise MemoryOperationError("this backend refuses facts", field_name="tier")
            return super().upsert(scope, records)

    p = CoLocatedMemoryProvider(vector=RefusingVector(), graph=FakeGraph())
    p.write(ACME, [event("alpha beta gamma", id="ep1")])

    with pytest.raises(MemoryOperationError, match="refuses facts"):
        p.consolidate(ACME)


def test_an_episode_whose_valid_time_has_not_started_is_not_consolidated():
    """`_visible_episodes` checks **both** bounds, not only `t_invalid`.

    The upper half is covered above by an invalidated episode; the lower half needs a future-dated
    one, and nothing else can observe it. Distilling one mints a semantic fact asserting something
    that has not happened yet -- and the fact carries no bound saying so, because `_distil`
    propagates the sources' bounds and the source looked live.
    """
    future = 3_000_000_000
    p, vector, _ = provider()
    p.write(ACME, [event("the migration completed", id="ep1", occurred_at=future)])

    p.consolidate(ACME)

    facts = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
    assert facts == [], "a future-dated episode is not evidence yet"
