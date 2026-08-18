"""Boundary validation and row mapping for the pgvector memory substrate (SM-V1 #95).

No database: what is checked here is everything the substrate decides *before* and *after* a round
trip -- what it refuses at construction, and how it maps a row in each direction. The behaviour in
between is in ``tests/integration/memory/pgvector/``, against a real engine.

The construction refusals run without a driver, because every one of them raises before the engine
is built. Anything that needs an engine object is gated: ``create_engine`` imports the DBAPI
immediately, so it fails in the ``unit-drivers-absent`` job where psycopg is deliberately absent.
"""

import asyncio
import math
import re
from hashlib import blake2b
from pathlib import Path

import pytest

from egpt.integrations.memory.vector.pgvector import PgVectorMemorySubstrate, sql
from egpt.integrations.memory.vector.pgvector import pgvector as module
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.substrates import VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import (
    MemoryOperationError,
    MemoryQuery,
    MemoryRecord,
    MemoryScopeRequiredError,
    Scope,
    Substrate,
)

DIM = 8
URL = "postgresql+psycopg://ai:secret@localhost:5532/ai"
ACME = Scope(tenant_id="acme", user_id="ada")


class StubEmbedder:
    """Deterministic, dimension-declaring, and async-capable -- the three things the substrate asks
    of an embedder. Which model to use is SM-V2's (#96), so nothing here stands in for one."""

    dimensions = DIM

    def get_embedding(self, text):
        vector = [0.0] * DIM
        for token in text.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % DIM] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


def record(id_="e1", content="a memory", scope=ACME, **kwargs):
    return MemoryRecord(id=id_, tier=MemoryTier.EPISODIC, content=content, scope=scope, **kwargs)


# -- the port -----------------------------------------------------------------


def test_the_class_carries_every_method_the_port_names():
    """``checked_vector`` verifies this at construction, but only for an instance that exists. A
    method renamed in a refactor would otherwise surface as a provider whose vector leg silently
    fails at the first read."""
    expected = sorted(name for name in vars(VectorSubstrate) if not name.startswith("_"))
    missing = [name for name in expected if not callable(getattr(PgVectorMemorySubstrate, name, None))]
    assert missing == [], missing


# -- construction refusals (no driver needed: each raises before the engine) ---


def test_a_missing_url_is_refused_at_construction(monkeypatch):
    """The env fallback is cleared explicitly, because the assertion is about its *absence*.

    Without this the test passes only on a machine where ``EGPT_MEMORY_PG_URL`` happens to be
    unset -- and it silently stops testing anything on one where it is, which is every machine
    running the integration suite. Found exactly that way.
    """
    monkeypatch.delenv("EGPT_MEMORY_PG_URL", raising=False)
    with pytest.raises(MemoryProviderConfigError, match="db_url"):
        PgVectorMemorySubstrate(db_url=None, embedder=StubEmbedder())


@pytest.mark.parametrize("url", ["postgresql+psycopg2://a:b@h/d", "postgresql+asyncpg://a:b@h/d"])
def test_a_driver_that_cannot_serve_both_halves_is_refused(url):
    """psycopg2 has no async and asyncpg no sync, so either leaves one half of the port
    unimplementable. Refused rather than silently swapped: a deployment that configured a driver
    and got a different one has its pool and SSL settings quietly stop applying."""
    with pytest.raises(MemoryProviderConfigError, match="driver"):
        PgVectorMemorySubstrate(db_url=url, embedder=StubEmbedder())


def test_the_password_survives_normalization():
    """``str(URL)`` renders a password as ``***``. Normalising through a string produced an engine
    that authenticated with three asterisks -- rejected by the real backend, invisible to a double.
    """
    url = PgVectorMemorySubstrate._normalized(URL)
    assert url.password == "secret"
    assert url.drivername == "postgresql+psycopg"
    # And it still masks when rendered, which is why the object is what gets stored.
    assert "secret" not in str(url) and "secret" not in repr(url)


def test_a_plain_postgresql_url_is_accepted_and_pinned_to_the_driver():
    assert PgVectorMemorySubstrate._normalized("postgresql://a:b@h/d").drivername == "postgresql+psycopg"


def test_an_absent_embedder_is_refused():
    """A vector store with no embedder has nothing to index; it would fail at the first write."""
    with pytest.raises(MemoryProviderConfigError, match="embedder"):
        PgVectorMemorySubstrate(db_url=URL, embedder=None)


def test_an_embedder_missing_the_async_half_is_refused():
    """Both halves of this substrate embed. One that only embeds synchronously leaves ``asearch``
    and ``aupsert`` broken -- discovered on the first async read rather than at construction."""

    class SyncOnly:
        dimensions = DIM

        def get_embedding(self, text):
            return [0.0] * DIM

    with pytest.raises(MemoryProviderConfigError, match="async_get_embedding"):
        PgVectorMemorySubstrate(db_url=URL, embedder=SyncOnly())


def test_embedder_options_beside_a_built_instance_is_refused():
    """The options build an embedder from a registry name. Passing both means one of them is inert,
    and which one depends on a precedence rule the caller cannot see."""
    with pytest.raises(MemoryProviderConfigError, match="embedder_options"):
        PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), embedder_options={"id": "x"})


def test_a_dimension_disagreeing_with_the_embedder_is_refused():
    """Caught here rather than by Postgres on the first insert, because by then the table exists at
    the wrong width and every write fails until it is dropped."""
    with pytest.raises(MemoryProviderConfigError, match="dim"):
        PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), dim=DIM + 1)


def test_an_unstated_dimension_is_taken_from_the_embedder():
    assert PgVectorMemorySubstrate._resolved_dim(None, StubEmbedder()) == DIM


def test_an_embedder_declaring_no_dimension_needs_an_explicit_one():
    class NoDim:
        dimensions = None

        def get_embedding(self, text):
            return []

        async def async_get_embedding(self, text):
            return []

    with pytest.raises(MemoryProviderConfigError, match="dim"):
        PgVectorMemorySubstrate._resolved_dim(None, NoDim())


class _WideEmbedder(StubEmbedder):
    """Declares more dimensions than pgvector's HNSW index can cover."""

    dimensions = sql.MAX_HNSW_DIM + 1


@pytest.mark.parametrize(
    "setting, field",
    [
        ({"ef_search": 0}, "ef_search"),
        ({"m": 0}, "m"),
        ({"ef_construction": 0}, "ef_construction"),
        ({"dim": sql.MAX_HNSW_DIM + 1}, "dim"),
        ({"schema": "bad-name"}, "schema"),
        ({"table": 'v"; DROP TABLE x; --'}, "table"),
    ],
    ids=["ef_search", "m", "ef_construction", "dim", "schema", "table"],
)
def test_every_rejected_setting_arrives_as_a_config_error_naming_its_field(setting, field):
    """Asserted on the **narrow** class, not on ``ValueError``, because that is the difference the
    caller sees. ``MemoryProviderConfigError`` is a ``ValueError`` subclass and not the reverse, so
    a test written against the base passes whether or not the wrapping happened -- which is how
    ``ef_search``, ``m``, ``ef_construction`` and the ``dim`` ceiling shipped leaking a bare
    ``ValueError`` while their four siblings were wrapped, with a green suite either way.

    ``field_name`` is asserted too: it is the only thing this class carries that the base does not,
    so a caller narrowing to it is narrowing in order to read this.
    """
    if field == "dim":
        setting = {**setting, "embedder": _WideEmbedder()}
    with pytest.raises(MemoryProviderConfigError) as raised:
        PgVectorMemorySubstrate(db_url=URL, embedder=setting.pop("embedder", StubEmbedder()), **setting)
    assert raised.value.field_name == field


# -- row mapping ---------------------------------------------------------------


@pytest.fixture
def substrate():
    """A substrate that never connects. ``create_engine`` still imports psycopg, hence the gate."""
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    return PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), auto_init_schema=False)


def test_a_record_under_a_foreign_scope_is_refused_by_slot_name_only(substrate):
    """The mismatch has two defensible readings and the caller cannot tell which it got: honour the
    call scope and a per-user fact is filed tenant-wide. The message names the slots and never the
    values -- this fires on a foreign scope by definition, so the values are exactly the identifiers
    that must not reach a log line."""
    foreign = record(scope=Scope(tenant_id="acme", user_id="bob"))
    with pytest.raises(MemoryOperationError) as caught:
        substrate._checked(ACME, [foreign])
    assert "user_id" in str(caught.value)
    assert "bob" not in str(caught.value) and "ada" not in str(caught.value)


def test_the_whole_batch_is_refused_before_anything_is_embedded(substrate):
    """A rejected batch must cost no embedding call: an embedder is a model or a paid API, and a
    partially-embedded refusal charges for work that is then thrown away."""
    calls = []
    substrate.embedder.get_embedding = lambda text: calls.append(text) or [0.0] * DIM
    with pytest.raises(MemoryOperationError):
        substrate.upsert(ACME, [record("e1"), record("e2", scope=Scope(tenant_id="globex"))])
    assert calls == []


def test_a_wrong_width_embedding_is_refused_naming_the_record():
    """Postgres would reject it too, but only after the batch has travelled and with a message
    naming the column rather than the record."""
    with pytest.raises(MemoryOperationError, match="e7"):
        module._vector_literal([0.1, 0.2], 8, "e7")


def test_the_stored_row_carries_every_scope_slot_and_the_bitemporal_envelope(substrate):
    """``expired_at`` and ``superseded_by`` are stored as written, because an upsert is also how a
    projection is retired; a backend that dropped them would serve two current beliefs for one id.
    """
    scope = Scope(tenant_id="acme", agent_id="ag", user_id="ada", team_id="tm", session_id="se")
    row = substrate._row(
        scope,
        record(scope=scope, t_valid=10, t_invalid=20, created_at=5, expired_at=30, superseded_by="e2"),
        [0.0] * DIM,
    )
    assert (row["tenant_id"], row["agent_id"], row["user_id"], row["team_id"], row["session_id"]) == (
        "acme",
        "ag",
        "ada",
        "tm",
        "se",
    )
    assert (row["t_valid"], row["t_invalid"], row["created_at"], row["expired_at"], row["superseded_by"]) == (
        10,
        20,
        5,
        30,
        "e2",
    )


def test_a_record_without_a_created_at_is_stamped(substrate):
    """The column is NOT NULL, and ``created_at`` is the scan order: an unstamped row would either
    be refused by the database or sort arbitrarily."""
    assert substrate._row(ACME, record(), [0.0] * DIM)["created_at"] is not None


@pytest.mark.parametrize("stored_shape", ["decoded", "external_write"])
def test_both_stored_json_shapes_decode_to_the_same_record(substrate, stored_shape):
    """A jsonb column reaches the read path in two shapes, and both must produce one record.

    ``decoded`` is what psycopg hands back for a row this backend wrote. ``external_write`` is raw
    text, which is what a migration or an operator's ``UPDATE`` can leave behind -- **not** an
    asyncpg path, as the first version of this label claimed: ``_normalized`` refuses that driver at
    construction, so no such path exists to test."""
    stored = {"source": "chat", "nested": {"a": 1}}
    raw = stored if stored_shape == "decoded" else '{"source": "chat", "nested": {"a": 1}}'
    topics = ["cats"] if stored_shape == "decoded" else '["cats"]'
    row = {
        "id": "e1",
        "tier": "episodic",
        "content": "a memory",
        "tenant_id": "acme",
        "agent_id": None,
        "user_id": "ada",
        "team_id": None,
        "session_id": None,
        "topics": topics,
        "provenance": raw,
        "metadata": raw,
        "t_valid": None,
        "t_invalid": None,
        "created_at": 1,
        "expired_at": None,
        "superseded_by": None,
        "distance": 0.25,
    }
    got = substrate._record(row)
    assert got.metadata == stored and got.provenance == stored
    assert got.topics == ["cats"]
    assert got.scope == ACME
    assert got.substrate is Substrate.VECTOR


def test_unreadable_json_is_refused_in_this_module_s_envelope(substrate):
    """A row written outside this backend does not round-trip, and the read path was the one place
    left where that surfaced as a bare ``ValueError`` from the stdlib."""
    with pytest.raises(MemoryOperationError, match="metadata"):
        module._from_json("{not json", "metadata", "e1")


def test_a_cosine_distance_becomes_a_similarity_in_the_unit_interval(substrate):
    """The port's contract, and not cosmetic: the provider *adds* the legs' scores, so handing over
    a distance would invert this leg's contribution to the ranking."""
    base = {
        "id": "e1",
        "tier": "episodic",
        "content": "c",
        "tenant_id": "acme",
        "agent_id": None,
        "user_id": "ada",
        "team_id": None,
        "session_id": None,
        "topics": None,
        "provenance": None,
        "metadata": None,
        "t_valid": None,
        "t_invalid": None,
        "created_at": 1,
        "expired_at": None,
        "superseded_by": None,
    }
    assert substrate._record({**base, "distance": 0.0}).score == 1.0
    assert substrate._record({**base, "distance": 1.0}).score == 0.0
    assert 0.0 <= substrate._record({**base, "distance": 0.4}).score <= 1.0
    # `scan` does not rank, and reports no score rather than a fabricated one.
    assert substrate._record({**base, "distance": None}).score is None


def test_the_page_is_ordered_by_distance_then_deterministically(substrate):
    """The server ordered by the bare distance operator so the index could answer the ``ORDER BY``.
    Ties are broken here on ``created_at``, content and id -- in that order, so the result never
    depends on which random id a memory was minted with."""
    rows = [
        {"distance": 0.5},
        {"distance": 0.1},
        {"distance": 0.5},
    ]
    records = [
        record("zzz", "beta", created_at=10),
        record("aaa", "alpha", created_at=99),
        record("mmm", "alpha", created_at=10),
    ]
    ranked = substrate._ranked(rows, records)
    assert [r.id for r in ranked] == ["aaa", "mmm", "zzz"]


def test_ordering_does_not_depend_on_the_ids(substrate):
    """The same corpus with different ids ranks the same way. A UUID in the ordering path makes two
    identical corpora rank differently across processes."""
    rows = [{"distance": 0.5}, {"distance": 0.5}]
    first = substrate._ranked(rows, [record("a", "beta", created_at=2), record("b", "alpha", created_at=1)])
    second = substrate._ranked(rows, [record("y", "beta", created_at=2), record("z", "alpha", created_at=1)])
    assert [r.content for r in first] == [r.content for r in second] == ["alpha", "beta"]


def test_an_empty_query_or_a_zero_limit_costs_no_round_trip(substrate):
    """The nearest neighbours of whatever a model returns for the empty string are noise ranked as
    if they were answers, and ``limit=0`` asks for no rows. Both are answered without asking."""
    from egpt.memory.types import MemoryQuery

    assert substrate._searchable(MemoryQuery(text="  ", limit=5), None) is None
    assert substrate._searchable(MemoryQuery(text="cats", limit=0), None) is None
    assert substrate._searchable(MemoryQuery(text="cats", tiers=[], limit=5), None) is None
    assert substrate._searchable(MemoryQuery(text="cats", limit=5), MemoryTier.SEMANTIC) == ["semantic"]


def test_a_zero_limit_scan_is_zero_rows_not_unlimited(substrate):
    """``None`` means unlimited and ``0`` means empty. Conflating them turns a caller's "take
    nothing this pass" into a full-tier walk."""
    assert substrate._scan_call("ai", "v", ACME, MemoryTier.EPISODIC, 0) is None
    assert substrate._scan_call("ai", "v", ACME, MemoryTier.EPISODIC, None) is not None


def test_close_refuses_without_disposing_either_half(substrate):
    """Dropping the reference is not closing: it leaves the pool's sockets open and permanently
    disables ``aclose``'s own guard, so the leak becomes unreachable rather than fixed.

    The refusal is **all-or-nothing**, which is the half a ``pytest.raises`` alone does not pin: a
    raising method that had disposed the sync pool on its way out would hand a caller who catches
    the error -- catchable, being the same class as any other per-request refusal -- an object whose
    sync half fails on every later call. So the dispose is asserted *not* to have happened, and the
    message is asserted to say so.
    """
    disposed = []
    substrate._aengine = object()
    substrate._engine.dispose = lambda *a, **k: disposed.append("sync")  # type: ignore[method-assign]
    with pytest.raises(MemoryOperationError, match="aclose") as raised:
        substrate.close()
    assert disposed == [], "close() disposed the sync pool on a path that reports releasing nothing"
    assert "released nothing" in str(raised.value)


def test_a_jsonb_array_in_a_mapping_column_is_refused_by_name():
    """psycopg decodes a jsonb *array* to a ``list``, and the first version accepted that beside
    ``dict``: ``dict(["a", "b"])`` raised a bare ``ValueError`` from the middle of a read, outside
    this module's error envelope. Only a row written outside this backend can produce it, which is
    exactly the case the decode path exists for."""
    with pytest.raises(MemoryOperationError, match="did not decode to a JSON object"):
        module._from_json(["a", "b"], "metadata", "e1")


def test_the_default_port_is_the_one_the_setup_script_publishes():
    """The default URL and ``run_pgvector.sh`` are two statements of one fact, and they drifted:
    this suite and the benchmark shipped ``5533`` -- the port of a container started by hand to
    avoid a clash -- while the script publishes ``5532``, so a bare run got `connection refused`.

    Parsed from the script rather than hard-coded, or this test would assert the same constant
    twice and pass however far the two drifted.
    """
    repo = Path(__file__).resolve().parents[5]
    script = (repo / "cookbook" / "scripts" / "run_pgvector.sh").read_text()
    published = re.search(r"-p (\d+):5432", script)
    assert published, "run_pgvector.sh no longer publishes a port in the expected form"
    assert f":{published.group(1)}/" in URL


def _built(table: str) -> PgVectorMemorySubstrate:
    """Construct with a given table name. Never connects, but `create_engine` imports the DBAPI."""
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    return PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), table=table, auto_init_schema=False)


@pytest.mark.parametrize(
    "length, accepted",
    [(sql.MAX_TABLE_BYTES, True), (sql.MAX_TABLE_BYTES + 1, False)],
    ids=["at the budget", "one byte over"],
)
def test_a_table_name_is_held_to_what_its_derived_identifiers_can_spend(length, accepted):
    """``identifier`` accepts any table up to Postgres' 63 bytes, but three identifiers are derived
    from it by suffixing -- two indexes at +5 and a partition at +35 -- so a name in the middle of
    that range is legal itself and produces an illegal partition. Before the guard that surfaced as
    a bare ``ValueError`` at the **first write of each new tenant**, a long way from the setting
    that caused it, and outside this backend's error envelope.

    The boundary is parametrised off ``sql.MAX_TABLE_BYTES`` rather than spelled as 28, so changing
    a suffix moves the test with the code instead of leaving it asserting a stale number.
    """
    table = "m" * length
    if accepted:
        assert _built(table).table == table
    else:
        with pytest.raises(MemoryProviderConfigError, match="63-byte identifier limit"):
            _built(table)


def test_a_ranked_row_without_a_distance_is_refused_rather_than_ranked_first():
    """``_ranked`` mapped a missing distance to ``0.0`` -- a *perfect* cosine match -- so an
    unranked row reaching the relevance filter would have led the page with ``score=None``, which
    the provider then fuses as zero. Unreachable from ``search``, whose statement always returns a
    float; refused by name so a future caller finds the contract instead of the misranking."""
    with pytest.raises(MemoryOperationError, match="carries no distance"):
        module._similar({"id": "e1"})


# -- the entry guard, and the valid-time window (SM-V4 #98) -------------------


#: The port methods that reach stored rows. ``ensure_partition`` takes a tenant id rather than a
#: ``Scope``, so it is not one of these. ``search_candidates`` / ``asearch_candidates`` (SM-V3 #97)
#: joined the list on merge -- they are new data-access methods, and the class this test guards
#: against is exactly "a method added later without the guard", so it must cover them too rather
#: than staying frozen at the set this ticket happened to add.
SCOPED_METHODS = (
    "upsert",
    "aupsert",
    "search",
    "asearch",
    "scan",
    "ascan",
    "search_candidates",
    "asearch_candidates",
)


def _invoke(substrate, name, scope):
    query = MemoryQuery(text="a memory")
    if name.endswith("upsert"):
        args = (scope, [record()])
    elif name.endswith("scan"):
        args = (scope, MemoryTier.EPISODIC)
    else:
        args = (scope, query)
    call = getattr(substrate, name)(*args)
    return asyncio.run(call) if name.startswith("a") else call


@pytest.mark.parametrize("name", SCOPED_METHODS)
@pytest.mark.parametrize("unscoped", [None, "acme", {"tenant_id": "acme"}, object()])
def test_every_scoped_port_method_refuses_an_unscoped_call(substrate, name, unscoped):
    """SM-C4's rule, discharged here rather than left to ``sql.scope_predicate`` (SM-V4 #98, AC 2).

    The port states this as "the first statement of every method", and until now this backend
    relied on the predicate builder alone -- which is enough only while every method routes through
    it. ``CoLocatedMemoryProvider.vector`` is a public attribute, so the seam is not the last line,
    and ``search`` returns ``[]`` before touching SQL for an empty query or a zero limit: on that
    path a bad scope reached no builder at all and the call succeeded.
    """
    with pytest.raises(MemoryScopeRequiredError) as excinfo:
        _invoke(substrate, name, unscoped)
    assert excinfo.value.verb == name


@pytest.mark.parametrize("half", ["search", "asearch"])
def test_the_guard_runs_before_the_short_circuit_that_costs_no_round_trip(substrate, half):
    """The guard must not sit behind an early return.

    ``search`` answers a blank query or a zero limit without touching SQL, so on that path a bad
    scope reaches no predicate builder. A guard placed after the check would let through exactly
    the calls that never see one.
    """
    blank = MemoryQuery(text="   ", limit=0)
    with pytest.raises(MemoryScopeRequiredError):
        call = getattr(substrate, half)(None, blank)
        asyncio.run(call) if half.startswith("a") else call


@pytest.mark.parametrize("name", ["upsert", "aupsert"])
def test_an_empty_batch_is_still_scope_checked(substrate, name):
    """Same shape on the write half: ``upsert`` returns early for an empty batch, and a caller
    passing a bad scope should hear about it then rather than on the first non-empty call."""
    with pytest.raises(MemoryScopeRequiredError):
        call = getattr(substrate, name)(None, [])
        asyncio.run(call) if name.startswith("a") else call


@pytest.mark.parametrize("half", ["search", "asearch"])
def test_both_halves_send_the_window_to_the_same_statement(substrate, half):
    """The async twin must build the same SQL, not merely exist. Captured at the statement layer,
    because the two halves reach the database through different engines."""
    captured = []
    substrate._statements = lambda statements: captured.append(statements) or [[]]

    async def _acapture(statements):
        captured.append(statements)
        return [[]]

    substrate._astatements = _acapture

    query = MemoryQuery(text="a memory", valid_from=400, valid_to=600)
    call = getattr(substrate, half)(ACME, query)
    asyncio.run(call) if half.startswith("a") else call

    statement, binds = captured[0][-1]
    assert "(t_valid IS NULL OR t_valid < :valid_to)" in statement
    assert "(t_invalid IS NULL OR t_invalid > :valid_from)" in statement
    assert binds["valid_from"] == 400 and binds["valid_to"] == 600


def test_an_unwindowed_search_sends_no_window_binds(substrate):
    """A window synthesised from ``now`` would narrow every plain read to the present."""
    captured = []
    substrate._statements = lambda statements: captured.append(statements) or [[]]
    substrate.search(ACME, MemoryQuery(text="a memory"))
    statement, binds = captured[0][-1]
    assert ":valid_from" not in statement and ":valid_to" not in statement
    assert "valid_from" not in binds and "valid_to" not in binds


@pytest.mark.parametrize("half", ["search_candidates", "asearch_candidates"])
def test_both_hybrid_halves_send_the_window_to_the_lexical_leg_too(substrate, half):
    """AC 3, and the regression test for the merge-time gap this ticket found: SM-V3's lexical leg
    built its own predicate rather than composing through ``search_predicate``, so it never
    inherited the window and a windowed hybrid read would have leaked an out-of-window row through
    it. Captured at the statement layer and checked against the **lexical** statement specifically
    -- the vector leg's is already covered by ``test_both_halves_send_the_window_to_the_same_statement``.
    """
    captured = []
    substrate._statements = lambda statements, **kwargs: captured.append(statements) or [[], [], []]

    async def _acapture(statements, **kwargs):
        captured.append(statements)
        return [[], [], []]

    substrate._astatements = _acapture

    query = MemoryQuery(text="a memory", valid_from=400, valid_to=600)
    call = getattr(substrate, half)(ACME, query)
    asyncio.run(call) if half.startswith("a") else call

    # `_statements` receives the bare `(statement, binds)` tuples -- `_candidate_calls` strips the
    # leg tag before the call, so index 2 here is the lexical statement directly, not a tagged pair.
    # Order is [tuning, vector, lexical], per `_candidate_calls`.
    lexical_statement, lexical_binds = captured[0][2]
    assert "(t_valid IS NULL OR t_valid < :valid_to)" in lexical_statement
    assert "(t_invalid IS NULL OR t_invalid > :valid_from)" in lexical_statement
    assert lexical_binds["valid_from"] == 400 and lexical_binds["valid_to"] == 600


# -- the hybrid candidate legs (SM-V3 #97) ------------------------------------


def test_the_class_carries_every_method_the_hybrid_port_names():
    """``HybridSearchSubstrate`` is how SM-H1's (#131) orchestrator will feature-detect two legs
    from one leg, so a method renamed in a refactor would demote this store to vector-only with
    nothing raising."""
    from egpt.memory.substrates import HybridSearchSubstrate

    assert isinstance(PgVectorMemorySubstrate.__new__(PgVectorMemorySubstrate), HybridSearchSubstrate), sorted(
        name for name in vars(HybridSearchSubstrate) if not name.startswith("_")
    )


@pytest.mark.parametrize("rank", [0.0, 0.001, 0.05, 0.1, 1.0, 12.5, 1e6])
def test_a_lexical_rank_becomes_a_similarity_in_the_unit_interval(rank):
    """``ts_rank_cd`` is unbounded and the port requires a similarity in ``[0.0, 1.0]``, higher
    better. A leg handing over its raw measure would dominate any weighted sum it entered."""
    assert 0.0 <= module._lexical_similarity(rank) < 1.0


def test_the_lexical_normalisation_cannot_reorder_its_own_list():
    """**The load-bearing property.** Rank-based fusion reads position and weighted fusion reads
    magnitude, so a non-monotone squash would make the two read different orderings from one list.

    Mutation-checked: replacing ``r / (r + k)`` with anything non-increasing fails here.
    """
    ranks = [0.0, 0.0001, 0.01, 0.1, 0.5, 2.0, 40.0, 1e5]
    scores = [module._lexical_similarity(r) for r in ranks]
    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores), "distinct ranks collapsed to one score"


def test_a_negative_rank_cannot_divide_by_zero_or_score_below_the_floor():
    """``ts_rank_cd`` never returns one, so this is the clamp removing a question rather than
    handling a case: at ``r = -k`` the unclamped form divides by zero."""
    assert module._lexical_similarity(-module._RANK_SATURATION) == 0.0
    assert module._lexical_similarity(-5.0) == 0.0


def test_both_legs_break_ties_the_same_way(substrate):
    """Two legs that order equally-scored records differently hand fusion two orderings for one
    corpus -- and rank-based fusion reads position, so the disagreement becomes a scoring
    difference rather than a cosmetic one."""
    records = [record("a", "beta", created_at=2), record("b", "alpha", created_at=1)]
    by_vector = substrate._ranked([{"distance": 0.5}, {"distance": 0.5}], list(records))
    by_lexical = substrate._lexically_ranked([{"rank": 0.5}, {"rank": 0.5}], list(records))
    assert [r.content for r in by_vector] == [r.content for r in by_lexical] == ["alpha", "beta"]


def test_the_lexical_page_is_ordered_by_rank_descending(substrate):
    """Higher ``ts_rank_cd`` is a better match, the opposite direction from cosine distance. A
    ranker that inherited the vector leg's ascending sort would return the worst matches first."""
    rows = [{"rank": 0.01}, {"rank": 0.9}, {"rank": 0.2}]
    records = [record("a", "weak"), record("b", "strong"), record("c", "middling")]
    assert [r.content for r in substrate._lexically_ranked(rows, records)] == ["strong", "middling", "weak"]


def test_each_leg_scores_from_its_own_measure_and_a_scan_row_scores_nothing(substrate):
    """One of the two is ever selected -- ``distance`` by the vector statement, ``rank`` by the
    lexical one. ``scan`` selects ``distance`` as NULL and must land on ``None``: ``0.0`` would
    read as a *perfect* cosine match and lead the page."""
    assert module._score({"rank": 0.1}) == pytest.approx(0.5)
    assert module._score({"distance": 0.0}) == pytest.approx(1.0)
    assert module._score({"distance": None}) is None
    assert module._score({}) is None


def test_a_lexical_row_maps_to_a_record_scored_on_its_rank(substrate):
    """The end-to-end of the two above: a row off the lexical statement becomes a record carrying
    a unit-interval score and the vector substrate label."""
    mapped = substrate._record(
        {
            "id": "e1",
            "tier": "episodic",
            "content": "shipment ORD-99417-XZ",
            "tenant_id": "acme",
            "agent_id": None,
            "user_id": "ada",
            "team_id": None,
            "session_id": None,
            "topics": None,
            "provenance": None,
            "metadata": None,
            "t_valid": None,
            "t_invalid": None,
            "created_at": 1,
            "expired_at": None,
            "superseded_by": None,
            "rank": 0.1,
        }
    )
    assert mapped.score == pytest.approx(0.5)
    assert mapped.substrate is Substrate.VECTOR


def test_an_unanswerable_query_returns_two_empty_legs_and_costs_no_round_trip(substrate):
    """An empty list is an answer -- "no lexical hits" -- and a fusion stage must be able to tell
    it from a leg that did not run. The short-circuit is shared with ``search``, so the two cannot
    come to disagree about which queries are worth a round trip."""
    from egpt.memory.types import MemoryQuery, RetrievalCandidates

    for query in (MemoryQuery(text="  ", limit=5), MemoryQuery(text="cats", limit=0)):
        candidates = substrate.search_candidates(ACME, query)
        assert candidates == RetrievalCandidates(vector=[], lexical=[])
        assert candidates.legs() == {"vector": [], "lexical": []}


def test_the_legs_are_named_rather_than_positional():
    """SM-R8 requires the fusion implementations to be N-leg-capable rather than hardcoded to two,
    and a consumer reading ``.vector``/``.lexical`` by name is hardcoded to two whatever the fusion
    function can take.

    The assertion is that this store reports **exactly** its two legs -- not that the container
    grows a third. The graph leg (SM-G9 #107) comes from a different substrate and arrives as a
    separate result the orchestrator fuses alongside this one."""
    from egpt.memory.types import RetrievalCandidates

    candidates = RetrievalCandidates(vector=[record("a")], lexical=[record("b")])
    assert list(candidates.legs()) == ["vector", "lexical"]
    assert [r.id for r in candidates.legs()["lexical"]] == ["b"]


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("to_tsvector('english'::regconfig, content)", "english"),
        ("to_tsvector('simple'::regconfig, content)", "simple"),
        ("to_tsvector('english', content)", "english"),
        ("(metadata -> 'tags'::text)", None),
        (None, None),
        ("", None),
    ],
)
def test_the_indexed_text_search_config_is_read_by_name_not_by_rendering(expression, expected):
    """Postgres renders the literal in its own normalized form, so comparing the rendered text
    warned on **every clean startup** -- an always-on warning is one nobody reads when it finally
    means something. A non-text GIN index is not this index and is left alone."""
    assert module._indexed_ts_config(expression) == expected


def test_an_unknown_text_search_config_is_a_setting_fault_not_an_operation_one(substrate):
    """Asserted on the narrow class and its ``field_name``: the ``CREATE INDEX`` this replaces
    reports an ``undefined_object`` from the middle of schema init, arriving as a
    ``MemoryOperationError`` that a caller narrowing to ``MemoryProviderConfigError`` never sees."""
    with pytest.raises(MemoryProviderConfigError) as raised:
        substrate._reject_unknown_text_search_config([{"present": False}])
    assert raised.value.field_name == "text_search_config"

    with pytest.raises(MemoryProviderConfigError):
        substrate._reject_unknown_text_search_config([])
    # The clean case must not raise, or the probe rejects every startup.
    substrate._reject_unknown_text_search_config([{"present": True}])


def test_a_text_search_config_that_could_break_out_of_its_literal_is_refused_at_construction():
    """The construction-time twin of the statement-layer check: it must arrive through
    ``_settings_checked`` as a config error naming its field, like every sibling setting."""
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    with pytest.raises(MemoryProviderConfigError) as raised:
        PgVectorMemorySubstrate(
            db_url=URL, embedder=StubEmbedder(), text_search_config="eng'lish", auto_init_schema=False
        )
    assert raised.value.field_name == "text_search_config"


class _CountingEmbedder(StubEmbedder):
    """Counts embedding calls, so "the guard ran **first**" is observable rather than assumed."""

    def __init__(self):
        self.calls = 0

    def get_embedding(self, text):
        self.calls += 1
        return super().get_embedding(text)

    async def async_get_embedding(self, text):
        self.calls += 1
        return await super().async_get_embedding(text)


@pytest.mark.parametrize(
    "method, args",
    [
        ("search", (MemoryQuery(text="anything", limit=5),)),
        ("asearch", (MemoryQuery(text="anything", limit=5),)),
        ("search_candidates", (MemoryQuery(text="anything", limit=5),)),
        ("asearch_candidates", (MemoryQuery(text="anything", limit=5),)),
        ("upsert", ([record()],)),
        ("aupsert", ([record()],)),
        ("scan", (MemoryTier.EPISODIC,)),
        ("ascan", (MemoryTier.EPISODIC,)),
    ],
)
def test_an_unscoped_call_is_refused_before_the_embedder_is_ever_called(method, args):
    """SM-C4 (#92) says the guard must be **first**, and "first" is the whole content of the rule:
    a refusal that arrives after the embedding round trip has already spent the API call it exists
    to prevent, and on a paid embedder that is the cost the guard was for.

    Asserting only that *something* raises does not test this. The statement layer guards inside
    ``sql.scope_predicate`` as a backstop, so removing the port method's own guard still raises --
    just several frames and one API call later. Mutation-checked: deleting either guard passes a
    raises-only test and fails this one.

    Parametrised over **every** data-access method rather than the two this ticket added, because
    the one without a test is the one that loses its guard.
    """
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    embedder = _CountingEmbedder()
    substrate = PgVectorMemorySubstrate(db_url=URL, embedder=embedder, auto_init_schema=False)

    with pytest.raises(MemoryScopeRequiredError):
        result = getattr(substrate, method)(None, *args)
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    assert embedder.calls == 0, f"{method} embedded before refusing an unscoped call"


def test_each_candidate_statement_is_tagged_with_the_leg_it_answers_for(substrate):
    """The vector page used to be read as ``results[-2]``, counting back from the end.

    That is correct only while ``_search_call`` emits exactly two statements. Its first is
    ``SET LOCAL hnsw.ef_search``, which returns no rows and which ``_rows`` maps to ``[]`` -- so a
    third statement added there would not mis-*rank* the vector leg, it would make it come back
    **empty**, with nothing raising and no way for a caller to tell that from a corpus with no
    matches.
    """
    query = MemoryQuery(text="order status", limit=5)
    calls = substrate._candidate_calls(ACME, query, ["episodic"], [0.0] * DIM)

    assert [leg for leg, _ in calls] == [None, "vector", "lexical"]
    # The untagged one is the tuning statement, which returns no rows -- which is exactly why
    # position is not a safe key for the pages that do.
    assert calls[0][1][0].startswith("SET LOCAL hnsw.ef_search")


def test_a_grown_search_call_fails_loudly_rather_than_emptying_a_leg(substrate, monkeypatch):
    """The guard for the above: adding a statement to ``_search_call`` must break at the line that
    has to change, not silently produce an empty vector list two frames later.

    Mutation-checked: with the old ``results[-2]`` indexing this returned
    ``RetrievalCandidates(vector=[], lexical=[...])`` and raised nothing.
    """
    original = substrate._search_call

    def grown(*args, **kwargs):
        return [("SET LOCAL enable_seqscan = off", {}), *original(*args, **kwargs)]

    monkeypatch.setattr(substrate, "_search_call", grown)
    with pytest.raises(ValueError):
        substrate._candidate_calls(ACME, MemoryQuery(text="order status", limit=5), ["episodic"], [0.0] * DIM)


def test_the_two_legs_are_assembled_from_labels_not_positions(substrate):
    """``_candidates`` is shared by both halves, so the sync and async reads cannot come to disagree
    about which page is which -- the divergence this ticket's own async parity test exists to catch."""

    def row(id_, **measure):
        stored = {
            "id": id_,
            "tier": "episodic",
            "content": id_,
            "tenant_id": "acme",
            "agent_id": None,
            "user_id": "ada",
            "team_id": None,
            "session_id": None,
            "topics": None,
            "provenance": None,
            "metadata": None,
            "t_valid": None,
            "t_invalid": None,
            "created_at": 1,
            "expired_at": None,
            "superseded_by": None,
        }
        return {**stored, **measure}

    # The labels are deliberately **not** in the order `_candidate_calls` emits them, and the
    # untagged tuning statement sits between the two legs rather than in front of them. Counting
    # back from the end (`results[-2]`, `results[-1]`) gets the right answer for the current
    # arrangement and the wrong one here, which is the whole difference between assembling by label
    # and assembling by position. With positional indexing this returns `vector=[]`.
    calls = [("vector", ("...", {})), (None, ("SET LOCAL x = 1", {})), ("lexical", ("...", {}))]
    candidates = substrate._candidates(calls, [[row("v", distance=0.2)], [], [row("l", rank=0.3)]])
    assert [r.id for r in candidates.vector] == ["v"]
    assert [r.id for r in candidates.lexical] == ["l"]


def test_a_lexical_row_without_a_rank_is_refused_rather_than_crashing_on_a_bare_subscript():
    """``_lexically_ranked`` used to read ``pair[0]["rank"]`` directly, a bare ``KeyError`` on a
    missing key rather than the named refusal ``_score`` gives the same field, and ``_similar``
    gives its own sibling field (``distance``) on the vector leg. Aligning the read removes the
    inconsistency; this pins the refusal it now raises."""
    with pytest.raises(MemoryOperationError, match="carries no rank"):
        module._rank_of({"id": "l1"})


def test_the_two_rankers_agree_on_what_no_measure_means(substrate):
    """The property the alignment buys: both rankers refuse a row missing their own field, by the
    same shape of error, rather than one raising ``KeyError`` and the other a named refusal."""
    with pytest.raises(MemoryOperationError):
        substrate._ranked([{"id": "v1"}], [record("v1")])
    with pytest.raises(MemoryOperationError):
        substrate._lexically_ranked([{"id": "l1"}], [record("l1")])
