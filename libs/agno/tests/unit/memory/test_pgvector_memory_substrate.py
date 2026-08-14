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
from hashlib import blake2b

import pytest

from egpt.integrations.memory.vector.pgvector import pgvector as module
from egpt.integrations.memory.vector.pgvector import PgVectorMemorySubstrate
from egpt.memory.config import MemoryProviderConfigError
from egpt.memory.substrates import VectorSubstrate
from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryOperationError, MemoryRecord, Scope, Substrate

DIM = 8
URL = "postgresql+psycopg://ai:secret@localhost:5533/ai"
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


def test_a_missing_url_is_refused_at_construction():
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


def test_an_out_of_range_ef_search_is_refused_at_construction():
    """It reaches a statement as a literal, so a bad value is cheapest to catch here."""
    with pytest.raises(ValueError, match="ef_search"):
        PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), ef_search=0)


def test_a_hostile_table_name_is_refused_at_construction():
    with pytest.raises(ValueError, match="table"):
        PgVectorMemorySubstrate(db_url=URL, embedder=StubEmbedder(), table='v"; DROP TABLE x; --')


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


@pytest.mark.parametrize("driver_shape", ["psycopg", "asyncpg"])
def test_both_drivers_json_shapes_decode_to_the_same_record(substrate, driver_shape):
    """psycopg decodes ``jsonb`` to a ``dict``; asyncpg hands back raw text. A substrate that
    handled one shape would return ``metadata`` as a string on the other half -- an asymmetry a test
    comparing one half against itself cannot see."""
    stored = {"source": "chat", "nested": {"a": 1}}
    raw = stored if driver_shape == "psycopg" else '{"source": "chat", "nested": {"a": 1}}'
    topics = ["cats"] if driver_shape == "psycopg" else '["cats"]'
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


def test_close_refuses_while_an_async_engine_is_open(substrate):
    """Dropping the reference is not closing: it leaves the pool's sockets open and permanently
    disables ``aclose``'s own guard, so the leak becomes unreachable rather than fixed."""
    substrate._aengine = object()
    with pytest.raises(MemoryOperationError, match="aclose"):
        substrate.close()
