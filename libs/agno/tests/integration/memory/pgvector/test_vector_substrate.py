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
from egpt.integrations.memory.vector.pgvector import pgvector as module  # noqa: E402
from egpt.memory.config import MemoryProviderConfigError  # noqa: E402
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
    return os.getenv("EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai")


def _fresh():
    substrate = PgVectorMemorySubstrate(db_url=_url(), embedder=TokenEmbedder(), table=TABLE, auto_init_schema=False)
    with substrate._engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {sql.qualified('ai', TABLE)} CASCADE"))
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
def any_store(request):
    """The real backend and the reference double, behind one name.

    A property parametrised over both is what keeps the double honest: a double more permissive
    than the backend cannot catch a provider bug that only appears when the backend is strict.

    The two are built **here** rather than by taking ``store`` as an argument. That form looked
    equivalent and was not: pytest materialises a requested fixture whatever the parametrisation
    does with it, so every ``double`` case also opened a connection and recreated the table --
    binding the one variant designed to need no database to a live one.
    """
    if request.param != "pgvector":
        yield FakeVector()
        return
    substrate = _fresh()
    yield substrate
    substrate.close()


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


# -- schema drift: what the DDL kept from a previous run -----------------------


class _NarrowEmbedder(TokenEmbedder):
    """Half the width of the fixture's embedder, to stand for a deployment changing models."""

    dimensions = DIM // 2

    def get_embedding(self, text_):
        return super().get_embedding(text_)[: self.dimensions]

    async def async_get_embedding(self, text_):
        await asyncio.sleep(0)
        return self.get_embedding(text_)


def test_a_dimension_change_is_refused_at_schema_init_not_at_the_first_write(store):
    """``CREATE TABLE IF NOT EXISTS`` does not reconcile an existing table with the DDL just
    submitted, so before this check a deployment that changed its embedder started up **clean** and
    then failed on every write with Postgres' own ``expected 8 dimensions, not 768`` -- raised from
    a write, naming a column, layers below the setting that caused it.

    ``store`` has already created the table at ``DIM``; this asks for half of it.
    """
    with pytest.raises(MemoryProviderConfigError) as raised:
        PgVectorMemorySubstrate(db_url=_url(), embedder=_NarrowEmbedder(), table=TABLE)
    assert raised.value.field_name == "dim"
    assert f"vector({DIM})" in str(raised.value)


async def test_the_async_half_refuses_the_same_drift(store):
    """The twin reads the same rows and reaches the same decision -- asserted rather than assumed,
    since a check added to one half only is this module's most repeated defect."""
    substrate = PgVectorMemorySubstrate(db_url=_url(), embedder=_NarrowEmbedder(), table=TABLE, auto_init_schema=False)
    with pytest.raises(MemoryProviderConfigError) as raised:
        await substrate.ainit_schema()
    assert raised.value.field_name == "dim"
    await substrate.aclose()


def test_changed_index_options_are_reported_and_not_refused(store, caplog):
    """A stale ``m`` is not the same failure as a stale width: the index still answers correctly, at
    a different recall/latency point. Refusing would brick a running deployment over a tuning
    parameter, and silently accepting would leave a configured setting that never applied -- so it
    is reported. Rebuilding the index would lock the table, which this backend does not do."""
    with caplog.at_level("WARNING"):
        rebuilt = PgVectorMemorySubstrate(db_url=_url(), embedder=TokenEmbedder(), table=TABLE, m=32)
    rebuilt.close()
    assert any("m=16" in record.message and "m=32" in record.message for record in caplog.records), caplog.text


# -- acceptance criteria: vector + lexical candidates for fusion (SM-V3 #97) --

#: A corpus built so the two legs genuinely disagree. ``DECOY`` repeats the query's ordinary words
#: without carrying the identifier; ``CARRIER`` carries it once inside a longer sentence, so a
#: bag-of-tokens cosine ranks the decoy **above** the record the caller actually wants.
IDENTIFIER = "ORD-99417-XZ"
CARRIER = f"Order {IDENTIFIER} status confirmed, shipped from the Rotterdam depot"
DECOY = "Order status page shows every order status update for each order"


def _seed_hybrid_corpus(store):
    store.upsert(
        ACME,
        [
            record("carrier", CARRIER),
            record("decoy", DECOY),
            record("cats", "the cat sat on the mat"),
            record("physics", "quantum tunnelling through a barrier"),
        ],
    )


def test_a_lexical_query_surfaces_the_exact_identifier_the_vector_leg_ranks_below_a_decoy(store):
    """**Acceptance criterion 3, the lexical half.** The ticket's own testing note: query an exact
    ID and assert the lexical leg surfaces it where the vector leg does not.

    This is why the store returns both lists rather than one blended ranking. The identifier is a
    rare token, so full-text matches it exactly and returns nothing else; the dense leg ranks the
    whole corpus, and here the record that merely *repeats the query's ordinary words* wins the
    cosine. A weighted blend computed in SQL -- what ``PgVector.hybrid_search`` returns -- would
    hand the caller one number and no way to see that the two legs disagreed.

    **The decoy winning the cosine is a property of ``TokenEmbedder``, not a claim about dense
    retrieval.** This file embeds with a deterministic bag-of-tokens projection so the ranking
    assertions depend on the store rather than on a model's weights; a real embedder may well rank
    the carrier first, and ``cookbook/11_memory/14_hybrid_candidates_pgvector.py`` shows
    ``bge-base-en-v1.5`` doing exactly that. What is asserted here is the mechanism -- two legs,
    two independently-ranked lists -- which is what this ticket ships.
    """
    _seed_hybrid_corpus(store)
    candidates = store.search_candidates(ACME, MemoryQuery(text=f"order {IDENTIFIER} status", limit=5))

    # The lexical leg matches conjunctively, so only the record carrying the identifier qualifies.
    assert [r.id for r in candidates.lexical] == ["carrier"]
    # ...while the vector leg puts the decoy first, which is the disagreement the two lists exist
    # to expose. `carrier` is present in it, just not where a caller would find it.
    assert candidates.vector[0].id == "decoy"
    assert "carrier" in {r.id for r in candidates.vector}


def test_a_semantic_query_survives_a_term_the_lexical_leg_cannot_match(store):
    """**Acceptance criterion 3, the semantic half.** The complementarity in the other direction.

    ``websearch_to_tsquery`` is **conjunctive**: one unmatched term empties the lexical leg
    entirely, however well the rest of the query fits. The vector leg degrades gracefully and still
    ranks the best partial match. A caller with only the lexical leg gets nothing back and reads it
    as an empty corpus.
    """
    _seed_hybrid_corpus(store)
    candidates = store.search_candidates(ACME, MemoryQuery(text="cat sat mat quantum", limit=5))

    assert candidates.lexical == []
    assert candidates.vector[0].id == "cats"


def test_both_legs_are_returned_for_one_query(store):
    """**Acceptance criteria 1 and 2.** Two ranked lists, kept apart, each honouring ``limit``
    independently -- a shared budget would let one leg's hits displace the other's before anything
    had compared them, which is a fusion decision the store is not entitled to make."""
    _seed_hybrid_corpus(store)
    candidates = store.search_candidates(ACME, MemoryQuery(text="order status", limit=2))

    assert candidates.legs().keys() == {"vector", "lexical"}
    assert len(candidates.vector) <= 2 and len(candidates.lexical) <= 2
    assert candidates.lexical, "the lexical leg matched nothing on a query whose terms are present"
    assert all(0.0 <= r.score <= 1.0 for r in candidates.vector + candidates.lexical)
    assert all(r.substrate is Substrate.VECTOR for r in candidates.vector + candidates.lexical)


def test_the_lexical_leg_is_ranked_highest_first(store):
    """Higher ``ts_rank_cd`` is a better match -- the opposite direction from cosine distance, so a
    leg that inherited the vector sort would hand fusion its worst candidates first."""
    _seed_hybrid_corpus(store)
    candidates = store.search_candidates(ACME, MemoryQuery(text="order status", limit=5))
    scores = [r.score for r in candidates.lexical]
    assert scores == sorted(scores, reverse=True), scores


def test_the_lexical_leg_cannot_reach_another_tenant(store):
    """The scope predicate is built by the same ``scope_predicate`` helper as the vector leg's, and
    this is the property that would fail if it were not: a second retrieval path is a second chance
    to forget the predicate, and a single-tenant test passes cleanly either way."""
    store.upsert(ACME, [record("mine", CARRIER)])
    store.upsert(GLOBEX, [record("theirs", CARRIER, scope=GLOBEX)])

    candidates = store.search_candidates(ACME, MemoryQuery(text=f"order {IDENTIFIER}", limit=10))
    assert [r.id for r in candidates.lexical] == ["mine"]


def test_the_lexical_leg_cannot_reach_another_user_inside_one_tenant(store):
    """Partitioning covers ``tenant_id`` and nothing else, so the other four slots are ordinary
    predicates a second statement can simply omit."""
    store.upsert(ACME, [record("ada", CARRIER)])
    store.upsert(ACME_BOB, [record("bob", CARRIER, scope=ACME_BOB)])

    candidates = store.search_candidates(ACME, MemoryQuery(text=f"order {IDENTIFIER}", limit=10))
    assert [r.id for r in candidates.lexical] == ["ada"]


def test_the_lexical_leg_returns_current_belief_only(store):
    """Legs that disagree about which rows exist do not produce a ranking discrepancy, they produce
    a leak: a superseded row the vector leg correctly withholds would re-enter the merged result
    through this one."""
    store.upsert(ACME, [record("live", CARRIER), record("retired", CARRIER, expired_at=1_700_000_000)])

    candidates = store.search_candidates(ACME, MemoryQuery(text=f"order {IDENTIFIER}", limit=10))
    assert [r.id for r in candidates.lexical] == ["live"]
    assert [r.id for r in candidates.vector] == ["live"]


def test_the_lexical_leg_honours_the_tier_the_query_asked_for(store):
    """Resolved exactly as ``MemoryQuery.resolved_tiers`` does. When one leg honoured the tier
    filter and the other did not, the leakage surfaced only in the merged result (finding F3)."""
    store.upsert(ACME, [record("ep", CARRIER), record("sem", CARRIER, tier=MemoryTier.SEMANTIC)])

    query = MemoryQuery(text=f"order {IDENTIFIER}", tiers=[MemoryTier.SEMANTIC], limit=10)
    candidates = store.search_candidates(ACME, query)
    assert [r.id for r in candidates.lexical] == ["sem"]
    assert [r.id for r in candidates.vector] == ["sem"]


def test_a_query_of_pure_punctuation_returns_empty_rather_than_failing(store):
    """``to_tsquery`` raises a syntax error on ordinary punctuation and ``websearch_to_tsquery``
    does not. Memory query text is whatever a user said to the agent, so the difference is between
    an empty answer and a failed read."""
    _seed_hybrid_corpus(store)
    candidates = store.search_candidates(ACME, MemoryQuery(text="!@#$ %^&* ???", limit=5))
    assert candidates.lexical == []


def test_the_full_text_index_exists_on_every_partition_including_later_ones(store):
    """Created on the **parent**, so a tenant whose first write lands at 3am inherits it without
    anything remembering to build one -- the same property the HNSW index needs and for the same
    reason.

    Scoped to this table's partitions, and asserted on the **indexed expression** rather than on
    "some GIN index exists". Both matter: the first version searched every GIN index in the
    database, and neither version could tell the full-text index from a GIN index on the ``metadata``
    jsonb column -- so a test named for the full-text index would have passed with the full-text
    index missing and something else in its place. That is a claim of coverage rather than coverage.
    """
    store.upsert(ACME, [record("e1", CARRIER)])
    store.upsert(GLOBEX, [record("e2", CARRIER, scope=GLOBEX)])

    with store._engine.begin() as connection:
        indexed = {
            row.partition: row.expression
            for row in connection.execute(
                text(
                    "SELECT c.relname AS partition, pg_get_expr(i.indexprs, i.indrelid) AS expression "
                    "FROM pg_inherits h "
                    "JOIN pg_class c ON c.oid = h.inhrelid "
                    "JOIN pg_index i ON i.indrelid = c.oid "
                    "JOIN pg_class ic ON ic.oid = i.indexrelid "
                    # `CAST(...)`, not `:parent::regclass` -- SQLAlchemy's `text()` does not treat a
                    # bind followed by a colon as a parameter at all, which `sql.cast_param`
                    # documents and this test reintroduced.
                    "WHERE h.inhparent = CAST(:parent AS regclass) "
                    "AND ic.relam = (SELECT oid FROM pg_am WHERE amname = 'gin')"
                ),
                {"parent": f"ai.{TABLE}"},
            )
        }
    assert len(indexed) == 2, indexed
    for scope in (ACME, GLOBEX):
        partition = sql.partition_name(TABLE, scope.tenant_id)
        assert partition in indexed, indexed
        # `to_tsvector`, on `content`, under the configured search config -- the expression the
        # lexical statement repeats. Postgres renders the literal as `'english'::regconfig`, so the
        # config is read by name rather than by matching this module's spelling.
        expression = indexed[partition]
        assert "to_tsvector" in expression and "content" in expression, (partition, expression)
        assert module._indexed_ts_config(expression) == store.text_search_config, (partition, expression)


async def test_the_async_half_returns_the_same_candidates_as_the_sync_one(async_store):
    """Not "an async twin exists" -- that a twin exists is easy to confirm and is not the thing
    that breaks. Both halves must make the same decisions and return the same two lists in the
    same order."""
    await async_store.aupsert(
        ACME,
        [record("carrier", CARRIER), record("decoy", DECOY), record("cats", "the cat sat on the mat")],
    )
    query = MemoryQuery(text=f"order {IDENTIFIER} status", limit=5)

    asynchronous = await async_store.asearch_candidates(ACME, query)
    synchronous = async_store.search_candidates(ACME, query)

    assert [r.id for r in asynchronous.lexical] == [r.id for r in synchronous.lexical] == ["carrier"]
    assert [r.id for r in asynchronous.vector] == [r.id for r in synchronous.vector]
    assert [r.score for r in asynchronous.lexical] == [r.score for r in synchronous.lexical]


async def test_the_async_half_scopes_the_lexical_leg_exactly_as_the_sync_half_does(async_store):
    """The scoping property, on the half a sync-only test cannot see."""
    await async_store.aupsert(ACME, [record("mine", CARRIER)])
    await async_store.aupsert(GLOBEX, [record("theirs", CARRIER, scope=GLOBEX)])

    candidates = await async_store.asearch_candidates(ACME, MemoryQuery(text=f"order {IDENTIFIER}", limit=10))
    assert [r.id for r in candidates.lexical] == ["mine"]


def test_the_sync_half_does_not_answer_an_unscoped_call(store):
    """SM-C4 (#92) on the new entry point. Guarded **first**, before the embedder is called: a
    refusal that arrives after an embedding round trip has already spent the API call it was
    supposed to prevent."""
    from egpt.memory.types import MemoryScopeRequiredError

    with pytest.raises(MemoryScopeRequiredError):
        store.search_candidates(None, MemoryQuery(text="anything", limit=5))


async def test_the_async_half_does_not_answer_an_unscoped_call(async_store):
    """The same refusal on the half a sync-only test cannot see. Written as a real coroutine rather
    than by driving the sync test's loop, because the guard is inside the coroutine body -- calling
    an ``async def`` returns without executing a line of it, so a ``pytest.raises`` around the call
    alone passes whether or not the guard is there."""
    from egpt.memory.types import MemoryScopeRequiredError

    with pytest.raises(MemoryScopeRequiredError):
        await async_store.asearch_candidates(None, MemoryQuery(text="anything", limit=5))


def test_a_vector_only_double_is_not_mistaken_for_a_hybrid_store():
    """``HybridSearchSubstrate`` is an **optional** extension, and SM-H1's (#131) orchestrator will
    run one leg or two on the strength of this check. A store that cannot rank lexically must be
    detectable as such, or the optionality is decorative."""
    from egpt.memory.substrates import HybridSearchSubstrate

    assert isinstance(FakeVector(), VectorSubstrate)
    assert not isinstance(FakeVector(), HybridSearchSubstrate)


def _commit_between_legs(substrate, runner_name):
    """Wrap a statement runner so another session commits a row between the two legs.

    The interleave has to happen *inside* the candidates transaction, which is the only place the
    snapshot question is decidable -- so this wraps the runner rather than racing two threads and
    hoping. A second substrate on its own engine does the write, because a commit on the reading
    connection would be that transaction's own and always visible to it.
    """
    original = getattr(substrate, runner_name)

    def interleave(statements, isolation=None):
        with substrate._translated("read/write"), substrate._read_transaction(isolation) as connection:
            pages = []
            for index, (body, binds) in enumerate(statements):
                result = connection.execute(text(body), binds)
                pages.append(_rows_of(result))
                if index == 1:  # after the vector statement, before the lexical one
                    writer = PgVectorMemorySubstrate(
                        db_url=_url(), embedder=TokenEmbedder(), table=TABLE, auto_init_schema=False
                    )
                    writer.upsert(ACME, [record("interloper", CARRIER)])
                    writer.close()
            return pages

    return original, interleave


def _rows_of(result):
    return [dict(row) for row in result.mappings()] if result.returns_rows else []


def test_the_two_legs_read_one_snapshot(store):
    """Both legs must answer the same question about which rows exist.

    One transaction is not enough. Postgres defaults to READ COMMITTED, under which every
    *statement* takes a fresh snapshot even inside one transaction -- so a row committed between the
    legs is invisible to the first and visible to the second, and the store returns a vector list
    and a lexical list describing different corpora from a single call.

    That is not a ranking discrepancy, it is a leak: the port states the property, and a fusion
    stage handed two inconsistent lists cannot detect the inconsistency. Mutation-checked -- dropping
    the isolation level reproduces `vector=['first'] lexical=['first', 'interloper']`.
    """
    store.upsert(ACME, [record("first", CARRIER)])
    original, interleave = _commit_between_legs(store, "_statements")
    store._statements = interleave
    try:
        candidates = store.search_candidates(ACME, MemoryQuery(text=f"order {IDENTIFIER} status", limit=10))
    finally:
        store._statements = original

    assert sorted(r.id for r in candidates.vector) == sorted(r.id for r in candidates.lexical)


async def test_the_async_half_also_reads_one_snapshot(async_store):
    """The same property on the half a sync-only test cannot observe -- and the half where the
    isolation plumbing is a separate code path, because SQLAlchemy's async engine sets the level
    through an awaited `execution_options` rather than a chained one."""
    await async_store.aupsert(ACME, [record("first", CARRIER)])
    original = async_store._astatements

    async def interleave(statements, isolation=None):
        with async_store._translated("read/write"):
            async with async_store._async_engine().connect() as connection:
                if isolation is not None:
                    await connection.execution_options(isolation_level=isolation)
                async with connection.begin():
                    pages = []
                    for index, (body, binds) in enumerate(statements):
                        pages.append(_rows_of(await connection.execute(text(body), binds)))
                        if index == 1:
                            writer = PgVectorMemorySubstrate(
                                db_url=_url(), embedder=TokenEmbedder(), table=TABLE, auto_init_schema=False
                            )
                            writer.upsert(ACME, [record("interloper", CARRIER)])
                            writer.close()
                    return pages

    async_store._astatements = interleave
    try:
        candidates = await async_store.asearch_candidates(
            ACME, MemoryQuery(text=f"order {IDENTIFIER} status", limit=10)
        )
    finally:
        async_store._astatements = original

    assert sorted(r.id for r in candidates.vector) == sorted(r.id for r in candidates.lexical)


async def test_the_async_half_honours_the_tier_on_both_legs(async_store):
    """Tier resolution on the async half, which the parity test could not see: it compares the two
    halves against each other, so a filter both drop is invisible to it.

    Both legs, because the tier predicate is written into two separate statements -- one leg
    honouring it and the other not is the cross-tier leakage finding F3 describes, and it surfaces
    only after fusion has already merged the two lists."""
    await async_store.aupsert(ACME, [record("ep", CARRIER), record("sem", CARRIER, tier=MemoryTier.SEMANTIC)])

    query = MemoryQuery(text=f"order {IDENTIFIER}", tiers=[MemoryTier.SEMANTIC], limit=10)
    candidates = await async_store.asearch_candidates(ACME, query)

    assert [r.id for r in candidates.lexical] == ["sem"]
    assert [r.id for r in candidates.vector] == ["sem"]
    assert all(r.tier is MemoryTier.SEMANTIC for r in candidates.vector + candidates.lexical)
