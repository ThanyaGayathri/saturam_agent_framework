"""Forensic dump: what Graphiti actually leaves in the graph after a supersede.

Confirms the root cause of the run_graphiti.py failures at the row level rather
than inferring it from the score. Run after run_graphiti.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from graphiti_core.driver.neo4j_driver import Neo4jDriver

from benchmarks.memory_bitemporal.scenarios import JAN, JUN, SEP
from benchmarks.memory_graph_engine.harness.graphiti_store import (
    GraphitiBiTemporalStore,
)


def _f(v):
    if v is None:
        return "NULL"
    return v.to_native().astimezone(timezone.utc).strftime("%Y-%m-%d")


async def main() -> int:
    driver = Neo4jDriver(
        uri=os.getenv("BT_NEO4J_URI", "bolt://localhost:7688"),
        user=os.getenv("BT_NEO4J_USER", "neo4j"),
        password=os.getenv("BT_NEO4J_PASSWORD", "bitemporal"),
    )
    store = GraphitiBiTemporalStore(driver)
    await store.reset()

    print("Scenario: three_way_chain  (NYC@Jan -> LA@Jun -> SF@Sep)")
    print(
        "SM-R10 expects 5 rows after the run (3 current + 2 bounded history versions).\n"
    )
    for obj, tv in (("NYC", JAN), ("LA", JUN), ("SF", SEP)):
        await store.assert_fact("user", "lives_in", obj, "u1", tv, tv)

    rows, _, _ = await driver.execute_query(
        "MATCH (s:Entity)-[e:RELATES_TO]->(o:Entity) "
        "WHERE e.group_id='smr2' AND e.name='lives_in' "
        "RETURN o.name AS obj, e.valid_at AS va, e.invalid_at AS ia, "
        "       e.created_at AS ca, e.expired_at AS ea ORDER BY e.valid_at"
    )
    print(f"  rows stored by Graphiti: {len(rows)}")
    print(
        f"  {'object':<6} {'valid_at':<12} {'invalid_at':<12} {'created_at':<12} {'expired_at':<12}"
    )
    for r in rows:
        print(
            f"  {r['obj']:<6} {_f(r['va']):<12} {_f(r['ia']):<12} "
            f"{_f(r['ca']):<12} {_f(r['ea']):<12}"
        )

    print()
    print("  Reading 'who did user live with in Feb 2024?' (5a, current belief):")
    got = await store.query_valid_at("user", "lives_in", "u1", 1_706_745_600)
    print(f"    -> {got}   (SM-R10 ground truth: ['NYC'])")

    print()
    print("Interpretation:")
    print("  Every superseded row carries expired_at != NULL, so it is no longer")
    print("  current belief -- and no bounded replacement version was inserted to")
    print("  carry the [Jan, Jun) window. The past is therefore unreachable from 5a.")
    print("  SM-R10's expire-and-reinsert exists precisely to keep that window.")

    await driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
