"""Run the SM-R10 bi-temporal scenario benchmark across all backends.

Edge backends (all share the SPEC SS6 rule; they differ in WHERE that rule executes):
* reference     -- in-memory, Python rule (Option A, executable spec; always runs)
* single_time   -- Option B baseline (created/updated only; destructive correction)
* postgres      -- Python rule over real SQL storage (skipped if unreachable)
* neo4j         -- Python rule over real Cypher storage (skipped if unreachable)
* postgres_sql  -- **SQL-native**: bt_upsert_edge() + the SPEC SS7 5a/5b SQL predicates
* neo4j_cypher  -- **Cypher-native**: graph_schema.cypher R1/R2 + the 5a/5b Cypher predicates

The last two are the equivalence test that matters for downstream: they exercise the artifacts
SM-G4/G5/X11 actually ship, rather than the Python rule those artifacts are documented against.
``equivalence_report`` compares their per-probe return values to the reference.

Also scores the 5c vector/episodic-tier filter (SPEC SS9) -- reference always, Postgres when
reachable -- including the rejected bare predicate as a scored counterexample.

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
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from .adapters.reference_store import ReferenceEdgeStore
from .adapters.single_time_store import SingleTimeEdgeStore
from .guardrail import GuardrailResult
from .harness import (
    BackendResult,
    VectorBackendResult,
    equivalence_report,
    run_backend,
    run_vector_backend,
)
from .scenarios import build_scenarios, build_vector_scenarios
from .store import BiTemporalEdgeStore
from .vector_tier import ReferenceVectorTierStore, VectorTierStore

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


def _pg_sql_native_factory() -> BiTemporalEdgeStore:
    from .adapters.sql_native_store import SqlNativeEdgeStore

    return SqlNativeEdgeStore(PG_DSN)


def _neo4j_cypher_native_factory() -> BiTemporalEdgeStore:
    from .adapters.cypher_native_store import CypherNativeEdgeStore

    return CypherNativeEdgeStore(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)


def _pg_vector_factory() -> VectorTierStore:
    from .vector_tier import PostgresVectorTierStore

    return PostgresVectorTierStore(PG_DSN)


_T = TypeVar("_T")


def _try(name: str, factory: Callable[[], _T]) -> Optional[Callable[[], _T]]:
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
    for name, factory in [
        ("postgres", _pg_factory),
        ("neo4j", _neo4j_factory),
        ("postgres_sql", _pg_sql_native_factory),
        ("neo4j_cypher", _neo4j_cypher_native_factory),
    ]:
        ok = _try(name, factory)
        if ok is not None:
            backends.append((name, ok))

    results: List[BackendResult] = []
    for name, factory in backends:
        t0 = time.perf_counter()
        res = run_backend(name, factory, scenarios)
        dt = (time.perf_counter() - t0) * 1000
        results.append(res)
        print(f"  {name:14s} accuracy={res.accuracy:6.1%} ({res.passed}/{res.total})  [{dt:7.1f} ms]")

    # Equivalence: do the SHIPPED SQL/Cypher artifacts return what the Python reference returns?
    equiv = equivalence_report(results, baseline="reference")
    print("\nEquivalence vs reference (per-probe return values):")
    for name, rep in equiv.items():
        mark = "OK" if rep["agree"] == rep["total"] else "DIVERGES"
        print(f"  {name:14s} {rep['agree']}/{rep['total']} probes identical  [{mark}]")
        diffs = rep["disagreements"]
        if isinstance(diffs, list):
            for d in diffs:
                print(f"      {d}")
    native_missing = [n for n in ("postgres_sql", "neo4j_cypher") if n not in equiv]
    if native_missing:
        print(f"  [not measured] {', '.join(native_missing)} -- backend unreachable in this run")

    # 5c -- vector/episodic-tier filter (SPEC SS9).
    vector_scenarios = build_vector_scenarios()
    vec_backends: List[Tuple[str, Callable[[], VectorTierStore]]] = [("reference", ReferenceVectorTierStore)]
    pg_vec = _try("postgres_vector", _pg_vector_factory)
    if pg_vec is not None:
        vec_backends.append(("postgres", pg_vec))

    vec_results: List[VectorBackendResult] = []
    n_filter_probes = sum(len(s.probes) for s in vector_scenarios)
    print(f"\n5c vector-tier filter: {len(vector_scenarios)} scenarios | {n_filter_probes} probes")
    for name, vfactory in vec_backends:
        vres = run_vector_backend(name, vfactory, vector_scenarios)
        vec_results.append(vres)
        print(
            f"  {name:14s} ratified={vres.accuracy:6.1%} ({vres.passed}/{vres.total})"
            f"   bare-predicate={vres.bare_accuracy:6.1%} ({vres.bare_passed}/{vres.total})"
        )

    # SPEC 6.1 -- the append-only guardrail is scored, not asserted.
    from .guardrail import check_append_only

    guard = check_append_only(PG_DSN)
    if guard.skipped:
        print(f"\nAppend-only guardrail (SPEC 6.1): [skip] {guard.skipped}")
    else:
        print(f"\nAppend-only guardrail (SPEC 6.1): {guard.passed}/{guard.total}")
        for f in guard.failures:
            print(f"      {f}")

    from .extraction import evaluate_rule_based

    ext_passed, ext_total = evaluate_rule_based()
    print(f"\nRule-based valid-time extraction: {ext_passed}/{ext_total} = {ext_passed / ext_total:.0%}")

    _write_reports(results, scenarios, (ext_passed, ext_total), equiv, vec_results, vector_scenarios, guard)


def _write_reports(  # type: ignore[no-untyped-def]
    results: List[BackendResult],
    scenarios,
    extraction: Tuple[int, int],
    equiv: Dict[str, Dict[str, object]],
    vec_results: List[VectorBackendResult],
    vector_scenarios,
    guard: GuardrailResult,
) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    measured_native = [n for n in ("postgres_sql", "neo4j_cypher") if n in equiv]
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
        "equivalence_vs_reference": equiv,
        "native_paths_measured": measured_native,
        "append_only_guardrail": {
            "passed": guard.passed,
            "total": guard.total,
            "failures": guard.failures,
            "skipped": guard.skipped,
        },
        "vector_tier_5c": {
            "n_scenarios": len(vector_scenarios),
            "backends": {
                v.backend: {
                    "ratified_predicate": {"passed": v.passed, "total": v.total, "accuracy": v.accuracy},
                    "bare_predicate_counterexample": {
                        "passed": v.bare_passed,
                        "total": v.total,
                        "accuracy": v.bare_accuracy,
                        "failures": v.bare_failures,
                    },
                    "failures": v.failures,
                }
                for v in vec_results
            },
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
    lines.append("## Equivalence vs the Python reference (per-probe return values)")
    lines.append("")
    lines.append("| Backend | Probes identical | Verdict |")
    lines.append("|---|---|---|")
    for name, rep in equiv.items():
        verdict = "equivalent" if rep["agree"] == rep["total"] else "DIVERGES"
        lines.append(f"| {name} | {rep['agree']}/{rep['total']} | {verdict} |")
    if not measured_native:
        lines.append("")
        lines.append(
            "**Native SQL/Cypher paths were NOT measured in this run** (backends unreachable). "
            "The `postgres`/`neo4j` rows above share the Python rule from `store.py`; they are "
            "three storage layers under one rule, not three independent derivations."
        )

    lines.append("")
    lines.append("## Append-only guardrail (SPEC SS6.1)")
    lines.append("")
    if guard.skipped:
        lines.append(f"Skipped: {guard.skipped}")
    else:
        lines.append(f"{guard.passed}/{guard.total} cases behaved as required.")
        for f in guard.failures:
            lines.append(f"- FAIL {f}")

    lines.append("")
    lines.append("## 5c vector/episodic-tier filter (SPEC SS9)")
    lines.append("")
    lines.append("| Backend | Ratified predicate | Bare predicate (rejected) |")
    lines.append("|---|---|---|")
    for v in vec_results:
        lines.append(
            f"| {v.backend} | {v.accuracy:.1%} ({v.passed}/{v.total}) "
            f"| {v.bare_accuracy:.1%} ({v.bare_passed}/{v.total}) |"
        )

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
