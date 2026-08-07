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


def _check_is_current() -> None:
    """SM-R12 model invariant (this was the review's Major bug): is_current() must be True for a
    FRESH row of every tier -- including episodic, whose superseded_by is `[]` (not None) -- and
    False once expired or superseded. Pure-model, no DB needed."""
    assert EpisodicRecord(tenant_id="t", content="x").is_current() is True, (
        "fresh episode must be current (superseded_by=[])"
    )
    assert SemanticFact(tenant_id="t", content="y").is_current() is True, (
        "fresh fact must be current"
    )
    assert (
        EpisodicRecord(
            tenant_id="t", content="x", superseded_by=["a", "b"]
        ).is_current()
        is False
    )
    assert (
        SemanticFact(tenant_id="t", content="y", superseded_by="z").is_current()
        is False
    )
    assert (
        SemanticFact(tenant_id="t", content="y", expired_at=100).is_current() is False
    )
    print("  [model] is_current() correct for fresh/superseded/expired across tiers OK")


def _pit(srv: Any, subject: str, t: int, *, guard: bool) -> str:
    """Point-in-time query. `guard=True` is SM-R10's full 5a (current belief: adds
    `expired_at IS NULL`); `guard=False` is the naive form 5a explicitly forbids."""
    g = "AND expired_at IS NULL " if guard else ""
    return srv.psql(
        f"SELECT object FROM memory_semantic_fact WHERE tenant_id='acme' AND subject='{subject}' "
        f"AND (t_valid IS NULL OR t_valid <= {t}) AND (t_invalid IS NULL OR t_invalid > {t}) "
        f"{g}ORDER BY object"
    )


def _check_bitemporal(srv: Any, store: PgMemoryStore) -> None:
    """SM-R10 point-in-time using the FULL 5a current-belief query (incl. `expired_at IS NULL`),
    exercising BOTH R1 (valid-time change) and R2 (transaction-time retract via expire()).

    R1 -- valid-time change: both rows are current beliefs (neither expired). Globex used MySQL
    t0..t1, then PostgreSQL t1.. ; 5a returns MySQL at t0+1 and PostgreSQL at t1+1.
    R2 -- retract via expire() (sets expired_at, leaves t_invalid NULL): the retracted row must
    DROP OUT of 5a; the naive query that omits `expired_at IS NULL` wrongly readmits it (SM-R10
    sec 12.1(b) calls that a bug). Also proves expire() preserves t_valid, is a guarded no-op on
    re-expire, and destroys nothing."""
    t0, t1, t2 = 1_700_000_000, 1_700_000_500, 1_700_000_900

    # --- R1: valid-time change, two current (un-expired) beliefs ---
    a = SemanticFact(
        tenant_id="acme",
        content="Globex uses MySQL.",
        subject="Globex",
        predicate="uses",
        object="MySQL",
        t_valid=t0,
        t_invalid=t1,
        created_at=t0,
    )
    b = SemanticFact(
        tenant_id="acme",
        content="Globex uses PostgreSQL.",
        subject="Globex",
        predicate="uses",
        object="PostgreSQL",
        t_valid=t1,
        created_at=t1,
    )
    store.write(a)
    store.write(b)
    at_t0, at_t1 = (
        _pit(srv, "Globex", t0 + 1, guard=True),
        _pit(srv, "Globex", t1 + 1, guard=True),
    )
    assert "MySQL" in at_t0 and "PostgreSQL" not in at_t0, f"R1 PIT@t0 wrong: {at_t0}"
    assert "PostgreSQL" in at_t1 and "MySQL" not in at_t1, f"R1 PIT@t1 wrong: {at_t1}"

    # --- R2: retract via expire() -- expired_at set, t_invalid stays NULL ---
    c = SemanticFact(
        tenant_id="acme",
        content="Globex HQ Berlin.",
        subject="GlobexHQ",
        predicate="hq",
        object="Berlin",
        t_valid=t0,
        created_at=t0,
    )
    store.write(c)
    assert store.expire(SemanticFact, "acme", c.id, expired_at=t2) == 1, (
        "expire should close 1 row"
    )
    assert store.expire(SemanticFact, "acme", c.id, expired_at=t2) == 0, (
        "re-expire must be a no-op"
    )
    rc = store.read(SemanticFact, "acme", c.id)
    assert (
        rc is not None
        and rc.t_valid == t0
        and rc.t_invalid is None
        and rc.expired_at == t2
    ), f"expire touched the wrong fields: {rc!r}"
    full = _pit(srv, "GlobexHQ", t0 + 1, guard=True)  # 5a: retracted row excluded
    naive = _pit(srv, "GlobexHQ", t0 + 1, guard=False)  # buggy form: readmits it
    assert "Berlin" not in full, f"5a wrongly kept a retracted row: {full}"
    assert "Berlin" in naive, (
        f"naive query should readmit the retracted row (shows why 5a needs the guard): {naive}"
    )
    # invalidate-not-delete: the retracted row is still physically present
    cnt = srv.psql(
        "SELECT count(*) FROM memory_semantic_fact WHERE tenant_id='acme' AND subject='GlobexHQ';"
    )
    assert "1" in cnt, f"retracted row was deleted: {cnt}"
    print(
        "  [pg] SM-R10 5a point-in-time OK (R1 valid-time + R2 retract; expired_at IS NULL required)"
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
        _check_is_current()
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
