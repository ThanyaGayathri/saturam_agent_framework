"""Every SQL statement the pgvector memory substrate can emit (SM-V1 #95).

No statement is written at a call site. Tenant isolation here is a **partition**, but the
partition is chosen by a value in the ``WHERE`` clause like any other predicate, so a forgotten
scope column is still a cross-scope read -- and four of the five slots are not partition keys at
all. So every row-reading statement is built by :func:`scope_predicate`, and a unit test asserts
the full predicate over every statement this module can emit rather than the ones a behavioural
test happens to exercise.

Layout is ADR 0003 section A.1, ratified from SM-R3's measurements: ``PARTITION BY LIST
(tenant_id)`` with one partition per tenant, each carrying its own HNSW index. The shared-table
``tenant_id`` filter that ``PgVector`` implements is **rejected on data** -- recall 0.80 -> 0.56 ->
0.22 as tenants go 1 -> 10 -> 50, because it filters a shared HNSW whose traversal rarely lands
enough in-tenant neighbours. Partitioning improves with tenant count (1.000 recall / 13.7 ms at 50
tenants) for the same reason.

The one place a scope value reaches DDL is the partition bound, and it is quoted by **Postgres**
via ``format('%I', ...)`` / ``format('%L', ...)`` rather than by an escaper written here. SM-R3
Finding 5 is the same class caught in the Milvus adapter, where filter keys and values were
interpolated unescaped; hand-rolled quoting is one review round from being right.
"""

from hashlib import md5
from typing import Any, Dict, List, Optional, Tuple

from egpt.memory.types import ScanCursor, Scope, ValidWindow, require_scope

__all__ = [
    "COLUMNS",
    "MAX_HNSW_DIM",
    "MAX_TABLE_BYTES",
    "SCOPE_SLOTS",
    "create_partition_statement",
    "ef_search_statement",
    "identifier",
    "lexical_search_statement",
    "live_schema_statements",
    "partition_name",
    "qualified",
    "table_identifier",
    "scan_statement",
    "schema_statements",
    "scope_predicate",
    "search_predicate",
    "SettingError",
    "search_statement",
    "text_search_config",
    "text_search_config_statement",
    "ts_vector_expression",
    "upsert_statement",
    "window_predicate",
]


class SettingError(ValueError):
    """A deployment setting this module rejects, carrying **which** one.

    A ``ValueError`` subclass so nothing that already catches one changes behaviour, and it names
    its field so the caller does not have to recover that by parsing a message. That is what lets
    ``PgVectorMemorySubstrate`` re-raise every one of these as a ``MemoryProviderConfigError`` with
    a correct ``field_name`` through a single wrapper -- and, more to the point, what makes a
    validator added here later participate in that automatically rather than becoming the one path
    that still leaks a bare ``ValueError``.
    """

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


#: Every scope slot, in the order :meth:`Scope.key` writes them. ``tenant_id`` is the partition
#: key; the other four are ordinary columns. Both are applied the same way on a read, because a
#: backend that honours the tenant and ignores the user serves one user's memory to another inside
#: the same tenant.
SCOPE_SLOTS: Tuple[str, ...] = ("tenant_id", "agent_id", "user_id", "team_id", "session_id")

#: How many bytes a table name may spend, so that **every** identifier derived from it still fits.
#:
#: Three are derived by suffixing: the two parent indexes add ``_hnsw`` / ``_scan``, and a partition
#: adds ``_p_`` plus a 32-character digest. Derived from the longest of those rather than written as
#: a number, because the two would drift the moment a suffix changed -- and the failure is invisible
#: until it lands: ``identifier`` accepts the bare table at 63 bytes, then the *derived* name raises
#: a bare ``ValueError``, for the index names at construction and for a partition at the first write
#: of each new tenant, outside this backend's error envelope either way.
MAX_TABLE_BYTES = 63 - len("_p_") - 32

#: pgvector indexes at most 2000 dimensions with HNSW, against 16000 for the column type. A
#: deployment above that has a ``vector`` column that no HNSW index can cover, so every search is
#: an exact scan -- SM-R3 Finding 3's failure mode arriving through configuration instead.
MAX_HNSW_DIM = 2000

#: The stored columns, in insert order. ``embedding`` is deliberately absent: it is bound as a
#: separate parameter so the vector literal never travels through the same list as the metadata.
COLUMNS: Tuple[str, ...] = (
    "tenant_id",
    "id",
    "tier",
    "content",
    "agent_id",
    "user_id",
    "team_id",
    "session_id",
    "topics",
    "provenance",
    "metadata",
    "embedding_version",
    "t_valid",
    "t_invalid",
    "created_at",
    "expired_at",
    "superseded_by",
)

#: Selected on every read. ``embedding`` is not: a page of 768-float arrays crosses the wire for
#: nothing, since ``MemoryRecord`` has no field to put it in.
_READ_COLUMNS = ", ".join(
    (
        "id",
        "tier",
        "content",
        "tenant_id",
        "agent_id",
        "user_id",
        "team_id",
        "session_id",
        "topics",
        "provenance",
        "metadata",
        "t_valid",
        "t_invalid",
        "created_at",
        "expired_at",
        "superseded_by",
    )
)

#: The query vector, written once so the ``ORDER BY`` and the selected distance cannot drift apart
#: -- ranking by one expression and reporting another is a scoreboard that does not match the game.
_QUERY_VECTOR = "CAST(:query_embedding AS vector)"

#: Transaction time, applied before ranking and before ``LIMIT``. Applying it afterwards would let
#: a page of superseded rows consume the whole limit (``VectorSubstrate.search``).
_CURRENT_BELIEF = "expired_at IS NULL"

#: Casts on the parameters whose Python type does not name a Postgres type on its own.
#:
#: Spelled in the statement because a textual statement carries no type information for its binds,
#: and psycopg will not guess: a bare ``dict`` parameter is met with ``cannot adapt type 'dict'``.
#: It could be wrapped in ``psycopg.types.json.Jsonb``, but that names the driver in every write
#: path, so the value travels as **JSON text** and the cast does the typing. Same reason the vector
#: parameter carries one -- ``pgvector`` registers its SQLAlchemy type on a *mapped column*, and
#: there is no mapped column here.
_CASTS: Dict[str, str] = {
    "topics": "jsonb",
    "provenance": "jsonb",
    "metadata": "jsonb",
}


def cast_param(name: str, type_name: Optional[str] = None) -> str:
    """A bind parameter, cast where its Python type does not name a Postgres one.

    ``CAST(:name AS type)`` rather than ``:name::type``, and not as a style preference: SQLAlchemy's
    ``text()`` does not treat ``:name`` **followed by a colon** as a parameter at all, so the
    postgres-native spelling sends the literal string ``:name::type`` to the server and fails with
    a syntax error. Caught against the real engine.
    """
    if type_name is None:
        return f":{name}"
    return f"CAST(:{name} AS {type_name})"


def identifier(name: str, field: str) -> str:
    """A schema or table name, validated rather than escaped.

    Quoting an arbitrary string is possible; letting one through is how an identifier ends up
    carrying a quote, a newline or 200 bytes into DDL that no test covers. These names come from
    deployment config and are always ordinary, so the conservative set is free.
    """
    if not isinstance(name, str) or not name:
        raise SettingError(f"{field} must be a non-empty string, got {name!r}.", field=field)
    if len(name.encode()) > 63:
        raise SettingError(f"{field} is {len(name.encode())} bytes; Postgres truncates identifiers at 63.", field=field)
    if not name[0].isascii() or not (name[0].isalpha() or name[0] == "_"):
        raise SettingError(f"{field} must start with an ASCII letter or underscore, got {name!r}.", field=field)
    for char in name[1:]:
        if not char.isascii() or not (char.isalnum() or char in "_$"):
            raise SettingError(
                f"{field} may contain ASCII letters, digits, '_' and '$' only, got {name!r}.", field=field
            )
    return name


def table_identifier(name: str, field: str = "table") -> str:
    """The memory table's name, held to the tighter budget its derived identifiers need.

    Separate from :func:`identifier` because the constraint is not the table's own: at 63 bytes it
    is a perfectly legal relation name, and only the names built *from* it overflow.
    """
    validated = identifier(name, field)
    if len(validated.encode()) > MAX_TABLE_BYTES:
        raise SettingError(
            f"table {name!r} is {len(validated.encode())} bytes, above the {MAX_TABLE_BYTES}-byte "
            f"budget: a partition of it is named '<table>_p_<32-char digest>', which would exceed "
            "Postgres' 63-byte identifier limit and fail at the first write for each new tenant.",
            field=field,
        )
    return validated


def qualified(schema: str, table: str) -> str:
    """``schema.table``, both validated by :func:`identifier`."""
    return f"{identifier(schema, 'schema')}.{identifier(table, 'table')}"


def text_search_config(name: str, field: str = "text_search_config") -> str:
    """A Postgres text-search configuration name (``english``, ``simple``, a custom one), **lowered**.

    Held to :func:`identifier`'s rules even though the value lands as a **literal** rather than an
    identifier -- ``to_tsvector('english', content)``. That is deliberate: the rules forbid a quote
    character, which is what makes interpolating the result into a literal safe without an escaper
    of this module's own. Whether the config exists is a database fact, checked at schema init
    (:func:`text_search_config_statement`) rather than guessed at here.

    **Lower-cased because that is what Postgres does with it**, not as a convenience.
    ``'English'::regconfig`` resolves to ``english`` -- the regconfig input function parses its
    argument as an identifier, and an unquoted identifier is down-cased. Passing the caller's
    spelling through unchanged therefore broke in two directions at once: the existence probe
    matched ``pg_ts_config.cfgname`` exactly and **refused a configuration the server accepts**, and
    had it got past that, the index renders ``'english'::regconfig`` while the setting still read
    ``English``, so the drift check would have warned on every clean startup.

    Normalising here rather than at each of those two sites is what makes them agree by
    construction. A configuration that would need quoting to be found is unreachable through this
    API anyway, since :func:`identifier` rejects a quote.
    """
    return identifier(name, field).lower()


def ts_vector_expression(ts_config: str) -> str:
    """The indexed document expression, written **once**.

    A functional index is only used when the query repeats its expression exactly, so the DDL in
    :func:`schema_statements` and the predicate in :func:`lexical_search_statement` must be the same
    text. Spelled apart they drift on the first edit, and the symptom is not an error -- the GIN
    index is silently skipped and every lexical read becomes a sequential scan of the partition.
    """
    return f"to_tsvector('{text_search_config(ts_config)}', content)"


def text_search_config_statement(ts_config: str) -> Tuple[str, Dict[str, Any]]:
    """Ask Postgres whether this configuration resolves, before any DDL uses it.

    ``pg_ts_config_is_visible`` is what makes this match how the name will actually be used: a bare
    ``pg_ts_config.cfgname`` lookup answers "some schema has one", which is a different question
    from "the DDL and the query will resolve it", and the two disagree exactly when a config is
    defined outside the connection's ``search_path``. Postgres has no ``to_regconfig``, so the
    visibility function is the resolution semantics spelled out rather than borrowed.

    Asked as a boolean rather than by casting ``:cfg::regconfig``, which resolves identically but
    **raises** ``undefined_object`` when it fails -- landing in ``_translated`` as a
    ``MemoryOperationError``, which is the shape this probe exists to avoid.

    Probed rather than left to the DDL because the two failures are different kinds: an
    unrecognised config is a **deployment setting** fault that should name the setting, while the
    ``CREATE INDEX`` it would otherwise break reports an ``undefined_object`` from the middle of
    schema init with no mention of which option caused it.
    """
    # Cast to text for the same reason `create_partition_statement` casts every `format()` argument:
    # a bare bind is `unknown` to the planner, which cannot pick an overload for it. Caught against
    # the real engine, not by any double.
    return (
        "SELECT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = "
        f"{cast_param('ts_config', 'text')} AND pg_ts_config_is_visible(oid)) AS present",
        {"ts_config": text_search_config(ts_config)},
    )


def partition_name(table: str, tenant_id: str) -> str:
    """The partition holding ``tenant_id``, named by digest rather than by the tenant itself.

    A tenant id is tenant-supplied text: it can be 200 bytes, hold a quote, or differ from another
    only past Postgres' 63-byte identifier truncation -- which would silently file two tenants in
    one partition. A digest is fixed-width, injection-free by construction, and collision-free at
    this scale. It is **not** a secret: the tenant is stored in plain text in the partition bound
    one line below, and hiding an identifier that the row itself carries would be theatre.
    """
    digest = md5(tenant_id.encode(), usedforsecurity=False).hexdigest()
    return identifier(f"{table}_p_{digest}", "partition")


def scope_predicate(scope: Scope, alias: str = "") -> Tuple[str, Dict[str, Any]]:
    """The conjunctive scope filter and its binds -- the one place scoping is decided.

    ``tenant_id`` always, plus every other slot the scope **sets**; an unset slot means "any" and
    contributes no predicate. Returned as a fragment plus binds rather than as formatted SQL, so a
    slot value can never reach the statement text.

    The SM-C4 (#92) guard runs here as well as at the port methods, and the redundancy is the
    point: the port methods guard *early*, before an embedding call is spent, while this one is
    what a **statement added later** inherits without anyone remembering. Every row-reading
    statement in this module is built from this function -- that is the module docstring's stated
    invariant -- so a guard here cannot be routed around by a new one. ``require_scope`` returns
    the same object and is idempotent, so paying for it twice costs a type check.
    """
    require_scope(scope, method="scope_predicate")
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}tenant_id = :tenant_id"]
    binds: Dict[str, Any] = {"tenant_id": scope.tenant_id}
    for slot in SCOPE_SLOTS[1:]:
        value = getattr(scope, slot)
        if value is not None:
            clauses.append(f"{prefix}{slot} = :{slot}")
            binds[slot] = value
    return " AND ".join(clauses), binds


def window_predicate(window: Optional[ValidWindow], alias: str = "") -> Tuple[str, Dict[str, Any]]:
    """The valid-time overlap filter and its binds -- ``("", {})`` when no window is asked for.

    The SQL half of :meth:`ValidWindow.overlaps`, and it must stay the same rule: a window that
    means one thing in the ``WHERE`` clause and another on the merged set drops a record on one
    path and keeps it on the other, which reads as a flaky search rather than as a bug.
    ``test_valid_window.py`` drives this against the Python form over one case table.

    An unset **record** bound is open at that end, so ``NULL`` passes. Excluding those instead
    would silently drop every record whose valid time was never stamped -- which the write path
    only guarantees for episodes.

    ``None`` and ``ValidWindow()`` (both bounds unset) both return ``("", {})`` -- the first by the
    early return below, the second because the loop appends no clause for either. Contract, not
    coincidence: a caller may treat the two as interchangeable "no window" spellings. What it must
    not do is skip the ``if window_clause:`` guard in :func:`search_predicate` and join this
    result in unconditionally -- ``" AND ".join([scope_clause, ""])`` leaves a dangling ``AND`` with
    nothing after it.
    """
    if window is None:
        return "", {}
    prefix = f"{alias}." if alias else ""
    clauses: List[str] = []
    binds: Dict[str, Any] = {}
    if window.valid_from is not None:
        clauses.append(f"({prefix}t_invalid IS NULL OR {prefix}t_invalid > :valid_from)")
        binds["valid_from"] = window.valid_from
    if window.valid_to is not None:
        clauses.append(f"({prefix}t_valid IS NULL OR {prefix}t_valid < :valid_to)")
        binds["valid_to"] = window.valid_to
    return " AND ".join(clauses), binds


def search_predicate(scope: Scope, window: Optional[ValidWindow], alias: str = "") -> Tuple[str, Dict[str, Any]]:
    """Every predicate a **ranked** read must apply, as one fragment.

    Scope, tier, current belief and the valid-time window, assembled here rather than at each
    statement, because ranked reads are about to stop being one statement: SM-V3 (#97) adds a BM25
    leg and a fused one. A statement that built its own ``WHERE`` would be filtered correctly on
    the day it was written and one slot short the next time a slot was added -- which is how the
    tenant predicate gets left off exactly one of three legs. Composing instead of copying is what
    makes a new leg inherit the filters rather than have to remember them.

    ``:tiers`` and ``:limit`` are bound by the caller: the tier list is resolved from the query and
    the limit is not a predicate.
    """
    scope_clause, binds = scope_predicate(scope, alias)
    prefix = f"{alias}." if alias else ""
    clauses = [scope_clause, f"{prefix}tier = ANY({cast_param('tiers', 'text[]')})", f"{prefix}{_CURRENT_BELIEF}"]
    window_clause, window_binds = window_predicate(window, alias)
    if window_clause:
        clauses.append(window_clause)
        binds.update(window_binds)
    return " AND ".join(clauses), binds


def schema_statements(schema: str, table: str, dim: int, m: int, ef_construction: int, ts_config: str) -> List[str]:
    """Create the extension, the partitioned parent, and the indexes every partition inherits.

    Idempotent, so a repeat costs latency and not correctness.

    **The indexes are created on the parent, not per partition.** A partitioned index is built on
    every existing partition and automatically on every future one, so a tenant created at 3am gets
    its HNSW index without anything remembering to build it. Physically these are still one small
    index per tenant, which is the layout SM-R3 measured.

    Building the index at create time rather than leaving it to a later ``optimize()`` is SM-R3
    Finding 3: ``PgVector.create()`` builds the table only, nothing on the insert or search path
    calls ``optimize()``, so pgvector silently answers every query with an exact sequential scan --
    recall 1.0, latency 134 ms where real HNSW is 15 ms. An index that must be asked for is an
    index that is missing in production.
    """
    if not isinstance(dim, int) or isinstance(dim, bool) or not 0 < dim <= MAX_HNSW_DIM:
        raise SettingError(
            f"dim must be an integer in 1..{MAX_HNSW_DIM}, got {dim!r}. pgvector's HNSW index covers "
            f"at most {MAX_HNSW_DIM} dimensions; a wider column is searchable only by exact scan.",
            field="dim",
        )
    for name, value in (("m", m), ("ef_construction", ef_construction)):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SettingError(f"{name} must be a positive integer, got {value!r}.", field=name)
    parent = qualified(schema, table)
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"CREATE SCHEMA IF NOT EXISTS {identifier(schema, 'schema')}",
        f"""CREATE TABLE IF NOT EXISTS {parent} (
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    tier TEXT NOT NULL,
    content TEXT NOT NULL,
    agent_id TEXT,
    user_id TEXT,
    team_id TEXT,
    session_id TEXT,
    embedding vector({dim}) NOT NULL,
    embedding_version TEXT,
    topics JSONB,
    provenance JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    t_valid BIGINT,
    t_invalid BIGINT,
    created_at BIGINT NOT NULL,
    expired_at BIGINT,
    superseded_by TEXT,
    PRIMARY KEY (tenant_id, id)
) PARTITION BY LIST (tenant_id)""",
        f"CREATE INDEX IF NOT EXISTS {identifier(f'{table}_hnsw', 'index')} ON {parent} "
        f"USING hnsw (embedding vector_cosine_ops) WITH (m = {m}, ef_construction = {ef_construction})",
        # `scan` is ordered by `(created_at, id)` and filtered by tier, and it is the read
        # consolidation drains a tier with -- an unindexed one is a full partition scan per pass.
        f"CREATE INDEX IF NOT EXISTS {identifier(f'{table}_scan', 'index')} ON {parent} (tier, created_at, id)",
        # The lexical leg (SM-V3 #97). Partitioned like the HNSW index above, so a tenant created
        # later inherits it; GIN is what makes `@@` an index lookup rather than a per-row
        # `to_tsvector` over the partition.
        f"CREATE INDEX IF NOT EXISTS {identifier(f'{table}_fts', 'index')} ON {parent} "
        f"USING gin ({ts_vector_expression(ts_config)})",
        # No index on `t_valid` / `t_invalid` (SM-V4 #98), and that is deliberate rather than an
        # oversight this predates. `search_predicate`'s window clause reaches these columns only as
        # a `Filter:` above the HNSW scan, the same place `_CURRENT_BELIEF` already sits -- adding
        # one here is not adding a plain B-tree, either: the table is partitioned, so it would have
        # to be a partitioned index, built and measured like the two above it. Nothing has, because
        # the window predicate has no benchmark yet to say whether the ANN scan's own selectivity
        # already makes it cheap. Revisit in `benchmarks/memory_vectordb/` before adding one.
    ]


def live_schema_statements(schema: str, table: str) -> List[Tuple[str, Dict[str, Any]]]:
    """Read back what the database **actually** holds: the vector width, and the HNSW options.

    ``CREATE TABLE IF NOT EXISTS`` and ``CREATE INDEX IF NOT EXISTS`` are no-ops against an existing
    object -- they do not reconcile it with the DDL just submitted. So a deployment that changes a
    setting and restarts gets a clean, silent startup on the *old* shape. Reading it back is the
    only way to know which one is live.

    The table is addressed through ``to_regclass(:qualified)`` rather than interpolated into a
    ``::regclass`` literal, so the one identifier that reaches this query is still a bind parameter.
    """
    qualified_name = {"qualified": f"{identifier(schema, 'schema')}.{table_identifier(table)}"}
    return [
        (
            "SELECT a.atttypmod AS dim FROM pg_attribute a "
            "WHERE a.attrelid = to_regclass(:qualified) AND a.attname = 'embedding' AND NOT a.attisdropped",
            qualified_name,
        ),
        (
            "SELECT c.relname AS name, c.reloptions AS options FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE i.indrelid = to_regclass(:qualified) "
            "AND c.relam = (SELECT oid FROM pg_am WHERE amname = 'hnsw')",
            qualified_name,
        ),
        # The GIN index's indexed **expression**, because the text-search configuration is baked
        # into it and `CREATE INDEX IF NOT EXISTS` will not rebuild it for a changed setting.
        # Reported rather than refused, matching the HNSW options above and for the same reason: a
        # stale config still answers *correctly* -- `to_tsvector` is evaluated at query time either
        # way -- it just stops matching the index expression, so every lexical read silently drops
        # to a sequential scan.
        #
        # `pg_get_expr` rather than `pg_get_indexdef` so the answer is the expression alone, with no
        # index or table name around it for a config name to be mistaken for. Postgres renders the
        # literal in its own normalized form -- `'english'::regconfig`, not the `'english'` this
        # module wrote -- which is why the caller parses the config *name* out rather than comparing
        # the rendered text: a substring comparison warns on every clean startup.
        (
            "SELECT c.relname AS name, pg_get_expr(i.indexprs, i.indrelid) AS expression FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE i.indrelid = to_regclass(:qualified) "
            "AND c.relam = (SELECT oid FROM pg_am WHERE amname = 'gin')",
            qualified_name,
        ),
    ]


def create_partition_statement() -> str:
    """A statement returning the DDL that creates one tenant's partition.

    Two steps -- ask Postgres to compose the DDL, then run what it returns -- because DDL takes no
    bind parameters and the partition bound is the one place a tenant-supplied value must appear
    literally. ``format('%I'/'%L')`` is Postgres' own identifier and literal quoting, so this
    module contains no escaper of its own to get wrong.
    """
    # Every parameter is cast to text. `format()` is variadic `any`, so Postgres cannot infer a
    # parameter's type from it and refuses the statement outright with "could not determine data
    # type of parameter $1" -- caught against the real engine, not by any double.
    return (
        "SELECT format('CREATE TABLE IF NOT EXISTS %I.%I PARTITION OF %I.%I FOR VALUES IN (%L)', "
        + ", ".join(cast_param(name, "text") for name in ("schema", "partition", "schema", "parent", "tenant_id"))
        + ")"
    )


def upsert_statement(schema: str, table: str) -> str:
    """Insert or replace by ``(tenant_id, id)`` -- the primary key, and the partition key leads it.

    Upsert rather than insert because ``id`` is the cross-substrate dedup key: a ``PartialWriteError``
    retry reuses the stamped ids precisely so that replaying it is idempotent, and an appending
    backend would turn that retry into duplicate rows the graph half does not have.

    Every column is overwritten, ``created_at`` included: a replace is a new projection of the row,
    not a partial patch, and leaving one column behind gives a record whose fields come from two
    different writes.
    """
    columns = ", ".join((*COLUMNS, "embedding"))
    placeholders = ", ".join((*(cast_param(c, _CASTS.get(c)) for c in COLUMNS), cast_param("embedding", "vector")))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in (*COLUMNS, "embedding") if c not in ("tenant_id", "id"))
    return (
        f"INSERT INTO {qualified(schema, table)} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT (tenant_id, id) DO UPDATE SET {updates}"
    )


def search_statement(
    schema: str, table: str, scope: Scope, window: Optional[ValidWindow]
) -> Tuple[str, Dict[str, Any]]:
    """Rank by cosine distance, scoped, current-belief, tier-filtered, limited server-side.

    ``ORDER BY`` is the bare distance operator with no secondary key. Adding a tiebreak column
    would force a sort node above the index scan and can drop the plan to a sequential scan, which
    is the exact failure SM-R3 Finding 3 describes; deterministic ordering is applied to the
    returned page instead, where it costs nothing.

    **The zero-similarity cut is not in this statement**, and that is measured rather than assumed.
    Written as ``AND embedding <=> :q < 1.0`` it lands in the plan's ``Filter:``, so the distance is
    computed again for every row the index scan *visits*, not for the few it returns: 5.23 ms p50
    against 3.15 ms without it, on the 100k/tenant partition. Because the scan is ordered by that
    same distance, every row the cut would remove ranks strictly below every row it keeps -- so
    applying it to the returned page instead yields the identical set, and ``_ranked`` does that.
    The distance still appears twice, in the select list and the ``ORDER BY``; only the second is
    per-visited-row, and it is what the index answers with.

    ``as_of`` is **not** applied here and no parameter carries it: valid time is enforced once, on
    the merged set, because it needs the graph's bounds as well (finding F2).

    ``window`` **is** applied here, and the two are not in tension. ``as_of`` asks which of two
    revisions the graph currently believes, so only the graph can answer it; a window asks which
    rows are candidates, which every row answers about itself. Deferring it to the merged set would
    spend the whole ``LIMIT`` on out-of-window rows -- the recall loss SM-V4 decision D1 rejects,
    and the same shape as the superseded-page argument for ``_CURRENT_BELIEF`` above. The provider
    re-applies it after reconciliation, because these bounds are a projection and may be stale.
    """
    predicate, binds = search_predicate(scope, window)
    statement = (
        f"SELECT {_READ_COLUMNS}, embedding <=> {_QUERY_VECTOR} AS distance "
        f"FROM {qualified(schema, table)} "
        f"WHERE {predicate} "
        f"ORDER BY embedding <=> {_QUERY_VECTOR} "
        "LIMIT :limit"
    )
    return statement, binds


def lexical_search_statement(
    schema: str, table: str, scope: Scope, ts_config: str, window: Optional[ValidWindow]
) -> Tuple[str, Dict[str, Any]]:
    """Rank by full-text relevance -- the lexical leg SM-H2's fusion consumes (SM-V3 #97).

    **Composed from** :func:`search_predicate`, **not rebuilt** -- scope, tier, current belief and
    the valid-time window (SM-V4 #98), identically to :func:`search_statement`. That function's own
    docstring names the failure this averts: a leg that built its own ``WHERE`` was filtered
    correctly the day it was written and one slot short the next time a slot was added. This leg
    *was* that slot, merged in without the window it should have inherited -- caught only because
    :func:`test_the_lexical_leg_inherits_the_search_predicate_exactly` compares the two statements'
    ``WHERE`` text rather than sampling a behaviour. Two retrieval legs that disagree about which
    rows exist do not produce a fusion problem, they produce a leak: a row the vector leg correctly
    excludes -- by tenant, by tier, by validity, or now by window -- would re-enter the merged result
    through this one.

    ``websearch_to_tsquery`` rather than ``to_tsquery``: the query text is whatever a user said to
    the agent, and ``to_tsquery`` raises a syntax error on ordinary punctuation. ``websearch_``
    never raises -- an unparseable query yields an empty tsquery, which matches nothing, which is
    the honest answer.

    **The match predicate is in the ``WHERE`` clause here, and that is the opposite of the vector
    leg's decision** -- see :func:`search_statement`, where the zero-similarity cut is deliberately
    kept out of it. The reason they differ: ``@@`` is what makes the GIN index usable at all, so
    here the predicate *is* the index lookup rather than a second computation layered over one. It
    also removes exactly the rows ``ts_rank_cd`` would score 0.0, which are non-matches occupying a
    page slot.

    ``ts_rank_cd`` is a coverage-density rank, **not Okapi BM25** -- Postgres has no native BM25.
    It ranks lexically, which is what the leg is for, but SM-R8 calibrated
    ``vector_weight ~ 0.4-0.5`` against a real ``rank_bm25`` leg, so that weight does not transfer
    unexamined. Recalibrating it is SM-H2's (#132), which owns the fusion.
    """
    predicate, binds = search_predicate(scope, window)
    document = ts_vector_expression(ts_config)
    query = f"websearch_to_tsquery('{text_search_config(ts_config)}', :query_text)"
    statement = (
        f"SELECT {_READ_COLUMNS}, ts_rank_cd({document}, {query}) AS rank "
        f"FROM {qualified(schema, table)} "
        f"WHERE {predicate} "
        f"AND {document} @@ {query} "
        f"ORDER BY rank DESC "
        "LIMIT :limit"
    )
    return statement, binds


def scan_statement(
    schema: str, table: str, scope: Scope, limit: Optional[int], after: Optional[ScanCursor] = None
) -> Tuple[str, Dict[str, Any]]:
    """Enumerate one tier in insertion order -- oldest first, no ranking.

    ``limit`` is a bound and not a page: ``None`` emits no ``LIMIT`` clause at all rather than a
    large number standing in for "everything". ``(created_at, id)`` is a total order, so a pass
    processes the same rows in the same sequence on every run.

    **No current-belief filter, unlike** :func:`search_statement`. The port states that predicate
    for ranked reads only, and this is an enumeration: a substrate that quietly narrowed it would
    let a consolidation pass report a drained tier while never seeing part of it. The caller
    decides what a retired row means to it.

    ``after`` resumes the walk: a Postgres row comparison against the same ``(created_at, id)``
    ordering, exclusive, so it names the two bind values rather than the tuple -- ``:after_id`` is
    ``TEXT`` and ``:after_created_at`` is ``BIGINT``, and passing the tuple as one bind would ask
    the driver to infer a composite type it does not have.

    **Relies on ``created_at`` never being ``NULL``.** A row comparison against a ``NULL`` column
    evaluates to unknown, which ``WHERE`` treats as false regardless of ``id`` -- a ``NULL`` row
    would be excluded by the cursor forever, the same silent-loss failure mode as a backdated
    write (``CoLocatedMemoryProvider._advance_cursor``'s docstring). The DDL's ``created_at
    BIGINT NOT NULL`` (this module's ``create_partition_statement``) is what makes that
    unreachable through this port -- an in-memory double that normalises ``None`` to ``0``
    (``FakeVector.scan``) is *more* permissive than the real backend here, not less, so a
    provider test passing against the double is not evidence this predicate handles ``NULL``.
    """
    predicate, binds = scope_predicate(scope)
    statement = (
        f"SELECT {_READ_COLUMNS}, NULL::float8 AS distance "
        f"FROM {qualified(schema, table)} "
        f"WHERE {predicate} AND tier = :tier"
    )
    if after is not None:
        statement += " AND (created_at, id) > (:after_created_at, :after_id)"
        binds["after_created_at"], binds["after_id"] = after
    statement += " ORDER BY created_at, id"
    if limit is not None:
        statement += " LIMIT :limit"
        binds["limit"] = limit
    return statement, binds


def get_record_statement(schema: str, table: str, scope: Scope, id: str) -> Tuple[str, Dict[str, Any]]:
    """A single row by id, within ``scope`` -- the real point lookup ``VectorSubstrate.get_record``
    promises (aicoe-tracker #198), not ``scan`` filtered client-side.

    Built from :func:`scope_predicate` like every other row-reading statement here, plus ``id``
    as an ordinary bound predicate -- never interpolated, for the same injection reason every
    other scope value travels as a bind. No ``LIMIT``/``ORDER BY``: ``id`` is the dedup key
    :func:`upsert_statement` replaces by, unique per tenant partition, so at most one row can
    match.

    **No current-belief filter, matching** :func:`scan_statement`, **not** :func:`search_statement`.
    This is a lookup by identity, not a ranked read -- the port states the current-belief predicate
    for ranked reads only, and a caller that already knows the id it wants (e.g. a profile row
    :meth:`~egpt.memory.semantic.profile.SemanticProfileStore.get` reads back by its deterministic
    id) is entitled to see the row it wrote, expired or not, the same way ``scan`` returns it.
    """
    predicate, binds = scope_predicate(scope)
    statement = (
        f"SELECT {_READ_COLUMNS}, NULL::float8 AS distance "
        f"FROM {qualified(schema, table)} "
        f"WHERE {predicate} AND id = :id"
    )
    binds["id"] = id
    return statement, binds


def ef_search_statement(ef_search: int) -> str:
    """``SET LOCAL hnsw.ef_search``, interpolated as a validated literal.

    Not a bind parameter, and not by preference: Postgres rejects one in ``SET`` outright, which is
    SM-R3 Finding 4 -- ``PgVector.optimize()`` runs ``SET maintenance_work_mem = %(value)s`` and
    raises ``syntax error at or near "$1"``, so the one code path that would have built the HNSW
    index could never run. An ``int`` cannot carry an injection, and the range check is what makes
    the ``int`` claim true rather than assumed.

    ``LOCAL`` so it lasts the transaction: a session-level ``SET`` on a pooled connection silently
    re-tunes whatever runs next on it.
    """
    if not isinstance(ef_search, int) or isinstance(ef_search, bool) or not 1 <= ef_search <= 1000:
        raise SettingError(f"ef_search must be an integer in 1..1000, got {ef_search!r}.", field="ef_search")
    return f"SET LOCAL hnsw.ef_search = {ef_search}"
