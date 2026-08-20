"""The anchor-seeded multi-hop leg of ``GraphSubstrate.traverse`` (SM-G9 #107).

Acceptance criteria (issue #107):

* Traversal returns ranked candidates from anchor entities.
* Bi-temporal filter respected.
* Feeds fusion (SM-H2); no query-time LLM.

Exercised against ``FakeGraph``, which mirrors ``Neo4jGraphSubstrate``'s anchor-selection and
hop-scoring rules in plain Python (see its own docstring). The Cypher shape itself is guarded by
``test_neo4j_graph_statements.py``; the behaviour against a real engine is
``tests/integration/memory/neo4j/test_graph_substrate.py``.
"""

import asyncio
from dataclasses import replace

from egpt.memory.tiers import MemoryTier
from egpt.memory.types import EntityWrite, FactWrite, MemoryQuery, MemoryRecord, Scope
from tests.unit.memory._fakes import FakeGraph

ACME = Scope(tenant_id="acme", user_id="ada")


def entity(name, scope=ACME):
    return EntityWrite(name=name, entity_type="thing", scope=scope)


def edge(id_, subject, predicate, object_, scope=ACME, t_valid=100, t_invalid=None):
    record = MemoryRecord(
        id=id_,
        tier=MemoryTier.SEMANTIC,
        content=f"{subject} {predicate} {object_}",
        scope=scope,
        t_valid=t_valid,
        t_invalid=t_invalid,
    )
    return FactWrite(record=record, subject=subject, predicate=predicate, object=object_)


def graph_with_chain(scope=ACME, max_traverse_hops=2):
    """acme -[:REL]-> berlin -[:REL]-> europe, a two-hop chain from the anchor "acme"."""
    graph = FakeGraph(max_traverse_hops=max_traverse_hops)
    graph.upsert_entities(scope, [entity("acme", scope), entity("berlin", scope), entity("europe", scope)])
    graph.upsert_facts(
        scope,
        [
            edge("hop1", "acme", "located_in", "berlin", scope=scope),
            edge("hop2", "berlin", "part_of", "europe", scope=scope),
        ],
    )
    return graph


# -- AC: ranked candidates from anchor entities -------------------------------


def test_a_fact_with_no_lexical_overlap_is_surfaced_by_its_anchor_entity():
    """The point of the leg: the query text shares no word with the fact's content at all.

    ``edge()``'s content always repeats the subject's name, so a query naming that same subject
    would also satisfy the flat direct-content leg -- not an isolated test of this one. Phrased
    without "acme" in the content at all, so only anchor resolution (subject/object name, not
    content) can reach it.
    """
    graph = FakeGraph()
    graph.upsert_entities(ACME, [entity("acme"), entity("berlin")])
    graph.upsert_facts(
        ACME,
        [
            FactWrite(
                record=MemoryRecord(
                    id="hop1", tier=MemoryTier.SEMANTIC, content="it is headquartered there", scope=ACME, t_valid=100
                ),
                subject="acme",
                predicate="located_in",
                object="berlin",
            )
        ],
    )

    hits = {r.id for r in graph.traverse(ACME, MemoryQuery(text="tell me about acme"))}

    assert "hop1" in hits


def test_a_second_hop_is_reachable_within_the_configured_bound():
    graph = graph_with_chain(max_traverse_hops=2)

    hits = {r.id: r for r in graph.traverse(ACME, MemoryQuery(text="acme"))}

    assert {"hop1", "hop2"} <= set(hits)
    assert hits["hop1"].score > hits["hop2"].score, "a nearer hop must outrank a farther one"


def test_a_hop_beyond_the_configured_bound_is_not_returned():
    """Closed at the source (hard scope limit): the bound is a backend ceiling, not a caller ask."""
    graph = graph_with_chain(max_traverse_hops=1)

    hits = {r.id for r in graph.traverse(ACME, MemoryQuery(text="acme"))}

    assert "hop1" in hits
    assert "hop2" not in hits


def test_the_anchor_set_is_capped_and_ordered_deterministically():
    """Round-1 review, Major: the hop bound alone does not cap this leg's cost -- a common query
    word matches every entity whose name contains it. Round-2 review: the double had no matching
    test at all. Two entities both match "acme"; capped to 1, only the first-sorted one becomes an
    anchor, so only its edge is walkable. Content is phrased without "acme" in it, so the flat
    direct-content leg cannot also explain a pass (same isolation ``graph_with_chain``'s own tests
    use).
    """
    graph = FakeGraph(max_traverse_anchor_entities=1)
    graph.upsert_entities(ACME, [entity("acme alpha"), entity("acme beta")])
    graph.upsert_facts(
        ACME,
        [
            FactWrite(
                record=MemoryRecord(
                    id="f-alpha", tier=MemoryTier.SEMANTIC, content="it operates here", scope=ACME, t_valid=100
                ),
                subject="acme alpha",
                predicate="operates",
                object="widgets",
            ),
            FactWrite(
                record=MemoryRecord(
                    id="f-beta", tier=MemoryTier.SEMANTIC, content="it operates there", scope=ACME, t_valid=100
                ),
                subject="acme beta",
                predicate="operates",
                object="gadgets",
            ),
        ],
    )

    hits = {r.id for r in graph.traverse(ACME, MemoryQuery(text="tell me about acme"))}

    # Deterministic by name: "acme alpha" sorts before "acme beta".
    assert "f-alpha" in hits, "the first-sorted anchor must still reach its edge"
    assert "f-beta" not in hits, "the second-sorted anchor must be cut by the cap"


def test_no_entity_lexically_matches_the_query_yields_no_multihop_candidates():
    """An anchor set of zero is a legitimate answer -- nothing to walk from, not an error."""
    graph = graph_with_chain()

    hits = graph.traverse(ACME, MemoryQuery(text="completely unrelated words"))

    assert hits == []


def test_an_entity_with_no_relation_contributes_no_multihop_candidate():
    """An anchor with no edges is not a bug -- it simply has nothing within any hop bound."""
    graph = FakeGraph()
    graph.upsert_entities(ACME, [entity("solo")])

    hits = graph.traverse(ACME, MemoryQuery(text="solo"))

    assert hits == []


def test_an_untripled_fact_is_unreachable_by_the_multihop_leg_but_still_returned_directly():
    """Documented scope boundary: no SPO means no entity to anchor from."""
    graph = FakeGraph()
    graph.upsert_entities(ACME, [entity("ada")])
    graph.upsert_facts(
        ACME,
        [
            FactWrite(
                record=MemoryRecord(
                    id="untripled", tier=MemoryTier.SEMANTIC, content="ada is diligent", scope=ACME, t_valid=100
                )
            )
        ],
    )

    hits = {r.id for r in graph.traverse(ACME, MemoryQuery(text="ada is diligent"))}

    assert "untripled" in hits, "still reachable via the direct-content leg"


def test_a_bare_relation_endpoint_with_no_explicit_entity_upsert_is_still_a_valid_anchor():
    """``RelationExtractionPipeline`` (SM-G3 #101) calls only ``upsert_facts``, never
    ``upsert_entities`` -- the real backend's triple-write MERGEs a bare endpoint node carrying a
    ``name`` but no normalised ``key`` (only ``upsert_entities_statement`` writes that). Anchor
    selection keyed on ``self.entities`` (which only ``upsert_entities``/``merge_entities``
    populate) alone would make every such node permanently unreachable, silently missing most
    production-extracted relations -- this is the regression that guards it, mirroring
    ``test_a_bare_relation_endpoint_with_no_explicit_entity_upsert_is_still_a_valid_anchor`` in the
    real-engine integration suite.

    Isolated to the multihop leg specifically, not merely to a passing assertion: ``hop1``'s own
    content ("acme located_in berlin") shares the word "acme" with the query, so the flat
    direct-content leg would find it regardless of anchor resolution. ``hop2``'s content ("berlin
    part_of europe") shares no word with the query at all -- it is reachable only by resolving
    "acme" as an anchor from its bare fact-endpoint name and walking one hop.
    """
    graph = FakeGraph()
    # No `upsert_entities` call at all -- every entity here exists only as a fact edge endpoint.
    graph.upsert_facts(
        ACME,
        [edge("hop1", "acme", "located_in", "berlin"), edge("hop2", "berlin", "part_of", "europe")],
    )
    assert (ACME.key(), "acme") not in graph.entities, "setup check: no explicit entity write"

    hits = {r.id for r in graph.traverse(ACME, MemoryQuery(text="tell me about acme"))}

    assert "hop2" in hits, "unreachable without the bare-endpoint anchor fallback"


# -- AC: bi-temporal filter respected ------------------------------------------


def test_an_out_of_window_edge_is_excluded_from_the_multihop_leg():
    graph = FakeGraph()
    graph.upsert_entities(ACME, [entity("acme"), entity("berlin")])
    graph.upsert_facts(ACME, [edge("hop1", "acme", "located_in", "berlin", t_valid=100, t_invalid=200)])

    windowed = MemoryQuery(text="acme", valid_from=400, valid_to=600)
    assert graph.traverse(ACME, windowed) == []

    unwindowed = MemoryQuery(text="acme")
    assert {r.id for r in graph.traverse(ACME, unwindowed)} == {"hop1"}


def test_an_out_of_window_edge_cannot_be_walked_through_to_reach_a_farther_hop():
    """The window shapes the walk itself, not only what is returned (SM-G9 #107)."""
    graph = graph_with_chain(max_traverse_hops=2)
    # Move hop1's valid time outside the window: hop2 is reachable only by walking through it.
    graph.rows[ACME.tenant_id] = [
        replace(r, t_valid=100, t_invalid=200) if r.id == "hop1" else r for r in graph.rows[ACME.tenant_id]
    ]

    windowed = MemoryQuery(text="acme", valid_from=400, valid_to=600)
    hits = {r.id for r in graph.traverse(ACME, windowed)}
    assert hits == set(), "hop1 is outside the window, so hop2 is unreachable through it"

    # A positive companion, not only the negative assertion (round-2 review, Critical): a purely
    # negative check passes vacuously if the whole leg silently returns nothing regardless of the
    # window. Confirms hop2 *is* reachable through hop1 once the window stops excluding it.
    unwindowed = {r.id for r in graph.traverse(ACME, MemoryQuery(text="acme"))}
    assert "hop2" in unwindowed


# -- AC: feeds fusion; no query-time LLM ---------------------------------------


def test_multihop_scores_are_similarities_in_the_unit_interval():
    graph = graph_with_chain()

    hits = graph.traverse(ACME, MemoryQuery(text="acme"))

    assert hits
    assert all(0.0 < (r.score or 0.0) <= 1.0 for r in hits)


def test_traversal_makes_no_model_or_embedding_call():
    """LLM-free by construction: FakeGraph has no model client to call, and anchor selection is
    pure word overlap against ``EntityWrite.key`` -- no ``embedding`` field is read anywhere in the
    multi-hop path. Asserted by seeding entities with no embedding at all and confirming the walk
    still finds them."""
    graph = FakeGraph()
    graph.upsert_entities(ACME, [entity("acme"), entity("berlin")])
    assert graph.entities[(ACME.key(), "acme")].embedding is None
    graph.upsert_facts(ACME, [edge("hop1", "acme", "located_in", "berlin")])

    hits = graph.traverse(ACME, MemoryQuery(text="acme"))

    assert {r.id for r in hits} == {"hop1"}


def test_sync_and_async_traversal_agree():
    graph = graph_with_chain()

    sync_hits = graph.traverse(ACME, MemoryQuery(text="acme"))
    async_hits = asyncio.run(graph.atraverse(ACME, MemoryQuery(text="acme")))

    assert [r.id for r in sync_hits] == [r.id for r in async_hits]
    assert [r.score for r in sync_hits] == [r.score for r in async_hits]


def test_a_wider_scope_traversal_does_not_bridge_two_users_private_entity_graphs():
    """Anchor selection stays inside one exact scope (SM-C4 #92); a name collision under a
    tenant-wide read must not let one user's walk cross into another's."""
    ada = Scope(tenant_id="acme", user_id="ada")
    bob = Scope(tenant_id="acme", user_id="bob")
    graph = FakeGraph()
    graph.upsert_entities(ada, [entity("acme", ada), entity("berlin", ada)])
    graph.upsert_entities(bob, [entity("acme", bob), entity("tokyo", bob)])
    graph.upsert_facts(ada, [edge("ada-hop", "acme", "located_in", "berlin", scope=ada)])
    graph.upsert_facts(bob, [edge("bob-hop", "acme", "located_in", "tokyo", scope=bob)])

    tenant_wide = Scope(tenant_id="acme")
    hits = {r.id: r.scope for r in graph.traverse(tenant_wide, MemoryQuery(text="acme"))}

    assert hits == {"ada-hop": ada, "bob-hop": bob}, "each walk must stay inside its own writer's scope"


def test_the_multihop_dedup_key_carries_scope_not_only_id():
    """Two different users' rows can legitimately share one id (SM-C1's dedup key is per-scope,
    not global); a bare-id dedup would silently collapse them into one under a tenant-wide read.

    Set up so the **same** edge id is walkable from **both** users' otherwise-identical private
    entity graphs -- a bare-id dedup keeps only one of the two, whichever scores higher.
    """
    ada = Scope(tenant_id="acme", user_id="ada")
    bob = Scope(tenant_id="acme", user_id="bob")
    graph = FakeGraph()
    graph.upsert_entities(ada, [entity("acme", ada), entity("berlin", ada)])
    graph.upsert_entities(bob, [entity("acme", bob), entity("tokyo", bob)])
    graph.upsert_facts(ada, [edge("shared-edge-id", "acme", "located_in", "berlin", scope=ada)])
    graph.upsert_facts(bob, [edge("shared-edge-id", "acme", "located_in", "tokyo", scope=bob)])

    hits = graph.traverse(Scope(tenant_id="acme"), MemoryQuery(text="acme"))

    assert {(r.id, r.scope) for r in hits} == {("shared-edge-id", ada), ("shared-edge-id", bob)}


def test_feeds_fusion_through_the_provider_read_path():
    """End to end: a query that never lexically touches a fact's content still surfaces it, via
    ``MemoryProvider.read`` -- the seam SM-H2 fuses from."""
    from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
    from egpt.memory.config import MemoryProviderConfig
    from tests.unit.memory._fakes import FakeVector

    graph = graph_with_chain()
    vector = FakeVector()
    provider = CoLocatedMemoryProvider(vector=vector, graph=graph, config=MemoryProviderConfig(provider="co_located"))

    result = provider.read(ACME, MemoryQuery(text="tell me about acme"))

    # `hop1`'s content ("acme located_in berlin") shares "acme" with the query, so the pre-existing
    # lexical leg alone would satisfy an assertion on it -- this would pass even with
    # `_multihop_edges` deleted entirely. `hop2`'s content ("berlin part_of europe") shares no word
    # with the query at all; it is reachable only through the multi-hop leg, so its presence is what
    # actually proves that leg feeds fusion (round-3 review).
    assert "hop2" in {r.id for r in result.records}
