"""Every statement the pgvector memory substrate can emit, checked without a database (SM-V1 #95).

The behavioural properties live in ``tests/integration/memory/pgvector/``, against a real engine.
What is here is the class of defect a behavioural test cannot be trusted to cover: a scope predicate
is only missing on the statement nobody happened to exercise, and a missing tenant predicate is a
cross-tenant read that a single-tenant test passes cleanly.

So these assert over **every** builder in the module rather than over a sample, and they need no
driver -- they run in the ``unit-drivers-absent`` job too, which is the one that would catch a
statement moved behind an import guard.
"""

from dataclasses import fields

import pytest

from egpt.integrations.memory.vector.pgvector import sql
from egpt.memory.types import MemoryScopeRequiredError, Scope, ValidWindow

FULL = Scope(tenant_id="acme", agent_id="ag", user_id="ada", team_id="tm", session_id="se")
TENANT_ONLY = Scope(tenant_id="acme")
#: Values that cannot appear inside a column name. ``agent_id`` contains ``ag`` and ``session_id``
#: contains ``se``, so a leak test written against :data:`FULL` passes on the column list alone.
WINDOW = ValidWindow(valid_from=100, valid_to=900)
UNMISTAKABLE = Scope(
    tenant_id="zztenantzz",
    agent_id="zzagentzz",
    user_id="zzuserzz",
    team_id="zzteamzz",
    session_id="zzsessionzz",
)


def row_reading_statements(scope):
    """Every statement in the module that reads stored rows, with the builder's name.

    Enumerated here rather than sampled, because the guard below is only worth its runtime if it
    covers the statement someone adds next. A new reader that is not added here still fails
    ``test_every_row_reading_builder_is_covered``.
    """
    return {
        "search": sql.search_statement("ai", "memory_vectors", scope, None)[0],
        # The windowed spelling is a separate entry rather than a replacement: SM-V4 (#98) adds a
        # second `WHERE` shape to the same builder, and a scope predicate dropped on only one of
        # them is exactly the "missing on the statement nobody exercised" defect this file exists
        # for.
        "search_windowed": sql.search_statement("ai", "memory_vectors", scope, WINDOW)[0],
        "search_open_start": sql.search_statement("ai", "memory_vectors", scope, ValidWindow(valid_to=900))[0],
        "search_open_end": sql.search_statement("ai", "memory_vectors", scope, ValidWindow(valid_from=100))[0],
        "lexical_search": sql.lexical_search_statement("ai", "memory_vectors", scope, "english", None)[0],
        # The lexical leg is the one place SM-V3 and SM-V4 actually meet: it must inherit the
        # window exactly as the vector leg does, or a hybrid read leaks an out-of-window row
        # through whichever leg forgot to filter it.
        "lexical_search_windowed": sql.lexical_search_statement("ai", "memory_vectors", scope, "english", WINDOW)[0],
        "scan": sql.scan_statement("ai", "memory_vectors", scope, 10)[0],
        "scan_unlimited": sql.scan_statement("ai", "memory_vectors", scope, None)[0],
    }


def test_every_row_reading_builder_is_covered():
    """The guards below are only as good as this list, so the list is checked against the module.

    Without this, adding a ``delete_statement`` that forgets the user predicate passes every test
    in this file -- the enumeration silently stops being an enumeration.
    """
    readers = {name for name in dir(sql) if name.endswith("_statement") and not name.startswith("_")}
    assert readers == {
        "search_statement",
        "lexical_search_statement",
        "scan_statement",
        "create_partition_statement",
        "upsert_statement",
        "ef_search_statement",
        # Reads `pg_catalog`, not stored rows, so it carries no scope and is absent from
        # `row_reading_statements` -- like `ef_search_statement` and `create_partition_statement`.
        "text_search_config_statement",
    }, readers


@pytest.mark.parametrize("name", sorted(row_reading_statements(FULL)))
def test_every_row_reading_statement_filters_on_every_set_slot(name):
    """A backend that honours the tenant and ignores the user serves one user's memory to another.

    Partitioning does not help here: it covers ``tenant_id`` and nothing else, so the other four
    slots are ordinary predicates that a hand-written statement can simply omit.
    """
    statement = row_reading_statements(FULL)[name]
    for slot in sql.SCOPE_SLOTS:
        assert f"{slot} = :{slot}" in statement, (name, slot)


@pytest.mark.parametrize("name", sorted(row_reading_statements(TENANT_ONLY)))
def test_an_unset_slot_contributes_no_predicate(name):
    """``None`` means "any", not "IS NULL".

    Filtering on ``user_id IS NULL`` would hide every row written under a user from a tenant-wide
    read -- an empty answer that looks like an empty corpus.
    """
    statement = row_reading_statements(TENANT_ONLY)[name]
    assert "tenant_id = :tenant_id" in statement
    for slot in sql.SCOPE_SLOTS[1:]:
        # The predicate form, not the bare name: every slot is also a selected column, so
        # `slot not in statement` would be asserting that the row mapping is broken.
        assert f"{slot} = :" not in statement, (name, slot)


@pytest.mark.parametrize("name", sorted(row_reading_statements(UNMISTAKABLE)))
def test_no_scope_value_reaches_the_statement_text(name):
    """Scope values travel as binds. A value interpolated into SQL is an injection whether or not
    this module's escaping happens to be right today."""
    statement = row_reading_statements(UNMISTAKABLE)[name]
    for slot in sql.SCOPE_SLOTS:
        assert getattr(UNMISTAKABLE, slot) not in statement, (name, slot)


def test_the_scope_predicate_binds_exactly_the_slots_it_names():
    """A bind the statement does not name is dead; a slot named without a bind fails at execution."""
    predicate, binds = sql.scope_predicate(FULL)
    assert set(binds) == set(sql.SCOPE_SLOTS)
    predicate, binds = sql.scope_predicate(TENANT_ONLY)
    assert set(binds) == {"tenant_id"}
    assert predicate == "tenant_id = :tenant_id"


def test_search_filters_current_belief_before_the_limit():
    """Transaction time is applied in the ``WHERE`` clause, not after the page comes back.

    Filtering afterwards lets a page of superseded rows consume the whole limit and return a short
    page of nothing -- the port states it for exactly this reason.
    """
    statement, _ = sql.search_statement("ai", "memory_vectors", FULL, None)
    where, order = statement.split("ORDER BY")
    assert "expired_at IS NULL" in where
    assert "LIMIT" in order and "expired_at" not in order


def test_scan_deliberately_does_not_filter_current_belief():
    """The asymmetry with ``search`` is a decision, and this is where it is recorded.

    ``scan`` is an enumeration: the port states the current-belief predicate for ranked reads only,
    and a substrate that narrowed an enumeration would let a consolidation pass report a drained
    tier while never seeing part of it. The reference double (``FakeVector.scan``) does not filter
    either, and a backend disagreeing with the double is how a provider test stops meaning anything.
    """
    statement, _ = sql.scan_statement("ai", "memory_vectors", FULL, 10)
    assert "expired_at IS NULL" not in statement
    # Still *selected*, so a caller can see the bound the row carries and decide for itself.
    assert "expired_at" in statement.split("FROM")[0]


def test_search_orders_by_the_distance_operator_alone():
    """A secondary sort key here forces a sort above the index scan and can drop the plan to a
    sequential scan -- SM-R3 Finding 3's failure, arriving through the ``ORDER BY``. Determinism is
    applied to the returned page instead, where it costs nothing.
    """
    _, order = sql.search_statement("ai", "memory_vectors", FULL, None)[0].split("ORDER BY")
    assert order.strip().startswith("embedding <=> CAST(:query_embedding AS vector)")
    assert "," not in order.split("LIMIT")[0]


def test_scan_orders_oldest_first_and_never_on_a_uuid_alone():
    """``(created_at, id)`` is a total order, so a pass walks the same rows in the same sequence."""
    statement, _ = sql.scan_statement("ai", "memory_vectors", FULL, None)
    assert statement.rstrip().endswith("ORDER BY created_at, id")


def test_an_unlimited_scan_emits_no_limit_clause_and_binds_no_limit():
    """``None`` means unlimited; a large number standing in for it is a different request."""
    statement, binds = sql.scan_statement("ai", "memory_vectors", FULL, None)
    assert "LIMIT" not in statement
    assert "limit" not in binds
    statement, binds = sql.scan_statement("ai", "memory_vectors", FULL, 0)
    assert binds["limit"] == 0


def test_the_upsert_replaces_by_the_partition_key_and_the_id():
    """Upsert by id is what makes a ``PartialWriteError`` retry idempotent: the retry reuses the
    stamped ids, and an appending backend would turn that into duplicate rows."""
    statement = sql.upsert_statement("ai", "memory_vectors")
    assert "ON CONFLICT (tenant_id, id) DO UPDATE SET" in statement


def test_the_upsert_carries_every_scope_slot_and_overwrites_every_non_key_column():
    """A slot the write does not store is a slot no read can filter on, and a column left out of
    the conflict clause gives a replaced row fields from two different writes."""
    statement = sql.upsert_statement("ai", "memory_vectors")
    inserted = statement.split("(", 1)[1].split(")", 1)[0]
    for slot in sql.SCOPE_SLOTS:
        assert slot in inserted, slot
    updates = statement.split("DO UPDATE SET", 1)[1]
    for column in (*sql.COLUMNS, "embedding"):
        if column in ("tenant_id", "id"):
            assert f"{column} = EXCLUDED.{column}" not in updates, column
        else:
            assert f"{column} = EXCLUDED.{column}" in updates, column


def test_json_columns_are_cast_rather_than_left_to_the_driver():
    """psycopg adapts a ``dict`` to ``jsonb`` and asyncpg refuses one, so a value that round-trips
    on the sync half would raise on the async one. Both send JSON text and cast."""
    statement = sql.upsert_statement("ai", "memory_vectors")
    for column in ("topics", "provenance", "metadata"):
        assert f"CAST(:{column} AS jsonb)" in statement, column
    assert "CAST(:embedding AS vector)" in statement


def test_the_schema_creates_a_list_partitioned_table_with_an_hnsw_index():
    """ADR 0003 section A.1, ratified from SM-R3: partition by tenant, one HNSW index per partition.

    The index is created on the parent, so every future partition inherits one -- an index that has
    to be asked for later is the index that is missing in production (Finding 3).
    """
    statements = sql.schema_statements("ai", "memory_vectors", 768, 16, 200, "english")
    joined = "\n".join(statements)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in joined
    assert "PARTITION BY LIST (tenant_id)" in joined
    assert "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200)" in joined
    assert "embedding vector(768) NOT NULL" in joined
    assert "PRIMARY KEY (tenant_id, id)" in joined


def test_the_shared_table_tenant_filter_layout_is_not_what_is_built():
    """The layout SM-R3 rejected on data (recall 0.22 at 50 tenants) is a shared table whose tenant
    lives in a metadata JSON blob. This pins that ``tenant_id`` is a real column and the partition
    key, so a future "simplification" back to the rejected shape fails here rather than in a
    benchmark nobody re-runs."""
    statements = sql.schema_statements("ai", "memory_vectors", 768, 16, 200, "english")
    create = next(s for s in statements if "CREATE TABLE" in s)
    assert "tenant_id TEXT NOT NULL" in create
    assert create.rstrip().endswith("PARTITION BY LIST (tenant_id)")


@pytest.mark.parametrize("dim", [0, -1, 2001, 768.0, True, None])
def test_a_dimension_hnsw_cannot_index_is_refused(dim):
    """pgvector's HNSW covers at most 2000 dimensions. A wider column is searchable only by exact
    scan, which is Finding 3's failure mode arriving through configuration instead of through a
    missing index -- and ``True`` is an ``int`` subclass, so ``vector(True)`` would be ``vector(1)``.
    """
    with pytest.raises(ValueError, match="dim"):
        sql.schema_statements("ai", "memory_vectors", dim, 16, 200, "english")


@pytest.mark.parametrize("m,ef", [(0, 200), (16, 0), (-1, 200), (True, 200)])
def test_non_positive_index_parameters_are_refused(m, ef):
    """These reach DDL as literals, so the range check is what makes the "it is an int" claim true."""
    with pytest.raises(ValueError):
        sql.schema_statements("ai", "memory_vectors", 768, m, ef, "english")


@pytest.mark.parametrize(
    "name",
    ['tab"le', "tab;le", "tab le", "tab\nle", "1table", "", "t" * 64, "tabl'e", "táble"],
)
def test_a_hostile_identifier_is_refused_rather_than_escaped(name):
    """Quoting an arbitrary identifier is possible; letting one through is how a quote, a newline or
    200 bytes end up in DDL that no test covers. These names come from deployment config and are
    always ordinary, so the conservative set costs nothing."""
    with pytest.raises(ValueError):
        sql.identifier(name, "table")


def test_the_partition_bound_is_quoted_by_postgres_not_by_this_module():
    """The one place a tenant-supplied value must appear literally in DDL, and it is composed by
    ``format('%I'/'%L')`` server-side. SM-R3 Finding 5 is the same class caught in the Milvus
    adapter, where filter keys and values were interpolated unescaped."""
    statement = sql.create_partition_statement()
    assert "format(" in statement and "%I" in statement and "%L" in statement
    assert ":tenant_id" in statement


def test_a_partition_name_is_a_safe_identifier_whatever_the_tenant_id_is():
    """A tenant id is tenant-supplied text: it can hold a quote, or be long enough that two tenants
    would collide past Postgres' 63-byte truncation and silently share one partition."""
    hostile = "acme'; DROP TABLE memory_vectors; --"
    name = sql.partition_name("memory_vectors", hostile)
    assert sql.identifier(name, "partition") == name
    long_a, long_b = "t" * 200 + "a", "t" * 200 + "b"
    assert sql.partition_name("memory_vectors", long_a) != sql.partition_name("memory_vectors", long_b)
    assert len(sql.partition_name("memory_vectors", long_a).encode()) <= 63


def test_a_partition_name_is_stable_across_calls():
    """It is derived on every write path, so an unstable name would create a second partition for a
    tenant that already has one -- and split its memories across both."""
    assert sql.partition_name("memory_vectors", "acme") == sql.partition_name("memory_vectors", "acme")
    assert sql.partition_name("memory_vectors", "acme") != sql.partition_name("memory_vectors", "globex")


def test_ef_search_is_a_validated_literal_and_local_to_the_transaction():
    """Postgres rejects a bind parameter in ``SET`` outright -- SM-R3 Finding 4, which is why
    ``PgVector.optimize()`` could never build its index. ``LOCAL`` because a session-level ``SET``
    on a pooled connection re-tunes whatever runs next on it."""
    assert sql.ef_search_statement(64) == "SET LOCAL hnsw.ef_search = 64"


@pytest.mark.parametrize("ef", [0, -1, 1001, "64", 64.0, True, None])
def test_a_non_integer_ef_search_is_refused(ef):
    """It is interpolated, so this range check is the whole of the injection argument."""
    with pytest.raises(ValueError, match="ef_search"):
        sql.ef_search_statement(ef)


def test_the_embedding_is_never_selected_back():
    """A page of 768-float arrays crosses the wire for nothing: ``MemoryRecord`` has no field for
    it, so every read would pay for a value it discards."""
    for statement in row_reading_statements(FULL).values():
        selected = statement.split("FROM")[0]
        assert "embedding" not in selected.replace("embedding <=> CAST(:query_embedding AS vector)", "")


def test_the_slot_list_matches_the_scope_it_filters_on():
    """A sixth slot on ``Scope`` is a sixth predicate here and a sixth column in the DDL.

    Derived automatically it would become a predicate on a column that does not exist; left
    unchecked it becomes a slot nothing filters on, which is the leak the port spells out. So it is
    stated once and compared, and adding a slot fails here until the migration is decided.
    """
    assert sql.SCOPE_SLOTS == tuple(f.name for f in fields(Scope))


# -- the valid-time window (SM-V4 #98) ----------------------------------------


def test_the_window_narrows_inside_the_where_clause_and_not_after_the_limit():
    """SM-V4 decision D1: a window applied after ``LIMIT`` returns a short page, not a filtered one.

    Same argument as ``expired_at IS NULL`` above -- out-of-window rows would consume the whole
    limit, and no over-fetch multiplier is correct.
    """
    statement, _ = sql.search_statement("ai", "memory_vectors", FULL, WINDOW)
    where, order = statement.split("ORDER BY")
    assert "(t_valid IS NULL OR t_valid < :valid_to)" in where
    assert "(t_invalid IS NULL OR t_invalid > :valid_from)" in where
    assert "t_valid" not in order and "t_invalid" not in order


def test_a_search_with_no_window_emits_no_window_clause_and_binds_nothing_for_it():
    """A bind the statement does not name is dead weight, and a clause the caller did not ask for
    narrows a read that should be wide."""
    statement, binds = sql.search_statement("ai", "memory_vectors", FULL, None)
    assert ":valid_from" not in statement and ":valid_to" not in statement
    assert "valid_from" not in binds and "valid_to" not in binds


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (ValidWindow(valid_from=100), {"valid_from"}),
        (ValidWindow(valid_to=900), {"valid_to"}),
        (ValidWindow(valid_from=100, valid_to=900), {"valid_from", "valid_to"}),
        (None, set()),
    ],
)
def test_the_window_predicate_binds_exactly_the_bounds_it_names(window, expected):
    """An open end contributes no predicate, for the same reason an unset scope slot does not:
    ``t_invalid IS NULL`` as a filter would hide every still-valid record."""
    clause, binds = sql.window_predicate(window)
    assert set(binds) == expected
    for bound in expected:
        assert f":{bound}" in clause
    for absent in {"valid_from", "valid_to"} - expected:
        assert f":{absent}" not in clause


def test_an_unset_record_bound_passes_the_window():
    """``NULL`` is open at that end, matching ``ValidWindow.overlaps``. Excluding it instead would
    silently drop every record whose valid time was never stamped."""
    clause, _ = sql.window_predicate(WINDOW)
    assert "t_valid IS NULL OR" in clause
    assert "t_invalid IS NULL OR" in clause


def test_the_search_where_clause_is_exactly_what_search_predicate_builds():
    """SM-V3 (#97) adds a BM25 leg and a fused one to this module. A statement that assembles its
    own ``WHERE`` is filtered correctly the day it is written and one slot short the next time a
    slot is added, so composing is the thing under test -- not the resulting string.
    """
    predicate, predicate_binds = sql.search_predicate(FULL, WINDOW)
    statement, binds = sql.search_statement("ai", "memory_vectors", FULL, WINDOW)
    where = statement.split("WHERE", 1)[1].split("ORDER BY")[0].strip()
    assert where == predicate
    assert binds == predicate_binds


def test_the_search_predicate_carries_scope_tier_and_current_belief_even_with_no_window():
    """The composed predicate is the floor for a ranked read; a window is the only optional part."""
    predicate, _ = sql.search_predicate(FULL, None)
    for slot in sql.SCOPE_SLOTS:
        assert f"{slot} = :{slot}" in predicate, slot
    assert "tier = ANY(CAST(:tiers AS text[]))" in predicate
    assert "expired_at IS NULL" in predicate


def test_scan_takes_no_window_because_an_enumeration_is_not_narrowed():
    """The same asymmetry ``scan`` already has with current belief: a consolidation pass that
    silently skipped out-of-window episodes would report a drained tier it never fully read."""
    from inspect import signature

    assert "window" not in signature(sql.scan_statement).parameters
    statement, _ = sql.scan_statement("ai", "memory_vectors", FULL, 10)
    assert ":valid_from" not in statement and ":valid_to" not in statement


@pytest.mark.parametrize("alias", ["", "m"])
def test_the_composed_predicate_qualifies_every_column_with_the_alias(alias):
    """SM-V3 (#97) joins legs, and a joined statement needs its columns table-qualified.

    ``search_predicate`` forwards ``alias`` to three builders. Forgetting to forward it to one --
    ``window_predicate`` is the newest and the easiest to miss -- emits an unqualified column that
    Postgres rejects as ambiguous only once a second table is in the ``FROM``. There is no such
    statement yet, so nothing else in this suite would notice.
    """
    predicate, _ = sql.search_predicate(FULL, WINDOW, alias)
    prefix = f"{alias}." if alias else ""
    for column in ("tier", "expired_at", "t_valid", "t_invalid", *sql.SCOPE_SLOTS):
        assert f"{prefix}{column}" in predicate, column
    if alias:
        # The qualification has to be on every occurrence, not merely present somewhere: a
        # predicate that qualified the scope slots and left the window bare would pass a check
        # that only asked whether the alias appeared at all.
        for column in ("tier", "expired_at", "t_valid", "t_invalid", *sql.SCOPE_SLOTS):
            bare = predicate.replace(f"{prefix}{column}", "")
            assert column not in bare.replace(f":{column}", ""), column


def test_none_and_an_empty_valid_window_produce_the_same_no_op():
    """Two spellings of "no window", both taking the caller down the same no-clause path -- via
    different branches inside ``window_predicate``, so this is what proves they actually agree
    rather than merely reading as if they should."""
    assert sql.window_predicate(None) == sql.window_predicate(ValidWindow())


def test_the_lexical_leg_where_clause_is_exactly_what_search_predicate_builds():
    """The composition property, on the leg that used to rebuild its predicate by hand.

    Merging SM-V3 and SM-V4 surfaced the exact defect ``search_predicate``'s own docstring warns
    about: ``lexical_search_statement`` built scope + tier + current belief from ``scope_predicate``
    directly, so it never inherited the window and would have silently leaked out-of-window rows
    into a hybrid read through the leg nobody remembered to update. Fixed by routing it through
    ``search_predicate`` like the vector leg; this is the regression test for that fix, not merely
    coverage for the window.
    """
    predicate, predicate_binds = sql.search_predicate(FULL, WINDOW)
    statement, binds = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", WINDOW)
    where = statement.split("WHERE", 1)[1].split("AND to_tsvector")[0].strip().rstrip("AND").strip()
    assert where == predicate
    assert {k: v for k, v in binds.items() if k in predicate_binds} == predicate_binds


def test_the_lexical_leg_narrows_to_the_window_inside_the_where_clause():
    """AC 1's composition proof for the lexical leg, mirroring the vector leg's own test above."""
    statement, _ = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", WINDOW)
    where = statement.split("WHERE", 1)[1]
    assert "(t_valid IS NULL OR t_valid < :valid_to)" in where
    assert "(t_invalid IS NULL OR t_invalid > :valid_from)" in where


def test_a_lexical_search_with_no_window_binds_nothing_for_it():
    statement, binds = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", None)
    assert ":valid_from" not in statement and ":valid_to" not in statement
    assert "valid_from" not in binds and "valid_to" not in binds


# -- the lexical leg (SM-V3 #97) ----------------------------------------------


def test_the_lexical_leg_indexes_and_queries_the_same_expression():
    """A functional index is only used when the query repeats its expression **exactly**.

    Spelled apart, the DDL and the query drift on the first edit and the symptom is not an error:
    the GIN index is silently skipped and every lexical read becomes a sequential scan of the
    partition. So both are built from ``ts_vector_expression`` and this asserts they agree.
    """
    expression = sql.ts_vector_expression("english")
    ddl = [s for s in sql.schema_statements("ai", "memory_vectors", 8, 16, 200, "english") if "gin" in s]
    statement = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", None)[0]

    assert len(ddl) == 1, ddl
    assert expression in ddl[0]
    assert expression in statement


def test_the_lexical_leg_filters_to_current_belief_like_the_vector_leg():
    """Two legs that disagree about which rows exist do not produce a ranking discrepancy, they
    produce a leak: a superseded row the vector leg correctly withholds re-enters the merged
    result through this one."""
    statement = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", None)[0]
    assert "expired_at IS NULL" in statement


def test_the_lexical_leg_filters_on_the_tier_set_and_bounds_the_page():
    """The tier filter and the ``LIMIT`` are the other two eligibility rules the vector leg
    applies; a leg that dropped either would hand fusion a differently-shaped candidate set."""
    statement = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", None)[0]
    assert "tier = ANY(CAST(:tiers AS text[]))" in statement
    assert "LIMIT :limit" in statement


def test_the_lexical_leg_carries_a_match_predicate_so_the_index_can_answer():
    """``@@`` is what makes the GIN index usable at all -- without it Postgres computes
    ``to_tsvector`` per row over the whole partition -- and it removes exactly the rows
    ``ts_rank_cd`` would score 0.0, which are non-matches occupying a page slot."""
    statement = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", None)[0]
    assert "@@" in statement
    assert "ORDER BY rank DESC" in statement


def test_the_query_text_is_a_bind_parameter_and_never_statement_text():
    """Memory query text is whatever a user said to the agent. It reaches the statement as a bind
    on both of its appearances -- the rank expression and the match predicate -- or one of them is
    an injection point that the other's parameterisation hides."""
    statement = sql.lexical_search_statement("ai", "memory_vectors", FULL, "english", None)[0]
    assert statement.count(":query_text") == 2
    # Every `to_tsquery(` is a `websearch_to_tsquery(`. Bare `to_tsquery` raises a syntax error on
    # ordinary punctuation, so one left anywhere in the statement turns a user typing "what's up?"
    # into a failed read rather than an empty one.
    assert statement.count("websearch_to_tsquery(") == 2
    assert statement.count("to_tsquery(") == statement.count("websearch_to_tsquery(")


@pytest.mark.parametrize("bad", ["not a config", "english; DROP TABLE x", "eng'lish", "", "0english", "a" * 64, None])
def test_a_text_search_config_that_could_break_out_of_its_literal_is_refused(bad):
    """The value lands as a **literal** inside ``to_tsvector('...', content)``, and this module
    contains no escaper of its own by design. What makes interpolating it safe is that the
    identifier rules forbid a quote character -- so the rules are the escaping."""
    with pytest.raises(sql.SettingError) as raised:
        sql.text_search_config(bad)
    assert raised.value.field == "text_search_config"


def test_the_text_search_config_probe_resolves_the_way_the_ddl_will():
    """A bare ``pg_ts_config.cfgname`` lookup answers "some schema has one", which is a different
    question from "the DDL and the query will resolve it". They disagree exactly when a config is
    defined outside the connection's ``search_path``."""
    statement, binds = sql.text_search_config_statement("english")
    assert "pg_ts_config_is_visible" in statement
    assert binds == {"ts_config": "english"}
    # Cast, because a bare bind is `unknown` to the planner and it cannot pick an overload for one.
    assert "CAST(:ts_config AS text)" in statement


@pytest.mark.parametrize("builder", ["search_statement", "lexical_search_statement", "scan_statement"])
def test_no_row_reading_statement_can_be_built_without_a_scope(builder):
    """SM-C4 (#92): a forgotten scope must error, never be read as "no predicate". Guarded inside
    ``scope_predicate`` -- which every row-reading statement here is built from -- so a statement
    added later inherits it without anyone remembering to."""
    args = {"search_statement": ("ai", "v"), "lexical_search_statement": ("ai", "v"), "scan_statement": ("ai", "v")}
    extra = {
        "search_statement": (None,),
        "lexical_search_statement": ("english", None),
        "scan_statement": (10,),
    }[builder]
    with pytest.raises(MemoryScopeRequiredError):
        getattr(sql, builder)(*args[builder], None, *extra)


@pytest.mark.parametrize("spelling", ["english", "English", "ENGLISH", "EngLish"])
def test_a_text_search_config_is_normalised_the_way_postgres_normalises_it(spelling):
    """``'English'::regconfig`` resolves to ``english``: the regconfig input function parses its
    argument as an identifier, and an unquoted identifier is down-cased.

    Passing the caller's spelling through broke in two directions at once -- the existence probe
    matches ``pg_ts_config.cfgname`` exactly and **refused a configuration the server accepts**, and
    the index renders ``'english'::regconfig`` while the setting still read ``English``, so the
    drift check would have warned on every clean startup. Normalising once is what makes those two
    sites agree by construction.
    """
    assert sql.text_search_config(spelling) == "english"
    assert sql.text_search_config_statement(spelling)[1] == {"ts_config": "english"}
    assert sql.ts_vector_expression(spelling) == "to_tsvector('english', content)"
