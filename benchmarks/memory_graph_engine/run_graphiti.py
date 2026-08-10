"""Score the real graphiti-core library on SM-R10's bi-temporal scenario set.

Reuses SM-R10's scenarios and expected answers verbatim -- the ground truth is
SM-R10's, not re-derived here, so the two engines are scored on one ruler.

Run:
    PYTHONPATH=. ../../.venvs/graphengine/bin/python benchmarks/memory_graph_engine/run_graphiti.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from graphiti_core.driver.neo4j_driver import Neo4jDriver

from benchmarks.memory_bitemporal.scenarios import build_scenarios
from benchmarks.memory_graph_engine.harness.graphiti_store import (
    GraphitiBiTemporalStore,
)

URI = os.getenv("BT_NEO4J_URI", "bolt://localhost:7688")
USER = os.getenv("BT_NEO4J_USER", "neo4j")
PASSWORD = os.getenv("BT_NEO4J_PASSWORD", "bitemporal")


async def main() -> int:
    driver = Neo4jDriver(uri=URI, user=USER, password=PASSWORD)
    store = GraphitiBiTemporalStore(driver)

    results = []
    total = hits = 0

    for sc in build_scenarios():
        await store.reset()
        ids = {}
        for op in sc.ops:
            if op.kind == "assert":
                uid = await store.assert_fact(
                    op.subject,
                    op.predicate,
                    op.object,
                    op.user,
                    op.t_valid,
                    op.at,
                    edge_id=op.edge_id,
                )
                if op.edge_id:
                    ids[op.edge_id] = uid
            elif op.kind == "retract":
                await store.retract(ids.get(op.edge_id, op.edge_id), op.at)

        for pr in sc.probes:
            if pr.tt is None:
                got = await store.query_valid_at(
                    pr.subject, pr.predicate, pr.user, pr.t
                )
            else:
                got = await store.query_bitemporal(
                    pr.subject, pr.predicate, pr.user, pr.t, pr.tt
                )
            # Expected: exactly one object (or none). More than one answer is a
            # failure even if the right one is among them -- 5a must be unique.
            ok = (got == [pr.expect]) if pr.expect is not None else (got == [])
            total += 1
            hits += int(ok)
            results.append(
                {
                    "scenario": sc.name,
                    "probe": f"t={pr.t}" + (f",tt={pr.tt}" if pr.tt else ""),
                    "note": pr.note,
                    "kind": "5b" if pr.tt else "5a",
                    "expected": pr.expect,
                    "got": got,
                    "ok": ok,
                }
            )

    await driver.close()

    by_scenario = {}
    for r in results:
        s = by_scenario.setdefault(r["scenario"], {"n": 0, "ok": 0})
        s["n"] += 1
        s["ok"] += int(r["ok"])

    summary = {
        "engine": "graphiti-core (real library)",
        "backend": "neo4j:5",
        "probes": total,
        "correct": hits,
        "accuracy": round(hits / total, 4) if total else 0.0,
        "by_scenario": by_scenario,
        "detail": results,
    }

    out = Path(__file__).parent / "results" / "graphiti_bitemporal.json"
    out.write_text(json.dumps(summary, indent=2))

    print(f"graphiti-core on SM-R10 scenario set: {hits}/{total} = {hits / total:.1%}")
    print()
    for name, s in by_scenario.items():
        flag = "" if s["ok"] == s["n"] else "   <-- FAIL"
        print(f"  {name:<26} {s['ok']}/{s['n']}{flag}")
    print()
    print("Failures:")
    for r in results:
        if not r["ok"]:
            print(
                f"  [{r['kind']}] {r['scenario']:<24} {r['probe']:<20} "
                f"expected={r['expected']!r} got={r['got']!r}  ({r['note']})"
            )
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
