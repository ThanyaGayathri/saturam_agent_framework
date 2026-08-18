"""Entity resolution: merging duplicate candidates the graph already holds (SM-G8 #106).

The three acceptance criteria, each with a test that fails without the code: duplicate entities
merge into one node, the ``MENTIONS`` edges into the losing nodes are re-pointed at the survivor,
and the surface forms folded away are retained as aliases rather than lost.
"""

import asyncio

import pytest

from egpt.memory.graph.episodic import entity_node_id_for
from egpt.memory.graph.resolve import (
    RESOLUTION_KEYS,
    RESOLVER_ID,
    EntityResolutionPipeline,
    resolution_details,
)
from egpt.memory.types import EntityMerge, EntityWrite, MemoryEvent, MemoryOperationError, Scope
from tests.unit.memory._fakes import FakeGraph

ACME = Scope(tenant_id="acme", user_id="ada")
BOB = Scope(tenant_id="acme", user_id="bob")


def ew(name, entity_type="company", vec=(1.0, 0.0, 0.0), aliases=None, eids=None, scope=ACME, **kwargs):
    return EntityWrite(
        name=name,
        entity_type=entity_type,
        scope=scope,
        embedding=list(vec) if vec is not None else None,
        embedding_version="stub/d3",
        aliases=aliases or [],
        source_episode_ids=eids or [],
        **kwargs,
    )


def seeded(graph, *entities):
    """Write ``entities`` straight to the double, as a prior pass already would have."""
    graph.upsert_entities(ACME, list(entities))
    return graph


def resolver(graph=None, **kwargs):
    graph = graph if graph is not None else FakeGraph()
    return EntityResolutionPipeline(graph=graph, **kwargs), graph


# ---------------------------------------------------------------------------
# AC 1 -- duplicate entities merged
# ---------------------------------------------------------------------------


def test_two_spellings_sharing_a_normalised_key_merge():
    """ "Acme Corp." and "acme  corp" share a key but not a name -- entities.py leaves them apart,
    exact-key matching here is what folds them."""
    pipe, graph = resolver()
    seeded(graph, ew("Acme Corp."), ew("acme  corp"))

    report = pipe.run(ACME, [ew("Acme Corp."), ew("acme  corp")])

    assert report.merged == 1
    assert sorted(name for _, name in graph.entities) == ["Acme Corp."]


def test_two_spellings_sharing_no_key_merge_on_embedding_similarity():
    """ "Acme" and "Acme Corporation" (test_two_spellings_stay_two_candidates, SM-G2) share no key.
    This is the boundary SM-G8 exists to close, via the embedding leg."""
    pipe, graph = resolver(min_score=0.9)
    seeded(graph, ew("Acme", vec=(1.0, 0.0, 0.0)), ew("Acme Corporation", vec=(0.99, 0.01, 0.0)))

    report = pipe.run(ACME, [ew("Acme", vec=(1.0, 0.0, 0.0)), ew("Acme Corporation", vec=(0.99, 0.01, 0.0))])

    assert report.merged == 1
    (survivor,) = [name for _, name in graph.entities]
    assert survivor in {"Acme", "Acme Corporation"}


def test_a_candidate_below_the_similarity_floor_does_not_merge():
    pipe, graph = resolver(min_score=0.95)
    seeded(graph, ew("Atlas", vec=(1.0, 0.0, 0.0)), ew("Nimbus", vec=(0.0, 1.0, 0.0)))

    report = pipe.run(ACME, [ew("Atlas", vec=(1.0, 0.0, 0.0)), ew("Nimbus", vec=(0.0, 1.0, 0.0))])

    assert report.merged == 0
    assert sorted(name for _, name in graph.entities) == ["Atlas", "Nimbus"]


def test_a_candidate_never_matches_its_own_prior_write():
    """Seeing "Acme" again is not a duplicate of itself; it is the same node. No match means no
    merge -- and this pipeline never calls ``upsert_entities`` on its own, so the prior write is
    left exactly as its writer left it."""
    pipe, graph = resolver()
    seeded(graph, ew("Acme", eids=["e1"]))

    report = pipe.run(ACME, [ew("Acme", eids=["e2"])])

    assert report.merged == 0
    assert graph.entities[(ACME.key(), "Acme")].source_episode_ids == ["e1"]


def test_three_spellings_in_one_pass_resolve_to_one_node():
    """ "Atlas" alone matches both "atlas" (key) and "Project Atlas" (embedding) in one
    ``match_entities`` call, so all three fold into one node from a single merge."""
    pipe, graph = resolver(min_score=0.9)
    seeded(
        graph,
        ew("Atlas", vec=(1.0, 0.0, 0.0)),
        ew("atlas", vec=(1.0, 0.0, 0.0)),  # same key as "Atlas"
        ew("Project Atlas", vec=(0.99, 0.02, 0.0)),  # close to "Atlas" on embedding
    )

    report = pipe.run(
        ACME,
        [
            ew("Atlas", vec=(1.0, 0.0, 0.0)),
            ew("atlas", vec=(1.0, 0.0, 0.0)),
            ew("Project Atlas", vec=(0.99, 0.02, 0.0)),
        ],
    )

    assert report.groups == 1
    assert report.merged == 2
    assert len({name for _, name in graph.entities}) == 1


def test_a_second_candidate_already_folded_by_the_first_is_skipped_not_re_matched():
    """The "atlas" candidate is a duplicate the *first* candidate's merge already absorbed; the
    pass must not match it again -- ``FakeGraph.calls`` proves only one ``merge_entities`` ran."""
    pipe, graph = resolver(min_score=0.9)
    seeded(graph, ew("Atlas", vec=(1.0, 0.0, 0.0)), ew("atlas", vec=(1.0, 0.0, 0.0)))

    pipe.run(ACME, [ew("Atlas", vec=(1.0, 0.0, 0.0)), ew("atlas", vec=(1.0, 0.0, 0.0))])

    assert graph.calls.count("merge_entities") == 1


def test_a_repeated_candidate_name_that_wins_its_own_merge_is_not_re_matched():
    """Review finding on PR #96 (round 2): when the *first* occurrence of a repeated name wins
    its own merge as canonical, its name never lands in ``merge.duplicate_names`` -- so without
    also adding ``candidate.name`` to ``folded``, a second literal occurrence of that name in the
    same batch would be matched again (finding nothing, since its duplicate is already gone, but
    at the cost of a redundant ``match_entities`` round trip)."""
    pipe, graph = resolver(min_score=0.9)
    seeded(graph, ew("Acme"), ew("Acme Corp", vec=(0.99, 0.01, 0.0)))

    pipe.run(ACME, [ew("Acme"), ew("Acme"), ew("Acme Corp", vec=(0.99, 0.01, 0.0))])

    assert graph.calls.count("match_entities") == 1


def test_no_match_leaves_the_candidate_exactly_as_its_writer_left_it():
    pipe, graph = resolver()
    seeded(graph, ew("Solo", vec=(1.0, 0.0, 0.0)))

    report = pipe.run(ACME, [ew("Solo", vec=(1.0, 0.0, 0.0))])

    assert report.candidates == 1
    assert report.merged == 0
    assert report.groups == 0
    assert graph.entities[(ACME.key(), "Solo")].entity_type == "company"


# ---------------------------------------------------------------------------
# AC 2 -- edges updated on merge
# ---------------------------------------------------------------------------


def test_edges_into_the_losing_node_are_repointed_at_the_survivor():
    pipe, graph = resolver(min_score=0.9)
    graph.upsert_episodes(ACME, [MemoryEvent(id="e1", content="ep1", scope=ACME, entities=["Acme"])])
    graph.upsert_episodes(ACME, [MemoryEvent(id="e2", content="ep2", scope=ACME, entities=["Acme Corporation"])])
    seeded(graph, ew("Acme", eids=["e1"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), eids=["e2"]))

    pipe.run(ACME, [ew("Acme", eids=["e1"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), eids=["e2"])])

    (survivor,) = [name for _, name in graph.entities]
    survivor_id = entity_node_id_for(ACME, survivor)
    for row in graph._bucket(ACME):
        if row.id in ("e1", "e2"):
            assert row.provenance["entity_ids"] == [survivor_id]


def test_an_episode_already_mentioning_the_survivor_is_not_double_linked():
    """An episode naming both spellings once must end up mentioning the merged node once, not
    twice -- ``link_mentions_statement``'s ``MERGE`` on ``link_id`` is what this pins in the double."""
    pipe, graph = resolver(min_score=0.9)
    graph.upsert_episodes(
        ACME, [MemoryEvent(id="e1", content="ep1", scope=ACME, entities=["Acme", "Acme Corporation"])]
    )
    seeded(graph, ew("Acme", eids=["e1"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), eids=["e1"]))

    pipe.run(ACME, [ew("Acme", eids=["e1"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), eids=["e1"])])

    (row,) = [r for r in graph._bucket(ACME) if r.id == "e1"]
    assert len(row.provenance["entity_ids"]) == 1


def test_merge_never_touches_another_users_episodes():
    """SM-G2/SM-G6's isolation guarantee extends to a merge: entity identity is scoped to the full
    scope, and Bob's episodes must be untouched by an Ada-scoped resolution pass."""
    pipe, graph = resolver(min_score=0.9)
    graph.upsert_episodes(BOB, [MemoryEvent(id="b1", content="bob ep", scope=BOB, entities=["Acme"])])
    seeded(graph, ew("Acme", eids=["e1"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), eids=["e2"]))

    pipe.run(ACME, [ew("Acme", eids=["e1"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), eids=["e2"])])

    (bob_row,) = [r for r in graph._bucket(BOB) if r.id == "b1"]
    assert bob_row.provenance["entity_ids"] == [entity_node_id_for(BOB, "Acme")]
    assert (BOB.key(), "Acme") not in graph.entities


# ---------------------------------------------------------------------------
# AC 3 -- aliases retained
# ---------------------------------------------------------------------------


def test_the_losing_names_become_aliases_of_the_survivor():
    pipe, graph = resolver(min_score=0.9)
    seeded(graph, ew("Acme", aliases=["ACME Inc"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0)))

    pipe.run(ACME, [ew("Acme", aliases=["ACME Inc"]), ew("Acme Corporation", vec=(0.99, 0.01, 0.0))])

    (survivor,) = graph.entities.values()
    assert "Acme Corporation" in survivor.aliases
    assert "ACME Inc" in survivor.aliases
    assert survivor.name not in survivor.aliases


def test_the_canonical_tiebreak_is_deterministic_when_confidence_type_and_description_all_tie():
    """The typical LLM-extraction case: every candidate shares the default ``confidence=1.0`` and
    the same ``entity_type``/``description``, so the tiebreak falls all the way through to
    ``tuple(source_episode_ids)`` -- the smaller tuple, lexicographically, wins as head. Pinned
    directly against ``_canonical`` rather than through a full resolve pass, so a change to the
    tiebreak order shows up here first."""
    first = ew("Acme", eids=["e1"])
    second = ew("Acme Corporation", eids=["e2"])

    canonical = EntityResolutionPipeline._canonical([second, first])

    assert canonical.name == "Acme"  # ("e1",) < ("e2",), so `first` is head regardless of order


def test_an_alias_equal_to_the_survivors_own_name_is_not_duplicated():
    pipe, graph = resolver(min_score=0.9)
    seeded(graph, ew("Acme"), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), aliases=["Acme"]))

    pipe.run(ACME, [ew("Acme"), ew("Acme Corporation", vec=(0.99, 0.01, 0.0), aliases=["Acme"])])

    (survivor,) = graph.entities.values()
    assert survivor.aliases.count(survivor.name) == 0


# ---------------------------------------------------------------------------
# report shape
# ---------------------------------------------------------------------------


def test_the_report_carries_every_resolution_key():
    pipe, graph = resolver(min_score=0.7)
    report = pipe.run(ACME, [])

    assert report.details.keys() >= set(RESOLUTION_KEYS)
    assert report.details["resolver"] == RESOLVER_ID
    assert report.details["min_score"] == 0.7


def test_resolution_details_carries_every_key_with_extras_free():
    details = resolution_details(resolver="x", min_score=0.5, extra="y")
    assert details["resolver"] == "x"
    assert details["min_score"] == 0.5
    assert details["extra"] == "y"


def test_an_empty_candidate_list_is_a_no_op_not_an_error():
    pipe, graph = resolver()
    report = pipe.run(ACME, [])
    assert report.candidates == 0
    assert report.merged == 0
    assert "merge_entities" not in graph.calls


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_candidate_from_a_foreign_scope_is_refused():
    pipe, graph = resolver()
    with pytest.raises(MemoryOperationError, match="scope"):
        pipe.run(ACME, [ew("Acme", scope=BOB)])


def test_an_unembedded_candidate_is_refused_rather_than_silently_unmatched():
    pipe, graph = resolver()
    seeded(graph, ew("Acme"))
    with pytest.raises(MemoryOperationError, match="embedding"):
        pipe.run(ACME, [ew("Acme", vec=None)])


@pytest.mark.parametrize("min_score", [-0.1, 1.1, "high", True])
def test_min_score_outside_range_or_wrong_type_is_refused(min_score):
    with pytest.raises(MemoryOperationError, match="min_score"):
        EntityResolutionPipeline(graph=FakeGraph(), min_score=min_score)


@pytest.mark.parametrize("min_score", [0.0, 1.0])
def test_min_score_at_either_boundary_is_accepted(min_score):
    EntityResolutionPipeline(graph=FakeGraph(), min_score=min_score)


@pytest.mark.parametrize("match_limit", [0, -1, 1.5, True])
def test_match_limit_below_one_or_wrong_type_is_refused(match_limit):
    with pytest.raises(MemoryOperationError, match="match_limit"):
        EntityResolutionPipeline(graph=FakeGraph(), match_limit=match_limit)


def test_a_graph_missing_either_async_verb_is_refused_at_construction():
    class HalfGraph:
        def match_entities(self, *a, **k): ...
        def merge_entities(self, *a, **k): ...

        # amatch_entities / amerge_entities deliberately absent

    with pytest.raises(MemoryOperationError, match="amatch_entities|amerge_entities"):
        EntityResolutionPipeline(graph=HalfGraph())


def test_entity_merge_refuses_an_empty_duplicate_list():
    with pytest.raises(MemoryOperationError, match="duplicate_names"):
        EntityMerge(canonical=ew("Acme"), duplicate_names=[])


def test_entity_merge_refuses_the_canonical_naming_itself_as_a_duplicate():
    with pytest.raises(MemoryOperationError, match="duplicate_names"):
        EntityMerge(canonical=ew("Acme"), duplicate_names=["Acme"])


def test_entity_merge_deduplicates_repeated_duplicate_names():
    merge = EntityMerge(canonical=ew("Acme"), duplicate_names=["Acme Corp", "Acme Corp", "  Acme Corp  "])
    assert merge.duplicate_names == ["Acme Corp"]


# ---------------------------------------------------------------------------
# sync/async parity
# ---------------------------------------------------------------------------


def test_arun_makes_the_same_decision_as_run():
    sync_pipe, sync_graph = resolver(min_score=0.9)
    seeded(sync_graph, ew("Acme"), ew("Acme Corporation", vec=(0.99, 0.01, 0.0)))
    sync_report = sync_pipe.run(ACME, [ew("Acme"), ew("Acme Corporation", vec=(0.99, 0.01, 0.0))])

    async_pipe, async_graph = resolver(min_score=0.9)
    seeded(async_graph, ew("Acme"), ew("Acme Corporation", vec=(0.99, 0.01, 0.0)))
    async_report = asyncio.run(async_pipe.arun(ACME, [ew("Acme"), ew("Acme Corporation", vec=(0.99, 0.01, 0.0))]))

    assert async_report.merged == sync_report.merged
    assert async_report.groups == sync_report.groups
    assert sorted(name for _, name in async_graph.entities) == sorted(name for _, name in sync_graph.entities)


def test_arun_also_refuses_an_unembedded_candidate():
    pipe, graph = resolver()
    seeded(graph, ew("Acme"))
    with pytest.raises(MemoryOperationError, match="embedding"):
        asyncio.run(pipe.arun(ACME, [ew("Acme", vec=None)]))


# ---------------------------------------------------------------------------
# FakeGraph conformance with Neo4jGraphSubstrate (review findings on PR #96, round 3)
# ---------------------------------------------------------------------------


def test_fakegraph_merge_entities_refuses_a_canonical_that_is_also_a_duplicate_elsewhere():
    graph = FakeGraph()
    graph.upsert_entities(ACME, [ew("Acme"), ew("Acme Corp"), ew("Acme Ltd")])

    with pytest.raises(MemoryOperationError, match="merges"):
        graph.merge_entities(
            ACME,
            [
                EntityMerge(canonical=ew("Acme"), duplicate_names=["Acme Corp"]),
                EntityMerge(canonical=ew("Acme Corp"), duplicate_names=["Acme Ltd"]),
            ],
        )


def test_fakegraph_merge_entities_refuses_the_same_duplicate_name_in_two_merges():
    graph = FakeGraph()
    graph.upsert_entities(ACME, [ew("Acme"), ew("ACME Inc"), ew("shared")])

    with pytest.raises(MemoryOperationError, match="duplicate in more than one merge"):
        graph.merge_entities(
            ACME,
            [
                EntityMerge(canonical=ew("Acme"), duplicate_names=["shared"]),
                EntityMerge(canonical=ew("ACME Inc"), duplicate_names=["shared"]),
            ],
        )


def test_fakegraph_merge_entities_never_erases_the_survivors_stored_embedding():
    """The canonical a merge writes never carries an embedding of its own -- it comes from
    ``EntityResolutionPipeline._canonical``, whose head can be a ``match_entities`` result, and
    ``match_entities`` never returns one. ``_upsert_entity`` (shared with plain ``upsert_entities``)
    must preserve what the node already has rather than overwrite it with ``None``."""
    graph = FakeGraph()
    graph.upsert_entities(ACME, [ew("Acme", vec=(1.0, 0.0, 0.0))])
    graph.upsert_entities(ACME, [ew("Acme Corp", vec=(0.99, 0.01, 0.0))])

    graph.merge_entities(ACME, [EntityMerge(canonical=ew("Acme", vec=None), duplicate_names=["Acme Corp"])])

    assert graph.entities[(ACME.key(), "Acme")].embedding == [1.0, 0.0, 0.0]


def test_fakegraph_upsert_entities_never_erases_an_existing_embedding_on_a_description_only_write():
    graph = FakeGraph()
    graph.upsert_entities(ACME, [ew("Acme", vec=(1.0, 0.0, 0.0))])

    graph.upsert_entities(ACME, [ew("Acme", vec=None, description="later")])

    stored = graph.entities[(ACME.key(), "Acme")]
    assert stored.embedding == [1.0, 0.0, 0.0]
    assert stored.description == "later"


def test_fakegraph_match_entities_returns_copies_not_live_references():
    graph = FakeGraph()
    graph.upsert_entities(ACME, [ew("Acme"), ew("Acme Corp", vec=(0.99, 0.01, 0.0))])

    ((match, _score),) = graph.match_entities(ACME, ew("Acme"), min_score=0.9)
    match.aliases.append("mutated")

    assert graph.entities[(ACME.key(), "Acme Corp")].aliases == []


def test_report_candidates_counts_only_the_names_actually_attempted():
    """Review finding on PR #96 (round 3): a repeated literal name skipped by the ``folded`` guard
    must not inflate ``report.candidates`` beyond what was actually offered for matching. Four
    literal candidates go in; "Acme" resolves against "Acme Corp" on the first attempt, which
    folds both names -- so the second "Acme" and the literal "Acme Corp" are both skipped, and
    only "Acme" (once) and "Nimbus" are actually attempted."""
    pipe, graph = resolver(min_score=0.9)
    seeded(graph, ew("Acme"), ew("Acme Corp", vec=(0.99, 0.01, 0.0)), ew("Nimbus", vec=(0.0, 1.0, 0.0)))

    report = pipe.run(
        ACME,
        [
            ew("Acme"),
            ew("Acme"),
            ew("Acme Corp", vec=(0.99, 0.01, 0.0)),
            ew("Nimbus", vec=(0.0, 1.0, 0.0)),
        ],
    )

    assert report.candidates == 2
