"""The episodic subgraph's shape and its guards (SM-G6 #104).

The invariants live in ``egpt.memory.graph.episodic`` so every backend applies the same ones, and
this file asserts them there plus once through the reference double. What the *engine* does with
them is ``tests/integration/memory/neo4j/test_episodic_subgraph.py``, parametrised over the double
and Neo4j together -- the two files split at "is the rule right" versus "does the backend obey it".
"""

import pytest

from egpt.memory.graph.episodic import (
    MENTIONS,
    entity_node_id_for,
    entity_node_id_prefix,
    mention_link_id,
    mention_rows,
    normalized_mentions,
    require_recall_filters,
    require_recall_limit,
    require_time_window,
)
from egpt.memory.schemas.episodic import episode_from_event, episode_to_record, graph_node_id_for
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MAX_LIMIT,
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
)
from tests.unit.memory._fakes import FakeGraph

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")


def episode(id_, content="ada moved to berlin", scope=ACME, entities=None, occurred_at=100):
    return MemoryEvent(
        content=content,
        scope=scope,
        id=id_,
        tier=MemoryTier.EPISODIC,
        entities=entities,
        occurred_at=occurred_at,
    )


# -- entity node identity ---------------------------------------------------


def test_an_entity_node_id_is_derived_from_the_full_scope_and_the_name():
    """Not the tenant: two users of one customer must not share an entity node, because deciding
    that one user's 'berlin' is another's is entity resolution and SM-G8's (#106) call."""
    assert entity_node_id_for(ACME, "berlin") != entity_node_id_for(ACME_BOB, "berlin")
    assert entity_node_id_for(ACME, "berlin") != entity_node_id_for(GLOBEX, "berlin")
    assert entity_node_id_for(ACME, "berlin") == entity_node_id_for(ACME, "berlin")


def test_an_entity_node_id_can_never_collide_with_an_episode_node_id():
    """Both extend the same scope key. Without the discriminating segment, an episode whose id
    equals an entity's name would address one node -- and every read of one would return the
    other."""
    assert entity_node_id_for(ACME, "berlin") != graph_node_id_for(ACME, "berlin")
    assert entity_node_id_for(ACME, "berlin").startswith(entity_node_id_prefix(ACME))
    assert not graph_node_id_for(ACME, "berlin").startswith(entity_node_id_prefix(ACME))


@pytest.mark.parametrize("name", ["berlin", "a/b", "a%b", "*", "a/b/c/d"])
def test_an_entity_node_id_has_fixed_arity_whatever_the_name_contains(name):
    """What the escaping actually buys, asserted directly. The two id spaces are told apart by
    *shape* -- six segments for an episode, seven for an entity -- and an unescaped separator in a
    name adds segments, so the discriminator stops being a discriminator and the docstring that
    claims otherwise becomes the comfortable false one."""
    assert entity_node_id_for(ACME, name).count("/") == 6
    assert graph_node_id_for(ACME, name).count("/") == 5


def test_an_entity_name_containing_a_separator_still_gets_its_own_id():
    """Fixed arity is not injectivity: an unescaped ``/`` merges adjacent segments, ``%`` would let
    an escape be escaped into a collision, and ``*`` is the unset-slot sentinel. Asserted as
    all-distinct over the confusable set, because a pairwise check passes whenever the pair happens
    not to be the colliding one."""
    names = ["a/b", "ab", "a%b", "a%2Fb", "a%252Fb", "*", "%2A", "a/b/c", "a", "b/c"]
    ids = [entity_node_id_for(ACME, name) for name in names]

    assert len(set(ids)) == len(names), "two entity names share a node id"
    assert all(value.startswith(entity_node_id_prefix(ACME)) for value in ids)


@pytest.mark.parametrize("bad", ["", "   ", None, 7, ["berlin"]])
def test_an_unnamed_entity_is_refused_rather_than_addressed(bad):
    with pytest.raises(MemoryOperationError) as excinfo:
        entity_node_id_for(ACME, bad)
    assert excinfo.value.field_name == "entities"


def test_a_mention_link_id_distinguishes_both_ends():
    """One edge per (episode, entity). A key that collapsed either end would make the uniqueness
    constraint behind it reject a legitimate second edge."""
    assert mention_link_id(ACME, "e1", "berlin") != mention_link_id(ACME, "e2", "berlin")
    assert mention_link_id(ACME, "e1", "berlin") != mention_link_id(ACME, "e1", "ada")
    assert mention_link_id(ACME, "e1", "berlin") != mention_link_id(GLOBEX, "e1", "berlin")
    # The separator is escaped on both halves, so no pair of (episode, entity) can spell another's.
    assert mention_link_id(ACME, "e1/x", "berlin") != mention_link_id(ACME, "e1", "x/berlin")


# -- the mention set --------------------------------------------------------


def test_mentions_are_trimmed_deduplicated_and_kept_in_order():
    assert normalized_mentions(["  berlin ", "ada", "berlin"], episode_id="e1") == ["berlin", "ada"]


def test_case_is_preserved_so_entity_resolution_stays_sm_g8s():
    """Pins SM-G8 (#106). Folding here would make graph identity depend on a policy nobody has
    written, and the original surface form is unrecoverable once merged. Delete this when SM-G8
    lands and decides otherwise."""
    assert normalized_mentions(["Berlin", "berlin"], episode_id="e1") == ["Berlin", "berlin"]


def test_no_entities_and_an_absent_entities_field_are_the_same_empty_set():
    assert normalized_mentions(None, episode_id="e1") == []
    assert normalized_mentions([], episode_id="e1") == []


@pytest.mark.parametrize("bad", ["berlin", {"berlin"}, {"berlin": 1}, 7, iter(["berlin"])])
def test_a_mention_collection_that_is_not_an_ordered_sequence_is_refused(bad):
    """``list("berlin")`` is six one-character entities and raises nothing, so a string reaching
    the write path becomes six entity nodes a reader cannot tell from six real ones. A set and a
    dict iterate too and would lose the order ``position`` exists to record."""
    with pytest.raises(MemoryOperationError) as excinfo:
        normalized_mentions(bad, episode_id="e1")
    assert excinfo.value.field_name == "entities"


def test_a_tuple_is_accepted_because_it_is_ordered_and_does_not_split():
    assert normalized_mentions(("berlin", "ada"), episode_id="e1") == ["berlin", "ada"]


@pytest.mark.parametrize("bad", [[""], ["  "], [None], [7], [["berlin"]]])
def test_an_unnamed_mention_is_refused(bad):
    with pytest.raises(MemoryOperationError) as excinfo:
        normalized_mentions(bad, episode_id="e1")
    assert excinfo.value.field_name == "entities"


def test_mention_rows_carry_the_position_that_keeps_the_two_views_in_step():
    rows = mention_rows(ACME, "e1", ["berlin", "ada"])

    assert [r["name"] for r in rows] == ["berlin", "ada"]
    assert [r["position"] for r in rows] == [0, 1]
    assert [r["link_id"] for r in rows] == [mention_link_id(ACME, "e1", n) for n in ("berlin", "ada")]
    assert {r["scope_key"] for r in rows} == {ACME.key()}


# -- the record linkage (SM-E1) ---------------------------------------------


def test_an_episode_row_and_the_subgraph_name_one_set_of_entity_nodes():
    """AC 2 at the record: ``entity_ids`` is the same derivation the ``MENTIONS`` edges use, in the
    same order, so the two views cannot answer differently."""
    row = episode_from_event(episode("e1", entities=["berlin", "ada"]))

    assert row.entity_ids == [r["link_id"].rsplit("/", 1)[0] for r in mention_rows(ACME, "e1", ["berlin", "ada"])]
    assert row.entity_ids == [entity_node_id_for(ACME, "berlin"), entity_node_id_for(ACME, "ada")]


def test_a_stored_entity_id_built_under_another_scope_is_refused_on_the_way_out():
    """The same check ``graph_node_id`` gets, one link over: an id derived elsewhere addresses
    another scope's entity node, and the read path is where a caller would act on it."""
    row = episode_from_event(episode("e1", entities=["berlin"]))
    row.entity_ids = [entity_node_id_for(GLOBEX, "berlin")]

    with pytest.raises(MemoryOperationError) as excinfo:
        episode_to_record(row)
    assert excinfo.value.field_name == "entity_ids"


# -- the recall guards ------------------------------------------------------


def test_a_recall_with_no_filter_at_all_is_refused_as_enumeration():
    """Pins SM-G9 (#107). Three ``None``s is every episode in scope, and what enumeration means
    over a temporal graph is SM-G9's to define. Delete this when it lands."""
    with pytest.raises(MemoryOperationError) as excinfo:
        require_recall_filters(None, None, None)
    assert "SM-G9" in str(excinfo.value)
    # No single field, unlike the empty-list refusal below: `since` or `until` resolves this
    # equally well, so naming `entities` would send a caller narrowing one field when any of the
    # three would do (round-2 review, PR #90).
    assert excinfo.value.field_name is None


def test_an_empty_entity_filter_is_refused_rather_than_read_as_any_or_none():
    with pytest.raises(MemoryOperationError) as excinfo:
        require_recall_filters([], None, None)
    assert excinfo.value.field_name == "entities"


@pytest.mark.parametrize("since, until", [(100, 100), (100, 99)])
def test_a_window_valid_at_no_instant_is_refused(since, until):
    """Half-open, so ``until == since`` is empty rather than "that one second" -- and an empty page
    is indistinguishable from a genuine miss."""
    with pytest.raises(MemoryOperationError) as excinfo:
        require_time_window(since, until)
    assert excinfo.value.field_name == "until"


@pytest.mark.parametrize("bad", [-1, -10, MAX_LIMIT + 1, True, 1.5, "5"])
def test_a_limit_outside_the_range_memoryquery_enforces_is_refused(bad):
    """A negative limit is a bare slice: ``limit=-1`` on an in-memory backend returns everything
    *except* the last row, while Cypher rejects it -- so unchecked, the double and the engine
    answer the same call differently. The ceiling is ``MemoryQuery``'s, imported rather than
    restated so one change reaches both."""
    with pytest.raises(MemoryOperationError) as excinfo:
        require_recall_limit(bad)
    assert excinfo.value.field_name == "limit"


@pytest.mark.parametrize("ok", [None, 0, 1, MAX_LIMIT])
def test_a_limit_inside_the_range_is_accepted(ok):
    """The counterweight: the guard must not be satisfied by refusing everything -- ``0`` and
    ``None`` are both legal and mean opposite things."""
    require_recall_limit(ok)


def test_both_backends_refuse_a_negative_limit_identically():
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [episode("e1")])

    with pytest.raises(MemoryOperationError) as excinfo:
        graph.recall_episodes(ACME, since=0, limit=-1)
    assert excinfo.value.field_name == "limit"


def test_an_open_ended_window_is_accepted_on_either_side():
    require_time_window(100, None)
    require_time_window(None, 100)
    require_time_window(None, None)


@pytest.mark.parametrize("name, value", [("since", True), ("until", False), ("since", "100"), ("until", 1.5)])
def test_a_bound_that_is_not_epoch_seconds_is_refused(name, value):
    """``bool`` is an ``int`` subclass, so ``since=True`` would silently mean epoch second 1 -- the
    coercion every other timestamp at this seam already refuses."""
    with pytest.raises(MemoryOperationError) as excinfo:
        require_time_window(**{name: value, ("until" if name == "since" else "since"): None})
    assert excinfo.value.field_name == name


# -- the double honours the same contract -----------------------------------


def test_the_double_refuses_exactly_what_the_guards_refuse():
    """A double that accepted a request the engine refuses is a double every provider test built on
    it would agree with, and the engine would not."""
    graph = FakeGraph()
    for kwargs in ({}, {"entities": []}, {"since": 100, "until": 100}):
        with pytest.raises(MemoryOperationError):
            graph.recall_episodes(ACME, **kwargs)


def test_the_double_returns_episodes_oldest_first_within_a_half_open_window():
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [episode("e3", occurred_at=300), episode("e1", occurred_at=100)])
    graph.upsert_episodes(ACME, [episode("e2", occurred_at=200)])

    assert [r.id for r in graph.recall_episodes(ACME, since=100, until=300)] == ["e1", "e2"]
    assert [r.id for r in graph.recall_episodes(ACME, since=100)] == ["e1", "e2", "e3"]


def test_the_double_honours_limit_zero_as_zero_rows_and_none_as_unlimited():
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [episode("e1"), episode("e2", content="ada in paris")])

    assert graph.recall_episodes(ACME, since=0, limit=0) == []
    assert len(graph.recall_episodes(ACME, since=0, limit=None)) == 2
    assert graph.recall_requests[0] == {"entities": None, "since": 0, "until": None, "limit": 0}


def test_the_double_matches_a_mention_under_the_rows_own_scope_not_the_readers():
    """A read that leaves ``user_id`` unset spans several write scopes. Deriving the entity id
    under the *call* scope would match none of them, while the engine -- which reaches the entity
    through the episode's own edge -- matches all of them."""
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    graph.upsert_episodes(ACME_BOB, [episode("e2", scope=ACME_BOB, entities=["berlin"])])

    tenant_wide = Scope(tenant_id="acme")

    assert [r.id for r in graph.recall_episodes(tenant_wide, entities=["berlin"])] == ["e1", "e2"]
    assert [r.id for r in graph.recall_episodes(ACME, entities=["berlin"])] == ["e1"]


def test_the_double_replaces_the_mention_set_on_re_upsert_rather_than_unioning_it():
    """Upsert is by id, so the edges go with the episode. A double that accumulated them would let
    a dropped entity keep passing every mention filter -- and pass the engine's test suite."""
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    graph.upsert_episodes(ACME, [episode("e1", entities=["paris"])])

    assert graph.recall_episodes(ACME, entities=["berlin"]) == []
    assert [r.id for r in graph.recall_episodes(ACME, entities=["paris"])] == ["e1"]


def test_the_double_never_leaks_a_mention_across_a_tenant():
    graph = FakeGraph()
    graph.upsert_episodes(ACME, [episode("e1", entities=["berlin"])])
    graph.upsert_episodes(GLOBEX, [episode("e2", scope=GLOBEX, entities=["berlin"])])

    assert [r.id for r in graph.recall_episodes(GLOBEX, entities=["berlin"])] == ["e2"]


def test_two_users_episodes_sharing_an_id_do_not_collide_in_the_double():
    """The regression test for round-1 review finding 2: ``_bucket`` keys on ``tenant_id`` alone, so
    ada's and bob's episodes sharing one ``id`` land in one bucket, and the dedup check that follows
    used to compare only ``id`` -- silently keeping the second write and discarding the first. Real
    ids are uuid4-stamped so this needs an explicit id, but the port allows it: ``upsert_episodes``
    is by id, not by ``(id, scope)``, and Neo4j's ``MERGE`` key is
    ``(scope_key, id, live)`` -- it would have kept both."""
    ada = Scope(tenant_id="acme", user_id="ada")
    bob = Scope(tenant_id="acme", user_id="bob")
    graph = FakeGraph()
    graph.upsert_episodes(ada, [episode("shared-id", content="ada's note", scope=ada)])
    graph.upsert_episodes(bob, [episode("shared-id", content="bob's note", scope=bob)])

    tenant_wide = Scope(tenant_id="acme")
    hits = {r.scope: r.content for r in graph.recall_episodes(tenant_wide, since=0)}

    assert hits == {ada: "ada's note", bob: "bob's note"}


def test_two_users_facts_sharing_an_id_do_not_collide_in_the_double():
    """The same class of defect as the episode dedup above, at ``_upsert_fact``'s identical
    check -- found by grepping every other dedup site rather than fixing only the one the review
    named. Neo4j's fact write is `MERGE`d on `(scope_key, id, live)` too, so it would keep both."""
    ada = Scope(tenant_id="acme", user_id="ada")
    bob = Scope(tenant_id="acme", user_id="bob")
    graph = FakeGraph()
    graph.upsert_facts(
        ada,
        [
            FactWrite(
                record=MemoryRecord(
                    id="shared-fact", tier=MemoryTier.SEMANTIC, content="ada earns 200k", scope=ada, t_valid=100
                )
            )
        ],
    )
    graph.upsert_facts(
        bob,
        [
            FactWrite(
                record=MemoryRecord(
                    id="shared-fact", tier=MemoryTier.SEMANTIC, content="bob earns 150k", scope=bob, t_valid=100
                )
            )
        ],
    )

    tenant_wide = Scope(tenant_id="acme")
    contents = {r.scope: r.content for r in graph.traverse(tenant_wide, MemoryQuery(text="earns"))}

    assert contents == {ada: "ada earns 200k", bob: "bob earns 150k"}


def test_the_edge_type_is_declared_once():
    """A backend spelling it differently would write a subgraph nothing else can read."""
    assert MENTIONS == "MENTIONS"
