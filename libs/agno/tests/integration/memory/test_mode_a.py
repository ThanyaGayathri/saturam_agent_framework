"""Mode A -- co-located memory (pgvector + Neo4j in-VPC), against real backends (SM-X1 #137).

Gated on **both** ``EGPT_INTEGRATION_PGVECTOR`` and ``EGPT_INTEGRATION_NEO4J``; start the engines
with ``cookbook/scripts/run_pgvector.sh`` and ``cookbook/scripts/run_neo4j.sh``. Neither substrate's
own integration suite (``memory/pgvector/``, ``memory/neo4j/``) exercises them together, and
``test_provider_swap.py`` says so explicitly: its config-only swap proof is run only against
``FakeVector``/``FakeGraph``, with a docstring stating that re-proving it against a real backend is
this ticket's job, not that file's.

**What this proves that a double cannot.** ``CoLocatedMemoryProvider`` was built and reviewed
against substrates written in the same change (SM-C1 #89) -- unfalsifiable by construction. SM-V1
(#95) and SM-G1 (#99) have since landed real backends, so this is the first place the two are wired
into one provider and driven through a real write -> consolidate -> read pass: a real ``WHERE``
tenant predicate on both legs, a real HNSW search, a real Cypher ``MERGE``, and the two engines
actually agreeing on what got consolidated.

**Deliberately not built here (owned elsewhere):**

* Per-tenant *selection* of Mode A vs Mode B is SM-C5's (#93); this file always builds one static
  config, the way ``09_memory_provider_seam.py`` does.
* Mode B's own real-backend re-proof is SM-X2's (#138) -- there is no external-service adapter to
  re-prove against yet. This file is Mode A only.
* Real extraction (entity/relation/fact pipelines) is SM-G2/SM-G3/SM-L3/SM-S3's. Consolidation here
  runs the deterministic default (``_distil``), the same path ``test_provider_co_located.py``
  exercises against fakes -- proving the *wiring*, not a new extraction strategy.
"""

import os

import pytest

pytest.importorskip("psycopg", reason="the pgvector memory substrate connects through psycopg 3")
pytest.importorskip("neo4j", reason="the Neo4j graph substrate imports its driver at module level")

pytestmark = pytest.mark.skipif(
    not (os.getenv("EGPT_INTEGRATION_PGVECTOR") and os.getenv("EGPT_INTEGRATION_NEO4J")),
    reason="needs both EGPT_INTEGRATION_PGVECTOR and EGPT_INTEGRATION_NEO4J",
)

from sqlalchemy import text  # noqa: E402

from egpt.integrations.memory.vector.pgvector import sql  # noqa: E402
from egpt.memory import MemoryProviderConfig, build_memory_provider  # noqa: E402
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider  # noqa: E402
from egpt.memory.tiers import MemoryTier  # noqa: E402
from egpt.memory.types import MemoryEvent, MemoryQuery, Scope, Substrate  # noqa: E402
from egpt.providers.model_provider import ProviderMode  # noqa: E402
from tests.unit.memory.test_provider_swap import consumer_workload  # noqa: E402

PG_TABLE = "test_mode_a_vectors"
ACME = Scope(tenant_id="acme", user_id="ada")
GLOBEX = Scope(tenant_id="globex", user_id="bob")


class OfflineTokenEmbedder:
    """A deterministic bag-of-tokens embedder, declaring itself Mode A-eligible.

    Not a stand-in for a model -- which one to use is SM-V2's (#96), and the real Mode A default
    (``bge-base-en-v1.5``) is a multi-hundred-MB download this suite should not require to run.
    ``offline_capable`` is the declared escape hatch ``embedding.py`` documents for exactly this: an
    in-process embedder that reaches nothing outside the air gap, asserted rather than inferred from
    a module path this class does not live under.
    """

    dimensions = 32
    offline_capable = True

    def get_embedding(self, text_):
        vector = [0.0] * self.dimensions
        for token in text_.lower().split():
            vector[hash(token) % self.dimensions] += 1.0
        return vector

    async def async_get_embedding(self, text_):
        return self.get_embedding(text_)


def _pg_url():
    return os.getenv("EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai")


def _neo4j_settings():
    return {
        "uri": os.getenv("NEO4J_URI", "bolt://localhost:7691"),
        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
        "password": os.getenv("NEO4J_PASSWORD", "smg1testpassword"),
    }


def _mode_a_config(**graph_overrides) -> MemoryProviderConfig:
    """One config, both legs real, mode=LOCAL (Mode A). The only construction call site is
    ``build_memory_provider`` -- nothing here names ``CoLocatedMemoryProvider`` directly."""
    return MemoryProviderConfig(
        provider="co_located",
        mode=ProviderMode.LOCAL,
        vector={
            "backend": "pgvector",
            "db_url": _pg_url(),
            "embedder": OfflineTokenEmbedder(),
            "dim": OfflineTokenEmbedder.dimensions,
            "table": PG_TABLE,
            "auto_init_schema": True,
        },
        graph={"backend": "neo4j", **_neo4j_settings(), **graph_overrides},
    )


@pytest.fixture
def provider():
    """A Mode A provider over freshly-cleared real backends. Teardown drops the vector table and
    clears the graph, so one test's rows cannot leak into the next."""
    built = build_memory_provider(_mode_a_config())
    assert isinstance(built, CoLocatedMemoryProvider)  # the factory this key resolves to, sanity-checked once
    with built.vector._engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(text(f"DELETE FROM {sql.qualified('ai', PG_TABLE)}"))
    with built.graph._driver.session(database=built.graph.database) as session:  # type: ignore[union-attr]
        session.run("MATCH (n) DETACH DELETE n")
    yield built
    built.vector.close()  # type: ignore[union-attr]
    built.graph.close()  # type: ignore[union-attr]


@pytest.fixture
async def async_provider(provider):
    """The same provider, for the async half. Async teardown needs its own close."""
    yield provider
    await provider.vector.aclose()  # type: ignore[union-attr]
    await provider.graph.aclose()  # type: ignore[union-attr]


def test_build_memory_provider_wires_both_real_substrates(provider):
    assert provider.substrates() == {Substrate.VECTOR: True, Substrate.GRAPH: True}
    assert provider.config.mode is ProviderMode.LOCAL


def test_config_only_swap_reaches_a_real_co_located_provider(provider):
    """The exact structural proof ``test_provider_swap.py`` leaves open for a real backend: the same
    ``consumer_workload`` function, unmodified, driving a provider built from real pgvector + real
    Neo4j rather than ``FakeVector``/``FakeGraph``. If the seam leaked reality through, this function
    -- which knows nothing but the ``MemoryProvider`` ABC -- would be the one to notice."""
    result = consumer_workload(provider, ACME)

    assert result["accepted"] == 2
    assert "email over phone" in result["context"]
    assert result["substrates"] == {"vector": True, "graph": True}
    assert result["consolidated"] == ACME.tenant_id


def test_write_consolidate_read_round_trips_through_both_engines(provider):
    """The episodic -> semantic path SM-C1 could only fake: write raw episodes, consolidate them
    into facts, and read the result back -- with both a real HNSW search and a real Cypher query
    agreeing on what came out."""
    provider.write(
        ACME,
        [
            MemoryEvent(content="the deploy pipeline moved to owned compute", scope=ACME, id="e1"),
            MemoryEvent(content="the deploy pipeline moved to owned compute", scope=ACME, id="e2"),
        ],
    )

    report = provider.consolidate(ACME)
    assert report.episodic_scanned == 2
    assert report.semantic_written >= 1

    slice_ = provider.read(ACME, MemoryQuery(text="deploy pipeline owned compute", tiers=[MemoryTier.SEMANTIC]))
    assert slice_.records, "consolidation wrote a fact but the fused read found nothing"
    assert all(r.tier is MemoryTier.SEMANTIC for r in slice_.records)

    # Landed on both engines, not only the one the fused read happened to favour. The fact carries
    # no subject/predicate/object (``_distil`` runs no SPO extraction), so it is an untripled
    # ``(:Fact)`` node on the graph side -- outside ``recall_facts``'s entity/relation-filtered
    # shape (its own docstring) -- and is checked directly, the same way the fixtures above check
    # backend state directly rather than through the seam.
    vector_hits = provider.vector.search(ACME, MemoryQuery(text="deploy pipeline", tiers=[MemoryTier.SEMANTIC]))  # type: ignore[union-attr]
    assert vector_hits, "the fact never reached pgvector"
    with provider.graph._driver.session(database=provider.graph.database) as session:  # type: ignore[union-attr]
        count = session.run(
            "MATCH (f:Fact {tenant_id: $tenant_id}) RETURN count(f) AS n", tenant_id=ACME.tenant_id
        ).single()["n"]
    assert count >= 1, "the fact never reached neo4j"


async def test_async_write_consolidate_read_round_trips_through_both_engines(async_provider):
    """The async twin of the round trip above. Not the same assertions copy-pasted onto a sync
    call -- a provider whose async half quietly delegated to a blocking one would pass the sync test
    and hang an event loop in production, invisibly, until something under load noticed."""
    await async_provider.awrite(
        GLOBEX,
        [MemoryEvent(content="the on-call rotation moved to owned compute", scope=GLOBEX, id="g1")],
    )

    report = await async_provider.aconsolidate(GLOBEX)
    assert report.episodic_scanned == 1
    assert report.semantic_written >= 1

    slice_ = await async_provider.aread(
        GLOBEX, MemoryQuery(text="on-call rotation owned compute", tiers=[MemoryTier.SEMANTIC])
    )
    assert slice_.records
    assert all(r.tier is MemoryTier.SEMANTIC for r in slice_.records)


def test_tenant_isolation_holds_on_real_engines(provider):
    """The property a double can share a bug on: SM-C4's (#92) scope rule, checked against a real
    Postgres ``WHERE`` and a real Cypher tenant predicate rather than a dict keyed by tenant."""
    provider.write(ACME, [MemoryEvent(content="acme deploy secret", scope=ACME, id="a1")])
    provider.write(GLOBEX, [MemoryEvent(content="globex deploy secret", scope=GLOBEX, id="b1")])
    provider.consolidate(ACME)
    provider.consolidate(GLOBEX)

    leaked = [
        r for r in provider.read(GLOBEX, MemoryQuery(text="deploy secret")).records if r.content.startswith("acme")
    ]
    assert leaked == []
