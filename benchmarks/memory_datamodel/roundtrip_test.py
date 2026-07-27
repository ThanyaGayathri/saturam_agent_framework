"""SM-R12 round-trip test: write -> read -> assert, on REAL PostgreSQL + pgvector.

Proves the data model is implementable and lossless against the physical schema:
  * every persisted tier round-trips through the pg backend, sync AND async;
  * bi-temporal invalidate-not-delete + point-in-time ("true at T") query works (SM-R10);
  * cross-tenant isolation holds -- a query in one tenant partition never sees another (SM-R3);
  * a second store adapter (in-memory) drops in with zero call-site changes (the seam).

Runs a private embedded Postgres via ``pgserver`` (bundles pgvector) -- no Docker, no shared
port: the instance is a unix socket inside a unique per-run data dir. Run:

    python -m benchmarks.memory_datamodel.roundtrip_test
"""

import asyncio
import pathlib
import shutil
import tempfile
from typing import Any, List, Tuple, Type

from .model import (
    EpisodicRecord,
    MemoryLink,
    MemoryTier,
    ProceduralRecord,
    SemanticEntity,
    SemanticFact,
    SemanticRelation,
)
from .store import InMemoryMemoryStore, MemoryStore, PgMemoryStore

DDL = pathlib.Path(__file__).parent / "ddl" / "schema.sql"
TENANTS = ("acme", "beta")
_EMBED_TABLES = [
    "memory_episodic",
    "memory_semantic_fact",
    "memory_entity",
    "memory_procedural",
]
_ALL_TABLES = _EMBED_TABLES + ["memory_relation", "memory_link"]


def _bootstrap(srv: Any) -> None:
    """Enable pgvector, apply the DDL, create per-tenant partitions + indexes."""
    srv.psql("CREATE EXTENSION IF NOT EXISTS vector;")
    srv.psql(DDL.read_text())
    for t in _ALL_TABLES:
        for tenant in TENANTS:
            srv.psql(
                f"CREATE TABLE {t}_{tenant} PARTITION OF {t} FOR VALUES IN ('{tenant}');"
            )
    for t in _EMBED_TABLES:
        for tenant in TENANTS:
            srv.psql(
                f"CREATE INDEX ON {t}_{tenant} USING hnsw (embedding vector_cosine_ops);"
            )


def _vec(lead: float, dim: int = 768) -> List[float]:
    """A ``dim``-length embedding matching the DDL's vector(768). Values are float4-exact
    (0.5/0.25) so round-trip asserts are exact; ``lead`` makes each record's vector distinct."""
    v = [0.5 if i % 2 == 0 else 0.25 for i in range(dim)]
    v[0] = lead
    return v


def _samples(tenant: str = "acme") -> List[Tuple[Type[Any], Any]]:
    """One record per persisted tier. Floats are float4-exact to keep asserts exact."""
    ep = EpisodicRecord(
        tenant_id=tenant,
        user_id="u1",
        content="Deployed v2 to prod.",
        context={"occurred_at": 1_700_000_000, "location": "prod", "cause": "release"},
        embedding=_vec(0.5),
        embedding_dim=768,
        embedding_version="bge-base-en-v1.5/d768/cos/v1",
        importance=0.75,
        strength=0.5,
        last_accessed_at=1_700_000_100,
        entity_ids=["e_acme"],
        graph_node_id="e_acme",
        t_valid=1_700_000_000,
        metadata={"src": "chat"},
    )
    fact = SemanticFact(
        tenant_id=tenant,
        content="Acme uses PostgreSQL.",
        subject="Acme",
        predicate="uses",
        object="PostgreSQL",
        confidence=1.0,
        source_episode_ids=[ep.id],
        entity_ids=["e_acme"],
        embedding=_vec(0.25),
        embedding_dim=768,
        importance=0.5,
        t_valid=1_700_000_000,
    )
    ent = SemanticEntity(
        tenant_id=tenant,
        id="e_acme",
        entity_type="company",
        name="Acme",
        description="Fintech co.",
        properties={"industry": "fintech"},
        embedding=_vec(0.125),
        embedding_dim=768,
    )
    rel = SemanticRelation(
        tenant_id=tenant,
        subject_entity_id="e_acme",
        predicate="uses",
        object_entity_id="e_pg",
        confidence=1.0,
        source_episode_ids=[ep.id],
        t_valid=1_700_000_000,
    )
    proc = ProceduralRecord(
        tenant_id=tenant,
        kind="skill",
        title="async io",
        content="Use gather() for concurrent IO.",
        trigger="optimizing IO-bound code",
        tags=["python", "async"],
        success_count=3,
        embedding=_vec(0.0625),
        embedding_dim=768,
    )
    link = MemoryLink(
        tenant_id=tenant,
        from_id=fact.id,
        from_tier=MemoryTier.SEMANTIC,
        to_id=ep.id,
        to_tier=MemoryTier.EPISODIC,
        link_type="derived_from",
        t_valid=1_700_000_000,
    )
    return [
        (EpisodicRecord, ep),
        (SemanticFact, fact),
        (SemanticEntity, ent),
        (SemanticRelation, rel),
        (ProceduralRecord, proc),
        (MemoryLink, link),
    ]


def _assert_equal(orig: Any, got: Any) -> None:
    assert got is not None, f"{type(orig).__name__}: read returned None"
    a, b = orig.model_dump(), got.model_dump()
    assert set(a) == set(b), f"{type(orig).__name__}: field mismatch {set(a) ^ set(b)}"
    for k in a:
        av, bv = a[k], b[k]
        if isinstance(av, list) and av and isinstance(av[0], float):
            assert len(av) == len(bv) and all(
                abs(x - y) < 1e-6 for x, y in zip(av, bv)
            ), f"{type(orig).__name__}.{k}: vector mismatch"
        elif isinstance(av, float):
            assert abs(av - (bv or 0.0)) < 1e-6, (
                f"{type(orig).__name__}.{k}: {av} != {bv}"
            )
        else:
            assert av == bv, f"{type(orig).__name__}.{k}: {av!r} != {bv!r}"


def _roundtrip_sync(store: MemoryStore, label: str) -> int:
    n = 0
    for model, rec in _samples():
        store.write(rec)
        _assert_equal(rec, store.read(model, rec.tenant_id, rec.id))
        n += 1
    print(f"  [{label}] sync round-trip: {n}/{n} tiers OK")
    return n


async def _roundtrip_async(store: MemoryStore, label: str) -> int:
    n = 0
    for model, rec in _samples():
        await store.awrite(rec)
        _assert_equal(rec, await store.aread(model, rec.tenant_id, rec.id))
        n += 1
    print(f"  [{label}] async round-trip: {n}/{n} tiers OK")
    return n


def _check_bitemporal(srv: Any, store: PgMemoryStore) -> None:
    """Invalidate-not-delete + point-in-time (SM-R10). Fact changes: MySQL (t0..t1) -> PostgreSQL (t1..)."""
    t0, t1 = 1_700_000_000, 1_700_000_500
    # Distinct subject so this check is independent of the round-trip fixtures above.
    old = SemanticFact(
        tenant_id="acme",
        content="Globex uses MySQL.",
        subject="Globex",
        predicate="uses",
        object="MySQL",
        t_valid=t0,
        t_invalid=t1,
    )
    new = SemanticFact(
        tenant_id="acme",
        content="Globex uses PostgreSQL.",
        subject="Globex",
        predicate="uses",
        object="PostgreSQL",
        t_valid=t1,
    )
    old.superseded_by = new.id
    old.expired_at = t1  # tx-time out: old row corrected, but NOT deleted
    store.write(old)
    store.write(new)
    # point-in-time "true at T": exactly one active fact valid at each instant
    q = (
        "SELECT object FROM memory_semantic_fact "
        "WHERE tenant_id='acme' AND subject='Globex' AND predicate='uses' "
        "AND (t_valid IS NULL OR t_valid <= %s) AND (t_invalid IS NULL OR t_invalid > %s) "
        "ORDER BY object"
    )
    at_t0 = srv.psql(q.replace("%s", str(t0 + 1)))
    at_t2 = srv.psql(q.replace("%s", str(t1 + 1)))
    assert "MySQL" in at_t0 and "PostgreSQL" not in at_t0, (
        f"point-in-time@t0 wrong: {at_t0}"
    )
    assert "PostgreSQL" in at_t2 and "MySQL" not in at_t2, (
        f"point-in-time@t1 wrong: {at_t2}"
    )
    # nothing destroyed: both rows still present
    cnt = srv.psql(
        "SELECT count(*) FROM memory_semantic_fact WHERE tenant_id='acme' AND subject='Globex';"
    )
    assert "2" in cnt, f"invalidate-not-delete broken: {cnt}"
    print("  [pg] bi-temporal invalidate-not-delete + point-in-time OK")


def _check_isolation(store: PgMemoryStore) -> None:
    """SM-R3: a record written to tenant 'acme' is invisible from tenant 'beta'."""
    _, fact = _samples("acme")[1]
    store.write(fact)
    leaked = store.read(SemanticFact, "beta", fact.id)
    assert leaked is None, "cross-tenant leak: acme row visible under beta"
    print("  [pg] cross-tenant isolation OK (0 leakage)")


def main() -> None:
    import pgserver

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="smr12_pgdata_"))
    srv = pgserver.get_server(tmp)  # unique embedded instance (unix socket in tmp)
    try:
        _bootstrap(srv)
        ver = (
            srv.psql(
                "SELECT current_setting('server_version'), extversion FROM pg_extension WHERE extname='vector';"
            )
            .split("\n")[2]
            .strip()
        )
        print("Backend: PostgreSQL + pgvector ->", " ".join(ver.split()))
        pg = PgMemoryStore(srv.get_uri())
        print("Real PostgreSQL + pgvector round-trip:")
        _roundtrip_sync(pg, "pg")
        asyncio.run(_roundtrip_async(pg, "pg"))
        _check_bitemporal(srv, pg)
        _check_isolation(pg)
        print("Second adapter (proves the seam, zero call-site changes):")
        mem = InMemoryMemoryStore()
        _roundtrip_sync(mem, "in-memory")
        asyncio.run(_roundtrip_async(mem, "in-memory"))
        print("\nALL ROUND-TRIP CHECKS PASSED")
    finally:
        srv.cleanup()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
