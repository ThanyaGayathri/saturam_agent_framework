"""The pgvector memory substrate against a real Postgres (SM-V1 #95).

Gated on ``EGPT_INTEGRATION_PGVECTOR``; start the engine with ``cookbook/scripts/run_pgvector.sh``.

**This is the half a double cannot supply.** SM-C1 recorded that anything built there could only be
exercised against stand-ins written in the same change; the defects this file caught on the way in
are the argument -- a password masked to ``***`` by ``str(URL)``, ``format()`` refusing a parameter
whose type it cannot infer, and SQLAlchemy declining to bind ``:name::type`` at all. None of the
three is expressible against a double.

Several properties run against **both** the real backend and the reference double, because the
double is the specification every provider test is written against: if the two disagree, one of them
is wrong and every provider test resting on the double is resting on the disagreement.
"""

import asyncio
import math
import os
from hashlib import blake2b

import pytest

pytest.importorskip("psycopg", reason="the pgvector memory substrate connects through psycopg 3")

from sqlalchemy import text  # noqa: E402

from egpt.integrations.memory.vector.pgvector import PgVectorMemorySubstrate, sql  # noqa: E402
from egpt.memory.substrates import VectorSubstrate  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MemoryOperationError, MemoryQuery, MemoryRecord, Scope, Substrate  # noqa: E402
from tests.unit.memory._fakes import FakeVector  # noqa: E402

DIM = 64
TABLE = "test_memory_vectors"

ACME = Scope(tenant_id="acme", user_id="ada")
ACME_BOB = Scope(tenant_id="acme", user_id="bob")
GLOBEX = Scope(tenant_id="globex", user_id="ada")


class TokenEmbedder:
    """A deterministic bag-of-tokens projection, unit-normalised.

    Not a stand-in for a model -- which one to use is SM-V2's (#96). It is here because the store is
    what is under test and a real model would make these properties depend on its weights: shared
    tokens give a genuinely smaller cosine distance, which is all the ranking assertions need.
    """

    dimensions = DIM

    def get_embedding(self, text_):
        vector = [0.0] * DIM
        for token in text_.lower().split():
            vector[int.from_bytes(blake2b(token.encode(), digest_size=4).digest(), "big") % DIM] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def async_get_embedding(self, text_):
        # A real suspension point: without one the async paths are exercised as thinly disguised
        # sync ones, which is how an async-only divergence survives a green suite.
        await asyncio.sleep(0)
        return self.get_embedding(text_)


def _url():
    return os.getenv("EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5533/ai")


def _fresh():
    substrate = PgVectorMemorySubstrate(db_url=_url(), embedder=TokenEmbedder(), table=TABLE, auto_init_schema=False)
    with substrate._engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS ai.{TABLE} CASCADE"))
    substrate.init_schema()
    return substrate


@pytest.fixture
def store():
    """A substrate for the sync tests. Teardown is sync, so nothing here may open the async half --
    ``close()`` refuses while an async engine is live, and that refusal would mask a real failure."""
    substrate = _fresh()
    yield substrate
    substrate.close()


@pytest.fixture
async def async_store():
    """The same substrate for tests that drive the async half, with teardown that can await."""
    substrate = _fresh()
    yield substrate
    await substrate.aclose()


@pytest.fixture(params=["pgvector", "double"])
def any_store(request, store):
    """The real backend and the reference double, behind one name.

    A property parametrised over both is what keeps the double honest: a double more permissive
    than the backend cannot catch a provider bug that only appears when the backend is strict.
    """
    return store if request.param == "pgvector" else FakeVector()


def record(id_, content, scope=ACME, tier=MemoryTier.EPISODIC, **kwargs):
    return MemoryRecord(id=id_, tier=tier, content=content, scope=scope, **kwargs)


# -- acceptance criterion 1: insert and search, with HNSW ---------------------


def test_the_hnsw_index_exists_on_every_partition_including_later_ones(store):
    """SM-R3 Finding 3: ``PgVector.create()`` builds the table and no ANN index, and nothing on the
    insert or search path calls ``optimize()`` -- so pgvector answers every query with an exact
    sequential scan, which is why the v1 benchmark reported recall 1.0 at 134 ms.

    Asserted on the *index*, not on a query plan: at test scale a planner will rightly prefer the
    primary key, so a plan assertion would pin the planner's cost model rather than this ticket's
    decision. The decision is that an index exists without anyone asking for one -- including on a
    tenant whose partition is created long after the parent index was.
    """
    store.upsert(ACME, [record("e1", "first tenant")])
    store.upsert(GLOBEX, [record("e2", "second tenant", scope=GLOBEX)])
    with store._engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname AS partition, am.amname FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_index x ON x.indrelid = c.oid "
                "JOIN pg_class ic ON ic.oid = x.indexrelid "
                "JOIN pg_am am ON am.oid = ic.relam "
                f"WHERE i.inhparent = 'ai.{TABLE}'::regclass"
            )
        ).all()
    by_partition = {}
    for partition, amname in rows:
        by_partition.setdefault(partition, set()).add(amname)
    assert len(by_partition) == 2, by_partition
    for partition, methods in by_partition.items():
        assert "hnsw" in methods, (partition, methods)


def test_the_table_is_list_partitioned_by_tenant(store):
    """ADR 0003 section A.1, ratified: the shared-table ``tenant_id`` filter is rejected on data
    (recall 0.22 at 50 tenants). This asserts the layout is what was ratified, in the database
    rather than in the DDL string."""
    with store._engine.begin() as connection:
        strategy = connection.execute(
            text(f"SELECT partstrat FROM pg_partitioned_table WHERE partrelid = 'ai.{TABLE}'::regclass")
        ).scalar()
        key = connection.execute(
            text(
                "SELECT a.attname FROM pg_partitioned_table p "
                # `partattrs` is an int2vector, which subscripts from 0 -- `[1]` reads the second
                # partition column of a table that has one, and returns NULL rather than failing.
                "JOIN pg_attribute a ON a.attrelid = p.partrelid AND a.attnum = p.partattrs[0] "
                f"WHERE p.partrelid = 'ai.{TABLE}'::regclass"
            )
        ).scalar()
    assert strategy == "l"
    assert key == "tenant_id"


def test_a_search_ranks_the_closer_memory_first(any_store):
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat"), record("e2", "the cat sat nearby")])
    hits = any_store.search(ACME, MemoryQuery(text="cat sat mat", limit=5))
    assert [hit.id for hit in hits][0] == "e1", hits


def test_every_score_is_a_similarity_in_the_unit_interval(any_store):
    """The provider adds the legs' scores, so a distance-valued leg inverts the ranking."""
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat"), record("e2", "quantum tunnelling")])
    hits = any_store.search(ACME, MemoryQuery(text="cat mat", limit=5))
    assert hits
    for hit in hits:
        assert hit.score is not None and 0.0 <= hit.score <= 1.0, hit
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


def test_an_upsert_replaces_by_id_rather_than_appending(any_store):
    """Upsert by id is what makes a ``PartialWriteError`` retry idempotent."""
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat")])
    any_store.upsert(ACME, [record("e1", "the cat sat on the rug")])
    rows = any_store.scan(ACME, MemoryTier.EPISODIC)
    assert [row.id for row in rows] == ["e1"]
    assert rows[0].content == "the cat sat on the rug"


def test_the_limit_is_applied_by_the_backend(any_store):
    any_store.upsert(ACME, [record(f"e{index}", f"cat number {index}") for index in range(5)])
    assert len(any_store.search(ACME, MemoryQuery(text="cat", limit=2))) == 2


def test_the_substrate_satisfies_the_port(store):
    assert isinstance(store, VectorSubstrate)


# -- acceptance criterion 2: tenant and metadata on every vector --------------


def test_a_read_cannot_reach_another_tenant(any_store):
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat")])
    any_store.upsert(GLOBEX, [record("e2", "the cat sat on the mat", scope=GLOBEX)])
    hits = any_store.search(GLOBEX, MemoryQuery(text="cat mat", limit=10))
    assert [hit.id for hit in hits] == ["e2"]
    assert all(hit.scope.tenant_id == "globex" for hit in hits)


def test_a_read_cannot_reach_another_user_inside_one_tenant(any_store):
    """The failure the port spells out slot by slot: a backend that honours the tenant and ignores
    the user serves one user's memory to another inside the same tenant."""
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat")])
    any_store.upsert(ACME_BOB, [record("e2", "the cat sat on the mat", scope=ACME_BOB)])
    assert [hit.id for hit in any_store.search(ACME, MemoryQuery(text="cat mat", limit=10))] == ["e1"]
    assert [hit.id for hit in any_store.search(ACME_BOB, MemoryQuery(text="cat mat", limit=10))] == ["e2"]


def test_an_unset_slot_widens_rather_than_narrowing(any_store):
    """``None`` means "any". A backend reading it as ``IS NULL`` returns an empty page that looks
    like an empty corpus."""
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat")])
    any_store.upsert(ACME_BOB, [record("e2", "the cat sat on the mat", scope=ACME_BOB)])
    hits = any_store.search(Scope(tenant_id="acme"), MemoryQuery(text="cat mat", limit=10))
    assert sorted(hit.id for hit in hits) == ["e1", "e2"]


def test_metadata_and_provenance_round_trip_unchanged(any_store):
    """AC2's "metadata stored on vectors": nested values included, since a graph property could not
    hold one and this column can."""
    stored = {"source": "chat", "nested": {"turn": 3, "tags": ["a", "b"]}}
    any_store.upsert(
        ACME,
        [record("e1", "the cat sat on the mat", metadata=stored, provenance={"source_episode_ids": ["x"]})],
    )
    got = any_store.scan(ACME, MemoryTier.EPISODIC)[0]
    assert got.metadata == stored
    assert got.provenance == {"source_episode_ids": ["x"]}


def test_topics_distinguish_absent_from_empty(any_store):
    any_store.upsert(ACME, [record("e1", "one", topics=None), record("e2", "two", topics=[])])
    got = {row.id: row.topics for row in any_store.scan(ACME, MemoryTier.EPISODIC)}
    assert got == {"e1": None, "e2": []}


def test_a_record_carrying_a_foreign_scope_is_refused(any_store):
    with pytest.raises((MemoryOperationError, ValueError)):
        any_store.upsert(ACME, [record("e1", "one", scope=GLOBEX)])


def test_two_tenants_land_in_two_partitions(store):
    store.upsert(ACME, [record("e1", "one")])
    store.upsert(GLOBEX, [record("e2", "two", scope=GLOBEX)])
    with store._engine.begin() as connection:
        for scope, expected in ((ACME, "e1"), (GLOBEX, "e2")):
            partition = sql.partition_name(TABLE, scope.tenant_id)
            ids = [row[0] for row in connection.execute(text(f"SELECT id FROM ai.{partition}"))]
            assert ids == [expected], (partition, ids)


def test_a_repeated_partition_creation_is_not_an_error(store):
    """Two workers writing a new tenant's first memories both create it; the loser sees
    ``DuplicateTable`` even with ``IF NOT EXISTS``. The partition exists either way."""
    store.upsert(ACME, [record("e1", "one")])
    store._partitions.clear()  # as a second process would see it
    store.upsert(ACME, [record("e2", "two")])
    assert sorted(row.id for row in store.scan(ACME, MemoryTier.EPISODIC)) == ["e1", "e2"]


def test_a_tenant_id_that_would_break_a_hand_rolled_quoter_is_stored_and_read_back(store):
    """The partition bound is the one place a tenant value appears literally in DDL, and it is
    quoted by Postgres' own ``format('%L')``. SM-R3 Finding 5 is this class caught in the Milvus
    adapter, where filter keys and values were interpolated unescaped."""
    hostile = Scope(tenant_id="ac'me\"; DROP TABLE ai." + TABLE + "; --", user_id="ada")
    store.upsert(hostile, [record("e1", "the cat sat on the mat", scope=hostile)])
    assert [hit.id for hit in store.search(hostile, MemoryQuery(text="cat mat", limit=5))] == ["e1"]
    assert store.scan(ACME, MemoryTier.EPISODIC) == []


# -- acceptance criterion 3: ranked candidates for fusion ---------------------


def test_search_returns_current_belief_only_and_filters_before_the_limit(any_store):
    """Transaction time, applied in the ``WHERE`` clause. Applying ``limit`` first would let a page
    of superseded rows consume the whole limit and return a short page of nothing."""
    any_store.upsert(
        ACME,
        [
            record("e1", "the cat sat on the mat", expired_at=50, superseded_by="e2"),
            record("e2", "the cat sat on the mat"),
        ],
    )
    hits = any_store.search(ACME, MemoryQuery(text="cat mat", limit=1))
    assert [hit.id for hit in hits] == ["e2"]


def test_search_does_not_apply_as_of(any_store):
    """Valid time is enforced once, on the merged set: under SCD-2 the graph holds the successor row
    carrying ``t_invalid``, and a substrate that filtered ``as_of`` itself would withhold exactly the
    row that carries the evidence (finding F2)."""
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat", t_valid=10, t_invalid=20)])
    hits = any_store.search(ACME, MemoryQuery(text="cat mat", limit=5, as_of=1_000))
    assert [hit.id for hit in hits] == ["e1"]
    assert (hits[0].t_valid, hits[0].t_invalid) == (10, 20)


def test_the_tier_set_is_resolved_exactly_as_the_query_resolves_it(any_store):
    """When one leg honoured the tier filter and the other did not, the leakage surfaced only in the
    merged result and was hard to trace back (finding F3)."""
    any_store.upsert(
        ACME,
        [
            record("e1", "the cat sat on the mat"),
            record("s1", "the cat sat on the mat", tier=MemoryTier.SEMANTIC),
            record("w1", "the cat sat on the mat", tier=MemoryTier.WORKING),
        ],
    )
    default = {hit.id for hit in any_store.search(ACME, MemoryQuery(text="cat mat", limit=10))}
    assert default == {"e1", "s1"}, default  # WORKING is not searched by default
    narrowed = any_store.search(ACME, MemoryQuery(text="cat mat", limit=10), tier=MemoryTier.SEMANTIC)
    assert [hit.id for hit in narrowed] == ["s1"]


def test_every_hit_is_labelled_as_coming_from_the_vector_substrate(any_store):
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat")])
    assert any_store.search(ACME, MemoryQuery(text="cat mat", limit=5))[0].substrate is Substrate.VECTOR


def test_an_unrelated_memory_is_not_offered_as_a_candidate(any_store):
    """Zero similarity is not a match. It would occupy a page slot, contribute nothing to the sum
    the provider fuses on, and put an unrelated memory in a prompt."""
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat"), record("e2", "quantum tunnelling")])
    assert [hit.id for hit in any_store.search(ACME, MemoryQuery(text="cat mat", limit=10))] == ["e1"]


def test_a_scan_walks_oldest_first_and_honours_its_bound(any_store):
    any_store.upsert(
        ACME,
        [
            record("e2", "two", created_at=200),
            record("e1", "one", created_at=100),
            record("e3", "three", created_at=300),
        ],
    )
    assert [row.id for row in any_store.scan(ACME, MemoryTier.EPISODIC)] == ["e1", "e2", "e3"]
    assert [row.id for row in any_store.scan(ACME, MemoryTier.EPISODIC, limit=2)] == ["e1", "e2"]
    assert any_store.scan(ACME, MemoryTier.EPISODIC, limit=0) == []


def test_a_scan_returns_copies_rather_than_live_rows(any_store):
    """A consolidation pass mutates what it scanned. Handing back the stored object would write
    into the store through the read path -- the defect SM-C1's own double once hid."""
    any_store.upsert(ACME, [record("e1", "one", metadata={"k": "v"})])
    scanned = any_store.scan(ACME, MemoryTier.EPISODIC)[0]
    scanned.metadata["k"] = "mutated"
    scanned.content = "mutated"
    again = any_store.scan(ACME, MemoryTier.EPISODIC)[0]
    assert again.metadata == {"k": "v"}
    assert again.content == "one"


def test_a_scan_does_not_hide_a_superseded_row(any_store):
    """The deliberate asymmetry with ``search``: an enumeration that quietly narrowed would let a
    pass report a drained tier while never seeing part of it. Both the backend and the double show
    the row, so the provider behaves the same against either."""
    any_store.upsert(ACME, [record("e1", "one", expired_at=50, superseded_by="e2")])
    assert [row.id for row in any_store.scan(ACME, MemoryTier.EPISODIC)] == ["e1"]


# -- the async half ------------------------------------------------------------


async def test_both_halves_return_the_same_records(async_store):
    """Not "the async half also works": the same query, compared field for field. The two decode
    ``jsonb`` through different drivers, so this is where a shape difference surfaces."""
    records = [record("e1", "the cat sat on the mat", metadata={"n": {"a": 1}}), record("e2", "a cat")]
    await async_store.aupsert(ACME, records)
    asynchronous = await async_store.asearch(ACME, MemoryQuery(text="cat mat", limit=5))
    synchronous = async_store.search(ACME, MemoryQuery(text="cat mat", limit=5))
    assert asynchronous == synchronous
    assert [row for row in await async_store.ascan(ACME, MemoryTier.EPISODIC)] == async_store.scan(
        ACME, MemoryTier.EPISODIC
    )


async def test_the_async_half_scopes_exactly_as_the_sync_half_does(async_store):
    await async_store.aupsert(ACME, [record("e1", "the cat sat on the mat")])
    await async_store.aupsert(GLOBEX, [record("e2", "the cat sat on the mat", scope=GLOBEX)])
    hits = await async_store.asearch(ACME, MemoryQuery(text="cat mat", limit=10))
    assert [hit.id for hit in hits] == ["e1"]


async def test_the_async_half_refuses_a_foreign_scope_before_embedding(async_store):
    with pytest.raises(MemoryOperationError):
        await async_store.aupsert(ACME, [record("e1", "one", scope=GLOBEX)])


async def test_a_partition_created_on_the_async_half_serves_the_sync_one(async_store):
    """One store, one table: the halves are two drivers over the same rows, not two stores."""
    await async_store.aupsert(GLOBEX, [record("e9", "the cat sat on the mat", scope=GLOBEX)])
    assert [hit.id for hit in async_store.search(GLOBEX, MemoryQuery(text="cat mat", limit=5))] == ["e9"]


# -- deliberate limitations, pinned ------------------------------------------


def test_metadata_filtering_is_not_offered_by_this_substrate(any_store):
    """``MemoryQuery.filters`` stays refused at the envelope after SM-V1. Metadata is *stored* on
    every vector, but honouring a predicate on this leg alone returns a half-filtered merged page,
    and the graph leg keeps metadata as a JSON string it can only filter after ``LIMIT``.

    Pinned as a failing test rather than a comment: when a ticket does define the predicate on both
    ports, this fails and names what to update.
    """
    with pytest.raises(ValueError, match="filters is reserved"):
        MemoryQuery(text="cat", filters={"source": "chat"})


def test_the_vector_projection_is_not_re_synced_when_the_graph_bounds_a_fact(any_store):
    """SPEC section 11.3 assigns the re-sync to SM-V1, and it is **not** built: nothing calls
    ``GraphSubstrate.invalidate`` through the seam -- ``MemoryProvider`` does not expose the verb,
    and deciding a contradiction happened is SM-G5's (#103) -- so a hook here would have no caller.

    The consequence, pinned: this leg keeps whatever bounds were written to it, and the provider
    reconciles against ``bounds_for_ids`` on every read instead. When the re-sync lands, the stored
    row will carry the graph's bound and this test fails.
    """
    any_store.upsert(ACME, [record("e1", "the cat sat on the mat", t_valid=10)])
    stored = any_store.search(ACME, MemoryQuery(text="cat mat", limit=5))[0]
    assert stored.t_invalid is None and stored.expired_at is None
