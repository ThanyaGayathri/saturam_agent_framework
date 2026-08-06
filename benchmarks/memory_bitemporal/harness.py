"""Scenario harness (SM-R10).

Applies each scenario's ops to a store, runs its probes, and scores them against the
timeline-derived ground truth. Quality metric = **point-in-time reconstruction accuracy** =
fraction of probes whose returned fact equals the expected fact. Reported overall and split by
correction / bi-temporal probes so Option A vs Option B is directly comparable.

Every probe's *returned value* is recorded (not just pass/fail) so backends can be compared to
each other directly -- see ``equivalence_report``. Two backends both scoring 100% necessarily
agree, but recording the values means a disagreement is localised to a probe instead of showing
up as two different aggregate numbers.

``run_vector_backend`` scores the 5c vector/episodic-tier filter (SPEC SS9) on the same basis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .models import BiTemporalEdge
from .scenarios import FilterProbe, Op, Probe, Scenario, VectorScenario
from .store import BiTemporalEdgeStore
from .vector_tier import VectorMemoryRow, VectorTierStore


def apply_ops(store: BiTemporalEdgeStore, ops: List[Op]) -> None:
    for op in ops:
        if op.kind == "assert":
            edge = BiTemporalEdge(
                subject_id=op.subject,
                predicate=op.predicate,
                object_id=op.object,
                user_id=op.user,
                t_valid=op.t_valid,
                created_at=op.at,
            )
            if op.edge_id:
                edge.edge_id = op.edge_id
            store.upsert_edge(edge)
        elif op.kind == "retract":
            assert op.edge_id is not None and op.at is not None
            store.retract_edge(op.edge_id, expired_at=op.at)
        else:  # pragma: no cover - guards scenario typos
            raise ValueError(f"unknown op kind: {op.kind}")


def run_probe(store: BiTemporalEdgeStore, p: Probe) -> tuple[bool, Optional[str]]:
    if p.tt is None:
        res = store.query_valid_at(p.subject, p.predicate, p.user, p.t)
        got = res.object_id if res is not None else None
    else:
        matches = store.query_bitemporal(p.subject, p.predicate, p.user, p.t, p.tt)
        got = matches[0].object_id if matches else None
    return got == p.expect, got


def probe_key(p: Probe) -> str:
    """Stable identifier for a probe, used to align results across backends.

    The tenant is part of the key: ``tenant_isolation`` probes the same subject/predicate/time
    for two different users, so omitting ``user`` collapses them onto one key and silently drops
    a probe from the equivalence comparison.
    """
    return f"{p.user}|{p.subject}.{p.predicate}@{p.t}" + (f"|tt={p.tt}" if p.tt else "")


@dataclass
class ScenarioResult:
    name: str
    passed: int
    total: int
    failures: List[Dict[str, object]] = field(default_factory=list)
    # probe_key -> value the backend actually returned (for cross-backend equivalence).
    returned: Dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class BackendResult:
    backend: str
    scenarios: List[ScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(s.passed for s in self.scenarios)

    @property
    def total(self) -> int:
        return sum(s.total for s in self.scenarios)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


def run_backend(backend: str, factory: Callable[[], BiTemporalEdgeStore], scenarios: List[Scenario]) -> BackendResult:
    """Run every scenario against a fresh/reset store. ``factory`` returns a ready store.

    If the store exposes ``reset()`` it is reused across scenarios (reset between each);
    otherwise a fresh store is built per scenario.
    """
    store = factory()
    result = BackendResult(backend=backend)
    reusable = hasattr(store, "reset")
    for sc in scenarios:
        if reusable:
            store.reset()  # type: ignore[attr-defined]
        else:
            store = factory()
        apply_ops(store, sc.ops)
        passed = 0
        failures: List[Dict[str, object]] = []
        returned: Dict[str, Optional[str]] = {}
        for p in sc.probes:
            ok, got = run_probe(store, p)
            returned[probe_key(p)] = got
            if ok:
                passed += 1
            else:
                failures.append(
                    {
                        "probe": probe_key(p),
                        "expected": p.expect,
                        "got": got,
                        "note": p.note,
                    }
                )
        result.scenarios.append(ScenarioResult(sc.name, passed, len(sc.probes), failures, returned))
    if hasattr(store, "close"):
        store.close()  # type: ignore[attr-defined]
    return result


# ---------------------------------------------------------------------- #
# Cross-backend equivalence.
# ---------------------------------------------------------------------- #


def equivalence_report(results: List[BackendResult], baseline: str = "reference") -> Dict[str, Dict[str, object]]:
    """Compare every backend's *returned values* against ``baseline``, probe by probe.

    This is the check the "backends agree" claim actually needs: matching accuracy numbers only
    show two backends are equally right about the ground truth, whereas this shows they return
    the same thing on every probe -- and names the probe when they do not.
    """
    base = next((r for r in results if r.backend == baseline), None)
    if base is None:  # pragma: no cover - misconfiguration guard
        raise ValueError(f"baseline backend {baseline!r} not in results")

    base_vals: Dict[str, Optional[str]] = {}
    for sc in base.scenarios:
        for k, v in sc.returned.items():
            base_vals[f"{sc.name}::{k}"] = v

    report: Dict[str, Dict[str, object]] = {}
    for r in results:
        if r.backend == baseline:
            continue
        agree = 0
        total = 0
        diffs: List[Dict[str, object]] = []
        for sc in r.scenarios:
            for k, v in sc.returned.items():
                full = f"{sc.name}::{k}"
                if full not in base_vals:
                    continue
                total += 1
                if base_vals[full] == v:
                    agree += 1
                else:
                    diffs.append({"probe": full, baseline: base_vals[full], r.backend: v})
        report[r.backend] = {
            "agree": agree,
            "total": total,
            "rate": (agree / total) if total else 0.0,
            "disagreements": diffs,
        }
    return report


# ---------------------------------------------------------------------- #
# 5c -- vector/episodic-tier filter scoring (SPEC SS9).
# ---------------------------------------------------------------------- #


@dataclass
class VectorBackendResult:
    backend: str
    passed: int = 0
    total: int = 0
    # Score of the REJECTED bare predicate, kept as a scored counterexample.
    bare_passed: int = 0
    failures: List[Dict[str, object]] = field(default_factory=list)
    bare_failures: List[Dict[str, object]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def bare_accuracy(self) -> float:
        return self.bare_passed / self.total if self.total else 0.0


def run_vector_backend(
    backend: str, factory: Callable[[], VectorTierStore], scenarios: List[VectorScenario]
) -> VectorBackendResult:
    """Score the 5c filter, and the rejected bare-predicate variant, on the same probes."""
    store = factory()
    res = VectorBackendResult(backend=backend)
    reusable = hasattr(store, "reset")
    for sc in scenarios:
        if reusable:
            store.reset()  # type: ignore[attr-defined]
        else:
            store = factory()
        for spec in sc.rows:
            store.add(
                VectorMemoryRow(
                    memory_id=spec.memory_id,
                    user_id=spec.user,
                    memory=spec.memory,
                    created_at=spec.created_at,
                    valid_from=spec.valid_from,
                    valid_to=spec.valid_to,
                )
            )
        # SS9.1 propagation: the graph invalidated the fact, so the derived copy records it.
        for spec in sc.rows:
            if spec.propagate_valid_to is not None:
                store.set_valid_to(spec.memory_id, spec.propagate_valid_to)

        for p in sc.probes:
            res.total += 1
            got = store.recall_at(p.user, p.t)
            if got == sorted(p.expect):
                res.passed += 1
            else:
                res.failures.append(
                    {"scenario": sc.name, "probe": _filter_probe_key(p), "expected": sorted(p.expect), "got": got}
                )
            bare = store.recall_at_bare(p.user, p.t)
            if bare == sorted(p.expect):
                res.bare_passed += 1
            else:
                res.bare_failures.append(
                    {"scenario": sc.name, "probe": _filter_probe_key(p), "expected": sorted(p.expect), "got": bare}
                )
    if hasattr(store, "close"):
        store.close()  # type: ignore[attr-defined]
    return res


def _filter_probe_key(p: FilterProbe) -> str:
    return f"{p.user}@{p.t}"
