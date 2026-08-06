"""Single-time baseline (Option B) -- the current seed behavior, for contrast.

Models what ``UserMemory`` does today: only created_at/updated_at, one system-time axis, no
valid time and no invalidation. A contradiction overwrites in place (destructive), so the
past is lost. This adapter deliberately CANNOT reconstruct point-in-time history; it exists so
the benchmark measures the gap between Option A (bi-temporal) and Option B rather than
asserting it. It intentionally ignores the ``t`` argument to ``query_valid_at`` -- a single-
time store has only "the current value".
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..models import BiTemporalEdge, EdgeOutcome
from ..store import BiTemporalEdgeStore


class SingleTimeEdgeStore(BiTemporalEdgeStore):
    """Overwrite-on-conflict store with no valid-time axis (baseline, Option B)."""

    def __init__(self) -> None:
        # key: (user, subject, predicate) -> current object only (history discarded)
        self._current: Dict[Tuple[str, str, str], BiTemporalEdge] = {}

    # Storage primitives are present so the type is a real adapter, but upsert/query are
    # overridden to enact single-time (destructive) semantics.
    def _insert(self, edge: BiTemporalEdge) -> None:
        self._current[(edge.user_id, edge.subject_id, edge.predicate)] = edge

    def _current_edges(self, subject_id: str, predicate: str, user_id: str) -> List[BiTemporalEdge]:
        e = self._current.get((user_id, subject_id, predicate))
        return [e] if e is not None else []

    def _mark_expired(self, edge_id: str, expired_at: int) -> None:
        for key, e in list(self._current.items()):
            if e.edge_id == edge_id:
                del self._current[key]  # destructive: no audit trail

    def _all(self) -> List[BiTemporalEdge]:
        return list(self._current.values())

    def upsert_edge(self, edge: BiTemporalEdge) -> EdgeOutcome:
        key = (edge.user_id, edge.subject_id, edge.predicate)
        existed = key in self._current
        self._current[key] = edge  # overwrite in place -- previous value is gone
        return EdgeOutcome.SUPERSEDE if existed else EdgeOutcome.INSERT

    def query_all_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> List[BiTemporalEdge]:
        # A single-time store returns whatever is current, regardless of the requested time.
        return self._current_edges(subject_id, predicate, user_id)

    def query_bitemporal(self, subject_id: str, predicate: str, user_id: str, t: int, tt: int) -> List[BiTemporalEdge]:
        return self._current_edges(subject_id, predicate, user_id)
