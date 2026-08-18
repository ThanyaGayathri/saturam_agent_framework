"""Entity resolution against a real engine (SM-G8 #106).

``test_graph_substrate.py`` and ``test_neo4j_graph_statements.py`` cover the statements this
module's two new verbs -- ``match_entities`` and ``merge_entities`` -- are built from. What only a
real engine proves is the piece those cannot: that ``db.index.vector.queryNodes`` actually returns
a candidate's neighbours through the vector index this ticket adds, and that ``DETACH DELETE`` on a
duplicate node genuinely leaves no dangling edge behind.
"""

import os

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate  # noqa: E402
from egpt.memory.graph.resolve import EntityResolutionPipeline  # noqa: E402
from egpt.memory.types import EntityMerge, EntityWrite, MemoryEvent, MemoryOperationError, Scope  # noqa: E402

ACME = Scope(tenant_id="acme", user_id="ada")
BOB = Scope(tenant_id="acme", user_id="bob")

#: The vector index is declared for 768 dimensions (ADR 0003 A.3); a query built against any other
#: width is not "no match", it is a request the index cannot answer.
_DIM = 768


def _vec(*hot, base=0.0):
    """A 768-dim vector, mostly ``base``, with ``hot`` overriding the leading components.

    Real embeddings, not this -- but two vectors sharing every component except the ones under
    test have a known cosine similarity, which is what lets a test assert "close" or "far" without
    depending on a real embedder's output.
    """
    values = [base] * _DIM
    for i, value in enumerate(hot):
        values[i] = value
    return values


def _settings():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    }


@pytest.fixture
def graph():
    substrate = Neo4jGraphSubstrate(**_settings())
    with substrate._driver.session(database=substrate.database) as session:
        session.run("MATCH (n) DETACH DELETE n")
    yield substrate
    substrate.close()


@pytest.fixture
async def async_graph():
    """A substrate for tests that drive the async half, with teardown that can await.

    Sync ``close()`` refuses while an async driver is open (see its own docstring), so a test that
    touched ``amatch_entities``/``amerge_entities`` needs this fixture rather than ``graph``.
    """
    substrate = Neo4jGraphSubstrate(**_settings())
    async with substrate._async_driver().session(database=substrate.database) as session:
        result = await session.run("MATCH (n) DETACH DELETE n")
        await result.consume()
    yield substrate
    await substrate.aclose()


def entity_write(name, scope=ACME, **kwargs):
    kwargs.setdefault("entity_type", "company")
    kwargs.setdefault("embedding", _vec(1.0))
    kwargs.setdefault("embedding_version", "test/d768")
    return EntityWrite(name=name, scope=scope, **kwargs)


def node_count(graph, scope=ACME):
    with graph._driver.session(database=graph.database) as session:
        return session.run(
            "MATCH (n:Entity {scope_key: $scope_key}) RETURN count(n) AS n", scope_key=scope.key()
        ).single()["n"]


def mentions(graph, episode_id, scope=ACME):
    with graph._driver.session(database=graph.database) as session:
        return [
            row["name"]
            for row in session.run(
                "MATCH (e:Episode {scope_key: $scope_key, id: $id})-[:MENTIONS]->(n:Entity) RETURN n.name AS name",
                scope_key=scope.key(),
                id=episode_id,
            )
        ]


# -- match_entities -----------------------------------------------------------


def test_an_exact_key_match_scores_1_0_and_excludes_the_candidates_own_name(graph):
    """A key match is scored ``1.0`` unconditionally -- it does not depend on the embedding leg, so
    the candidate's own (unrelated) vector is deliberately used here rather than a matching one."""
    graph.upsert_entities(ACME, [entity_write("Acme Corp.", embedding=_vec(1.0))])

    matches = graph.match_entities(ACME, entity_write("acme  corp", embedding=_vec(0.0, 1.0)))

    assert [(m.name, score) for m, score in matches] == [("Acme Corp.", 1.0)]


def test_an_embedding_neighbour_below_min_score_is_excluded(graph):
    """An orthogonal vector reliably scores ``0.5`` against the index (empirically pinned, not
    derived from the index's normalisation formula: the HNSW index behind ``vector.similarity_
    function: cosine`` is approximate and its scores for genuinely dissimilar vectors are not
    exact, but a candidate at 90 degrees is comfortably below any threshold worth setting)."""
    graph.upsert_entities(ACME, [entity_write("Nimbus", embedding=_vec(0.0, 1.0))])

    matches = graph.match_entities(ACME, entity_write("Atlas", embedding=_vec(1.0)), min_score=0.6)

    assert matches == []


def test_an_embedding_neighbour_above_min_score_is_returned_with_its_normalised_score(graph):
    graph.upsert_entities(ACME, [entity_write("Acme Corporation", embedding=_vec(0.99, 0.01))])

    matches = graph.match_entities(ACME, entity_write("Acme", embedding=_vec(1.0)), min_score=0.9)

    (match, score) = matches[0]
    assert match.name == "Acme Corporation"
    assert 0.9 <= score <= 1.0


def test_a_match_never_crosses_a_scope_boundary(graph):
    """Isolation, over the vector leg specifically -- the key leg is already MERGE-scoped."""
    graph.upsert_entities(BOB, [entity_write("Acme", scope=BOB, embedding=_vec(1.0))])

    matches = graph.match_entities(ACME, entity_write("Acme Corp", embedding=_vec(1.0)))

    assert matches == []


def test_match_entities_refuses_an_unembedded_candidate(graph):
    with pytest.raises(MemoryOperationError, match="embedding"):
        graph.match_entities(ACME, EntityWrite(name="Acme", entity_type="company", scope=ACME))


async def test_amatch_entities_agrees_with_the_sync_reads(async_graph):
    async_graph.upsert_entities(ACME, [entity_write("Acme Corp.")])

    sync_matches = async_graph.match_entities(ACME, entity_write("acme  corp"))
    async_matches = await async_graph.amatch_entities(ACME, entity_write("acme  corp"))

    assert [(m.name, s) for m, s in sync_matches] == [(m.name, s) for m, s in async_matches]


# -- merge_entities -------------------------------------------------------------


def test_a_merge_leaves_exactly_one_node_carrying_the_union_of_both(graph):
    graph.upsert_entities(
        ACME,
        [
            entity_write("Acme", source_episode_ids=["e1"], aliases=["ACME Inc"]),
            entity_write("Acme Corporation", source_episode_ids=["e2"]),
        ],
    )

    graph.merge_entities(
        ACME,
        [
            EntityMerge(
                canonical=entity_write(
                    "Acme", source_episode_ids=["e1", "e2"], aliases=["ACME Inc", "Acme Corporation"]
                ),
                duplicate_names=["Acme Corporation"],
            )
        ],
    )

    assert node_count(graph) == 1
    with graph._driver.session(database=graph.database) as session:
        props = session.run(
            "MATCH (n:Entity {scope_key: $scope_key, name: 'Acme'}) RETURN properties(n) AS props",
            scope_key=ACME.key(),
        ).single()["props"]
    assert sorted(props["source_episode_ids"]) == ["e1", "e2"]
    assert sorted(props["aliases"]) == ["ACME Inc", "Acme Corporation"]


def test_a_merge_repoints_mentions_edges_and_leaves_no_dangling_edge_on_the_deleted_node(graph):
    graph.upsert_episodes(ACME, [MemoryEvent(id="e1", content="ep1", scope=ACME, entities=["Acme"])])
    graph.upsert_episodes(ACME, [MemoryEvent(id="e2", content="ep2", scope=ACME, entities=["Acme Corporation"])])
    graph.upsert_entities(
        ACME,
        [entity_write("Acme", source_episode_ids=["e1"]), entity_write("Acme Corporation", source_episode_ids=["e2"])],
    )

    graph.merge_entities(
        ACME,
        [
            EntityMerge(
                canonical=entity_write("Acme", source_episode_ids=["e1", "e2"]),
                duplicate_names=["Acme Corporation"],
            )
        ],
    )

    assert mentions(graph, "e1") == ["Acme"]
    assert mentions(graph, "e2") == ["Acme"]
    with graph._driver.session(database=graph.database) as session:
        dangling = session.run(
            "MATCH ()-[r:MENTIONS]->(n:Entity {scope_key: $scope_key, name: 'Acme Corporation'}) RETURN count(r) AS n",
            scope_key=ACME.key(),
        ).single()["n"]
    assert dangling == 0


def test_a_merge_never_touches_another_scopes_node_of_the_same_name(graph):
    graph.upsert_entities(ACME, [entity_write("Acme"), entity_write("Acme Corporation")])
    graph.upsert_entities(BOB, [entity_write("Acme Corporation", scope=BOB)])

    graph.merge_entities(ACME, [EntityMerge(canonical=entity_write("Acme"), duplicate_names=["Acme Corporation"])])

    assert node_count(graph, ACME) == 1
    assert node_count(graph, BOB) == 1


def test_merge_entities_refuses_a_canonical_from_a_foreign_scope(graph):
    with pytest.raises(MemoryOperationError, match="scope"):
        graph.merge_entities(
            ACME, [EntityMerge(canonical=entity_write("Acme", scope=BOB), duplicate_names=["Acme Corp"])]
        )


def test_an_empty_merge_batch_is_a_no_op_rather_than_a_round_trip(graph):
    graph.upsert_entities(ACME, [entity_write("Acme")])
    graph.merge_entities(ACME, [])
    assert node_count(graph) == 1


async def test_amerge_entities_leaves_the_same_durable_state_as_the_sync_half(async_graph):
    async_graph.upsert_entities(ACME, [entity_write("Acme"), entity_write("Acme Corporation")])

    await async_graph.amerge_entities(
        ACME, [EntityMerge(canonical=entity_write("Acme"), duplicate_names=["Acme Corporation"])]
    )

    assert node_count(async_graph) == 1


# -- EntityResolutionPipeline, end to end ---------------------------------------


def test_the_pipeline_resolves_two_spellings_across_episodes_end_to_end(graph):
    """The acceptance criteria together: duplicate entities merged, edges updated, aliases kept --
    driven through the pipeline rather than by calling the port verbs by hand."""
    graph.upsert_episodes(ACME, [MemoryEvent(id="e1", content="ep1", scope=ACME, entities=["Acme"])])
    graph.upsert_episodes(ACME, [MemoryEvent(id="e2", content="ep2", scope=ACME, entities=["Acme Corporation"])])
    graph.upsert_entities(
        ACME,
        [
            entity_write("Acme", source_episode_ids=["e1"], embedding=_vec(1.0)),
            entity_write("Acme Corporation", source_episode_ids=["e2"], embedding=_vec(0.99, 0.01)),
        ],
    )

    pipeline = EntityResolutionPipeline(graph=graph, min_score=0.9)
    report = pipeline.run(
        ACME,
        [
            entity_write("Acme", source_episode_ids=["e1"], embedding=_vec(1.0)),
            entity_write("Acme Corporation", source_episode_ids=["e2"], embedding=_vec(0.99, 0.01)),
        ],
    )

    assert report.merged == 1
    assert node_count(graph) == 1
    survivor_mentions_e1 = mentions(graph, "e1")
    survivor_mentions_e2 = mentions(graph, "e2")
    assert survivor_mentions_e1 == survivor_mentions_e2  # both point at the one surviving node
