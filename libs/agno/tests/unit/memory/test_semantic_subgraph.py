"""The semantic subgraph's shape and its guards (SM-G7 #105).

The invariants live in ``egpt.memory.graph.semantic`` so every backend applies the same ones, and
this file asserts them there plus once through the reference double -- the same split
``test_episodic_subgraph.py`` uses. What the *engine* does with them is
``tests/integration/memory/neo4j/test_semantic_subgraph.py``.
"""

import pytest

from egpt.memory.graph.episodic import require_recall_limit, require_time_window
from egpt.memory.graph.semantic import require_fact_filters
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MAX_LIMIT, FactWrite, MemoryEvent, MemoryOperationError, MemoryRecord, Scope
from tests.unit.memory._fakes import FakeGraph

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")


def fact(id_, subject=None, predicate=None, object=None, content="ada works at acme corp", scope=ACME, t_valid=100):
    return FactWrite(
        record=MemoryRecord(id=id_, tier=MemoryTier.SEMANTIC, content=content, scope=scope, t_valid=t_valid),
        subject=subject,
        predicate=predicate,
        object=object,
    )


# -- the recall guards ------------------------------------------------------


def test_a_recall_with_no_filter_at_all_is_refused_as_enumeration():
    """Pins SM-G9 (#107), mirroring `episodic.require_recall_filters`'s equivalent refusal."""
    with pytest.raises(MemoryOperationError) as excinfo:
        require_fact_filters(None, None, None, None)
    assert "SM-G9" in str(excinfo.value)
    assert excinfo.value.field_name is None


def test_an_empty_entity_filter_is_refused_rather_than_read_as_any_or_none():
    with pytest.raises(MemoryOperationError) as excinfo:
        require_fact_filters([], None, None, None)
    assert excinfo.value.field_name == "entities"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_relation_filter_is_refused_rather_than_read_as_any_or_none(blank):
    with pytest.raises(MemoryOperationError) as excinfo:
        require_fact_filters(None, blank, None, None)
    assert excinfo.value.field_name == "relation"


def test_a_relation_filter_alone_satisfies_the_at_least_one_requirement():
    require_fact_filters(None, "lives_in", None, None)


@pytest.mark.parametrize("since, until", [(100, 100), (100, 99)])
def test_a_window_valid_at_no_instant_is_refused(since, until):
    with pytest.raises(MemoryOperationError) as excinfo:
        require_time_window(since, until)
    assert excinfo.value.field_name == "until"


@pytest.mark.parametrize("bad", [-1, -10, MAX_LIMIT + 1, True, 1.5, "5"])
def test_a_limit_outside_the_range_memoryquery_enforces_is_refused(bad):
    """The same ceiling `episodic.require_recall_limit` enforces -- re-exported, not re-derived."""
    with pytest.raises(MemoryOperationError) as excinfo:
        require_recall_limit(bad)
    assert excinfo.value.field_name == "limit"


# -- the double honours the same contract -----------------------------------


def test_the_double_refuses_exactly_what_the_guards_refuse():
    graph = FakeGraph()
    for kwargs in ({}, {"entities": []}, {"relation": "  "}, {"since": 100, "until": 100}):
        with pytest.raises(MemoryOperationError):
            graph.recall_facts(ACME, **kwargs)


def test_the_double_returns_fact_edges_oldest_first_within_a_half_open_window():
    graph = FakeGraph()
    graph.upsert_facts(
        ACME,
        [
            fact("f3", "ada", "lives_in", "berlin", content="ada lives in berlin", t_valid=300),
            fact("f1", "ada", "works_at", "acme corp", t_valid=100),
        ],
    )
    graph.upsert_facts(ACME, [fact("f2", "ada", "manages", "bob", content="ada manages bob", t_valid=200)])

    assert [r.id for r in graph.recall_facts(ACME, since=100, until=300)] == ["f1", "f2"]
    assert [r.id for r in graph.recall_facts(ACME, since=100)] == ["f1", "f2", "f3"]


def test_the_double_matches_an_entity_at_either_end_of_the_edge():
    graph = FakeGraph()
    graph.upsert_facts(ACME, [fact("f1", "ada", "works_at", "acme corp")])
    graph.upsert_facts(ACME, [fact("f2", "acme corp", "located_in", "berlin", content="acme corp is in berlin")])

    assert [r.id for r in graph.recall_facts(ACME, entities=["ada"])] == ["f1"]
    assert {r.id for r in graph.recall_facts(ACME, entities=["acme corp"])} == {"f1", "f2"}
    assert [r.id for r in graph.recall_facts(ACME, entities=["berlin"])] == ["f2"]


def test_the_double_filters_by_relation_label_exactly():
    graph = FakeGraph()
    graph.upsert_facts(ACME, [fact("f1", "ada", "works_at", "acme corp")])
    graph.upsert_facts(ACME, [fact("f2", "ada", "lives_in", "berlin", content="ada lives in berlin")])

    assert [r.id for r in graph.recall_facts(ACME, relation="works_at")] == ["f1"]
    assert graph.recall_facts(ACME, relation="Works_At") == []


def test_the_double_never_returns_an_untripled_fact_node():
    """The literal scope of `recall_facts`: an edge only. A node has no SPO to filter on, matching
    `require_fact_filters`'s own scope."""
    graph = FakeGraph()
    graph.upsert_facts(ACME, [fact("f1", content="ada is thoughtful")])  # no subject/predicate/object

    assert graph.recall_facts(ACME, since=0) == []


def test_the_double_honours_limit_zero_as_zero_rows_and_none_as_unlimited():
    graph = FakeGraph()
    graph.upsert_facts(
        ACME,
        [fact("f1", "ada", "works_at", "acme corp"), fact("f2", "ada", "lives_in", "berlin", content="ada in berlin")],
    )

    assert graph.recall_facts(ACME, since=0, limit=0) == []
    assert len(graph.recall_facts(ACME, since=0, limit=None)) == 2
    assert graph.fact_recall_requests[0] == {
        "entities": None,
        "relation": None,
        "since": 0,
        "until": None,
        "limit": 0,
    }


def test_the_double_never_leaks_a_fact_across_a_tenant():
    graph = FakeGraph()
    graph.upsert_facts(ACME, [fact("f1", "ada", "works_at", "acme corp", scope=ACME)])
    graph.upsert_facts(GLOBEX, [fact("f2", "ada", "works_at", "acme corp", scope=GLOBEX)])

    assert [r.id for r in graph.recall_facts(GLOBEX, entities=["ada"])] == ["f2"]


def test_the_double_surfaces_spo_on_provenance_not_by_parsing_content():
    """An entity name containing a space makes splitting `content` ambiguous -- the SPO fields must
    come from the write's own structure."""
    graph = FakeGraph()
    graph.upsert_facts(
        ACME, [fact("f1", "Ada Lovelace", "works_at", "Acme Corp", content="Ada Lovelace works at Acme Corp")]
    )

    (row,) = graph.recall_facts(ACME, entities=["Ada Lovelace"])
    assert row.provenance["subject"] == "Ada Lovelace"
    assert row.provenance["predicate"] == "works_at"
    assert row.provenance["object"] == "Acme Corp"


# -- episodic and semantic subgraphs are distinct ----------------------------


def test_recall_facts_never_returns_an_episode_and_recall_episodes_never_returns_a_fact():
    """AC 1: the two subgraphs are distinct reads over distinct tiers, not one read filtered after
    the fact -- the property the ticket's testing notes ask to pin."""
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [MemoryEvent(content="ada moved to berlin", scope=ACME, id="e1", occurred_at=100)])
    graph.upsert_facts(ACME, [fact("f1", "ada", "lives_in", "berlin", t_valid=100)])

    episodes = graph.recall_episodes(ACME, since=0)
    facts = graph.recall_facts(ACME, since=0)

    assert [r.id for r in episodes] == ["e1"]
    assert [r.id for r in facts] == ["f1"]
    assert all(r.tier is MemoryTier.EPISODIC for r in episodes)
    assert all(r.tier is MemoryTier.SEMANTIC for r in facts)
