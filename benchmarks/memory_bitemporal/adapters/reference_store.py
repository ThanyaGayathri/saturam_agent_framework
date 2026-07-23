"""In-memory reference adapter for ``BiTemporalEdgeStore``.

Zero external dependencies (no database, no network): the deterministic, always-runnable
reference implementation and the CI-safe eval harness backend. It is the executable
specification of the bi-temporal semantics -- the Postgres and Neo4j adapters must return
identical results on the scenario set.
"""

from __future__ import annotations

from typing import Dict, List

from ..models import BiTemporalEdge
from ..store import BiTemporalEdgeStore


class ReferenceEdgeStore(BiTemporalEdgeStore):
    """List/dict-backed in-process edge store."""

    def __init__(self) -> None:
        self._edges: Dict[str, BiTemporalEdge] = {}

    def _insert(self, edge: BiTemporalEdge) -> None:
        self._edges[edge.edge_id] = edge

    def _current_edges(self, subject_id: str, predicate: str, user_id: str) -> List[BiTemporalEdge]:
        return [
            e
            for e in self._edges.values()
            if e.subject_id == subject_id and e.predicate == predicate and e.user_id == user_id and e.expired_at is None
        ]

    def _mark_expired(self, edge_id: str, expired_at: int) -> None:
        self._edges[edge_id].expired_at = expired_at

    def _all(self) -> List[BiTemporalEdge]:
        return list(self._edges.values())
