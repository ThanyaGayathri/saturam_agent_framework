"""The semantic subgraph against a real engine (SM-G7 #105).

Gated on ``EGPT_INTEGRATION_NEO4J``; start the engine with ``cookbook/scripts/run_neo4j.sh``.

Mirrors ``test_episodic_subgraph.py``'s split: the unit file
(``tests/unit/memory/test_semantic_subgraph.py``) asserts the guards, this one asserts the engine
obeys them and that the reference double agrees with it. A fact edge asserted against a double
written in the same change is unfalsifiable -- this is the file that asserts it against storage.
"""

import os

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import Neo4jGraphSubstrate  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MAX_LIMIT, FactWrite, MemoryEvent, MemoryOperationError, MemoryRecord, Scope  # noqa: E402
from tests.unit.memory._fakes import FakeGraph  # noqa: E402

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")


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


def edge(id_, subject, predicate, object_, content=None, scope=ACME, t_valid=100):
    return FactWrite(
        record=MemoryRecord(
            id=id_,
            tier=MemoryTier.SEMANTIC,
            content=content or f"{subject} {predicate} {object_}",
            scope=scope,
            t_valid=t_valid,
        ),
        subject=subject,
        predicate=predicate,
        object=object_,
    )


def ids(records):
    return [r.id for r in records]


# -- AC 1: fact edges are stored as the semantic subgraph, distinct from episodic -----------------


def test_the_fact_edge_keeps_its_spo_and_its_time(any_graph):
    """AC 1. The write's own structure round-trips through the read, not through parsing content."""
    any_graph.upsert_facts(ACME, [edge("f1", "Ada", "works_at", "Acme Corp", t_valid=1_700_000_000)])

    hits = any_graph.recall_facts(ACME, since=1_700_000_000)

    assert ids(hits) == ["f1"]
    assert hits[0].provenance["subject"] == "Ada"
    assert hits[0].provenance["predicate"] == "works_at"
    assert hits[0].provenance["object"] == "Acme Corp"
    assert hits[0].t_valid == 1_700_000_000
    assert hits[0].tier is MemoryTier.SEMANTIC


def test_recall_facts_returns_nothing_from_the_episodic_shape(any_graph):
    """The symmetric property to episodic's ``test_recall_returns_facts_from_neither_shape``: the
    two subgraphs are distinct reads, not one query filtered after the fact."""
    any_graph.upsert_episodes(ACME, [MemoryEvent(content="ada moved to berlin", scope=ACME, id="e1", occurred_at=100)])
    any_graph.upsert_facts(ACME, [edge("f1", "ada", "lives_in", "berlin")])

    assert ids(any_graph.recall_facts(ACME, since=0)) == ["f1"]


def test_recall_facts_returns_nothing_from_the_untripled_node_shape(any_graph):
    """A ``(:Fact)`` node has no subject/object to filter on -- out of scope by construction,
    matching ``require_fact_filters``'s own scope."""
    any_graph.upsert_facts(
        ACME,
        [
            FactWrite(
                record=MemoryRecord(
                    id="f1", tier=MemoryTier.SEMANTIC, content="ada is thoughtful", scope=ACME, t_valid=100
                )
            )
        ],
    )

    assert any_graph.recall_facts(ACME, since=0) == []


# -- AC 2: fact edges queryable by entity, relation and time -------------------------------------


def test_an_entity_filter_matches_either_end_of_the_edge(any_graph):
    any_graph.upsert_facts(
        ACME,
        [
            edge("f1", "Ada", "works_at", "Acme Corp"),
            edge("f2", "Acme Corp", "located_in", "Berlin"),
            edge("f3", "Bob", "works_at", "Globex"),
        ],
    )

    assert set(ids(any_graph.recall_facts(ACME, entities=["Acme Corp"]))) == {"f1", "f2"}
    assert ids(any_graph.recall_facts(ACME, entities=["Ada"])) == ["f1"]
    assert any_graph.recall_facts(ACME, entities=["Nobody"]) == []


def test_a_relation_filter_matches_the_label_exactly(any_graph):
    any_graph.upsert_facts(ACME, [edge("f1", "Ada", "works_at", "Acme Corp"), edge("f2", "Ada", "lives_in", "Berlin")])

    assert ids(any_graph.recall_facts(ACME, relation="works_at")) == ["f1"]
    assert any_graph.recall_facts(ACME, relation="Works_At") == []


def test_edges_come_back_oldest_first_within_a_half_open_window(any_graph):
    any_graph.upsert_facts(
        ACME,
        [
            edge("f3", "a", "r", "c", t_valid=300),
            edge("f1", "a", "r", "c", t_valid=100),
            edge("f2", "a", "r", "c", t_valid=200),
        ],
    )

    assert ids(any_graph.recall_facts(ACME, since=100, until=300)) == ["f1", "f2"]
    assert ids(any_graph.recall_facts(ACME, since=100)) == ["f1", "f2", "f3"]


def test_an_edge_with_no_known_time_is_in_no_window(any_graph):
    """Three-valued comparison, made explicit: a null ``t_valid`` fails *either* bound. Mirrors
    episodic's ``test_an_episode_with_no_known_time_is_in_no_window`` (round-2 review, PR #102) --
    the alternative readings (treat it as 0, or as unbounded) both look reasonable and would
    silently differ between the double and the engine."""
    any_graph.upsert_facts(ACME, [edge("f1", "a", "r", "b", t_valid=None), edge("f2", "a", "r", "c", t_valid=100)])

    assert ids(any_graph.recall_facts(ACME, since=0)) == ["f2"]
    assert ids(any_graph.recall_facts(ACME, until=1000)) == ["f2"]


def test_limit_is_a_bound_and_zero_means_zero(any_graph):
    any_graph.upsert_facts(ACME, [edge("f1", "a", "r", "b"), edge("f2", "a", "r2", "c")])

    assert any_graph.recall_facts(ACME, since=0, limit=0) == []
    assert len(any_graph.recall_facts(ACME, since=0, limit=None)) == 2


# -- scope isolation -------------------------------------------------------------------------------


def test_recall_never_crosses_a_scope_boundary(any_graph):
    any_graph.upsert_facts(ACME, [edge("f1", "Ada", "works_at", "Acme Corp", scope=ACME)])
    any_graph.upsert_facts(GLOBEX, [edge("f2", "Ada", "works_at", "Acme Corp", scope=GLOBEX)])

    assert ids(any_graph.recall_facts(GLOBEX, entities=["Ada"])) == ["f2"]


def test_a_tenant_wide_read_sees_every_users_edges(any_graph):
    any_graph.upsert_facts(ACME, [edge("f1", "Ada", "works_at", "Acme Corp", scope=ACME)])
    any_graph.upsert_facts(ACME_BOB, [edge("f2", "Ada", "works_at", "Acme Corp", scope=ACME_BOB)])

    tenant_wide = Scope(tenant_id="acme")
    hits = any_graph.recall_facts(tenant_wide, entities=["Ada"])

    assert set(ids(hits)) == {"f1", "f2"}
    # Each row's own write scope, not the tenant-wide call scope -- round-1 review, PR #102. A
    # caller deriving further ids from `record.scope` under the call scope would reproduce the
    # `recall_episodes` bug PR #90 already fixed for the episodic side.
    assert {r.id: r.scope for r in hits} == {"f1": ACME, "f2": ACME_BOB}


# -- refusals ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs", [{}, {"entities": []}, {"relation": "  "}, {"since": 100, "until": 100}, {"since": 200, "until": 100}]
)
def test_both_backends_refuse_the_same_malformed_recall(any_graph, kwargs):
    """One set of guards, in ``memory.graph.semantic``, so a caller gets the same refusal whichever
    backend the config named."""
    with pytest.raises(MemoryOperationError):
        any_graph.recall_facts(ACME, **kwargs)


@pytest.mark.parametrize("limit", [-1, MAX_LIMIT + 1, True, 1.5])
def test_both_backends_refuse_a_limit_outside_the_range_naming_the_field(any_graph, limit):
    with pytest.raises(MemoryOperationError) as excinfo:
        any_graph.recall_facts(ACME, since=0, limit=limit)
    assert excinfo.value.field_name == "limit"


def test_an_unfiltered_recall_is_refused_rather_than_enumerating(any_graph):
    with pytest.raises(MemoryOperationError):
        any_graph.recall_facts(ACME)


# -- async twin ---------------------------------------------------------------------------------


async def test_the_async_half_makes_the_same_decisions_as_the_sync_one(async_graph):
    await async_graph.aupsert_facts(ACME, [edge("f1", "Ada", "works_at", "Acme Corp", t_valid=100)])

    hits = await async_graph.arecall_facts(ACME, entities=["Ada"])

    assert ids(hits) == ["f1"]
    assert hits[0].provenance["predicate"] == "works_at"


async def test_the_async_half_refuses_the_same_malformed_recall(async_graph):
    with pytest.raises(MemoryOperationError):
        await async_graph.arecall_facts(ACME)


# -- the double stores nothing the schema probe does not know about -------------------------------


def test_the_reference_double_and_neo4j_agree_on_an_entity_only_filtered_read(graph):
    """A behavioural cross-check beyond the unit suite's per-double assertions: the same write and
    read against the real engine and the double produce the same visible shape."""
    double = FakeGraph()
    facts = [edge("f1", "Ada", "works_at", "Acme Corp"), edge("f2", "Bob", "works_at", "Globex")]

    graph.upsert_facts(ACME, facts)
    double.upsert_facts(ACME, facts)

    assert ids(graph.recall_facts(ACME, entities=["Ada"])) == ids(double.recall_facts(ACME, entities=["Ada"]))
