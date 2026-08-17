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
from egpt.memory.types import MemoryOperationError, MemoryRecord, Scope, Substrate
from egpt.providers.model_provider import ProviderMode

DIM = 8
URL = "postgresql+psycopg://ai:secret@localhost:5532/ai"
ACME = Scope(tenant_id="acme", user_id="ada")


class StubEmbedder:
    """Deterministic, dimension-declaring, and async-capable -- the three things the substrate asks
    of an embedder. Which model to use is SM-V2's (#96), so nothing here stands in for one.

    ``offline_capable`` is the honest answer for an in-process stub that reaches nothing, and it is
    what lets these tests run under the default Mode A after SM-V2 made offline capability a hard
    constraint there (ADR 0003 A.3c). A double that quietly bypassed that check would be modelling
    a deployment the framework refuses."""

    dimensions = DIM
    offline_capable = True

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


def test_an_absent_embedder_takes_the_mode_default(monkeypatch):
    """SM-V1 refused this; SM-V2 (#96) answers it, which is what that refusal said it was waiting for.

    The construction is stubbed rather than run: taking the Mode A default means building
    ``sentence_transformer``, which downloads a model and needs an SDK the drivers-absent job does
    not have. What is asserted is the decision -- *which* embedder the mode selects -- not the SDK's
    ability to load it.
    """
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    from egpt.memory import embedding

    built = {}

    def _fake_construct(key, options):
        built["key"], built["options"] = key, options
        return StubEmbedder()

    monkeypatch.setattr(embedding, "_construct", _fake_construct)
    store = PgVectorMemorySubstrate(db_url=URL, embedder=None, dim=DIM, auto_init_schema=False)
    assert built["key"] == embedding.MODE_DEFAULTS[ProviderMode.LOCAL]["backend"]
    assert built["options"]["id"] == embedding.MODE_DEFAULTS[ProviderMode.LOCAL]["id"]
    assert store.mode is ProviderMode.LOCAL


def test_an_embedder_missing_the_async_half_is_refused():
    """Both halves of this substrate embed. One that only embeds synchronously leaves ``asearch``
    and ``aupsert`` broken -- discovered on the first async read rather than at construction."""

    class SyncOnly:
        dimensions = DIM
        offline_capable = True

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


# -- the embedding pipeline is what the store embeds through (SM-V2 #96) -------


class _BatchStub(StubEmbedder):
    """Declares batching and records each call, so a per-record loop is visible as a call count."""

    accepts_text_batches = True
    batch_size = 100

    def __init__(self):
        self.calls = []

    def get_embedding(self, text):
        texts = [text] if isinstance(text, str) else list(text)
        self.calls.append(list(texts))
        return [StubEmbedder.get_embedding(self, one) for one in texts]

    async def async_get_embedding(self, text):
        await asyncio.sleep(0)
        return self.get_embedding(text)


@pytest.fixture
def batching(monkeypatch):
    """A substrate whose embedder batches and whose round trips are stubbed out.

    Both write verbs and both read verbs are intercepted after the embedding step, so what these
    tests observe is exactly what the store asked the embedder for.
    """
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    store = PgVectorMemorySubstrate(db_url=URL, embedder=_BatchStub(), auto_init_schema=False)
    monkeypatch.setattr(store, "ensure_partition", lambda tenant_id: None)
    monkeypatch.setattr(store, "aensure_partition", _anoop)
    monkeypatch.setattr(store, "_write", lambda statement, rows: None)
    monkeypatch.setattr(store, "_awrite", _anoop2)
    monkeypatch.setattr(store, "_statements", lambda calls: [[]])
    monkeypatch.setattr(store, "_astatements", _aempty)
    return store


async def _anoop(tenant_id):
    await asyncio.sleep(0)


async def _anoop2(statement, rows):
    await asyncio.sleep(0)


async def _aempty(calls):
    await asyncio.sleep(0)
    return [[]]


def test_a_write_embeds_the_batch_in_one_call_not_one_per_record(batching):
    """This ticket's fourth criterion, asserted at the store rather than only at the pipeline: SM-V1
    embedded one record per call and named SM-V2 as the owner of fixing it, so the regression to
    guard is the store bypassing the pipeline it was given.

    Asserted on what the embedder was **asked for**, not on the vectors returned -- those are
    identical either way, so a result-only assertion passes while every memory costs its own call.
    """
    batching.upsert(ACME, [record("e1", "alpha"), record("e2", "beta"), record("e3", "gamma")])
    assert batching.embedder.calls == [["alpha", "beta", "gamma"]]


def test_the_async_write_batches_identically(batching):
    """The divergence to expect: SM-V1's async upsert embedded sequentially *and said so*, so a
    batching sync half beside a per-record async half is the shape this ticket would fail in."""
    asyncio.run(batching.aupsert(ACME, [record("e1", "alpha"), record("e2", "beta"), record("e3", "gamma")]))
    assert batching.embedder.calls == [["alpha", "beta", "gamma"]]


def test_a_query_is_embedded_through_the_pipeline_too(batching):
    """SM-R4 decision 2: write and query must use the same embedder, or the vectors are not
    comparable. Going through the pipeline is also what applies the width check to a query -- a
    query vector of the wrong width otherwise reaches Postgres as a statement error."""
    from egpt.memory.types import MemoryQuery

    batching.search(ACME, MemoryQuery(text="a question", limit=5))
    assert batching.embedder.calls == [["a question"]]


def test_the_async_query_is_embedded_through_the_pipeline_too(batching):
    from egpt.memory.types import MemoryQuery

    asyncio.run(batching.asearch(ACME, MemoryQuery(text="a question", limit=5)))
    assert batching.embedder.calls == [["a question"]]


def test_a_query_vector_of_the_wrong_width_is_refused_before_the_round_trip(batching):
    """The check the raw embedder does not carry. Bypassing the pipeline on the read path leaves a
    wrong-width query vector to fail inside Postgres, naming the column rather than the model."""
    from egpt.memory.types import MemoryQuery

    batching.embedder.get_embedding = lambda text: [[0.0, 0.0]]
    with pytest.raises(MemoryProviderConfigError, match="dimension"):
        batching.search(ACME, MemoryQuery(text="a question", limit=5))


def test_the_column_width_is_the_pipelines_and_is_not_re_derived(batching):
    """One dimension authority. A width restated beside the model that determines it drifts the
    first time the model changes, and the column is then built at a width the embedder cannot fill."""
    assert batching.dim == batching.pipeline.dimensions == DIM


def test_every_row_is_stamped_with_the_pipelines_version_tag(batching):
    """``embedding_version`` is what a re-embedding migration keys its cutover on (SM-R4 section 7,
    D8). Left unstamped it is ``None`` on every row, and a migration cannot tell converted rows from
    unconverted ones."""
    row = batching._row(ACME, record("e1", "alpha"), [0.0] * DIM)
    assert row["embedding_version"] == batching.pipeline.embedding_version
    assert row["embedding_version"].endswith(f"|d{DIM}|cos|v1")


def test_an_explicit_version_tag_still_wins():
    """A deployment mid-migration needs to pin the tag it writes under, so the derived value is a
    default rather than an override."""
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    store = PgVectorMemorySubstrate(
        db_url=URL, embedder=StubEmbedder(), embedding_version="pinned/v0", auto_init_schema=False
    )
    assert store.embedding_version == "pinned/v0"


def test_an_explicitly_empty_version_tag_is_kept_not_replaced_by_the_derived_one():
    """``""`` is a deliberate instruction to stamp nothing, and the only caller who passes it is a
    migration tool that is being precise about this field. A falsy test makes it indistinguishable
    from "not supplied" and silently stamps the current model instead -- so rows the tool asked to
    leave unmarked come back marked, and its cutover query returns the wrong set."""
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    store = PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), embedding_version="", auto_init_schema=False)
    assert store.embedding_version == ""


def test_the_store_defaults_to_mode_A_so_the_air_gap_is_the_default():
    """Defaulting to Mode B would make the A.3c refusal opt-in, which is how an air gap breaks: the
    deployment that forgets to set the mode is exactly the one that needed the check."""
    pytest.importorskip("psycopg", reason="create_engine imports the DBAPI when the engine is built")
    assert (
        PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), auto_init_schema=False).mode is ProviderMode.LOCAL
    )


def test_a_remote_embedder_is_refused_by_the_store_under_mode_A():
    """The carried-forward A.3c item, reached through the store's own constructor -- the path a
    deployment takes. Proved at the pipeline too; asserted here because the store is what a config
    actually builds, and a store that forgot to pass its mode would pass every pipeline test."""
    with pytest.raises(MemoryProviderConfigError, match="A.3c"):
        PgVectorMemorySubstrate(db_url=URL, embedder="huggingface", mode=ProviderMode.LOCAL)
