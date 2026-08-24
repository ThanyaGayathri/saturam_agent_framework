"""The PPR leg against a real GDS-backed engine (SM-H4 #134).

Gated on ``EGPT_INTEGRATION_NEO4J_GDS`` (``tests/integration/conftest.py``'s path-fragment table,
matched before the general ``memory/neo4j`` -> ``EGPT_INTEGRATION_NEO4J`` entry -- this file does
not need both set); start the engine with ``cookbook/scripts/run_neo4j_gds.sh``. Separate from
``test_graph_substrate.py``'s suite because GDS is an optional plugin most of that suite's tests
never need -- see the script's own comment on why it is not folded into the default dev instance.

**Why this file exists at all, and not just the statement-shape guards in
``tests/unit/memory/test_neo4j_graph_statements.py``.** Those assert the Cypher looks right; they
cannot tell a projection-time predicate that never actually excludes anything from one that does,
or a personalized seed from a fallback to unseeded global PageRank -- exactly the two failure
modes SM-R2's own live probe (``benchmarks/memory_graph_engine/run_ppr_route.py``) went looking
for on a real engine rather than trusting the mechanism by inspection. This suite runs the same
checks against this backend's actual schema (bi-temporal ``Entity``/``REL`` rows, not the spike's
toy five-node graph), which is what makes AC 1 ("PPR retrieves multi-hop evidence") and AC 3
("composes with bi-temporal filtering") real properties rather than assertions about intent.
"""

import os

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate  # noqa: E402
from egpt.memory.types import EntityWrite, MemoryQuery  # noqa: E402

from .test_graph_substrate import ACME, fact, ids  # noqa: E402


def _settings():
    return {
        "uri": os.getenv("NEO4J_GDS_URI", "bolt://localhost:7692"),
        "username": os.getenv("NEO4J_GDS_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_GDS_PASSWORD", "smh4testpassword"),
    }


def _drop_all_ppr_graphs(substrate):
    """Best-effort GDS catalog cleanup between tests, so one test's leak cannot fail another's."""
    with substrate._driver.session(database=substrate.database) as session:
        names = [r["graphName"] for r in session.run("CALL gds.graph.list() YIELD graphName RETURN graphName")]
        for name in names:
            session.run("CALL gds.graph.drop($n, false) YIELD graphName RETURN graphName", n=name).consume()


@pytest.fixture
def graph():
    substrate = Neo4jGraphSubstrate(**_settings(), multihop_ppr_enabled=True, multihop_ppr_seed_top_m=3)
    with substrate._driver.session(database=substrate.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    _drop_all_ppr_graphs(substrate)
    yield substrate
    _drop_all_ppr_graphs(substrate)
    substrate.close()


@pytest.fixture
def graph_flag_off():
    """The identical engine, PPR left at its default -- proves the flag actually gates the leg."""
    substrate = Neo4jGraphSubstrate(**_settings())
    with substrate._driver.session(database=substrate.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield substrate
    substrate.close()


def raw_ids(rows):
    """``ids()`` (imported from ``test_graph_substrate``) expects ``MemoryRecord``s -- what
    ``traverse``/``atraverse`` return. ``_ppr_rows``/``_appr_rows`` return the plain ``dict`` rows
    one level below that (the same shape ``_record`` would turn into a ``MemoryRecord``), for tests
    that want the PPR leg in isolation rather than merged into a full ``traverse`` call."""
    return sorted(row["id"] for row in rows)


def _chain(graph):
    """A-B-C-D chain, each hop one relation: ``max_traverse_hops`` defaults to 2, so BFS from an
    anchor on "a" reaches B (hop 1) and C (hop 2) but not D (hop 3) -- the gap PPR's single-step
    ranked walk does not have, because it ranks the whole projected graph's stationary
    distribution rather than stopping at a hop ceiling."""
    graph.upsert_facts(
        ACME,
        [
            fact("f1", "alpha relates_to bravo", subject="alpha", predicate="relates_to", object="bravo"),
            fact("f2", "bravo relates_to charlie", subject="bravo", predicate="relates_to", object="charlie"),
            fact("f3", "charlie relates_to delta", subject="charlie", predicate="relates_to", object="delta"),
        ],
    )


def test_gds_is_actually_installed_and_personalization_is_real(graph):
    """Setup check, not an SM-H4 property: fails loudly and specifically if the engine this test
    run points at has no GDS plugin, rather than every test below failing on an opaque Cypher
    'unknown procedure' error. Mirrors SM-R2's own probe: seeding from two different anchors must
    produce two different rankings, or a silent fallback to unseeded global PageRank would still
    pass every other assertion here."""
    _chain(graph)

    from_alpha = graph._ppr_rows(ACME, MemoryQuery(text="alpha", limit=10))
    from_delta = graph._ppr_rows(ACME, MemoryQuery(text="delta", limit=10))

    assert from_alpha, "PPR seeded from alpha found nothing -- is the GDS plugin actually installed?"
    # Keyed by id, not compared as sorted score lists: a straight chain (alpha-bravo-charlie-delta)
    # is symmetric under reversal, so alpha-seeded and delta-seeded runs produce the *same multiset*
    # of score values by that symmetry alone (f1 and f3 swap ranks but not magnitudes) -- comparing
    # sorted lists would pass even under unseeded, non-personalized PageRank on this graph shape.
    # The id -> score mapping is what actually differs: f1 (alpha's own edge) outranks f3 when
    # seeded from alpha, and the reverse when seeded from delta.
    scores_from_alpha = {row["id"]: row["score"] for row in from_alpha}
    scores_from_delta = {row["id"]: row["score"] for row in from_delta}
    assert scores_from_alpha != scores_from_delta, (
        "seeding from two different anchors produced identical id->score rankings -- looks like unseeded PageRank"
    )
    assert scores_from_alpha["f1"] > scores_from_alpha["f3"], "alpha-seeded must rank alpha's own edge highest"
    assert scores_from_delta["f3"] > scores_from_delta["f1"], "delta-seeded must rank delta's own edge highest"


def test_ppr_surfaces_multihop_evidence_bfs_cannot_reach(graph):
    """AC 1 ("PPR retrieves multi-hop evidence") and the ticket's own Testing Note ("a multi-hop
    question is answered via PPR paths"), pinned as a regression rather than only a spike claim.

    `f3` (charlie-delta) is 3 hops from an anchor on "alpha" -- past `max_traverse_hops`'s default
    of 2 -- so the plain BFS leg cannot return it (confirmed directly below, not assumed). PPR's
    single-step ranked walk is not hop-bounded the same way, and does surface it.
    """
    _chain(graph)
    query = MemoryQuery(text="alpha", limit=10)

    baseline = Neo4jGraphSubstrate(**_settings())
    try:
        bfs_alone = baseline.traverse(ACME, query)
        assert "f3" not in ids(bfs_alone), "setup check: f3 must be past the BFS hop bound, or this proves nothing"
    finally:
        baseline.close()

    with_ppr = graph.traverse(ACME, query)
    assert "f3" in ids(with_ppr), "PPR must surface f3, the multi-hop evidence BFS alone cannot reach"


def test_the_flag_actually_gates_the_leg(graph_flag_off):
    """AC 2 ("behind a per-tenant flag ... off by default"), proved by disproof: with the flag at
    its default, the same multi-hop evidence a flagged-on substrate finds must NOT appear."""
    _chain(graph_flag_off)

    assert graph_flag_off.multihop_ppr_enabled is False, "setup check: this fixture's whole point is the default"
    hits = graph_flag_off.traverse(ACME, MemoryQuery(text="alpha", limit=10))
    assert "f3" not in ids(hits), "flag-off must not exercise the PPR leg at all"


def test_ppr_composes_with_the_bitemporal_predicate(graph):
    """AC 3 ("composes with bi-temporal filtering"), exercised on this backend's real bi-temporal
    edges -- not the spike's toy chain and not asserted by inspection. SM-R10 section 7's 5a
    predicate (verbatim, per ADR 0003 section B.2) is a **valid-time** window, not "gone the moment
    it is superseded": ``graph.invalidate(t_invalid=200)`` records that the fact was true in the
    world through t=200, and SCD-2's expire-and-reinsert keeps that belief queryable at any as-of
    strictly before 200 through the successor row (the *original* physical row is what gets
    ``expired_at`` set -- the successor inherits the logical id with a fresh, live row and the new
    bound). So the correctness property is the upper bound itself: present up to ``t_invalid``,
    excluded once ``as_of`` passes it.
    """
    graph.upsert_facts(
        ACME,
        [fact("f1", "alpha relates_to bravo", subject="alpha", predicate="relates_to", object="bravo", t_valid=100)],
    )
    graph.invalidate(ACME, "f1", t_invalid=200, at=210)

    as_of_within_validity = MemoryQuery(text="alpha", limit=10, as_of=150)
    rows_within = graph._ppr_rows(ACME, as_of_within_validity)
    assert "f1" in raw_ids(rows_within), "a fact must still seed/extend PPR before its own t_invalid bound"

    as_of_past_invalidation = MemoryQuery(text="alpha", limit=10, as_of=100_000)
    rows_after = graph._ppr_rows(ACME, as_of_past_invalidation)
    # This is the property `coalesce(t_invalid, infinity)` exists to get right: this row has a
    # concrete, non-null `t_invalid`, so it must be excluded once `as_of` passes it -- the failure
    # mode ADR 0003 section B.2 names is the *open-ended* (`t_invalid IS NULL`) case wrongly
    # excluded, not this one, but the upper-bound comparison is the same predicate clause.
    assert "f1" not in raw_ids(rows_after), "an invalidated fact must be excluded once as_of passes its t_invalid"


def test_an_open_ended_currently_true_edge_is_not_silently_dropped(graph):
    """The other half of SM-R10 section 7's 5a predicate: an edge with no known ``t_invalid`` (an
    open-ended, currently-true fact) must still be included -- ``coalesce(t_invalid, infinity)``,
    not a bare ``t_invalid > $T`` that a ``NULL`` comparison would silently fail."""
    graph.upsert_facts(
        ACME,
        [fact("f1", "alpha relates_to bravo", subject="alpha", predicate="relates_to", object="bravo", t_valid=100)],
    )

    rows = graph._ppr_rows(ACME, MemoryQuery(text="alpha", limit=10, as_of=1_000_000))

    assert "f1" in raw_ids(rows), "an open-ended, still-current edge must not drop out of the projection"


async def test_sync_and_async_ppr_agree():
    """The async twin makes the same decisions as the sync half -- not merely that it exists."""
    substrate = Neo4jGraphSubstrate(**_settings(), multihop_ppr_enabled=True)
    with substrate._driver.session(database=substrate.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    _drop_all_ppr_graphs(substrate)
    try:
        _chain(substrate)
        query = MemoryQuery(text="alpha", limit=10)

        sync_hits = ids(substrate.traverse(ACME, query))
        async_hits = ids(await substrate.atraverse(ACME, query))

        assert sync_hits == async_hits
        assert "f3" in sync_hits, "setup check: the multi-hop evidence must actually be present to compare"
    finally:
        _drop_all_ppr_graphs(substrate)
        await substrate.aclose()


def test_a_query_naming_no_entity_contributes_nothing_not_a_wrong_answer(graph):
    """Round-1 review, Major, against a real engine (not assumed from the statement shape): GDS's
    ``gds.pageRank.stream(..., {sourceNodes: []})`` does not refuse -- it silently falls back to
    unseeded global PageRank, which this leg's own max-normalisation then hands a full-strength
    score, indistinguishable from a genuinely personalized result. Measured directly: before the
    fix, this exact scenario returned an unrelated fact at `score=1.0`. The fix must make it return
    nothing, and must not raise either (the reviewer's originally-suspected failure mode was an
    abort; measured, it wasn't -- see the PR reply on this thread).
    """
    _chain(graph)

    rows = graph._ppr_rows(ACME, MemoryQuery(text="zzz shares no word with any entity", limit=10))

    assert rows == [], "an empty anchor set must contribute nothing, not an unseeded global ranking"


def test_ppr_score_never_exceeds_the_bfs_legs_ceiling(graph):
    """Round-1 review, Major, measured against a real ranked run rather than only asserted on the
    statement text: every returned score must sit at or below the BFS leg's own ceiling, so PPR
    cannot structurally outrank every possible BFS hit in `_merge_hits`'s global sort/limit cut."""
    _chain(graph)

    rows = graph._ppr_rows(ACME, MemoryQuery(text="alpha", limit=10))

    assert rows, "setup check: must actually rank something to bound"
    assert all(row["score"] <= 0.5 + 1e-9 for row in rows), [row["score"] for row in rows]


def test_ppr_window_honours_overlap_not_a_collapsed_point(graph):
    """Round-1 review, Major, the worked counter-example from the review reproduced against a real
    engine: a fact valid ``[100, 200)`` under a query window ``[100, 1000)`` overlaps that window
    (visible to every other leg via `ValidWindow.overlaps`) but fails a point-in-time check at
    ``T=1000`` (`1000 < 200` is false) -- which is exactly what an earlier version's `valid_to`
    collapse did. The fix must surface it under the window, not silently drop it.
    """
    graph.upsert_facts(
        ACME,
        [
            fact(
                "f1",
                "alpha relates_to bravo",
                subject="alpha",
                predicate="relates_to",
                object="bravo",
                t_valid=100,
                t_invalid=200,
            )
        ],
    )

    rows = graph._ppr_rows(ACME, MemoryQuery(text="alpha", limit=10, valid_from=100, valid_to=1000))

    assert "f1" in raw_ids(rows), "an edge whose validity overlaps the window must be visible under PPR too"


def test_concurrent_same_scope_ppr_calls_do_not_collide(graph):
    """Round-1 review, Major: a deterministic per-scope GDS catalog name let two concurrent PPR
    calls for the same scope race on one entry. Two real concurrent calls against the live engine,
    each against a distinct chain so a wrong-topology result would be visible as the wrong evidence
    rather than merely an exception."""
    import threading

    graph.upsert_entities(
        ACME,
        [EntityWrite(name=n, entity_type="place", scope=ACME) for n in ("north1", "north2", "north3", "north4")],
    )
    graph.upsert_facts(
        ACME,
        [
            fact("n1", "north1 relates_to north2", subject="north1", predicate="relates_to", object="north2"),
            fact("n2", "north2 relates_to north3", subject="north2", predicate="relates_to", object="north3"),
            fact("n3", "north3 relates_to north4", subject="north3", predicate="relates_to", object="north4"),
        ],
    )
    _chain(graph)  # a second, independent chain sharing the same scope

    results: dict = {}
    errors: list = []
    barrier = threading.Barrier(2)

    def run(key, text):
        try:
            barrier.wait(timeout=10)
            results[key] = raw_ids(graph._ppr_rows(ACME, MemoryQuery(text=text, limit=10)))
        except Exception as exc:  # a loud, correctly-scoped failure would also be an accepted outcome
            errors.append((key, exc))

    threads = [
        threading.Thread(target=run, args=("north", "north1")),
        threading.Thread(target=run, args=("alpha", "alpha")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"concurrent calls must not fail each other: {errors}"
    assert set(results.get("north", [])) & {"n1", "n2", "n3"}, results.get("north")
    assert "f3" in results.get("alpha", []), results.get("alpha")
    # The sharpest check: neither call's topology leaked into the other's result.
    assert not set(results.get("north", [])) & {"f1", "f2", "f3"}, "north-seeded call saw the alpha chain"
    assert not set(results.get("alpha", [])) & {"n1", "n2", "n3"}, "alpha-seeded call saw the north chain"
