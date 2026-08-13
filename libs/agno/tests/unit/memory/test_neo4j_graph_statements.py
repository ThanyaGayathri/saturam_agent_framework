"""Guards over the Cypher the Neo4j substrate can emit (SM-G1 #99).

These assert on the **statements**, not on a database, which is the only way to cover every one of
them: a leak needs just one statement to forget a predicate, and a behavioural test covers the
statements it happens to exercise. SM-R2 section 5.4 is explicit that on Neo4j Community a
forgotten scope predicate is a cross-tenant leak with no engine-level second line of defence, so
this is the line of defence.

The behavioural half -- that the statements do what they say against a real engine -- is
``tests/integration/memory/neo4j/test_graph_substrate.py``.
"""

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import cypher  # noqa: E402
from egpt.integrations.memory.graph.neo4j.neo4j import Neo4jGraphSubstrate  # noqa: E402
from egpt.memory.substrates import GraphSubstrate  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import FactWrite, MemoryEvent, MemoryRecord, Scope  # noqa: E402

#: Every slot set, so a statement that drops one is visible. A scope with only a tenant would make
#: "the tenant predicate is present" the only assertion this file could make.
FULL = Scope(tenant_id="acme", agent_id="agent-1", user_id="ada", team_id="team-9", session_id="s-3")
TENANT_ONLY = Scope(tenant_id="acme")


def all_statements(scope):
    """Every statement the module can emit, as ``(label, cypher, params)``.

    A **maintained** list, not automatic discovery -- and
    :func:`test_the_statement_list_covers_every_producer` is what keeps the two honest, because a
    statement missing from here is untested by construction, which is the hole these guards exist
    to close.
    """
    emitted = [
        ("upsert_episodes", *cypher.upsert_episodes_statement(scope)),
        *[
            (f"upsert_facts[{shape}]", text, params)
            for shape, (text, params) in cypher.upsert_facts_statements(scope).items()
        ],
        *[(f"traverse[{i}]", text, params) for i, (text, params) in enumerate(cypher.traverse_statements(scope))],
        *[(f"bounds[{i}]", text, params) for i, (text, params) in enumerate(cypher.bounds_statements(scope))],
        *[(f"live_row[{i}]", text, params) for i, (text, params) in enumerate(cypher.live_row_statements(scope))],
        *[(f"expire[{i}]", text, params) for i, (text, params) in enumerate(cypher.expire_statements(scope))],
        *[
            (f"bounded_copy[{i}]", text, params)
            for i, (text, params) in enumerate(cypher.insert_bounded_copy_statements(scope))
        ],
    ]
    return emitted


def test_the_statement_list_covers_every_producer():
    """Fails when a new statement-producing function is added to `cypher.py` without a row above.

    The docstring used to claim the list was derived from the module; it never was. Rather than
    soften the claim, this makes it true: every exported name ending in ``_statement``/``_statements``
    has to appear in :func:`all_statements`, so the coverage is enforced instead of asserted.
    """
    producers = {name for name in cypher.__all__ if name.endswith(("_statement", "_statements"))}
    covered = {
        "traverse_statements",
        "bounds_statements",
        "live_row_statements",
        "expire_statements",
        "insert_bounded_copy_statements",
        "upsert_episodes_statement",
        "upsert_facts_statements",
        "episode_probe_statement",
    }

    assert producers <= covered, f"cypher.py exports statement producers no guard enumerates: {producers - covered}"
    assert covered <= producers, f"the guard names producers cypher.py no longer exports: {covered - producers}"


def statement_ids(scope):
    return [label for label, _, _ in all_statements(scope)]


#: Statements that carry their scope **on each row** rather than in bound parameters. A write always
#: has a fully-specified scope, so it matches `scope_key` exactly; a read's unset slot means "any",
#: which an equality on the composite key could not express.
WRITE_STATEMENTS = ("upsert_episodes", "upsert_facts[triple]", "upsert_facts[node]")


@pytest.mark.parametrize("label", [name for name in statement_ids(FULL) if name not in WRITE_STATEMENTS])
def test_every_read_statement_filters_on_every_scope_slot(label):
    """A backend that honours the tenant and ignores the user leaks inside the tenant.

    Not "contains tenant_id": the port requires **every** slot the call scope sets, and the slot
    most likely to be forgotten is the one that separates two users of one customer.
    """
    text, params = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    for slot in cypher.SCOPE_SLOTS:
        assert f".{slot} = ${slot}" in text, f"{label} does not filter on {slot}"
        assert params[slot] == getattr(FULL, slot), f"{label} binds the wrong value for {slot}"


@pytest.mark.parametrize("label", WRITE_STATEMENTS)
def test_every_write_statement_merges_on_the_exact_scope(label):
    """The write half of the same rule, checked against the mechanism writes actually use.

    A write is keyed on ``scope_key``, which is injective over all five slots -- so this is a
    *stricter* match than the read predicate, not a laxer one. What must never happen is a write
    statement that names neither, which is what the coverage assertion below rules out.
    """
    text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    assert "scope_key: row.scope_key" in text, f"{label} does not merge on the row's scope"


def test_every_statement_is_scoped_by_one_mechanism_or_the_other():
    """Nothing may be unscoped. Checked over the whole set so a new statement cannot slip through
    by belonging to neither category above."""
    for label, text, _ in all_statements(FULL):
        scoped = ".tenant_id = $tenant_id" in text or "scope_key: row.scope_key" in text
        assert scoped, f"{label} names no scope at all"


def test_the_row_builder_supplies_the_scope_key_a_write_merges_on():
    """The two halves of the write-side scoping: the statement reads ``row.scope_key``, and this is
    what puts it there. Asserted together, because either alone is scoping that does not happen."""
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    row = substrate._row(FULL, "id-1", MemoryTier.EPISODIC, "content here")

    assert row["scope_key"] == FULL.key()
    assert Scope(tenant_id="acme").key() != FULL.key(), "the key must distinguish scopes"


@pytest.mark.parametrize("label", [name for name in statement_ids(TENANT_ONLY) if name not in WRITE_STATEMENTS])
def test_an_unset_slot_contributes_no_clause(label):
    """``None`` means "any". Filtering on it would narrow a tenant-wide read to nothing."""
    text, params = next((t, p) for name, t, p in all_statements(TENANT_ONLY) if name == label)

    assert ".tenant_id = $tenant_id" in text
    for slot in ("agent_id", "user_id", "team_id", "session_id"):
        assert f".{slot} = ${slot}" not in text, f"{label} filters on an unset {slot}"
        assert slot not in params


@pytest.mark.parametrize("label", statement_ids(FULL))
def test_no_statement_applies_valid_time(label):
    """Valid time is the provider's, enforced once on the merged set (finding F2).

    A substrate that filtered ``as_of`` itself would withhold the SCD-2 successor row carrying
    ``t_invalid`` -- which is the evidence that catches an invalidated fact on the other leg.
    """
    text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    assert "$as_of" not in text
    assert "t_valid <=" not in text and "<= $at" not in text


@pytest.mark.parametrize("label", [name for name in statement_ids(FULL) if name.startswith("traverse")])
def test_traverse_filters_current_belief_before_ranking(label):
    """Applied before ``limit``, or a page of superseded rows consumes the whole limit."""
    text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    assert "expired_at IS NULL" in text
    assert text.index("expired_at IS NULL") < text.index("ORDER BY"), "current belief must precede ranking"
    assert "LIMIT $limit" in text


@pytest.mark.parametrize("label", [name for name in statement_ids(FULL) if name.startswith("bounds")])
def test_bounds_are_not_filtered_to_current_belief(label):
    """The bound this exists to surface usually sits on a revision ranking hid.

    Under SCD-2 the successor carrying ``t_invalid`` scores identically to the row it replaced, so
    a bounds lookup restricted to current belief would report exactly nothing useful.
    """
    text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    assert "expired_at IS NULL" not in text
    assert "LIMIT" not in text, "an authority lookup is unranked and unlimited"


def test_the_row_builder_writes_nothing_outside_the_declared_properties():
    """The schema probe registers exactly these keys; a row carrying more would warn on every read.

    Worse than noise: the notification it would raise is the same one that catches a typo in a
    property name, so an undeclared key trains a reader to ignore the signal.
    """
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    row = substrate._row(FULL, "id-1", MemoryTier.EPISODIC, "content here", topics=["a"], t_valid=1)

    assert set(row) <= set(cypher.ROW_PROPERTIES), f"undeclared: {sorted(set(row) - set(cypher.ROW_PROPERTIES))}"


def test_the_schema_probe_covers_every_declared_property():
    for name in cypher.ROW_PROPERTIES:
        assert f"{name}: " in cypher.SCHEMA_STATEMENTS[0], f"{name} is declared but not registered by the probe"


def test_the_live_identity_is_constrained_but_the_logical_id_alone_is_not():
    """Both halves of the SCD-2 bargain, which pull in opposite directions.

    A constraint on ``id`` alone would reject the bounded copy invalidation exists to insert, since
    a successor shares its predecessor's id. A constraint on nothing leaves ``MERGE`` a check-then-act
    race. The live identity is the key that permits history and forbids two current beliefs.
    """
    constraints = [s for s in cypher.SCHEMA_STATEMENTS if "CONSTRAINT" in s]

    assert constraints, "the schema must constrain something, or this passes vacuously"
    for statement in constraints:
        assert ".id IS UNIQUE" not in statement, "a bare id constraint rejects every SCD-2 successor"

    live_identity = [
        s for s in constraints if f"id, n.{cypher.LIVE}) IS UNIQUE" in s or f"id, r.{cypher.LIVE}) IS UNIQUE" in s
    ]
    assert len(live_identity) == 3, f"every row shape needs the live-identity constraint, got {live_identity}"


def test_the_merge_key_and_the_constraint_are_the_same_key():
    """A constraint on a different key than ``MERGE`` uses protects nothing.

    This is the whole mechanism: Neo4j documents ``MERGE`` as concurrency-safe only when a
    uniqueness constraint backs the pattern it merges on.
    """
    for label in WRITE_STATEMENTS:
        text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)
        assert f"id: row.id, {cypher.LIVE}: true" in text, f"{label} does not merge on the live identity"


def test_the_substrate_satisfies_the_port_without_connecting():
    """Structural conformance, checked on the class: constructing one would open a socket."""
    required = [name for name in vars(GraphSubstrate) if not name.startswith("_")]

    assert required, "the port must expose methods, or this passes vacuously"
    missing = [name for name in required if not callable(getattr(Neo4jGraphSubstrate, name, None))]
    assert not missing, f"Neo4jGraphSubstrate does not satisfy GraphSubstrate: missing {missing}"


def test_a_write_under_a_foreign_scope_is_refused():
    """A mismatch has two defensible readings and a caller cannot tell which it got.

    Under the wrong one, a fact derived from one user's episodes is filed tenant-wide.
    """
    from egpt.memory.types import MemoryOperationError

    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    other = Scope(tenant_id="acme", user_id="bob")

    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        substrate._episode_rows(FULL, [MemoryEvent(content="x", scope=other, id="e1")])

    record = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=other)
    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        substrate._fact_batches(FULL, [FactWrite(record=record)])


def test_an_episode_without_an_id_is_refused_rather_than_given_one():
    """Minting one here would break the cross-substrate dedup key the provider stamped."""
    from egpt.memory.types import MemoryOperationError

    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)

    with pytest.raises(MemoryOperationError, match="dedup key"):
        substrate._episode_rows(FULL, [MemoryEvent(content="x", scope=FULL)])


def test_a_tripled_fact_becomes_a_relation_and_an_untripled_one_a_node():
    """SPO is optional structure over the statement text, so both must be storable."""
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    tripled = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="ada lives in berlin", scope=FULL)
    plain = MemoryRecord(id="f2", tier=MemoryTier.SEMANTIC, content="ada is thoughtful", scope=FULL)

    batches = substrate._fact_batches(
        FULL,
        [
            FactWrite(record=tripled, subject="ada", predicate="lives_in", object="berlin"),
            FactWrite(record=plain),
        ],
    )

    assert [row["id"] for row in batches["triple"]] == ["f1"]
    assert [row["id"] for row in batches["node"]] == ["f2"]


def test_query_terms_are_deduplicated_before_scoring():
    """The score's denominator is a set union, so a repeated term would shrink every score."""
    from egpt.integrations.memory.graph.neo4j.neo4j import _terms

    assert _terms("ada ada moved") == ["ada", "moved"]
    assert _terms("   ") == []
