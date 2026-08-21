"""Guards over the Cypher the Neo4j substrate can emit (SM-G1 #99).

These assert on the **statements**, not on a database, which is the only way to cover every one of
them: a leak needs just one statement to forget a predicate, and a behavioural test covers the
statements it happens to exercise. SM-R2 section 5.4 is explicit that on Neo4j Community a
forgotten scope predicate is a cross-tenant leak with no engine-level second line of defence, so
this is the line of defence.

The behavioural half -- that the statements do what they say against a real engine -- is
``tests/integration/memory/neo4j/test_graph_substrate.py``.
"""

import re

import pytest

pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

from egpt.integrations.memory.graph.neo4j import cypher  # noqa: E402
from egpt.integrations.memory.graph.neo4j.neo4j import Neo4jGraphSubstrate  # noqa: E402
from egpt.memory.substrates import GraphSubstrate  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import (  # noqa: E402
    EntityMerge,
    EntityWrite,
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
        ("upsert_entities", *cypher.upsert_entities_statement(scope)),
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
        ("episode_probe", *cypher.episode_probe_statement(scope)),
        ("clear_mentions", *cypher.clear_mentions_statement(scope)),
        ("link_mentions", *cypher.link_mentions_statement(scope)),
        ("match_entities_by_key", *cypher.match_entities_by_key_statement(scope)),
        ("match_entities_by_embedding", *cypher.match_entities_by_embedding_statement(scope, k=25)),
        ("mentioning_episodes", *cypher.mentioning_episodes_statement(scope)),
        ("delete_entities", *cypher.delete_entities_statement(scope)),
        # Every filter combination, because each one appends its own clause to the predicate and a
        # scope guard that only held for the fully-filtered form would be the "one wiring" gap.
        *[
            (f"recall_episodes[{label}]", *cypher.recall_episodes_statement(scope, **kwargs))
            for label, kwargs in (
                ("entities", {"entities": ["berlin"], "since": None, "until": None, "limit": None}),
                ("window", {"entities": None, "since": 10, "until": 20, "limit": 5}),
                ("since", {"entities": None, "since": 10, "until": None, "limit": 0}),
                ("until", {"entities": None, "since": None, "until": 20, "limit": None}),
            )
        ],
        # Same reasoning as `recall_episodes` above, over `recall_facts`'s extra `relation` axis.
        *[
            (f"recall_facts[{label}]", *cypher.recall_facts_statement(scope, **kwargs))
            for label, kwargs in (
                ("entities", {"entities": ["berlin"], "relation": None, "since": None, "until": None, "limit": None}),
                ("relation", {"entities": None, "relation": "lives_in", "since": None, "until": None, "limit": None}),
                ("window", {"entities": None, "relation": None, "since": 10, "until": 20, "limit": 5}),
                ("since", {"entities": None, "relation": None, "since": 10, "until": None, "limit": 0}),
                ("until", {"entities": None, "relation": None, "since": None, "until": 20, "limit": None}),
            )
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

    # Derived from what `all_statements` *calls*, not from a second list. The literal set this
    # replaced named `episode_probe_statement` while `all_statements` did not emit it, so the
    # coverage check passed and the probe sat outside every scope guard -- a list restating the
    # claim rather than checking it.
    import inspect

    called = {name for name in producers if f"cypher.{name}(" in inspect.getsource(all_statements)}

    assert producers == called, (
        f"statement producers `all_statements` never calls: {sorted(producers - called)}; "
        f"names it calls that cypher.py no longer exports: {sorted(called - producers)}"
    )


def statement_ids(scope):
    return [label for label, _, _ in all_statements(scope)]


#: Statements that carry their scope **on each row** rather than in bound parameters. A write always
#: has a fully-specified scope, so it matches `scope_key` exactly; a read's unset slot means "any",
#: which an equality on the composite key could not express.
#: The two mention statements are here for the same reason: both address the live episode by its
#: exact ``scope_key``, so an episode written under one scope is unreachable from another whichever
#: end the statement starts at.
WRITE_STATEMENTS = (
    "upsert_episodes",
    "upsert_entities",
    "upsert_facts[triple]",
    "upsert_facts[node]",
    "clear_mentions",
    "link_mentions",
)

#: Entity resolution's lookups and cleanup (SM-G8 #106): scoped by exact ``scope_key``, like a
#: write, but bound as a single query parameter rather than carried per-row -- there is exactly one
#: caller scope per call, not a batch of rows that might not share one. Always issued for an
#: already-fully-specified :class:`~egpt.memory.types.EntityWrite`'s own scope (validated at the
#: pipeline, never a partially-specified caller scope), so the write-style exact match is the
#: correct predicate here, not the read-style per-slot conjunction ``scope_predicate`` builds --
#: which would also defeat the ``egpt_entity_key`` / ``egpt_entity_embedding`` indexes, both keyed
#: on ``scope_key`` rather than on the individual slots.
KEY_SCOPED_STATEMENTS = (
    "match_entities_by_key",
    "match_entities_by_embedding",
    "mentioning_episodes",
    "delete_entities",
)


@pytest.mark.parametrize(
    "label",
    [name for name in statement_ids(FULL) if name not in WRITE_STATEMENTS and name not in KEY_SCOPED_STATEMENTS],
)
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


@pytest.mark.parametrize("label", KEY_SCOPED_STATEMENTS)
def test_every_key_scoped_statement_filters_on_the_exact_bound_scope_key(label):
    """The third mechanism's own version of the rule above: one bound ``$scope_key``, not a row."""
    text, params = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    assert "scope_key: $scope_key" in text or "scope_key = $scope_key" in text, (
        f"{label} does not filter on the bound scope_key"
    )
    assert params["scope_key"] == FULL.key(), f"{label} binds the wrong scope_key"


def test_every_statement_is_scoped_by_one_mechanism_or_the_other():
    """Nothing may be unscoped. Checked over the whole set so a new statement cannot slip through
    by belonging to none of the three categories above."""
    for label, text, _ in all_statements(FULL):
        scoped = (
            ".tenant_id = $tenant_id" in text
            or "scope_key: row.scope_key" in text
            or "scope_key: $scope_key" in text
            or "scope_key = $scope_key" in text
        )
        assert scoped, f"{label} names no scope at all"


def test_the_row_builder_supplies_the_scope_key_a_write_merges_on():
    """The two halves of the write-side scoping: the statement reads ``row.scope_key``, and this is
    what puts it there. Asserted together, because either alone is scoping that does not happen."""
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    row = substrate._row(FULL, "id-1", MemoryTier.EPISODIC, "content here")

    assert row["scope_key"] == FULL.key()
    assert Scope(tenant_id="acme").key() != FULL.key(), "the key must distinguish scopes"


@pytest.mark.parametrize(
    "label",
    [name for name in statement_ids(TENANT_ONLY) if name not in WRITE_STATEMENTS and name not in KEY_SCOPED_STATEMENTS],
)
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
    # `"ORDER BY score"`, not a bare `"ORDER BY"`: the multi-hop leg (SM-G9 #107 round-1 review)
    # also orders its anchor set (`ORDER BY anchor.name ASC LIMIT $max_anchors`) before the final
    # ranking clause, and a bare search finds that one first.
    assert text.index("expired_at IS NULL") < text.index("ORDER BY score"), "current belief must precede ranking"
    assert "LIMIT $limit" in text


def test_return_selects_every_scope_slot():
    """Round-1 review, Major: without these columns every ``traverse`` row reconstructs as the
    *call* scope (see ``test_traverse_reconstructs_a_rows_own_write_scope_not_the_call_scope``),
    which degenerates ``Neo4jGraphSubstrate._merge_hits``'s ``(scope, id)`` dedup key to a bare id
    under a tenant-wide read."""
    for slot in cypher.SCOPE_SLOTS:
        assert f"row.{slot} AS {slot}" in cypher._RETURN


def test_traverse_reconstructs_a_rows_own_write_scope_not_the_call_scope():
    """``_record`` must reconstruct scope from the row's own columns for a ``traverse``-shaped row
    (no ``mentions``/``subject`` key) -- not only for ``recall_episodes``/``recall_facts`` rows,
    which is all it did before round-1 review found the gap."""
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    call_scope = Scope(tenant_id="acme")
    write_scope = Scope(tenant_id="acme", user_id="bob")
    row = {
        "id": "f1",
        "tier": "semantic",
        "content": "bob earns 150k",
        "topics": None,
        "t_valid": None,
        "t_invalid": None,
        "created_at": None,
        "expired_at": None,
        "superseded_by": None,
        "provenance": "{}",
        "metadata": "{}",
        "tenant_id": "acme",
        "agent_id": None,
        "user_id": "bob",
        "team_id": None,
        "session_id": None,
        "score": 0.5,
    }

    record = substrate._record(row, call_scope)

    assert record.scope == write_scope, "must reconstruct the row's own write scope, not the call scope"


@pytest.mark.parametrize("label", [name for name in statement_ids(FULL) if name.startswith("traverse")])
def test_every_traverse_leg_applies_the_bi_temporal_window(label):
    """SM-G9 (#107): the window is pushed into every leg, not only the row-shape ones. Retires the
    gap ``test_the_graph_leg_does_not_narrow_to_the_valid_time_window`` pinned."""
    text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)

    assert "$valid_from" in text
    assert "$valid_to" in text
    # See `test_traverse_filters_current_belief_before_ranking` for why this is `"ORDER BY score"`.
    assert text.index("$valid_to") < text.index("ORDER BY score"), "the window must narrow before ranking"


def _multihop_traverse_statement():
    """The anchor-seeded leg specifically -- identified by the hop-bound syntax no other leg emits,
    not by list position, so a reordering of :func:`cypher.traverse_statements` cannot silently
    point this file's assertions at the wrong statement."""
    for label, text, params in all_statements(FULL):
        if label.startswith("traverse") and "*1.." in text:
            return label, text, params
    raise AssertionError("no traverse leg emits a bounded-hop relationship pattern")


def test_the_multihop_leg_bounds_the_walk_to_a_literal_hop_count():
    """Not a bound parameter -- Cypher does not accept one for a variable-length pattern, and SM-G9
    closes the bound at the source (construction time) rather than trusting a per-call value."""
    _, text, _ = _multihop_traverse_statement()
    assert f"*1..{cypher.DEFAULT_MAX_HOPS}" in text

    custom_text = next(t for t, _ in cypher.traverse_statements(FULL, max_hops=5) if "*1.." in t)
    assert "*1..5" in custom_text


@pytest.mark.parametrize("max_hops", [0, -1, 1.5, True])
def test_max_hops_must_be_a_positive_integer(max_hops):
    with pytest.raises(MemoryOperationError, match="max_hops"):
        cypher.traverse_statements(FULL, max_hops=max_hops)


def test_the_multihop_leg_selects_anchors_lexically_against_the_query_terms():
    """No embedding, no model call -- an anchor is chosen by word overlap against a stored entity's
    normalised ``key``, matching this port's no-query-time-LLM contract."""
    _, text, _ = _multihop_traverse_statement()

    assert "$terms" in text
    assert "anchor.key" in text
    assert text.index("anchor.key") < text.index("MATCH path"), "anchors must be selected before the walk"


def test_the_multihop_leg_scopes_the_walk_to_one_exact_entity_graph():
    """``other.scope_key = anchor.scope_key`` -- a tenant-wide read must not let a name collision
    bridge two users' private entity graphs (SM-C4 #92)."""
    _, text, _ = _multihop_traverse_statement()

    assert "other.scope_key = anchor.scope_key" in text


def test_the_multihop_leg_scopes_every_intermediate_node_too():
    """Round-1 review, Major: checking only ``other`` (the walk's far end) left every node strictly
    between the anchor and ``other`` unscoped. ``nodes(path)[1..-1]`` is every node except the two
    ends -- present and ordered before the relationships clause, since the node check and the
    edge-window/current-belief check govern the same ``MATCH ... WHERE`` and both must hold for a
    path to be walked at all."""
    _, text, _ = _multihop_traverse_statement()

    assert "all(n IN nodes(path)[1..-1] WHERE n.scope_key = anchor.scope_key)" in text
    assert text.index("other.scope_key = anchor.scope_key") < text.index("nodes(path)[1..-1]")


def test_the_multihop_leg_bounds_the_anchor_set():
    """Round-1 review, Major: the hop bound alone does not cap this leg's cost -- a common query
    word matches every entity whose name contains it, so the anchor set needs its own ceiling."""
    _, text, params = _multihop_traverse_statement()

    assert "LIMIT $max_anchors" in text
    assert text.index("WITH DISTINCT anchor") < text.index("LIMIT $max_anchors") < text.index("MATCH path")
    assert params["max_anchors"] == cypher.DEFAULT_MAX_ANCHOR_ENTITIES

    custom_params = next(p for t, p in cypher.traverse_statements(FULL, max_anchor_entities=7) if "*1.." in t)
    assert custom_params["max_anchors"] == 7


def test_the_multihop_leg_orders_the_anchor_cut_deterministically():
    """No uuid in an ordering, matching every other statement here -- which anchors survive
    ``LIMIT $max_anchors`` must not depend on visitation order the engine happens to use."""
    _, text, _ = _multihop_traverse_statement()

    assert "ORDER BY anchor.name ASC LIMIT $max_anchors" in text


@pytest.mark.parametrize("max_anchor_entities", [0, -1, 2.5, True])
def test_max_anchor_entities_must_be_a_positive_integer(max_anchor_entities):
    with pytest.raises(MemoryOperationError, match="max_anchor_entities"):
        cypher.traverse_statements(FULL, max_anchor_entities=max_anchor_entities)


def test_the_multihop_leg_refuses_to_walk_through_a_superseded_or_out_of_window_edge():
    """Applied to *every* edge in the path, not only the one returned -- a superseded or
    out-of-window edge cannot extend a walk, matching what :func:`cypher._traverse` refuses to
    rank."""
    _, text, _ = _multihop_traverse_statement()

    all_clause_start = text.index("all(rel IN relationships(path)")
    all_clause_end = text.index("UNWIND")
    all_clause = text[all_clause_start:all_clause_end]
    assert "rel.expired_at IS NULL" in all_clause
    assert "$valid_to" in all_clause and "$valid_from" in all_clause


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
    for name in (*cypher.ROW_PROPERTIES, *cypher.ENTITY_PROPERTIES):
        assert f"{name}: " in cypher.SCHEMA_STATEMENTS[0], f"{name} is declared but not registered by the probe"


def test_the_entity_key_and_embedding_are_indexed():
    """Review finding on PR #89, round 2. Without these, SM-G8's (#106) two lookups -- exact-match
    grouping on ``key`` and similarity matching on ``embedding`` -- are full scans of every
    ``:Entity`` node across every scope, not just the current one."""
    statements = " ".join(cypher.SCHEMA_STATEMENTS)

    assert f"INDEX egpt_{cypher.ENTITY.lower()}_key" in statements
    assert "ON (n.scope_key, n.key)" in statements
    assert f"VECTOR INDEX egpt_{cypher.ENTITY.lower()}_embedding" in statements
    assert "ON (n.embedding)" in statements


def test_the_rel_edges_t_valid_predicate_is_indexed():
    """Review finding on PR #102, round 1. The id-equality index (`egpt_rel_scope`) never helps
    `recall_facts_statement`'s `t_valid` window predicate, which never mentions `id` -- without a
    dedicated index, a `since`/`until` bound `recall_facts` read is a sequential scan over every REL
    edge in the tenant. Mirrors `egpt_episode_t_valid`'s reason on the episodic side."""
    statements = " ".join(cypher.SCHEMA_STATEMENTS)

    assert "INDEX egpt_rel_t_valid" in statements
    assert "ON (r.tenant_id, r.t_valid)" in statements


def test_the_entity_row_builder_writes_nothing_outside_the_declared_properties():
    """The :data:`ROW_PROPERTIES` guard, applied to the other shape (SM-G2 #100).

    An entity node's properties are a different set, so the row guard above could not have covered
    them -- and an undeclared key warns on every read that names it, which is the same signal a
    typo raises.
    """
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    (row,) = substrate._entity_rows(FULL, [EntityWrite(name="Acme", entity_type="company", scope=FULL)])

    written = {key for key in row if key != "props"} | set(row["props"])
    assert written <= set(cypher.ENTITY_PROPERTIES), f"undeclared: {sorted(written - set(cypher.ENTITY_PROPERTIES))}"

    # The two unioned fields must never be *inside* props (review finding on PR #89, round 2):
    # `upsert_entities_statement` runs `SET n += row.props` before its two union `SET`s, so a field
    # that entered `props` would be replaced there and the union that runs after would be unioning
    # against its own just-replaced value -- silently breaking the SM-R7 provenance guarantee with
    # no failing test, since the membership check above cannot see *where* a key landed.
    assert "source_episode_ids" not in row["props"], "union semantics break if this enters props"
    assert "aliases" not in row["props"], "union semantics break if this enters props"


def test_an_unembedded_entity_omits_the_embedding_keys_rather_than_nulling_them():
    """Review finding on PR #96 (SM-G8): ``SET n += row.props`` deletes a property whose value in
    the map is ``null``. A row for an ``EntityWrite`` with no embedding must omit ``embedding`` and
    ``embedding_version`` from ``props`` entirely, or every such write -- a merge whose canonical
    came from a match result, or a plain description-only re-upsert -- erases whatever embedding
    the node already had.
    """
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    (row,) = substrate._entity_rows(FULL, [EntityWrite(name="Acme", entity_type="company", scope=FULL)])

    assert "embedding" not in row["props"]
    assert "embedding_version" not in row["props"]


def test_an_embedded_entity_still_carries_both_embedding_keys():
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    (row,) = substrate._entity_rows(
        FULL, [EntityWrite(name="Acme", entity_type="company", scope=FULL, embedding=[0.1, 0.2], embedding_version="v")]
    )

    assert row["props"]["embedding"] == [0.1, 0.2]
    assert row["props"]["embedding_version"] == "v"


def test_an_entity_node_carries_no_bi_temporal_field():
    """ADR 0003 section 1 ratifies ``SemanticEntity`` as embeddable but **not** bi-temporal.

    An entity existing is not a claim about the world that can become false, so a node carrying
    ``t_invalid`` would invite an invalidation the port has no verb for -- and a ``live`` marker
    would put it under the SCD-2 constraint, where a second sighting is a duplicate current belief.
    """
    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    (row,) = substrate._entity_rows(FULL, [EntityWrite(name="Acme", entity_type="company", scope=FULL)])

    written = {key for key in row if key != "props"} | set(row["props"])
    assert written.isdisjoint({"t_valid", "t_invalid", "expired_at", "superseded_by", cypher.LIVE, "tier"})


def test_the_entity_statement_unions_provenance_rather_than_replacing_it():
    """SM-R7 section 7's trail. Every other write on this backend replaces by id; this one must
    not, or the second episode to mention an entity erases the first as its provenance.

    ``coalesce`` matters as much as the union: an ``(:Entity)`` created as the endpoint of a triple
    write has no list at all, and appending to null in Cypher yields null.
    """
    text, _ = cypher.upsert_entities_statement(FULL)

    for name in ("source_episode_ids", "aliases"):
        assert f"coalesce(n.{name}, [])" in text, f"{name} is not unioned against a possibly-absent list"
        assert f"WHERE NOT x IN row.{name}] + row.{name}" in text, f"{name} accumulates duplicates on a re-run"
    assert "SET n += row.props" in text, "everything outside the two lists is last-write"


def test_only_an_entitys_first_sighting_sets_created_at():
    """``created_at`` is when the entity entered the graph; ``updated_at`` is the last mention.

    Both from ``SET n += row.props`` would make the two the same number on every write, and the
    first-seen ordering SM-G8 (#106) reads would be lost.

    Asserted as ``coalesce``, not literal ``ON CREATE``: review finding on PR #89 -- this MERGE is
    not always the node's creation, since ``upsert_facts_statements`` merges the same identity as a
    triple endpoint and sets no ``created_at`` at all. ``ON CREATE`` would then never fire on a node
    SM-G3 wrote first, leaving ``created_at`` null forever. ``coalesce`` fills a null unconditionally
    and leaves an already-timestamped node alone, which is what actually holds the invariant.
    """
    text, _ = cypher.upsert_entities_statement(FULL)

    assert "SET n.created_at = coalesce(n.created_at, row.created_at)" in text
    assert text.index("n.created_at = coalesce") < text.index("SET n += row.props")


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


#: Every ``MERGE`` pattern a statement can contain: a labelled node, or a typed relationship.
_MERGE_NODE = re.compile(r"MERGE \(\w*:(\w+) \{([^}]*)\}\)")
_MERGE_REL = re.compile(r"MERGE \(\w+\)-\[\w*:(\w+) \{([^}]*)\}\]->")
_CONSTRAINT_LABEL = re.compile(r"FOR \(\w*:(\w+)\)|FOR \(\)-\[\w*:(\w+)\]-\(\)")
_CONSTRAINT_KEY = re.compile(r"REQUIRE \(([^)]*)\) IS UNIQUE")


def merged_keys(text):
    """``(label, {property names})`` for every ``MERGE`` pattern in one statement."""
    return [
        (label, frozenset(part.split(":")[0].strip() for part in props.split(",")))
        for pattern in (_MERGE_NODE, _MERGE_REL)
        for label, props in pattern.findall(text)
    ]


def constrained_keys():
    """``(label, {property names})`` for every composite uniqueness constraint the schema creates.

    A single-property constraint counts too, as long as it parenthesizes its key
    (``REQUIRE (r.link_id) IS UNIQUE``, not the bare ``REQUIRE r.link_id IS UNIQUE`` every
    surrogate-key (``row_id``) constraint uses) -- ``egpt_mentions_link`` is deliberately spelled
    that way so this derivation can see it, since ``link_id`` is a real ``MERGE`` key and the
    ``row_id`` ones are not.
    """
    keys = set()
    for statement in cypher.SCHEMA_STATEMENTS:
        label_match, key_match = _CONSTRAINT_LABEL.search(statement), _CONSTRAINT_KEY.search(statement)
        if not label_match or not key_match:
            continue
        label = label_match.group(1) or label_match.group(2)
        keys.add((label, frozenset(part.strip().split(".", 1)[1] for part in key_match.group(1).split(","))))
    return keys


def test_the_merge_key_and_the_constraint_are_the_same_key():
    """A constraint on a different key than ``MERGE`` uses protects nothing.

    This is the whole mechanism: Neo4j documents ``MERGE`` as concurrency-safe only when a
    uniqueness constraint backs the pattern it merges on.

    Derived from the statements rather than asserted as a literal key, because the literal one was
    the live identity and SM-G2's ``(:Entity)`` nodes are deliberately not bi-temporal -- so a
    literal check could only have been satisfied by exempting the new shape, which is the check
    not running.
    """
    constrained = constrained_keys()
    assert constrained, "the schema must constrain something, or this passes vacuously"

    checked = 0
    skipped = 0
    for label in WRITE_STATEMENTS:
        text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)
        keys = merged_keys(text)
        if not keys:
            # `clear_mentions` deletes what it matched; it merges nothing, so there is no key to
            # back and nothing here to check. Every other write statement must contribute one.
            assert label == "clear_mentions", f"{label} contains no MERGE pattern this test can read"
            skipped += 1
            continue
        for key in keys:
            assert key in constrained, f"{label} merges on {key}, which no uniqueness constraint backs"
            checked += 1
    assert checked >= len(WRITE_STATEMENTS) - skipped, "every merging write statement must contribute a key"


def test_the_entity_merge_is_backed_by_its_own_constraint():
    """Both statements that ``MERGE`` an entity node key on the same pair, so one constraint covers
    them -- and a shape that keyed on the tenant instead would share one user's entities with
    another (entity resolution is SM-G8's call, not a side effect of a merge key)."""
    for label in ("upsert_facts[triple]", "link_mentions"):
        text, _ = next((t, p) for name, t, p in all_statements(FULL) if name == label)
        assert "MERGE (n:Entity {scope_key: row.scope_key, name: row." in text or (
            "MERGE (s:Entity {scope_key: row.scope_key, name: row." in text
        ), f"{label} does not merge entities on the full scope"
    assert any("(n.scope_key, n.name) IS UNIQUE" in s for s in cypher.SCHEMA_STATEMENTS), (
        "nothing constrains the entity identity"
    )


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
        substrate._episode_rows(FULL, [MemoryEvent(content="x", scope=other, id="e1")], 100)

    record = MemoryRecord(id="f1", tier=MemoryTier.SEMANTIC, content="x", scope=other)
    with pytest.raises(MemoryOperationError, match="does not match the write scope"):
        substrate._fact_batches(FULL, [FactWrite(record=record)])


def test_an_episode_without_an_id_is_refused_rather_than_given_one():
    """Minting one here would break the cross-substrate dedup key the provider stamped."""
    from egpt.memory.types import MemoryOperationError

    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)

    with pytest.raises(MemoryOperationError, match="dedup key"):
        substrate._episode_rows(FULL, [MemoryEvent(content="x", scope=FULL)], 100)


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
    "upsert_entities": lambda g: g.upsert_entities(FULL, [EntityWrite(name="Acme", entity_type="company", scope=FULL)]),
    "match_entities": lambda g: g.match_entities(
        FULL, EntityWrite(name="Acme", entity_type="company", scope=FULL, embedding=[0.1, 0.2])
    ),
    "merge_entities": lambda g: g.merge_entities(
        FULL,
        [
            EntityMerge(
                canonical=EntityWrite(name="Acme", entity_type="company", scope=FULL),
                duplicate_names=["Acme Corp"],
            )
        ],
    ),
    "traverse": lambda g: g.traverse(FULL, MemoryQuery(text="x")),
    "recall_episodes": lambda g: g.recall_episodes(FULL, since=10),
    "recall_facts": lambda g: g.recall_facts(FULL, since=10),
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
    "aupsert_entities": lambda g: g.aupsert_entities(
        FULL, [EntityWrite(name="Acme", entity_type="company", scope=FULL)]
    ),
    "amatch_entities": lambda g: g.amatch_entities(
        FULL, EntityWrite(name="Acme", entity_type="company", scope=FULL, embedding=[0.1, 0.2])
    ),
    "amerge_entities": lambda g: g.amerge_entities(
        FULL,
        [
            EntityMerge(
                canonical=EntityWrite(name="Acme", entity_type="company", scope=FULL),
                duplicate_names=["Acme Corp"],
            )
        ],
    ),
    "atraverse": lambda g: g.atraverse(FULL, MemoryQuery(text="x")),
    "arecall_episodes": lambda g: g.arecall_episodes(FULL, since=10),
    "arecall_facts": lambda g: g.arecall_facts(FULL, since=10),
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


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_the_no_op_invalidation_branch_rolls_back(half):
    """A clean exit from the sync transaction *commits*; the async twin rolls back explicitly.

    Nothing is written before the check today, so no behavioural test can tell the two apart -- and
    that is exactly why the divergence is worth pinning now rather than after a write appears above
    it. Asserted on the transaction calls, because the stored state is identical either way.

    Parametrised because the previous version was named "both halves" and drove only the sync one.
    When a write does appear above the check, the untested half is the one that silently keeps it.
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

    class _AResult:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _ATx:
        async def run(self, *_args, **_kwargs):
            return _AResult()

        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    class _ASession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def begin_transaction(self):
            return _ATx()

    class _Driver:
        def session(self, **_kwargs):
            return _Session()

    class _ADriver:
        def session(self, **_kwargs):
            return _ASession()

    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    substrate.database, substrate.uri, substrate._adriver = "neo4j", "bolt://x", None
    substrate._driver = _Driver()

    # No live row for this id, so `_invalidation_params` returns None and the branch is taken.
    if half == "sync":
        substrate.invalidate(FULL, "never-written", t_invalid=300, at=310)
    else:
        substrate._adriver = _ADriver()
        substrate._async_driver = lambda: substrate._adriver
        await substrate.ainvalidate(FULL, "never-written", t_invalid=300, at=310)

    assert calls == ["rollback"], f"the no-op branch must roll back and not commit, got {calls}"


@pytest.mark.parametrize("half", ["sync", "async"])
async def test_merge_entities_runs_the_read_and_the_write_in_one_transaction(half):
    """Review finding on PR #96 (round 2): the mentioning-episodes read and the upsert/re-point/
    delete write must observe one transaction, or a concurrent write between them can be lost --
    see the ``merge_entities`` port docstring's TOCTOU note. Pinned by counting transactions
    opened, not by asserting the code structure: a future refactor that reintroduces two separate
    round trips would open two, and this would catch it regardless of how it did so.
    """
    opened = []

    class _Tx:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def run(self, *_args, **_kwargs):
            return []

        def commit(self):
            pass

        def rollback(self):
            pass

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def begin_transaction(self):
            opened.append("tx")
            return _Tx()

    class _ATx:
        async def run(self, *_args, **_kwargs):
            class _Result:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            return _Result()

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class _ASession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def begin_transaction(self):
            opened.append("tx")
            return _ATx()

    class _Driver:
        def session(self, **_kwargs):
            return _Session()

    class _ADriver:
        def session(self, **_kwargs):
            return _ASession()

    substrate = Neo4jGraphSubstrate.__new__(Neo4jGraphSubstrate)
    substrate.database, substrate.uri, substrate._adriver = "neo4j", "bolt://x", None
    substrate._driver = _Driver()
    merge = EntityMerge(
        canonical=EntityWrite(name="Acme", entity_type="company", scope=FULL), duplicate_names=["Acme Corp"]
    )

    if half == "sync":
        substrate.merge_entities(FULL, [merge])
    else:
        substrate._adriver = _ADriver()
        substrate._async_driver = lambda: substrate._adriver
        await substrate.amerge_entities(FULL, [merge])

    assert opened == ["tx"], f"expected exactly one transaction, got {len(opened)}"
