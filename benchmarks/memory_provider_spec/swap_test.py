"""Swap test (SM-R11): prove the MemoryProvider seam is plug-and-play.

The proof is structural: ONE consumer function (``consumer_workload``) is run
verbatim against providers built from different configs. Swapping the backend
(``co_located`` Mode A -> ``external_stub`` Mode B) is a one-line config change;
the consumer code never branches on provider type. If the seam leaked, the same
function could not drive both.

Deterministic and offline (no LLM / network / randomness), so it is CI-safe.

Run:  python -m benchmarks.memory_provider_spec.swap_test
Exit: 0 on all-pass, 1 on any failure. Writes results/swap_results.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Dict, List

from benchmarks.memory_provider_spec import adapters  # noqa: F401  (registers providers)
from benchmarks.memory_provider_spec.interface import (
    DeploymentMode,
    MemoryEvent,
    MemoryProvider,
    MemoryProviderConfig,
    MemoryQuery,
    MemoryTier,
    Scope,
    build_memory_provider,
)

# Two configs that differ ONLY in provider + mode. Everything downstream is identical.
CONFIGS = [
    MemoryProviderConfig(provider="co_located", mode=DeploymentMode.CO_LOCATED),
    MemoryProviderConfig(provider="external_stub", mode=DeploymentMode.EXTERNAL),
]

_SAMPLE = [
    ("Acme migrated its vector store to pgvector last quarter", ["pgvector", "store"]),
    ("Acme migrated its vector store to pgvector last quarter", ["pgvector", "store"]),  # duplicate
    ("The platform team standardized on Neo4j for the graph tier", ["neo4j", "graph"]),
    ("Nightly jobs consolidate episodic memory into semantic facts", ["consolidation"]),
]


def _events(scope: Scope) -> List[MemoryEvent]:
    return [MemoryEvent(content=text, scope=scope, topics=topics) for text, topics in _SAMPLE]


# =============================================================================
# The ONE consumer workload — identical for every provider (this is the proof)
# =============================================================================


def consumer_workload(provider: MemoryProvider, scope: Scope) -> Dict[str, Any]:
    """Exercise the full seam through the abstract type only. No provider-specific code."""
    receipt = provider.write(scope, _events(scope))
    fused = provider.read(scope, MemoryQuery(text="pgvector store", tiers=[MemoryTier.EPISODIC], limit=5))
    tier_hits = provider.read_tier(scope, MemoryTier.EPISODIC, MemoryQuery(text="neo4j graph", limit=5))
    report = provider.consolidate(scope)
    return {
        "accepted": receipt.accepted,
        "read_records": len(fused.records),
        "read_top": fused.records[0].content if fused.records else None,
        "fusion_method": fused.fusion.get("method"),
        "read_tier_hits": len(tier_hits),
        "episodic_scanned": report.episodic_scanned,
        "substrates": {s.value: wired for s, wired in provider.substrates().items()},
    }


async def aconsumer_workload(provider: MemoryProvider, scope: Scope) -> Dict[str, Any]:
    """Async twin of :func:`consumer_workload` — same seam, async variants only."""
    receipt = await provider.awrite(scope, _events(scope))
    fused = await provider.aread(scope, MemoryQuery(text="pgvector store", tiers=[MemoryTier.EPISODIC], limit=5))
    report = await provider.aconsolidate(scope)
    return {
        "accepted": receipt.accepted,
        "read_records": len(fused.records),
        "episodic_scanned": report.episodic_scanned,
    }


# =============================================================================
# Checks
# =============================================================================


class Result:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks)


def run() -> Result:
    result = Result()
    scope = Scope(tenant_id="acme", agent_id="a1", user_id="u1")

    # 1. Config-only swap: identical consumer drives both providers.
    observed: Dict[str, Dict[str, Any]] = {}
    for config in CONFIGS:
        provider = build_memory_provider(config)
        obs = consumer_workload(provider, scope)
        observed[config.provider] = obs
        result.check(
            "{p}: write+read+consolidate via one seam".format(p=config.provider),
            obs["accepted"] == len(_SAMPLE) and obs["read_records"] >= 1 and obs["read_tier_hits"] >= 1,
            "accepted={a} read={r} tier={t}".format(a=obs["accepted"], r=obs["read_records"], t=obs["read_tier_hits"]),
        )

    # 2. Same call site, different backends -> both produced a roundtrip.
    result.check(
        "swap is config-only (no call-site change)",
        all(o["read_top"] is not None for o in observed.values()),
        "consumer_workload run verbatim on {n} providers".format(n=len(observed)),
    )

    # 3. Cross-substrate dedup: one episode matched by BOTH substrates -> one fused record.
    dedup_scope = Scope(tenant_id="acme", user_id="dd")
    co = build_memory_provider(CONFIGS[0])
    co.write(
        dedup_scope,
        [MemoryEvent(content="Acme runs pgvector and neo4j together", scope=dedup_scope, topics=["pgvector", "neo4j"])],
    )
    dup_read = co.read(dedup_scope, MemoryQuery(text="pgvector neo4j", tiers=[MemoryTier.EPISODIC], limit=10))
    both_substrates = dup_read.fusion.get("vector_hits", 0) >= 1 and dup_read.fusion.get("graph_hits", 0) >= 1
    result.check(
        "co_located: fusion folds a cross-substrate match to one record",
        len(dup_read.records) == 1 and both_substrates,
        "method={m} fused={n} vector_hits={v} graph_hits={g}".format(
            m=dup_read.fusion.get("method"),
            n=len(dup_read.records),
            v=dup_read.fusion.get("vector_hits"),
            g=dup_read.fusion.get("graph_hits"),
        ),
    )

    # 3b. SM-R8: fusion is config-selectable — WEIGHTED is the default, RRF on request.
    fw = build_memory_provider(MemoryProviderConfig(provider="co_located"))
    fr = build_memory_provider(MemoryProviderConfig(provider="co_located", fusion={"method": "rrf", "k": 60}))
    for prov in (fw, fr):
        prov.write(
            dedup_scope,
            [MemoryEvent(content="Acme runs pgvector and neo4j", scope=dedup_scope, topics=["pgvector", "neo4j"])],
        )
    fw_slice = fw.read(dedup_scope, MemoryQuery(text="pgvector neo4j", tiers=[MemoryTier.EPISODIC], limit=5))
    fr_slice = fr.read(dedup_scope, MemoryQuery(text="pgvector neo4j", tiers=[MemoryTier.EPISODIC], limit=5))
    result.check(
        "SM-R8: weighted is the default fusion; RRF selectable by config",
        fw_slice.fusion.get("method") == "weighted"
        and "vector_weight" in fw_slice.fusion
        and fr_slice.fusion.get("method") == "rrf",
        "default={d} rrf-config={r}".format(d=fw_slice.fusion.get("method"), r=fr_slice.fusion.get("method")),
    )

    # 4. Tenant isolation: another tenant sees nothing.
    other = Scope(tenant_id="globex", user_id="u9")
    leak = co.read(other, MemoryQuery(text="pgvector store", tiers=[MemoryTier.EPISODIC]))
    result.check(
        "tenant isolation", len(leak.records) == 0, "cross-tenant records leaked={n}".format(n=len(leak.records))
    )

    # 5. Bi-temporal (SM-R10) seam: invalidate-not-delete + as_of point-in-time read.
    # Explicit occurred_at (t_valid=100) keeps the probe deterministic regardless of
    # the global write clock.
    bt_scope = Scope(tenant_id="acme", user_id="bt")
    bt = build_memory_provider(CONFIGS[0])
    r = bt.write(
        bt_scope,
        [MemoryEvent(content="Acme HQ is in Boston", scope=bt_scope, topics=["hq", "boston"], occurred_at=100)],
    )
    eid = r.event_ids[0]
    before = bt.read(bt_scope, MemoryQuery(text="hq boston", tiers=[MemoryTier.EPISODIC]))
    bt._graph.invalidate(bt_scope, eid, t_invalid=1000)  # type: ignore[attr-defined]  # SM-R10 SCD-2
    after_now = bt.read(bt_scope, MemoryQuery(text="hq boston", tiers=[MemoryTier.EPISODIC], as_of=2000))
    after_past = bt.read(bt_scope, MemoryQuery(text="hq boston", tiers=[MemoryTier.EPISODIC], as_of=500))
    result.check(
        "bi-temporal invalidate-not-delete + as_of upper bound",
        len(before.records) == 1 and len(after_now.records) == 0 and len(after_past.records) == 1,
        "before={b} as_of_after={a} as_of_before={p}".format(
            b=len(before.records), a=len(after_now.records), p=len(after_past.records)
        ),
    )

    # 5b. SM-R10 two-sided "true at T": a future-dated record must NOT leak before t_valid.
    fd_scope = Scope(tenant_id="acme", user_id="fd")
    fd = build_memory_provider(CONFIGS[0])
    fd.write(
        fd_scope,
        [
            MemoryEvent(
                content="Acme opens a Tokyo office", scope=fd_scope, topics=["tokyo", "office"], occurred_at=5000
            )
        ],
    )
    q = "tokyo office"
    before_valid = fd.read(fd_scope, MemoryQuery(text=q, tiers=[MemoryTier.EPISODIC], as_of=1000))
    after_valid = fd.read(fd_scope, MemoryQuery(text=q, tiers=[MemoryTier.EPISODIC], as_of=6000))
    result.check(
        "SM-R10 two-sided: future-dated record excluded before t_valid",
        len(before_valid.records) == 0 and len(after_valid.records) == 1,
        "as_of<t_valid={b} as_of>t_valid={a}".format(b=len(before_valid.records), a=len(after_valid.records)),
    )

    # 5b'. SM-R10 bounded interval: BOTH bounds set on one record ([t_valid, t_invalid)).
    # Exercises both sides of _valid_at simultaneously (before-valid, within, after-invalid).
    bi_scope = Scope(tenant_id="acme", user_id="bi")
    bi = build_memory_provider(CONFIGS[0])
    rb = bi.write(
        bi_scope,
        [MemoryEvent(content="Acme leased floor 3", scope=bi_scope, topics=["lease", "floor"], occurred_at=100)],
    )
    bi._graph.invalidate(bi_scope, rb.event_ids[0], t_invalid=300)  # type: ignore[attr-defined]  # SM-R10 SCD-2
    biq = "lease floor"
    n_before = len(bi.read(bi_scope, MemoryQuery(text=biq, tiers=[MemoryTier.EPISODIC], as_of=50)).records)
    n_within = len(bi.read(bi_scope, MemoryQuery(text=biq, tiers=[MemoryTier.EPISODIC], as_of=200)).records)
    n_after = len(bi.read(bi_scope, MemoryQuery(text=biq, tiers=[MemoryTier.EPISODIC], as_of=400)).records)
    result.check(
        "SM-R10 bounded interval [t_valid, t_invalid): both bounds enforced",
        n_before == 0 and n_within == 1 and n_after == 0,
        "before_valid={b} within={w} after_invalid={a}".format(b=n_before, w=n_within, a=n_after),
    )

    # 5b''. SM-R10 write model: invalidate is expire-and-reinsert (SCD-2), NOT in-place, and a
    # second invalidate does not clobber the first's already-set bound (the P3/5a bug).
    sc_scope = Scope(tenant_id="acme", user_id="scd2")
    sc = build_memory_provider(CONFIGS[0])
    re = sc.write(
        sc_scope, [MemoryEvent(content="Acme CEO is Ada", scope=sc_scope, topics=["ceo", "ada"], occurred_at=10)]
    )
    eid2 = re.event_ids[0]
    sc._graph.invalidate(sc_scope, eid2, t_invalid=100)  # type: ignore[attr-defined]
    sc._graph.invalidate(sc_scope, eid2, t_invalid=60)  # type: ignore[attr-defined]  # second, tighter
    rows = [r for r in sc._graph._bucket(sc_scope) if r.id == eid2]  # type: ignore[attr-defined]
    expired = [r for r in rows if r.expired_at is not None]
    current = [r for r in rows if r.expired_at is None]
    # Two invalidations -> 2 expired versions + 1 current belief; nothing deleted.
    # Original row untouched in place (its t_invalid stays None); current bound is tightest (60).
    original_untouched = any(r.t_invalid is None and r.expired_at is not None for r in rows)
    result.check(
        "SM-R10 write is expire-and-reinsert (SCD-2), no in-place clobber",
        len(current) == 1 and current[0].t_invalid == 60 and original_untouched and len(expired) == 2,
        "current={c} expired={e} bound={b} original_untouched={o}".format(
            c=len(current), e=len(expired), b=current[0].t_invalid if current else None, o=original_untouched
        ),
    )

    # 5c. SM-R7 reference consolidate writes the distilled fact to BOTH substrates.
    cs_scope = Scope(tenant_id="acme", user_id="cs")
    cs = build_memory_provider(CONFIGS[0])
    cs.write(
        cs_scope,
        [MemoryEvent(content="Acme standardized on neo4j for graph memory", scope=cs_scope, topics=["neo4j", "graph"])],
    )
    rep = cs.consolidate(cs_scope)
    in_vector = cs._vector.search(cs_scope, MemoryQuery(text="neo4j graph", tiers=[MemoryTier.SEMANTIC], limit=5))  # type: ignore[attr-defined]
    in_graph = cs._graph.traverse(cs_scope, MemoryQuery(text="neo4j graph", tiers=[MemoryTier.SEMANTIC], limit=5))  # type: ignore[attr-defined]
    result.check(
        "SM-R7: consolidate writes the semantic fact to both substrates",
        rep.semantic_written >= 1 and len(in_vector) >= 1 and len(in_graph) >= 1,
        "written={w} vector={v} graph={g}".format(w=rep.semantic_written, v=len(in_vector), g=len(in_graph)),
    )

    # 6. Sync/async parity of the seam.
    async_obs = {c.provider: asyncio.run(aconsumer_workload(build_memory_provider(c), scope)) for c in CONFIGS}
    result.check(
        "sync + async variants both satisfy the contract",
        all(o["accepted"] == len(_SAMPLE) and o["read_records"] >= 1 for o in async_obs.values()),
        "async accepted+read verified for {n} providers".format(n=len(async_obs)),
    )

    _write_results(result, observed, async_obs)
    return result


def _write_results(result: Result, sync_obs: Dict[str, Any], async_obs: Dict[str, Any]) -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "task": "SM-R11 swap test",
        "deterministic": True,
        "providers": [c.provider for c in CONFIGS],
        "checks": result.checks,
        "passed": result.passed,
        "sync_observations": sync_obs,
        "async_observations": async_obs,
    }
    with open(os.path.join(out_dir, "swap_results.json"), "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def main() -> int:
    result = run()
    print("SM-R11 MemoryProvider swap test")
    for check in result.checks:
        mark = "PASS" if check["ok"] else "FAIL"
        suffix = "  ({d})".format(d=check["detail"]) if check["detail"] else ""
        print("  [{mark}] {name}{suffix}".format(mark=mark, name=check["name"], suffix=suffix))
    print("Result:", "PASS" if result.passed else "FAIL")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
