"""Bi-temporal edge store abstraction (SM-R10).

``BiTemporalEdgeStore`` is the plug-and-play seam SM-G4/G5/X11 program against. Call sites
depend on this abstract type only; concrete backends (reference in-memory, Postgres, Neo4j)
are adapters selected by configuration -- swapping one for another touches no call site.

The invalidation rule (R1 supersede / R2 retract / NOOP) and the point-in-time query
semantics live **once** in this base, expressed over four abstract storage primitives. Every
adapter therefore shares identical bi-temporal semantics; adapters implement only storage.

Both sync and async variants of every public method are provided. The default async
implementations delegate to the sync path via ``asyncio.to_thread`` so inherently-sync
adapters (reference, psycopg) get real async variants; natively-async adapters (asyncpg,
neo4j async driver) override the async publics directly.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from egpt.utils.dttm import now_epoch_s

from .models import BiTemporalEdge, EdgeOutcome, upper


class BiTemporalEdgeStore(ABC):
    """Abstract bi-temporal edge store. Adapters implement the storage primitives below."""

    # ------------------------------------------------------------------ #
    # Storage primitives -- the only surface an adapter must implement.
    # ------------------------------------------------------------------ #
    @abstractmethod
    def _insert(self, edge: BiTemporalEdge) -> None:
        """Persist a new edge row verbatim."""

    @abstractmethod
    def _current_edges(self, subject_id: str, predicate: str, user_id: str) -> List[BiTemporalEdge]:
        """Return currently-believed edges (``expired_at IS NULL``) for the identity triple."""

    @abstractmethod
    def _mark_expired(self, edge_id: str, expired_at: int) -> None:
        """Set an edge's transaction-time end (retraction / error correction)."""

    @abstractmethod
    def _all(self) -> List[BiTemporalEdge]:
        """Return every edge (all axes), for full bi-temporal reconstruction and audit."""

    # ------------------------------------------------------------------ #
    # Public API (sync) -- the seam SM-G4/G5/X11 consume.
    # ------------------------------------------------------------------ #
    def upsert_edge(self, edge: BiTemporalEdge) -> EdgeOutcome:
        """Apply the invalidate-not-delete rule for an incoming assertion (SCD-2 versioning).

        * NOOP      -- an identical fact is already believed over ``edge``'s validity start.
        * SUPERSEDE -- a conflicting current fact whose valid window *contains* ``edge.t_valid``
                       is invalidated: expire its current version in transaction-time and
                       insert a bounded version with ``t_invalid = edge.t_valid``. Nothing is
                       deleted or mutated in place.
        * back-date -- a conflicting current fact starts *after* ``edge``: bound ``edge``'s own
                       ``t_invalid`` to that fact's ``t_valid`` (insert into history).
        * INSERT    -- no conflict; first assertion of this fact.

        Invalidation is transaction-time-versioned (expire-and-reinsert), not an in-place
        ``t_invalid`` write, so *past beliefs about validity* stay reconstructable (5b). A
        conflicting fact whose window ends at/before ``edge.t_valid`` is left untouched (its
        history is already correct). See SPEC.md for the rejected in-place variant, which the
        P3 scenario eval showed fails both the multi-step-chain (5a) and belief-evolution (5b)
        cases.
        """
        current = self._current_edges(edge.subject_id, edge.predicate, edge.user_id)
        assert edge.t_valid is not None and edge.created_at is not None  # normalized in __post_init__

        # NOOP: identical fact already believed for a window covering edge.t_valid.
        for existing in current:
            if existing.object_id == edge.object_id and existing.valid_at(edge.t_valid):
                return EdgeOutcome.NOOP

        superseded = False
        for existing in current:
            if existing.object_id == edge.object_id:
                continue
            assert existing.t_valid is not None
            if existing.valid_at(edge.t_valid):
                # SUPERSEDE: new fact starts inside this fact's current validity window.
                self._mark_expired(existing.edge_id, edge.created_at)
                self._insert(existing.bounded_version(edge.t_valid, edge.created_at))
                superseded = True
            elif edge.t_valid < existing.t_valid:
                # Back-dated insert: bound the new edge so it ends where the later fact begins.
                if edge.t_invalid is None or edge.t_invalid > existing.t_valid:
                    edge.t_invalid = existing.t_valid
            # else: existing ends at/before edge.t_valid -> already-correct history, untouched.

        self._insert(edge)
        return EdgeOutcome.SUPERSEDE if superseded else EdgeOutcome.INSERT

    def invalidate_edge(self, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        """R1 for SM-G5: invalidate an edge at valid-time ``t_invalid`` (real-world change).

        Transaction-time-versioned: expire the current version and reinsert it bounded, so the
        prior belief remains queryable via 5b. ``at`` is the transaction time (defaults to now).
        """
        at = now_epoch_s() if at is None else at
        for existing in self._all():
            if existing.edge_id == edge_id and existing.expired_at is None:
                self._mark_expired(edge_id, at)
                self._insert(existing.bounded_version(t_invalid, at))
                return

    def retract_edge(self, edge_id: str, expired_at: int) -> None:
        """R2: expire a row in transaction-time (it was recorded in error). Non-destructive."""
        self._mark_expired(edge_id, expired_at)

    def query_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> Optional[BiTemporalEdge]:
        """5a -- the single fact true at world-time ``t`` per current belief (or None)."""
        matches = self.query_all_valid_at(subject_id, predicate, user_id, t)
        return matches[0] if matches else None

    def query_all_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> List[BiTemporalEdge]:
        """5a (multi) -- all facts true at world-time ``t`` per current belief (``expired_at IS NULL``)."""
        return [e for e in self._current_edges(subject_id, predicate, user_id) if e.valid_at(t)]

    def query_bitemporal(self, subject_id: str, predicate: str, user_id: str, t: int, tt: int) -> List[BiTemporalEdge]:
        """5b -- what the system believed at transaction-time ``tt`` about world-time ``t``."""
        return [
            e
            for e in self._all()
            if e.subject_id == subject_id
            and e.predicate == predicate
            and e.user_id == user_id
            and e.valid_at(t)
            and e.believed_at(tt)
        ]

    # ------------------------------------------------------------------ #
    # Public API (async) -- default delegation; native-async adapters override.
    # ------------------------------------------------------------------ #
    async def aupsert_edge(self, edge: BiTemporalEdge) -> EdgeOutcome:
        return await asyncio.to_thread(self.upsert_edge, edge)

    async def ainvalidate_edge(self, edge_id: str, t_invalid: int, at: Optional[int] = None) -> None:
        await asyncio.to_thread(self.invalidate_edge, edge_id, t_invalid, at)

    async def aretract_edge(self, edge_id: str, expired_at: int) -> None:
        await asyncio.to_thread(self.retract_edge, edge_id, expired_at)

    async def aquery_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> Optional[BiTemporalEdge]:
        return await asyncio.to_thread(self.query_valid_at, subject_id, predicate, user_id, t)

    async def aquery_all_valid_at(self, subject_id: str, predicate: str, user_id: str, t: int) -> List[BiTemporalEdge]:
        return await asyncio.to_thread(self.query_all_valid_at, subject_id, predicate, user_id, t)

    async def aquery_bitemporal(
        self, subject_id: str, predicate: str, user_id: str, t: int, tt: int
    ) -> List[BiTemporalEdge]:
        return await asyncio.to_thread(self.query_bitemporal, subject_id, predicate, user_id, t, tt)


# Re-export for convenience.
__all__ = ["BiTemporalEdgeStore", "BiTemporalEdge", "EdgeOutcome", "upper"]
