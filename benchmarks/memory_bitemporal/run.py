"""Run the SM-R10 bi-temporal scenario benchmark across all backends.

Backends:
* reference   -- in-memory bi-temporal (Option A, executable spec; always runs)
* single_time -- Option B baseline (created/updated only; destructive correction)
* postgres    -- real SQL against schema.sql (skipped if unreachable)
* neo4j       -- real Cypher (skipped if unreachable)

Writes a results JSON + a summary markdown to results/, and prints a comparison table.

Usage:
    PYTHONPATH=libs/agno .venvs/bitemporal/bin/python -m benchmarks.memory_bitemporal.run

Env overrides: BT_PG_DSN, BT_NEO4J_URI / BT_NEO4J_USER / BT_NEO4J_PASSWORD.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .adapters.reference_store import ReferenceEdgeStore
from .adapters.single_time_store import SingleTimeEdgeStore
from .harness import BackendResult, run_backend
from .scenarios import build_scenarios
from .store import BiTemporalEdgeStore

RESULTS_DIR = Path(__file__).resolve().parent / "results"
PG_DSN = os.environ.get("BT_PG_DSN", "postgresql://bt:bt@localhost:5533/bitemporal")
NEO4J_URI = os.environ.get("BT_NEO4J_URI", "bolt://localhost:7688")
NEO4J_USER = os.environ.get("BT_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("BT_NEO4J_PASSWORD", "bitemporal")


def _pg_factory() -> BiTemporalEdgeStore:
    from .adapters.postgres_store import PostgresEdgeStore

    return PostgresEdgeStore(PG_DSN)


def _neo4j_factory() -> BiTemporalEdgeStore:
    from .adapters.neo4j_store import Neo4jEdgeStore

    return Neo4jEdgeStore(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)


def _try(name: str, factory: Callable[[], BiTemporalEdgeStore]) -> Optional[Callable[[], BiTemporalEdgeStore]]:
    """Return the factory if the backend is reachable, else None (with a printed note)."""
    try:
        s = factory()
        if hasattr(s, "close"):
            s.close()  # type: ignore[attr-defined]
        return factory
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"  [skip] {name} unreachable: {exc}")
        return None


def main() -> None:
    scenarios = build_scenarios()
    total_probes = sum(len(s.probes) for s in scenarios)
    print(f"Scenarios: {len(scenarios)} | probes: {total_probes}")

    backends: List[Tuple[str, Callable[[], BiTemporalEdgeStore]]] = [
        ("reference", ReferenceEdgeStore),
        ("single_time", SingleTimeEdgeStore),
    ]
    for name, factory in [("postgres", _pg_factory), ("neo4j", _neo4j_factory)]:
        ok = _try(name, factory)
        if ok is not None:
            backends.append((name, ok))

    results: List[BackendResult] = []
    for name, factory in backends:
        t0 = time.perf_counter()
        res = run_backend(name, factory, scenarios)
        dt = (time.perf_counter() - t0) * 1000
        results.append(res)
        print(f"  {name:12s} accuracy={res.accuracy:6.1%} ({res.passed}/{res.total})  [{dt:6.1f} ms]")

    from .extraction import evaluate_rule_based

    ext_passed, ext_total = evaluate_rule_based()
    print(f"\nRule-based valid-time extraction: {ext_passed}/{ext_total} = {ext_passed / ext_total:.0%}")

    _write_reports(results, scenarios, (ext_passed, ext_total))


def _write_reports(results: List[BackendResult], scenarios, extraction: Tuple[int, int]) -> None:  # type: ignore[no-untyped-def]
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload: Dict[str, object] = {
        "run": stamp,
        "n_scenarios": len(scenarios),
        "extraction_rule_based": {"passed": extraction[0], "total": extraction[1]},
        "backends": {
            r.backend: {
                "accuracy": r.accuracy,
                "passed": r.passed,
                "total": r.total,
                "scenarios": {
                    s.name: {"passed": s.passed, "total": s.total, "failures": s.failures} for s in r.scenarios
                },
            }
            for r in results
        },
    }
    json_path = RESULTS_DIR / f"results_{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2))

    lines = [f"# Bi-temporal benchmark results -- {stamp}", ""]
    lines.append(f"Rule-based valid-time extraction: {extraction[0]}/{extraction[1]}")
    lines.append("")
    lines.append("| Backend | Accuracy | Passed/Total |")
    lines.append("|---|---|---|")
    for r in results:
        lines.append(f"| {r.backend} | {r.accuracy:.1%} | {r.passed}/{r.total} |")
    lines.append("")
    lines.append("## Per-scenario (accuracy by backend)")
    lines.append("")
    header = "| Scenario | " + " | ".join(r.backend for r in results) + " |"
    lines.append(header)
    lines.append("|---" * (len(results) + 1) + "|")
    for i, sc in enumerate(scenarios):
        row = [sc.name]
        for r in results:
            s = r.scenarios[i]
            row.append(f"{s.passed}/{s.total}")
        lines.append("| " + " | ".join(row) + " |")
    md_path = RESULTS_DIR / f"summary_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {json_path.name} and {md_path.name}")


if __name__ == "__main__":
    main()
