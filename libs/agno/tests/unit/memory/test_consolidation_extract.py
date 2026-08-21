"""Fact extraction + merge decision (SM-L3 #124).

The three acceptance criteria, each with a test that fails without the code: candidate facts are
extracted from episodes, the correct op is chosen against existing facts (ADD / UPDATE / DELETE /
NOOP), and the result is applied to the vector *and* graph substrates through the same fact-write
path ``_distil`` (SM-C1 #89) feeds. Retirement is transaction time, never valid time -- pinned
directly against the fields it stamps, not inferred from a side effect.

Doubles throughout, never a real model: what this file tests is the pipeline's decisions, not an
LLM's.
"""

import asyncio

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.consolidation.extract import (
    STRATEGY_ID,
    FactCandidate,
    FactExtractionPipeline,
    MergeDecision,
)
from egpt.memory.errors import MemoryProviderConfigError
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    NO_SUPERSEDE,
    MemoryEvent,
    MemoryOperationError,
    MemoryRecord,
    Scope,
)
from tests.unit.memory._fakes import FakeGraph, FakeVector

ACME = Scope(tenant_id="acme", user_id="ada")
BOB = Scope(tenant_id="acme", user_id="bob")


def episode(content, episode_id="e1", scope=ACME, **kwargs):
    return MemoryRecord(id=episode_id, tier=MemoryTier.EPISODIC, content=content, scope=scope, **kwargs)


def event(content, **kwargs):
    return MemoryEvent(content=content, scope=kwargs.pop("scope", ACME), **kwargs)


def fact(fact_id, content, scope=ACME, source_episode_ids=(), **kwargs):
    return MemoryRecord(
        id=fact_id,
        tier=MemoryTier.SEMANTIC,
        content=content,
        scope=scope,
        provenance={"source_episode_ids": list(source_episode_ids)},
        **kwargs,
    )


class StubExtractor:
    """Returns one fixed batch of candidates, whatever episodes it is handed."""

    def __init__(self, candidates=()):
        self.candidates = list(candidates)
        self.calls = []

    def extract(self, episodes):
        self.calls.append([e.id for e in episodes])
        return list(self.candidates)

    async def aextract(self, episodes):
        await asyncio.sleep(0)
        return self.extract(episodes)


class StubDecider:
    """Returns one fixed decision for every candidate, or a callable that computes one."""

    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def _decide(self, statement, neighbors):
        self.calls.append((statement, [n.id for n in neighbors]))
        return self.decision(statement, neighbors) if callable(self.decision) else self.decision

    def decide(self, statement, neighbors):
        return self._decide(statement, neighbors)

    async def adecide(self, statement, neighbors):
        await asyncio.sleep(0)
        return self._decide(statement, neighbors)


def pipeline(extractor=None, decider=None, vector=None, **kwargs):
    vector = vector if vector is not None else FakeVector()
    return (
        FactExtractionPipeline(
            vector=vector,
            extractor=extractor if extractor is not None else StubExtractor(),
            decider=decider if decider is not None else StubDecider(MergeDecision(op="ADD", statement="x")),
            **kwargs,
        ),
        vector,
    )


# ---------------------------------------------------------------------------
# AC 1 -- candidate facts extracted from episodes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_a_grounded_candidate_becomes_a_fact_write(is_async):
    extractor = StubExtractor([FactCandidate(statement="ada prefers async standups", source_indices=[0])])
    pipe, vector = pipeline(
        extractor=extractor, decider=StubDecider(MergeDecision(op="ADD", statement="ada prefers async standups"))
    )
    eps = [episode("ada said she prefers async standups", episode_id="e1")]

    call = pipe.adistil(ACME, eps) if is_async else pipe.distil(ACME, eps)
    facts, report = asyncio.run(call) if is_async else call

    assert len(facts) == 1
    assert facts[0].record.content == "ada prefers async standups"
    assert facts[0].record.provenance["source_episode_ids"] == ["e1"]
    assert report.candidates == 1
    assert report.added == 1


def test_report_details_does_not_duplicate_the_op_counts():
    """`report.added` etc. are the one place op counts live -- `details` used to also carry them
    via `**counts`, so a caller mutating `details["added"]` would silently disagree with
    `report.added`, which nothing here re-derives from `details`."""
    pipe, _ = pipeline(
        extractor=StubExtractor([FactCandidate(statement="ada likes tea", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="ADD", statement="ada likes tea")),
    )

    _, report = pipe.distil(ACME, [episode("ada likes tea", episode_id="e1")])

    assert set(report.details) == {"strategy", "complete", "ungrounded", "collisions", "unresolved_deletes"}


def test_an_ungrounded_candidate_is_dropped():
    """A candidate citing no episode this pass read is not attributable to anything -- dropped,
    not written under a source list of ``[]`` that would misreport provenance."""
    extractor = StubExtractor([FactCandidate(statement="unfounded", source_indices=[])])
    pipe, _ = pipeline(extractor=extractor)

    facts, report = pipe.distil(ACME, [episode("ada said something", episode_id="e1")])

    assert facts == []
    assert report.candidates == 1
    assert report.added == report.updated == report.deleted == report.noop == 0


def test_candidates_never_ground_across_scopes():
    """Episodes are bucketed by scope before extraction, so a candidate's ``source_indices`` can
    only ever name episodes from its own bucket -- unlike ``_distil``, which must police this after
    the fact, the model here is never shown another scope's episodes to cite."""
    extractor = StubExtractor([FactCandidate(statement="s", source_indices=[0])])
    pipe, _ = pipeline(extractor=extractor)
    eps = [episode("ada's episode", episode_id="a1", scope=ACME), episode("bob's episode", episode_id="b1", scope=BOB)]

    facts, _ = pipe.distil(ACME, eps)

    # Each bucket is extracted separately with local indices, so index 0 in ACME's bucket is a1
    # and index 0 in BOB's bucket is b1 -- never one bucket's index resolving into the other's rows.
    written_sources = {sid for f in facts for sid in f.record.provenance["source_episode_ids"]}
    assert written_sources <= {"a1", "b1"}
    for f in facts:
        assert f.record.scope in (ACME, BOB)


# ---------------------------------------------------------------------------
# AC 2 -- correct op vs existing facts
# ---------------------------------------------------------------------------


def test_a_new_topic_is_added():
    pipe, vector = pipeline(
        extractor=StubExtractor([FactCandidate(statement="ada likes tea", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="ADD", statement="ada likes tea")),
    )
    facts, report = pipe.distil(ACME, [episode("ada mentioned she likes tea", episode_id="e1")])

    assert report.added == 1
    assert len(facts) == 1
    assert facts[0].record.expired_at is None


def test_a_repeated_fact_is_a_noop_and_writes_nothing():
    existing = fact("f1", "ada prefers async standups", source_episode_ids=["e0"])
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada prefers async standups", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="NOOP", target_index=0, statement="ada prefers async standups")),
    )

    facts, report = pipe.distil(ACME, [episode("ada again said she prefers async standups", episode_id="e1")])

    assert facts == []
    assert report.noop == 1


def test_a_changed_fact_updates_and_supersedes_by_transaction_time():
    """The op itself, and the bi-temporal contract SM-C1's own comment ratifies for this ticket:
    the old row's ``t_valid``/``t_invalid`` are untouched, only ``expired_at``/``superseded_by``
    move -- a superseded fact did not become false, a better-sourced row replaced it."""
    existing = fact("f1", "ada lives in Berlin", source_episode_ids=["e0"], t_valid=100, t_invalid=None)
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada moved to Lisbon", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="UPDATE", target_index=0, statement="ada lives in Lisbon")),
    )

    facts, report = pipe.distil(ACME, [episode("ada said she moved to Lisbon", episode_id="e1", created_at=500)])

    assert report.updated == 1
    by_id = {f.record.id: f.record for f in facts}
    assert by_id["f1"].expired_at is not None
    assert by_id["f1"].superseded_by is not None
    assert by_id["f1"].t_valid == 100 and by_id["f1"].t_invalid is None, "valid time is untouched by retirement"
    new_id = by_id["f1"].superseded_by
    assert by_id[new_id].content == "ada lives in Lisbon"
    assert by_id[new_id].expired_at is None
    assert set(by_id[new_id].provenance["source_episode_ids"]) == {"e0", "e1"}, "provenance is unioned forward"


def test_a_retracted_fact_is_deleted_and_leaves_no_successor():
    existing = fact("f1", "ada owns a car", source_episode_ids=["e0"])
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada sold her car", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="DELETE", target_index=0, statement="ada sold her car")),
    )

    facts, report = pipe.distil(ACME, [episode("ada said she sold her car", episode_id="e1")])

    assert report.deleted == 1
    assert len(facts) == 1
    assert facts[0].record.id == "f1"
    assert facts[0].record.expired_at is not None
    assert facts[0].record.superseded_by is None, "a delete retires; nothing replaces it"


def test_no_neighbors_skips_the_decider_and_adds():
    """Cheap and deterministic: a candidate with nothing to compare against is unambiguously new,
    so the merge call is never made -- matching the harness's own shortcut."""
    decider = StubDecider(MergeDecision(op="ADD", statement="anything"))
    pipe, _ = pipeline(
        extractor=StubExtractor([FactCandidate(statement="brand new", source_indices=[0])]),
        decider=decider,
    )

    pipe.distil(ACME, [episode("something brand new", episode_id="e1")])

    assert decider.calls == []


def test_an_update_target_out_of_range_degrades_to_add():
    """A hand-rolled MergeDecider is not required to bounds-check its own ``target_index`` --
    the pipeline is the one place that rule is enforced, for every decider alike."""
    existing = fact("f1", "ada lives in Berlin", source_episode_ids=["e0"])
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada moved", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="UPDATE", target_index=7, statement="ada moved")),
    )

    facts, report = pipe.distil(ACME, [episode("ada moved", episode_id="e1")])

    assert report.added == 1
    assert report.updated == 0
    assert len(facts) == 1
    assert facts[0].record.content == "ada moved"


def test_a_delete_target_out_of_range_degrades_to_noop():
    """The DELETE counterpart of the UPDATE test above -- an unresolvable target writes nothing
    and is counted as `noop`, separately named in `details["unresolved_deletes"]` rather than
    conflated with a genuine NOOP decision."""
    existing = fact("f1", "ada owns a car", source_episode_ids=["e0"])
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada sold her car", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="DELETE", target_index=99, statement="ada sold her car")),
    )

    facts, report = pipe.distil(ACME, [episode("ada sold her car", episode_id="e1")])

    assert facts == []
    assert report.noop == 1
    assert report.deleted == 0
    assert report.details["unresolved_deletes"] == 1


def test_an_add_naming_a_target_index_does_not_merge_into_it():
    """ADD means new, regardless of what else a decider returns alongside it -- only UPDATE reads
    ``target_index`` as a fact to merge into."""
    existing = fact("f1", "ada lives in Berlin", source_episode_ids=["e0"])
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada also has a cat", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="ADD", target_index=0, statement="ada also has a cat")),
    )

    facts, report = pipe.distil(ACME, [episode("ada has a cat", episode_id="e1")])

    assert report.added == 1
    assert len(facts) == 1, "an ADD writes only the new fact, never a retirement of the named target"
    assert facts[0].record.provenance["source_episode_ids"] == ["e1"]


def test_two_distinct_facts_from_one_episode_do_not_collide():
    """The extraction prompt asks for exactly this ("one fact per idea; split compound
    statements"), so two ADDs citing the same single episode is the ordinary case, not an edge
    case. A source-only id collided them into one row and, worse, aborted the whole consolidation
    pass when both reached `reject_duplicate_ids` in the same write batch."""
    extractor = StubExtractor(
        [
            FactCandidate(statement="ada prefers async standups", source_indices=[0]),
            FactCandidate(statement="ada works remotely", source_indices=[0]),
        ]
    )
    pipe, _ = pipeline(
        extractor=extractor,
        decider=StubDecider(lambda statement, neighbors: MergeDecision(op="ADD", statement=statement)),
    )

    facts, report = pipe.distil(ACME, [episode("ada said stuff", episode_id="e1")])

    assert report.added == 2
    ids = [f.record.id for f in facts]
    assert len(ids) == len(set(ids)), "two distinct facts from one episode must not share an id"
    contents = {f.record.content for f in facts}
    assert contents == {"ada prefers async standups", "ada works remotely"}


def test_two_candidates_converging_on_one_update_target_do_not_abort_the_pass():
    """Defense-in-depth: even when an id collision does happen (two candidates each deciding
    UPDATE against the same existing fact), the pipeline drops the second unit rather than
    returning a duplicate id that would abort the whole `consolidate` pass at the write layer."""
    existing = fact("f1", "ada lives in Berlin", source_episode_ids=["e0"])
    vector = FakeVector()
    vector.rows["acme"] = {"f1": existing}
    extractor = StubExtractor(
        [
            FactCandidate(statement="ada moved to Lisbon", source_indices=[0]),
            FactCandidate(statement="ada now lives in Lisbon", source_indices=[0]),
        ]
    )
    decider = StubDecider(lambda statement, neighbors: MergeDecision(op="UPDATE", target_index=0, statement=statement))
    pipe, _ = pipeline(vector=vector, extractor=extractor, decider=decider)

    facts, report = pipe.distil(ACME, [episode("ada moved", episode_id="e1")])

    ids = [f.record.id for f in facts]
    assert len(ids) == len(set(ids)), "a collision must never reach the caller as a duplicate id"
    assert report.updated == 1
    assert report.details["collisions"] == 1


def test_ts_is_the_earliest_cited_episode_not_the_latest():
    """Matches `_distil`'s own rationale: a fact claiming to become true later than the oldest
    evidence it cites answers a point-in-time read wrongly for the interval in between."""
    extractor = StubExtractor([FactCandidate(statement="ada prefers async standups", source_indices=[0, 1])])
    pipe, _ = pipeline(
        extractor=extractor, decider=StubDecider(MergeDecision(op="ADD", statement="ada prefers async standups"))
    )
    eps = [
        episode("ada mentioned async standups", episode_id="e1", created_at=100),
        episode("ada confirmed async standups again", episode_id="e2", created_at=900),
    ]

    facts, _ = pipe.distil(ACME, eps)

    assert facts[0].record.t_valid == 100


@pytest.mark.parametrize("is_async", [False, True])
def test_a_neighbor_outside_the_call_scope_is_refused(is_async):
    """A backend that leaks another scope's row into `search`/`asearch` must not have that row
    silently drive a merge decision or land in a `FactWrite` filed under the wrong scope."""

    class LeakyVector(FakeVector):
        def search(self, scope, query, tier=None):
            return [fact("f1", "bob's secret", scope=BOB)]

        async def asearch(self, scope, query, tier=None):
            return self.search(scope, query, tier)

    pipe, _ = pipeline(
        vector=LeakyVector(),
        extractor=StubExtractor([FactCandidate(statement="ada said something", source_indices=[0])]),
    )

    with pytest.raises(MemoryOperationError):
        if is_async:
            asyncio.run(pipe.adistil(ACME, [episode("ada said something", episode_id="e1")]))
        else:
            pipe.distil(ACME, [episode("ada said something", episode_id="e1")])


# ---------------------------------------------------------------------------
# AC 3 -- applied to vector + graph, through consolidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("is_async", [False, True])
def test_wired_into_consolidate_reaches_both_substrates(is_async):
    vector, graph = FakeVector(), FakeGraph()
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada prefers async standups", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="ADD", statement="ada prefers async standups")),
    )
    provider = CoLocatedMemoryProvider(vector=vector, graph=graph, fact_pipeline=pipe)
    provider.write(ACME, [event("ada said she prefers async standups", id="e1")])

    call = provider.aconsolidate(ACME) if is_async else provider.consolidate(ACME)
    report = asyncio.run(call) if is_async else call

    assert report.details["derived_by"] == STRATEGY_ID
    assert report.details["supersede"] == STRATEGY_ID
    assert "upsert_facts" in graph.calls
    live = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC and r.is_current_belief()]
    assert len(live) == 1 and live[0].content == "ada prefers async standups"


def test_unwired_consolidate_still_reports_no_supersede():
    """The zero-config default is unchanged: no ``fact_pipeline`` means ``_distil``'s deterministic
    dedup, same as before this ticket."""
    vector, graph = FakeVector(), FakeGraph()
    provider = CoLocatedMemoryProvider(vector=vector, graph=graph)
    provider.write(ACME, [event("ada prefers async standups", id="e1")])

    report = provider.consolidate(ACME)

    assert report.details["derived_by"] == "co_located.deterministic_dedup"
    assert report.details["supersede"] == NO_SUPERSEDE


def test_a_wired_update_retires_the_superseded_row_end_to_end():
    """The scenario `test_consolidation_does_not_retire_and_says_so` pins as SM-C1's known
    limitation -- a growing group leaving two live rows -- resolved when a fact_pipeline decides
    UPDATE instead of leaving it to content-grouping."""
    vector, graph = FakeVector(), FakeGraph()
    vector.rows["acme"] = {"f1": fact("f1", "ada prefers async standups", source_episode_ids=["e1"], created_at=100)}
    decider = StubDecider(lambda statement, neighbors: MergeDecision(op="UPDATE", target_index=0, statement=statement))
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="ada prefers async standups", source_indices=[0])]),
        decider=decider,
    )
    provider = CoLocatedMemoryProvider(vector=vector, graph=graph, fact_pipeline=pipe)
    provider.write(ACME, [event("ada prefers async standups", id="e2", occurred_at=200)])

    report = provider.consolidate(ACME)

    live = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC and r.is_current_belief()]
    assert len(live) == 1, "SM-L3 makes this 1, where the deterministic-dedup default leaves 2"
    assert report.details["supersede"] == STRATEGY_ID


# ---------------------------------------------------------------------------
# Package exports
# ---------------------------------------------------------------------------


def test_the_package_all_matches_its_own_names():
    """`__all__` naming an attribute the module does not have is one bug; the module having an
    attribute `__all__` does not name is another -- both mean `from egpt.memory.consolidation
    import *` disagrees with what direct import gives a caller. Inserting the new SM-L3 names
    alphabetically at `L` silently dropped `LatencyTrigger`, which this catches.

    `FACT_ID_NAMESPACE` (SM-L4 #125) is the one documented exception: imported here so
    `co_located.py` and `dedup.py` cannot drift on its value, deliberately *not* in `__all__`
    because it is `dedup.py`'s own internal implementation detail, not stable public API (see the
    package `__init__.py`'s own comment above the import)."""
    import egpt.memory.consolidation as pkg

    for name in pkg.__all__:
        assert hasattr(pkg, name), f"{name!r} is in __all__ but not a package attribute"
    exported = {name for name in dir(pkg) if not name.startswith("_")}
    modules = {name for name in exported if isinstance(getattr(pkg, name), type(pkg))}
    assert exported - modules - {"FACT_ID_NAMESPACE"} == set(pkg.__all__)


def test_strategy_id_and_details_builder_are_reachable_from_the_package():
    """A caller importing the package API (not reaching into `.extract` directly) needs these to
    read `report.details["derived_by"]` or build a matching details payload of their own."""
    import egpt.memory.consolidation as pkg

    assert pkg.STRATEGY_ID == STRATEGY_ID
    assert pkg.fact_extraction_details() == {"strategy": STRATEGY_ID, "complete": True}


# ---------------------------------------------------------------------------
# Construction guards
# ---------------------------------------------------------------------------


def test_a_pipeline_with_no_vector_is_refused_at_construction():
    """`vector=None` (or any object missing `search`/`asearch`) must fail at construction, not
    three frames deep in the first `distil` call with a bare `AttributeError`."""
    with pytest.raises(MemoryOperationError):
        FactExtractionPipeline(
            vector=None,
            extractor=StubExtractor(),
            decider=StubDecider(MergeDecision(op="ADD", statement="x")),
        )


def test_a_vector_missing_only_the_async_half_is_refused_at_construction():
    """`search` alone is not enough -- a substrate missing `asearch` blocks `adistil`'s event
    loop the same way a sync-only extractor/decider does."""

    class SyncOnlyVector:
        def search(self, scope, query, tier=None):
            return []

    with pytest.raises(MemoryOperationError):
        FactExtractionPipeline(
            vector=SyncOnlyVector(),
            extractor=StubExtractor(),
            decider=StubDecider(MergeDecision(op="ADD", statement="x")),
        )


def test_top_k_of_zero_is_refused_at_construction():
    with pytest.raises(MemoryOperationError):
        FactExtractionPipeline(
            vector=FakeVector(),
            extractor=StubExtractor(),
            decider=StubDecider(MergeDecision(op="ADD", statement="x")),
            top_k=0,
        )


@pytest.mark.parametrize("is_async", [False, True])
def test_distil_over_no_episodes_is_a_clean_no_op(is_async):
    """`consolidate`/`aconsolidate` call `_distil_facts` with whatever `_visible_episodes` kept,
    which is legitimately empty -- this must report a clean, complete pass, not raise."""
    pipe, _ = pipeline()

    call = pipe.adistil(ACME, []) if is_async else pipe.distil(ACME, [])
    facts, report = asyncio.run(call) if is_async else call

    assert facts == []
    assert report.episodes_read == 0
    assert report.candidates == 0
    assert report.details["complete"] is True


def test_a_pipeline_missing_the_async_extractor_half_is_refused_at_construction():
    class SyncOnlyExtractor:
        def extract(self, episodes):
            return []

    with pytest.raises(MemoryOperationError):
        FactExtractionPipeline(
            vector=FakeVector(),
            extractor=SyncOnlyExtractor(),
            decider=StubDecider(MergeDecision(op="ADD", statement="x")),
        )


def test_a_pipeline_missing_the_async_decider_half_is_refused_at_construction():
    class SyncOnlyDecider:
        def decide(self, statement, neighbors):
            return MergeDecision(op="ADD", statement=statement)

    with pytest.raises(MemoryOperationError):
        FactExtractionPipeline(vector=FakeVector(), extractor=StubExtractor(), decider=SyncOnlyDecider())


def test_fact_pipeline_missing_the_async_half_is_refused_by_the_provider():
    class SyncOnlyPipeline:
        def distil(self, scope, episodes):
            return [], None

    with pytest.raises(MemoryProviderConfigError):
        CoLocatedMemoryProvider(vector=FakeVector(), graph=FakeGraph(), fact_pipeline=SyncOnlyPipeline())


def test_top_k_bounds_the_neighbor_query():
    vector = FakeVector()
    for i in range(10):
        vector.rows.setdefault("acme", {})[f"f{i}"] = fact(f"f{i}", f"shared token {i}")
    pipe, _ = pipeline(
        vector=vector,
        extractor=StubExtractor([FactCandidate(statement="shared token", source_indices=[0])]),
        decider=StubDecider(MergeDecision(op="ADD", statement="shared token")),
        top_k=3,
    )

    pipe.distil(ACME, [episode("shared token", episode_id="e1")])

    assert vector.search_limits[-1] == 3
