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
        last_accessed_at=1_700_000_100,
        entity_ids=["e_acme"],
        graph_node_id="e_acme",
        t_valid=1_700_000_000,
        metadata={"src": "chat"},
        # SM-R6 episode identity / segmentation:
        revision=1,
        supersedes=["ep_old_1"],
        superseded_by=[],
        boundary_method="time_gap",
        boundary_trigger=1800.0,
        boundary_confidence=1.0,
        refined=True,
        segmenter_version="seg-v1/param-hash",
    )
    fact = SemanticFact(
        tenant_id=tenant,
        content="Acme uses PostgreSQL.",
        subject="Acme",
        predicate="uses",
        object="PostgreSQL",
        confidence=1.0,
        source_episode_ids=[ep.id],
        derived_by="mem0",
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


def _roundtrip_sync(store: MemoryStore, label: str, tenant: str = "acme") -> int:
    n = 0
    for model, rec in _samples(tenant):
        store.write(rec)
        _assert_equal(rec, store.read(model, rec.tenant_id, rec.id))
        n += 1
    print(f"  [{label}] sync round-trip: {n}/{n} tiers OK")
    return n


async def _roundtrip_async(store: MemoryStore, label: str, tenant: str = "beta") -> int:
    # Different tenant from the sync pass: some fixtures use fixed ids (entity 'e_acme'), and
    # write() is now insert-only, so the two passes must not target the same (tenant, id).
    n = 0
    for model, rec in _samples(tenant):
        await store.awrite(rec)
        _assert_equal(rec, await store.aread(model, rec.tenant_id, rec.id))
        n += 1
    print(f"  [{label}] async round-trip: {n}/{n} tiers OK")
    return n


def _check_bitemporal(srv: Any, store: PgMemoryStore) -> None:
    """SM-R10 SCD-2 via expire() on a PERSISTED row -- the real reuse-id UPDATE path -- then a
    fresh insert. Proves invalidate-not-delete + point-in-time AND that expire() never mutates
    t_valid/created_at (the failure the old blanket UPSERT allowed). Globex changes MySQL->pg."""
    t0, t1 = 1_700_000_000, 1_700_000_500
    old = SemanticFact(
        tenant_id="acme",
        content="Globex uses MySQL.",
        subject="Globex",
        predicate="uses",
        object="MySQL",
        t_valid=t0,  # open-ended / current when written
        created_at=t0,  # tx-time in; keeps expired_at >= created_at (CHECK) consistent
    )
    store.write(old)
    new = SemanticFact(
        tenant_id="acme",
        content="Globex uses PostgreSQL.",
        subject="Globex",
        predicate="uses",
        object="PostgreSQL",
        t_valid=t1,
        created_at=t1,
    )
    # Close the old row in place (the ONLY legitimate mutation) then insert the new version.
    closed = store.expire(
        SemanticFact, "acme", old.id, expired_at=t1, t_invalid=t1, superseded_by=new.id
    )
    assert closed == 1, f"expire() should close exactly one row, got {closed}"
    store.write(new)
    # Re-expiring is a guarded no-op (expired_at IS NULL guard).
    assert store.expire(SemanticFact, "acme", old.id, expired_at=t1) == 0, (
        "re-expire must be a no-op"
    )
    # expire() moved only expired_at/t_invalid/superseded_by -- t_valid is untouched.
    reread = store.read(SemanticFact, "acme", old.id)
    assert (
        reread is not None
        and reread.t_valid == t0
        and reread.t_invalid == t1
        and reread.expired_at == t1
        and reread.superseded_by == new.id
    ), f"expire touched the wrong fields: {reread!r}"
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
    print(
        "  [pg] bi-temporal expire()+point-in-time OK (t_valid preserved; re-expire no-op)"
    )


def _check_episodic_supersede(store: PgMemoryStore) -> None:
    """SM-R6: expire() on an EpisodicRecord writes the uuid[] identity graph into the `TEXT[]`
    `superseded_by` column (the array override) via the real psycopg driver, and it reads back as
    a genuine multi-element Python list. Exercises the array path expire() only ever assumed."""
    ep = EpisodicRecord(
        tenant_id="acme", content="raw event", created_at=100, t_valid=100
    )
    store.write(ep)
    n = store.expire(
        EpisodicRecord,
        "acme",
        ep.id,
        expired_at=200,
        t_invalid=200,
        superseded_by=["seg_a", "seg_b"],
    )
    assert n == 1, f"episodic expire() rowcount {n}"
    got = store.read(EpisodicRecord, "acme", ep.id)
    assert got is not None and isinstance(got.superseded_by, list), (
        f"episodic superseded_by not a list: {got!r}"
    )
    assert got.superseded_by == ["seg_a", "seg_b"], (
        f"TEXT[] array round-trip wrong: {got.superseded_by!r}"
    )
    assert got.t_valid == 100 and got.expired_at == 200 and got.t_invalid == 200
    print(
        "  [pg] SM-R6 episodic expire() -> TEXT[] superseded_by round-trips as a list OK"
    )


def _check_insert_only(store: PgMemoryStore) -> None:
    """SM-R10 guardrail: write() is insert-only. Re-writing an existing id must FAIL rather than
    silently overwrite temporal fields (what the previous blanket ON CONFLICT DO UPDATE did)."""
    import psycopg

    rec = SemanticFact(
        tenant_id="acme",
        content="Insert-only probe.",
        subject="Probe",
        predicate="p",
        object="o",
        t_valid=1_700_000_000,
    )
    store.write(rec)
    clobber = rec.model_copy(update={"t_valid": 9_999_999_999, "content": "MUTATED"})
    try:
        store.write(clobber)  # same (tenant_id, id)
        raise AssertionError(
            "write() allowed an overwrite -- insert-only guardrail missing"
        )
    except psycopg.errors.UniqueViolation:
        pass
    reread = store.read(SemanticFact, "acme", rec.id)
    assert (
        reread is not None
        and reread.t_valid == 1_700_000_000
        and reread.content == "Insert-only probe."
    ), f"temporal fields mutated despite conflict: {reread!r}"
    print("  [pg] insert-only guardrail OK (duplicate id rejected; t_valid intact)")


def _check_constraints(srv: Any) -> None:
    """SM-R10 CHECK constraints fire on ALL FOUR bi-temporal tables (not one standing in for the
    rest): an impossible window (t_invalid < t_valid) and a backwards expiry (expired_at <
    created_at) are rejected by the DB. NULL t_invalid/expired_at intentionally PASS (explicit
    `IS NULL` guards in the CHECKs) so open-ended/current rows are allowed -- that positive case is
    covered by every open-ended fixture in the round-trip (t_invalid=None writes succeed)."""
    # (table, required-NOT NULL cols besides the time pair, matching values)
    tables = [
        (
            "memory_episodic",
            "tenant_id, id, content, created_at",
            "'acme', '{i}', 'x', 100",
        ),
        (
            "memory_semantic_fact",
            "tenant_id, id, content, created_at",
            "'acme', '{i}', 'x', 100",
        ),
        (
            "memory_relation",
            "tenant_id, id, subject_entity_id, predicate, object_entity_id, created_at",
            "'acme', '{i}', 's', 'p', 'o', 100",
        ),
        (
            "memory_link",
            "tenant_id, id, from_id, from_tier, to_id, to_tier, created_at",
            "'acme', '{i}', 'f', 'episodic', 't', 'semantic', 100",
        ),
    ]
    for table, cols, vals in tables:
        # bad window: t_invalid(100) < t_valid(200); bad expiry: expired_at(50) < created_at(100)
        bad_window = f"INSERT INTO {table} ({cols}, t_valid, t_invalid) VALUES ({vals.format(i='ck_w')}, 200, 100);"
        bad_expiry = f"INSERT INTO {table} ({cols}, expired_at) VALUES ({vals.format(i='ck_e')}, 50);"
        for sql in (bad_window, bad_expiry):
            try:
                srv.psql(sql)  # prints CHECK error to stderr rather than raising
            except Exception:
                pass
        cnt = srv.psql(f"SELECT count(*) FROM {table} WHERE id IN ('ck_w', 'ck_e');")
        landed = cnt.strip().split("\n")[2].strip()
        assert landed == "0", (
            f"{table}: CHECK failed to reject bad rows (found {landed})"
        )
    print("  [pg] bi-temporal CHECK constraints reject bad windows on all 4 tables OK")


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
        _check_episodic_supersede(pg)
        _check_insert_only(pg)
        _check_constraints(srv)
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
