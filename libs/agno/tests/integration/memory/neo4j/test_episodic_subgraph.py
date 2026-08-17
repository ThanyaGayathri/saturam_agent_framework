"""The episodic subgraph against a real engine (SM-G6 #104).

Gated on ``EGPT_INTEGRATION_NEO4J``; start the engine with ``cookbook/scripts/run_neo4j.sh``.

The three acceptance criteria are here rather than in the unit tests because each of them is a
claim about *storage*: an episode node that keeps its content, edges that reach real entity nodes,
and a read that finds them by time and by link. A mention edge asserted against a double written in
the same change is unfalsifiable -- the unit file asserts the rules, this one asserts the engine
obeys them.

Most properties run over ``any_graph``: the reference double and Neo4j behind one name. Where the
two disagree, one of them is wrong, and every provider test written against the double is resting
on it.
"""

import os

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate, cypher  # noqa: E402
from egpt.memory.graph.episodic import MENTIONS, entity_node_id_for  # noqa: E402
from egpt.memory.schemas.episodic import episode_from_event  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MAX_LIMIT, MemoryEvent, MemoryOperationError, MemoryQuery, Scope  # noqa: E402
from tests.unit.memory._fakes import FakeGraph  # noqa: E402

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")
TENANT_WIDE = Scope(tenant_id="acme")


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
    substrate = Neo4jGraphSubstrate(**_settings())
    async with substrate._async_driver().session(database=substrate.database) as session:
        result = await session.run("MATCH (n) DETACH DELETE n")
        await result.consume()
    yield substrate
    await substrate.aclose()


@pytest.fixture(params=["neo4j", "double"])
def any_graph(request, graph):
    return graph if request.param == "neo4j" else FakeGraph()


def episode(id_, content="ada moved to berlin", scope=ACME, entities=None, occurred_at=100):
    return MemoryEvent(
        content=content,
        scope=scope,
        id=id_,
        tier=MemoryTier.EPISODIC,
        entities=entities,
        occurred_at=occurred_at,
    )


def ids(records):
    return [r.id for r in records]


def linked(record):
    return record.provenance["entity_ids"]


# -- AC 1: raw episodes are stored as episodic nodes -------------------------


def test_the_episode_node_keeps_the_raw_content_and_its_time(any_graph):
    """AC 1. Non-lossy is the whole point of the tier: nothing here summarises, scores or drops a
    field on the way in."""
    any_graph.upsert_episodes(ACME, [episode("e1", content="ada moved to berlin in march", occurred_at=1_700_000_000)])

    hits = any_graph.recall_episodes(ACME, since=1_700_000_000)

    assert ids(hits) == ["e1"]
    assert hits[0].content == "ada moved to berlin in march"
    assert hits[0].t_valid == 1_700_000_000
    assert hits[0].tier is MemoryTier.EPISODIC


def test_an_episode_with_mentions_is_still_one_node_not_one_per_entity(graph):
    """The mentions are edges off the episode, not copies of it. A shape that duplicated the row
    per entity would return the same episode three times from every read."""
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin", "ada", "paris"])])

    with graph._driver.session(database=graph.database) as session:
        nodes = session.run(f"MATCH (e:{cypher.EPISODE}) RETURN count(e) AS n").single()["n"]
        edges = session.run(f"MATCH (:{cypher.EPISODE})-[m:{MENTIONS}]->(:Entity) RETURN count(m) AS n").single()["n"]

    assert (nodes, edges) == (1, 3)


# -- AC 2: linked to the entities they mention -------------------------------


def test_an_episode_is_linked_to_every_entity_it_mentions(any_graph):
    """AC 2, and the testing note's "store an episode; traverse to its entities": the ids come back
    off the **edges**, so this fails if the write linked nothing and only stored a list."""
    any_graph.upsert_episodes(ACME, [episode("e1", entities=["berlin", "ada"])])

    hit = any_graph.recall_episodes(ACME, since=0)[0]

    assert linked(hit) == [entity_node_id_for(ACME, "berlin"), entity_node_id_for(ACME, "ada")]


def test_the_edges_and_the_episode_row_name_the_same_entity_nodes(any_graph):
    """The two views of one link, asserted against each other rather than each against a literal.
    ``EpisodicRecord.entity_ids`` is built by the projection; this list is built from the graph."""
    event = episode("e1", entities=["berlin", "ada"])
    any_graph.upsert_episodes(ACME, [event])

    assert linked(any_graph.recall_episodes(ACME, since=0)[0]) == episode_from_event(event).entity_ids


def test_an_episode_that_mentions_nothing_carries_an_empty_link_list(any_graph):
    """A floor, not an absence: a reader that branches on the key must not have to guard it."""
    any_graph.upsert_episodes(ACME, [episode("e1")])

    assert linked(any_graph.recall_episodes(ACME, since=0)[0]) == []


def test_two_episodes_naming_one_entity_share_its_node(graph):
    """One entity node per (scope, name), which is the identity ``upsert_facts`` already merges on
    -- so a mention and a fact about one entity meet rather than forking into two nodes."""
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"]), episode("e2", entities=["berlin"])])

    with graph._driver.session(database=graph.database) as session:
        entities = session.run("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]

    assert entities == 1
    assert ids(graph.recall_episodes(ACME, entities=["berlin"])) == ["e1", "e2"]


def test_one_users_entity_node_is_not_another_users(graph):
    """Entities merge on the **full** scope. Sharing them across users inside a tenant would make
    the isolation guarantee depend on the entity-resolution policy SM-G8 (#106) has not written."""
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    graph.upsert_episodes(ACME_BOB, [episode("e2", scope=ACME_BOB, entities=["berlin"])])

    with graph._driver.session(database=graph.database) as session:
        entities = session.run("MATCH (n:Entity) RETURN count(n) AS n").single()["n"]

    assert entities == 2
    assert ids(graph.recall_episodes(ACME, entities=["berlin"])) == ["e1"]


def test_re_upserting_an_episode_replaces_its_mentions_rather_than_unioning_them(any_graph):
    """Upsert is by id, so the edges go with the episode. Left unioned, an episode keeps reading as
    mentioning an entity its current content does not name -- and no read can tell."""
    any_graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    any_graph.upsert_episodes(ACME, [episode("e1", content="ada moved to paris", entities=["paris"])])

    assert any_graph.recall_episodes(ACME, entities=["berlin"]) == []
    assert ids(any_graph.recall_episodes(ACME, entities=["paris"])) == ["e1"]
    assert linked(any_graph.recall_episodes(ACME, since=0)[0]) == [entity_node_id_for(ACME, "paris")]


def test_re_upserting_the_same_mentions_does_not_duplicate_the_edges(graph):
    """The uniqueness constraint behind the merge key, exercised. Without it the second write adds
    a parallel edge and every recall returns the entity twice."""
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])

    with graph._driver.session(database=graph.database) as session:
        edges = session.run(f"MATCH ()-[m:{MENTIONS}]->() RETURN count(m) AS n").single()["n"]

    assert edges == 1
    assert linked(graph.recall_episodes(ACME, since=0)[0]) == [entity_node_id_for(ACME, "berlin")]


def test_the_mention_order_survives_the_round_trip(any_graph):
    """A graph returns edges in whatever order it stores them. Without the recorded ``position``
    this list is a permutation of the row's ``entity_ids`` on every read, and a real disagreement
    between the two views would be indistinguishable from that noise."""
    names = ["berlin", "ada", "paris", "zurich", "bob"]
    any_graph.upsert_episodes(ACME, [episode("e1", entities=names)])

    assert linked(any_graph.recall_episodes(ACME, since=0)[0]) == [entity_node_id_for(ACME, n) for n in names]


# -- AC 3: retrievable by time and by link -----------------------------------


def test_episodes_come_back_oldest_first_within_a_half_open_window(any_graph):
    """AC 3, the time half. Half-open: the episode at ``until`` is out, the one at ``since`` is in."""
    any_graph.upsert_episodes(
        ACME,
        [episode("e3", occurred_at=300), episode("e1", occurred_at=100), episode("e2", occurred_at=200)],
    )

    assert ids(any_graph.recall_episodes(ACME, since=100, until=300)) == ["e1", "e2"]
    assert ids(any_graph.recall_episodes(ACME, since=200)) == ["e2", "e3"]
    assert ids(any_graph.recall_episodes(ACME, until=200)) == ["e1"]


def test_episodes_come_back_by_mention_link(any_graph):
    """AC 3, the link half. An anchor set is a disjunction -- any of these entities, not all."""
    any_graph.upsert_episodes(
        ACME,
        [
            episode("e1", entities=["berlin"]),
            episode("e2", entities=["paris"]),
            episode("e3", entities=["zurich"]),
        ],
    )

    assert ids(any_graph.recall_episodes(ACME, entities=["berlin", "paris"])) == ["e1", "e2"]


def test_time_and_link_narrow_together_rather_than_either_alone(any_graph):
    """AC 3 as one sentence: "retrievable by time **and** link". Two filters that were applied as
    an OR would return the whole set and still look like a working query."""
    any_graph.upsert_episodes(
        ACME,
        [
            episode("e1", entities=["berlin"], occurred_at=100),
            episode("e2", entities=["berlin"], occurred_at=300),
            episode("e3", entities=["paris"], occurred_at=100),
        ],
    )

    assert ids(any_graph.recall_episodes(ACME, entities=["berlin"], since=0, until=200)) == ["e1"]


def test_an_episode_with_no_known_time_is_in_no_window(any_graph):
    """Three-valued comparison, made explicit: a null ``t_valid`` fails *either* bound. Worth
    pinning because the alternative readings -- treat it as 0, or as unbounded -- both look
    reasonable and would silently differ between the double and the engine."""
    any_graph.upsert_episodes(ACME, [episode("e1", occurred_at=None), episode("e2", occurred_at=100)])

    assert ids(any_graph.recall_episodes(ACME, since=0)) == ["e2"]
    assert ids(any_graph.recall_episodes(ACME, until=1000)) == ["e2"]


def test_limit_is_a_bound_and_zero_means_zero(any_graph):
    """``limit=0`` returns nothing and ``None`` is unlimited, as ``VectorSubstrate.scan`` defines
    it. A backend conflating the two fetches everything when it was asked for nothing."""
    any_graph.upsert_episodes(ACME, [episode(f"e{i}", occurred_at=100 + i) for i in range(4)])

    assert any_graph.recall_episodes(ACME, since=0, limit=0) == []
    assert ids(any_graph.recall_episodes(ACME, since=0, limit=2)) == ["e0", "e1"]
    assert len(any_graph.recall_episodes(ACME, since=0, limit=None)) == 4


def test_the_limit_cuts_a_time_ordered_set_rather_than_an_arbitrary_page(any_graph):
    """Ordering is applied server-side, before the cut. A backend that limited first and sorted
    after returns a page of arbitrary episodes in ascending order -- which looks identical."""
    any_graph.upsert_episodes(ACME, [episode(f"e{i}", occurred_at=1000 - i) for i in range(5)])

    assert ids(any_graph.recall_episodes(ACME, since=0, limit=2)) == ["e4", "e3"]


def test_recall_never_crosses_a_scope_boundary(any_graph):
    """Every slot, not only the tenant: a backend honouring the tenant and dropping ``user_id``
    serves one user's episodes to another inside one customer."""
    any_graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    any_graph.upsert_episodes(ACME_BOB, [episode("e2", scope=ACME_BOB, entities=["berlin"])])
    any_graph.upsert_episodes(GLOBEX, [episode("e3", scope=GLOBEX, entities=["berlin"])])

    assert ids(any_graph.recall_episodes(ACME, entities=["berlin"])) == ["e1"]
    assert ids(any_graph.recall_episodes(GLOBEX, entities=["berlin"])) == ["e3"]
    # An unset slot means "any", so the tenant-wide read spans both users and no other tenant.
    assert sorted(ids(any_graph.recall_episodes(TENANT_WIDE, entities=["berlin"]))) == ["e1", "e2"]


def test_recall_returns_facts_from_neither_shape(any_graph):
    """The episodic subgraph is episodes. A recall that also matched ``(:Fact)`` or a relation would
    put distilled semantics into a verb whose whole claim is that it is non-lossy."""
    any_graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    from egpt.memory.types import FactWrite, MemoryRecord

    any_graph.upsert_facts(
        ACME,
        [
            FactWrite(
                record=MemoryRecord(
                    id="f1", tier=MemoryTier.SEMANTIC, content="ada lives in berlin", scope=ACME, t_valid=100
                ),
                subject="ada",
                predicate="lives_in",
                object="berlin",
            )
        ],
    )

    assert ids(any_graph.recall_episodes(ACME, since=0)) == ["e1"]


#: An episode revision that has left current belief -- what SM-R5 decay archival and SM-R6
#: re-segmentation will write (ADR 0003 section B.6), and the only way to make one today: neither
#: `upsert_episodes` nor `invalidate` will produce one, which is exactly why the filter that skips
#: it is otherwise unfalsifiable.
_ARCHIVED_EPISODE = (
    f"CREATE (e:{cypher.EPISODE} {{scope_key: $scope_key, id: 'e-old', tier: 'episodic', "
    "content: 'an archived episode', t_valid: 100, t_invalid: null, created_at: 100, "
    "expired_at: 200, superseded_by: null, topics: null, provenance: '{}', metadata: '{}', "
    "row_id: 'row-old', tenant_id: 'acme', user_id: 'ada'})"
)


def test_recall_skips_an_episode_revision_that_has_left_current_belief(graph):
    """Transaction time, applied before ``limit`` as everywhere else on this port. Without it an
    archived episode consumes a page slot and comes back labelled current -- and no write path
    reachable today produces one, so it is asserted against a row written directly."""
    graph.upsert_episodes(ACME, [episode("e1", occurred_at=100)])
    with graph._driver.session(database=graph.database) as session:
        session.run(_ARCHIVED_EPISODE, scope_key=ACME.key()).consume()

    assert ids(graph.recall_episodes(ACME, since=0)) == ["e1"]
    assert ids(graph.recall_episodes(ACME, since=0, limit=1)) == ["e1"]


def test_the_double_also_skips_an_archived_episode():
    """The same rule on the reference double, since a double that returned it would make every
    provider test written against it disagree with the engine."""
    from dataclasses import replace

    double = FakeGraph()
    double.upsert_episodes(ACME, [episode("e1", occurred_at=100)])
    live = double._bucket(ACME)[0]
    double._bucket(ACME).append(replace(live, id="e-old", expired_at=200))

    assert ids(double.recall_episodes(ACME, since=0)) == ["e1"]


def test_recall_is_unranked_and_carries_no_score(any_graph):
    """Ordered by time, not relevance. A ``score`` of 0.0 would be fused as a real similarity by
    the provider's weighted path; ``None`` says "this leg did not rank"."""
    any_graph.upsert_episodes(ACME, [episode("e1")])

    assert any_graph.recall_episodes(ACME, since=0)[0].score is None


# -- refusals ----------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [{}, {"entities": []}, {"since": 100, "until": 100}, {"since": 200, "until": 100}])
def test_both_backends_refuse_the_same_malformed_recall(any_graph, kwargs):
    """One set of guards, in ``memory.graph.episodic``, so a caller gets the same refusal whichever
    backend the config named."""
    with pytest.raises(MemoryOperationError):
        any_graph.recall_episodes(ACME, **kwargs)


@pytest.mark.parametrize("limit", [-1, MAX_LIMIT + 1, True, 1.5])
def test_both_backends_refuse_a_limit_outside_the_range_naming_the_field(any_graph, limit):
    """``field_name``, not just the exception class. Neo4j rejects ``LIMIT -1`` on its own and
    translates that into a ``MemoryOperationError`` too, so a test asserting only the type passes
    whether the guard is wired or not -- and the double, which would silently slice from the end,
    keeps disagreeing with the engine underneath a green suite."""
    with pytest.raises(MemoryOperationError) as excinfo:
        any_graph.recall_episodes(ACME, since=0, limit=limit)
    assert excinfo.value.field_name == "limit"


def test_an_unnamed_entity_is_refused_before_anything_is_written(graph):
    """The refusal has to beat the write, or a batch lands half-linked and the retry the receipt
    advises re-links it against a node that should not exist."""
    with pytest.raises(MemoryOperationError) as excinfo:
        graph.upsert_episodes(ACME, [episode("e1", entities=["berlin", "  "])])
    assert excinfo.value.field_name == "entities"

    with graph._driver.session(database=graph.database) as session:
        assert session.run(f"MATCH (e:{cypher.EPISODE}) RETURN count(e) AS n").single()["n"] == 0


# -- the async half ----------------------------------------------------------


async def test_both_halves_leave_the_same_subgraph(async_graph):
    """Not "an async twin exists" -- the same durable edges, read back through both verbs."""
    await async_graph.aupsert_episodes(ACME, [episode("e1", entities=["berlin", "ada"])])

    from_async = await async_graph.arecall_episodes(ACME, entities=["berlin"])
    from_sync = async_graph.recall_episodes(ACME, entities=["berlin"])

    assert ids(from_async) == ids(from_sync) == ["e1"]
    assert linked(from_async[0]) == linked(from_sync[0])
    assert linked(from_async[0]) == [entity_node_id_for(ACME, "berlin"), entity_node_id_for(ACME, "ada")]


async def test_the_async_half_replaces_mentions_exactly_as_the_sync_one_does(async_graph):
    """The decision, not just the verb: an async twin that re-linked without clearing would leave
    a different graph behind for the same call sequence."""
    await async_graph.aupsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    await async_graph.aupsert_episodes(ACME, [episode("e1", entities=["paris"])])

    assert await async_graph.arecall_episodes(ACME, entities=["berlin"]) == []
    assert ids(await async_graph.arecall_episodes(ACME, entities=["paris"])) == ["e1"]


async def test_the_async_half_refuses_the_same_malformed_recall(async_graph):
    with pytest.raises(MemoryOperationError):
        await async_graph.arecall_episodes(ACME)


# -- what SM-G6 deliberately did not build ------------------------------------


def test_no_entity_is_extracted_from_content(graph):
    """Pins SM-G2 (#100). The content names berlin; nothing here reads it. Entities are supplied,
    and a backend that started extracting would make every write an LLM call on the request path.
    Delete this test when SM-G2 lands."""
    graph.upsert_episodes(ACME, [episode("e1", content="ada moved to berlin", entities=None)])

    with graph._driver.session(database=graph.database) as session:
        assert session.run("MATCH (n:Entity) RETURN count(n) AS n").single()["n"] == 0
    assert graph.recall_episodes(ACME, entities=["berlin"]) == []


def test_two_spellings_of_one_entity_stay_two_nodes(graph):
    """Pins SM-G8 (#106). Case-folding here would decide entity resolution by side effect, and the
    original surface form is unrecoverable once merged. Delete this test when SM-G8 lands."""
    graph.upsert_episodes(ACME, [episode("e1", entities=["Berlin"]), episode("e2", entities=["berlin"])])

    assert ids(graph.recall_episodes(ACME, entities=["Berlin"])) == ["e1"]
    assert ids(graph.recall_episodes(ACME, entities=["berlin"])) == ["e2"]


def test_recall_does_not_walk_past_the_entity_it_matched(graph):
    """Pins SM-G9 (#107). ``e2`` mentions ``berlin``; ``e1`` mentions ``ada``, who lives in berlin.
    Multi-hop would return both, and it is not this ticket's. Delete this test when SM-G9 lands."""
    from egpt.memory.types import FactWrite, MemoryRecord

    graph.upsert_episodes(ACME, [episode("e1", entities=["ada"]), episode("e2", entities=["berlin"])])
    graph.upsert_facts(
        ACME,
        [
            FactWrite(
                record=MemoryRecord(
                    id="f1", tier=MemoryTier.SEMANTIC, content="ada lives in berlin", scope=ACME, t_valid=100
                ),
                subject="ada",
                predicate="lives_in",
                object="berlin",
            )
        ],
    )

    assert ids(graph.recall_episodes(ACME, entities=["berlin"])) == ["e2"]


def test_an_unfiltered_recall_is_refused_rather_than_enumerating(any_graph):
    """Pins SM-G9 (#107) from the other side, beside ``test_the_graph_is_not_enumerable``. Delete
    this test when SM-G9 defines what enumeration means over a temporal graph."""
    any_graph.upsert_episodes(ACME, [episode("e1")])

    with pytest.raises(MemoryOperationError) as excinfo:
        any_graph.recall_episodes(ACME)
    assert "SM-G9" in str(excinfo.value)


def test_the_episodic_subgraph_is_not_ranked_by_recency_or_importance(any_graph):
    """Pins SM-E4 (#111) / SM-E5 (#112). This returns what a window and an anchor set select, in
    time order; scoring them is theirs. Delete this test when the composite scorer (SM-E7 #114)
    lands and this verb feeds it."""
    any_graph.upsert_episodes(ACME, [episode("e1", occurred_at=100), episode("e2", occurred_at=200)])

    hits = any_graph.recall_episodes(ACME, since=0)

    assert ids(hits) == ["e1", "e2"]  # oldest first, not most-recent-first
    assert all(hit.score is None for hit in hits)


def test_traverse_still_ignores_the_mention_edges(any_graph):
    """The two reads stay separate. ``traverse`` ranks content, ``recall_episodes`` follows links --
    a traverse that scored mention-adjacency would be the SM-G9 multi-hop path by accident."""
    any_graph.upsert_episodes(ACME, [episode("e1", content="a quiet week", entities=["berlin"])])

    assert any_graph.traverse(ACME, MemoryQuery(text="berlin", tiers=[MemoryTier.EPISODIC])) == []
