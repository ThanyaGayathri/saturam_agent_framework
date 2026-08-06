"""5c -- vector/episodic-tier valid-time filter (SM-R10 SS9).

SS9 decides that the graph is the bi-temporal system-of-record and the vector/episodic tier
carries **filter-only** ``valid_from``/``valid_to`` -- no ``t_invalid``/``expired_at``, no
invalidation authority. That decision was previously asserted but never exercised: the scenario
harness probes graph edges only. This module makes 5c measurable.

Two things are deliberately modelled here:

* **The filter is filter-only.** There is no supersede rule. A ``valid_to`` is only ever written
  as a *derived* value, propagated from a graph invalidation (SS9.1) -- never decided locally.
* **NULL ``valid_from`` means "unknown, assume always valid".** The SS9 migration is additive, so
  every row written before it has ``valid_from IS NULL``. A bare ``valid_from <= :T`` predicate
  evaluates to NULL for those rows and silently drops **every pre-migration memory** from recall.
  ``recall_at_bare`` keeps that bare predicate as a scored counterexample so the regression
  cannot quietly return.

Both sync and async variants are provided for every public method.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

# Epoch-second sentinels mirroring the SQL COALESCE bounds (BIGINT range).
NEG_INF_EPOCH = -9223372036854775808
POS_INF_EPOCH = 9223372036854775807


@dataclass
class VectorMemoryRow:
    """A vector/episodic-tier row: the seed ``UserMemory`` shape plus the two additive columns."""

    memory_id: str
    user_id: str
    memory: str
    created_at: int
    valid_from: Optional[int] = None  # epoch s; NULL = unknown (treated as -infinity)
    valid_to: Optional[int] = None  # epoch s; NULL = open


class VectorTierStore(ABC):
    """Filter-only valid-time store for the vector/episodic tier (no invalidation authority)."""

    @abstractmethod
    def add(self, row: VectorMemoryRow) -> None:
        """Insert a memory row."""

    @abstractmethod
    def set_valid_to(self, memory_id: str, valid_to: Optional[int]) -> None:
        """Propagate a graph invalidation onto the derived vector copy (SS9.1 re-sync).

        This is the *only* way ``valid_to`` is ever written: the graph decides, the vector tier
        records. Idempotent by construction -- writing the same value twice is a no-op.
        """

    @abstractmethod
    def recall_at(self, user_id: str, t: int) -> List[str]:
        """5c -- memory ids whose valid window contains world-time ``t`` (ids sorted)."""

    @abstractmethod
    def recall_at_bare(self, user_id: str, t: int) -> List[str]:
        """The **rejected** bare predicate (``valid_from <= t``), kept as a scored counterexample."""

    # ------------------------------------------------------------------ #
    # Async variants.
    # ------------------------------------------------------------------ #
    async def aadd(self, row: VectorMemoryRow) -> None:
        await asyncio.to_thread(self.add, row)

    async def aset_valid_to(self, memory_id: str, valid_to: Optional[int]) -> None:
        await asyncio.to_thread(self.set_valid_to, memory_id, valid_to)

    async def arecall_at(self, user_id: str, t: int) -> List[str]:
        return await asyncio.to_thread(self.recall_at, user_id, t)

    async def arecall_at_bare(self, user_id: str, t: int) -> List[str]:
        return await asyncio.to_thread(self.recall_at_bare, user_id, t)


class ReferenceVectorTierStore(VectorTierStore):
    """In-memory reference implementation of the 5c filter (no services needed)."""

    def __init__(self) -> None:
        self._rows: List[VectorMemoryRow] = []

    def reset(self) -> None:
        self._rows = []

    def add(self, row: VectorMemoryRow) -> None:
        self._rows.append(row)

    def set_valid_to(self, memory_id: str, valid_to: Optional[int]) -> None:
        for r in self._rows:
            if r.memory_id == memory_id:
                r.valid_to = valid_to

    def recall_at(self, user_id: str, t: int) -> List[str]:
        lo = NEG_INF_EPOCH
        hi = POS_INF_EPOCH
        return sorted(
            r.memory_id
            for r in self._rows
            if r.user_id == user_id
            and (r.valid_from if r.valid_from is not None else lo) <= t
            and t < (r.valid_to if r.valid_to is not None else hi)
        )

    def recall_at_bare(self, user_id: str, t: int) -> List[str]:
        # Deliberately wrong: a NULL valid_from makes the comparison NULL -> row dropped.
        hi = POS_INF_EPOCH
        return sorted(
            r.memory_id
            for r in self._rows
            if r.user_id == user_id
            and r.valid_from is not None
            and r.valid_from <= t
            and t < (r.valid_to if r.valid_to is not None else hi)
        )


class PostgresVectorTierStore(VectorTierStore):
    """Real SQL implementation of the 5c filter against ``memory_vector_tier`` (schema.sql SS4)."""

    def __init__(self, dsn: str) -> None:
        import psycopg

        self.conn = psycopg.connect(dsn, autocommit=True)

    def reset(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE memory_vector_tier;")

    def close(self) -> None:
        self.conn.close()

    def add(self, row: VectorMemoryRow) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO memory_vector_tier "
                "(memory_id, user_id, memory, created_at, valid_from, valid_to) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (row.memory_id, row.user_id, row.memory, row.created_at, row.valid_from, row.valid_to),
            )

    def set_valid_to(self, memory_id: str, valid_to: Optional[int]) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE memory_vector_tier SET valid_to=%s WHERE memory_id=%s",
                (valid_to, memory_id),
            )

    def recall_at(self, user_id: str, t: int) -> List[str]:
        # The ratified 5c predicate -- BOTH bounds COALESCE'd.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id FROM memory_vector_tier "
                "WHERE user_id=%s "
                "  AND COALESCE(valid_from, -9223372036854775808) <= %s "
                "  AND %s < COALESCE(valid_to, 9223372036854775807) "
                "ORDER BY memory_id",
                (user_id, t, t),
            )
            return [r[0] for r in cur.fetchall()]

    def recall_at_bare(self, user_id: str, t: int) -> List[str]:
        # The rejected predicate: no COALESCE on the lower bound.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT memory_id FROM memory_vector_tier "
                "WHERE user_id=%s "
                "  AND valid_from <= %s "
                "  AND %s < COALESCE(valid_to, 9223372036854775807) "
                "ORDER BY memory_id",
                (user_id, t, t),
            )
            return [r[0] for r in cur.fetchall()]
