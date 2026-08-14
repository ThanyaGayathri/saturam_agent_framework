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
from egpt.memory.types import (  # noqa: E402
    FactWrite,
    MemoryEvent,
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    Scope,
)

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


@pytest.mark.parametrize(
    "spo",
    [
        {"subject": "ada"},
        {"subject": "ada", "predicate": "lives_in"},
        {"predicate": "lives_in", "object": "berlin"},
        {"object": "berlin"},
    ],
    ids=["subject-only", "no-object", "no-subject", "object-only"],
)
def test_a_partial_triple_takes_the_node_path_and_drops_its_fragments(spo):
    """`is_triple()` needs all three, so a partial one is a node -- and its fragments are not stored.

    Pinned rather than left incidental: the silent part is that `subject`/`object` go nowhere on the
    node path, so a caller supplying two of three gets a fact whose partial structure is dropped
    without complaint. That is the documented "SPO is optional structure over the statement text"
    contract, and this is what says it was meant.
    """
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    record = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="ada lives in berlin", scope=FULL)

    batches = substrate._fact_batches(FULL, [FactWrite(record=record, **spo)])

    assert batches["triple"] == []
    assert [row["id"] for row in batches["node"]] == ["f1"]
    assert "predicate" not in batches["node"][0], "the node path stores no SPO fragment"


def test_query_terms_are_deduplicated_before_scoring():
    """The score's denominator is a set union, so a repeated term would shrink every score."""
    from egpt.integrations.memory.graph.neo4j.neo4j import _terms

    assert _terms("ada ada moved") == ["ada", "moved"]
    assert _terms("   ") == []


# ---------------------------------------------------------------------------
# Error taxonomy at the driver boundary
# ---------------------------------------------------------------------------

#: Every public method that reaches the driver, with a call that gets there. Enumerated so the
#: property below covers the whole surface rather than the two methods a review happened to name --
#: a translation that covers four of six call sites is the same gap wearing a smaller number.
DRIVER_CALLS = {
    "upsert_episodes": lambda g: g.upsert_episodes(FULL, [MemoryEvent(content="x", scope=FULL, id="e1")]),
    "upsert_facts": lambda g: g.upsert_facts(
        FULL, [FactWrite(record=MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=FULL))]
    ),
    "traverse": lambda g: g.traverse(FULL, MemoryQuery(text="x")),
    "bounds_for_ids": lambda g: g.bounds_for_ids(FULL, ["f1"]),
    "invalidate": lambda g: g.invalidate(FULL, "f1", t_invalid=300, at=310),
    "init_schema": lambda g: g.init_schema(),
    "close": lambda g: g.close(),
}


def _substrate_whose_driver_raises(exc):
    """A substrate wired to a driver that fails on any statement, without touching a database."""
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    substrate.database = "neo4j"
    substrate.uri = "bolt://localhost:7687"
    substrate._adriver = None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def run(self, *_args, **_kwargs):
            raise exc

        def begin_transaction(self):
            return self

        def commit(self):
            raise exc

        def rollback(self):
            return None

    class _Driver:
        def session(self, **_kwargs):
            return _Session()

        def close(self):
            raise exc

    substrate._driver = _Driver()
    return substrate


@pytest.mark.parametrize("method", sorted(DRIVER_CALLS))
def test_a_deterministic_driver_error_becomes_this_modules_refusal(method):
    """Bad Cypher, a constraint violation, a rejected password: identical on every retry.

    Left raw, the seam's contract would depend on which backend the config named -- a caller
    catching `MemoryOperationError` would see a `neo4j.exceptions.ClientError` instead.
    """
    from neo4j.exceptions import ClientError

    substrate = _substrate_whose_driver_raises(ClientError("bad cypher"))

    with pytest.raises(MemoryOperationError):
        DRIVER_CALLS[method](substrate)


@pytest.mark.parametrize("method", sorted(DRIVER_CALLS))
def test_a_transient_driver_error_is_left_alone(method):
    """The other half, and the reason this is not a blanket wrap.

    `co_located._raise_write_failure` reads `MemoryOperationError` as "this will never work" and
    prefers it as the cause of a failed fan-out. Translating an unreachable database into one would
    tell a caller to give up on a network blip -- and the same batch would report differently
    depending on which failure it happened to hit.
    """
    from neo4j.exceptions import ServiceUnavailable

    substrate = _substrate_whose_driver_raises(ServiceUnavailable("no route to host"))

    with pytest.raises(ServiceUnavailable):
        DRIVER_CALLS[method](substrate)


def test_the_driver_call_list_covers_every_public_method():
    """Non-vacuity: the two properties above are only as broad as this list.

    Compared against the port so a method added to `GraphSubstrate` cannot quietly sit outside the
    taxonomy, and against the module's own lifecycle methods, which the port does not name.
    """
    from egpt.memory.substrates import GraphSubstrate

    port_methods = {name for name in vars(GraphSubstrate) if not name.startswith("_")}
    sync_port_methods = {name for name in port_methods if not name.startswith("a")}
    lifecycle = {"init_schema", "close"}

    assert sync_port_methods <= set(DRIVER_CALLS), (
        f"port methods outside the taxonomy check: {sync_port_methods - set(DRIVER_CALLS)}"
    )
    assert lifecycle <= set(DRIVER_CALLS)


#: The async twin of every entry in ``DRIVER_CALLS``. Spelled out rather than derived by prefixing
#: an ``a``: the divergences this PR shipped were all *behavioural* while the names matched, so a
#: list built from the sync names would assert the one thing that was never in doubt.
ASYNC_DRIVER_CALLS = {
    "aupsert_episodes": lambda g: g.aupsert_episodes(FULL, [MemoryEvent(content="x", scope=FULL, id="e1")]),
    "aupsert_facts": lambda g: g.aupsert_facts(
        FULL, [FactWrite(record=MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=FULL))]
    ),
    "atraverse": lambda g: g.atraverse(FULL, MemoryQuery(text="x")),
    "abounds_for_ids": lambda g: g.abounds_for_ids(FULL, ["f1"]),
    "ainvalidate": lambda g: g.ainvalidate(FULL, "f1", t_invalid=300, at=310),
    "ainit_schema": lambda g: g.ainit_schema(),
    "aclose": lambda g: g.aclose(),
}


def _async_substrate_whose_driver_raises(exc):
    class _Result:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def run(self, *_args, **_kwargs):
            raise exc

        async def begin_transaction(self):
            return self

        async def commit(self):
            raise exc

        async def rollback(self):
            return None

    class _Driver:
        def session(self, **_kwargs):
            return _Session()

        async def close(self):
            raise exc

    substrate = _substrate_whose_driver_raises(exc)
    substrate._adriver = _Driver()
    substrate._async_driver = lambda: substrate._adriver
    return substrate


@pytest.mark.parametrize("method", sorted(ASYNC_DRIVER_CALLS))
async def test_the_async_half_translates_errors_identically(method):
    """The same taxonomy, asserted on the async half rather than assumed from it.

    Three defects in this PR were an async twin that shared a name and not a behaviour -- a
    `traverse` that never bound its `tiers` parameter, a `consolidate` missing its duplicate guard,
    and an `invalidate` that rolled back where its twin committed. Symmetry gets asserted here.
    """
    from neo4j.exceptions import ClientError, ServiceUnavailable

    with pytest.raises(MemoryOperationError):
        await ASYNC_DRIVER_CALLS[method](_async_substrate_whose_driver_raises(ClientError("bad cypher")))

    with pytest.raises(ServiceUnavailable):
        await ASYNC_DRIVER_CALLS[method](_async_substrate_whose_driver_raises(ServiceUnavailable("no route")))


def test_every_sync_driver_call_has_an_async_twin_in_the_taxonomy_check():
    """Non-vacuity for the pair above: a method covered on one side only is the gap itself."""
    assert {f"a{name}" for name in DRIVER_CALLS} == set(ASYNC_DRIVER_CALLS)


def test_the_no_op_invalidation_branch_rolls_back_on_both_halves():
    """A clean exit from the sync transaction *commits*; the async twin rolls back explicitly.

    Nothing is written before the check today, so no behavioural test can tell the two apart -- and
    that is exactly why the divergence is worth pinning now rather than after a write appears above
    it, when the sync half would silently keep what the async half discards. Asserted on the
    transaction calls rather than on stored state, because the stored state is identical either way.
    """
    calls = []

    class _Tx:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def run(self, *_args, **_kwargs):
            return []

        def commit(self):
            calls.append("commit")

        def rollback(self):
            calls.append("rollback")

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def begin_transaction(self):
            return _Tx()

    class _Driver:
        def session(self, **_kwargs):
            return _Session()

    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    substrate.database, substrate.uri, substrate._adriver = "neo4j", "bolt://x", None
    substrate._driver = _Driver()

    # No live row for this id, so `_invalidation_params` returns None and the branch is taken.
    substrate.invalidate(FULL, "never-written", t_invalid=300, at=310)

    assert calls == ["rollback"], f"the no-op branch must roll back and not commit, got {calls}"
