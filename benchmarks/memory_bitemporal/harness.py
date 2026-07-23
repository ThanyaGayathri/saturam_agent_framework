"""Scenario harness (SM-R10).

Applies each scenario's ops to a store, runs its probes, and scores them against the
timeline-derived ground truth. Quality metric = **point-in-time reconstruction accuracy** =
fraction of probes whose returned fact equals the expected fact. Reported overall and split by
correction / bi-temporal probes so Option A vs Option B is directly comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .models import BiTemporalEdge
from .scenarios import Op, Probe, Scenario
from .store import BiTemporalEdgeStore


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


@dataclass
class ScenarioResult:
    name: str
    passed: int
    total: int
    failures: List[Dict[str, object]] = field(default_factory=list)


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
        for p in sc.probes:
            ok, got = run_probe(store, p)
            if ok:
                passed += 1
            else:
                failures.append(
                    {
                        "probe": f"{p.subject}.{p.predicate}@{p.t}" + (f"|tt={p.tt}" if p.tt else ""),
                        "expected": p.expect,
                        "got": got,
                        "note": p.note,
                    }
                )
        result.scenarios.append(ScenarioResult(sc.name, passed, len(sc.probes), failures))
    if hasattr(store, "close"):
        store.close()  # type: ignore[attr-defined]
    return result
